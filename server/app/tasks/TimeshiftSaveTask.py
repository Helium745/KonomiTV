
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import ClassVar, Literal

import httpx

from app import logging, schemas
from app.config import Config, ServerSettings
from app.utils.mirakc import MirakcClient, WebTimeshiftRecord
from app.utils.TSInformation import TSInformation


# MPEG2-TS のパケット長 (バイト)
# コピー範囲をこの単位に切り上げ/切り下げすることで、TS パケットの途中で切れないようにする
# (パケット境界に揃えても PAT/PMT やキーフレームの位置までは保証されないが、
#  tsreadex や後段の解析処理は次の PAT/PMT を見つけて自動的に同期する前提)
TS_PACKET_SIZE = 188

# 1回の HTTP レスポンス読み取りで受け取るチャンクサイズ
CHUNK_SIZE = 256 * 1024  # 256KB

# 保存先ファイルを配置するサブフォルダ名 (最初の録画フォルダの直下に作成する)
SAVE_SUBFOLDER_NAME = 'Timeshift'

# メモリ上に保持するジョブの最大件数 (これを超えたら古い完了/失敗ジョブから削除する)
MAX_RETAINED_JOBS = 50

# 保存を許可する最小の範囲長 (秒)
## RecordedScanTask.MINIMUM_RECORDING_SECONDS (60秒) 未満のファイルは「録画失敗または切り抜き」とみなされ、
## メタデータ解析後に DB へ登録されないまま静かに無視される (エラーにもならず、保存ジョブは Completed のまま残る)
## さらに、mirakc から取得できる record の duration/file_size はあくまで名目上の値であり、
## 実際に TS の PTS から算出される再生時間はこれよりわずかに短くなることがあるため、
## 60秒ちょうどを許容範囲の下限にすると同じ理由で登録から漏れてしまう
## そのため RecordedScanTask 側の下限より余裕を持たせた閾値で、保存時点で弾く
MINIMUM_SAVE_DURATION_SECONDS = 70


def _AlignDownToPacketBoundary(byte_position: int) -> int:
    """ 指定バイト位置を TS パケット境界に切り下げる """
    return (byte_position // TS_PACKET_SIZE) * TS_PACKET_SIZE


def _AlignUpToPacketBoundary(byte_position: int) -> int:
    """ 指定バイト位置を TS パケット境界に切り上げる """
    remainder = byte_position % TS_PACKET_SIZE
    if remainder == 0:
        return byte_position
    return byte_position + (TS_PACKET_SIZE - remainder)


@dataclass
class _CopySegment:
    """ 1つの mirakc record から切り出してコピーする範囲 (時間範囲指定保存では複数 record にまたがる) """
    record_id: int
    start_byte: int
    end_byte: int


@dataclass
class _SaveJobState:
    """ TimeshiftSaveTask が内部で保持する保存ジョブの状態 """
    id: str
    recorder_id: str
    # 番組単位の保存 (enqueueFullRecord) の場合のみセットされる。時間範囲指定保存では複数 record にまたがるため None
    record_id: int | None
    title: str
    # True: 時間範囲を指定した、番組をまたぐ可能性のある切り出し保存 / False: 番組(record) 1本をまるごと保存
    is_range_cut: bool
    start_time: datetime
    end_time: datetime
    status: Literal['Pending', 'Running', 'Completed', 'Failed']
    progress: float
    file_size_total: int
    file_size_written: int
    error_message: str | None
    created_at: datetime
    # 以下はコピー処理でのみ使う内部状態 (API レスポンスには含めない)
    # 番組単位の保存では要素数 1、時間範囲指定保存では時系列順に複数の record 分の要素が並ぶ
    segments: list[_CopySegment] = field(default_factory=list)
    output_path: Path = field(default_factory=Path)

    def toSchema(self) -> schemas.TimeshiftSaveJob:
        return schemas.TimeshiftSaveJob(
            id = self.id,
            recorder_id = self.recorder_id,
            record_id = self.record_id,
            title = self.title,
            is_range_cut = self.is_range_cut,
            start_time = self.start_time,
            end_time = self.end_time,
            status = self.status,
            progress = self.progress,
            file_size_total = self.file_size_total,
            file_size_written = self.file_size_written,
            error_message = self.error_message,
            created_at = self.created_at,
        )


class TimeshiftSaveTask:
    """
    mirakc タイムシフト録画 (リングバッファ) の内容を、恒久保存用の TS ファイルとして
    録画フォルダ配下に書き出すシングルトンタスク

    タイムシフト録画は DB に保存されずリングバッファの上書きで自動的に消えていくため、
    残しておきたい範囲はこのタスクを介して実ファイルとしてコピーする必要がある
    書き出したファイルは RecordedScanTask (録画フォルダ監視タスク) が自動検知し、
    通常の録画番組と同じように RecordedProgram/RecordedVideo として DB 登録される

    保存には2種類ある:
    - 番組単位の保存 (enqueueFullRecord): mirakc の record (= 1番組) をまるごと保存する
    - 時間範囲指定保存 (enqueueRange): 「1:00〜2:00」のように、番組の区切りとは無関係にレコーダーの
      リングバッファ全体から絶対時刻で範囲を切り出す。範囲が複数の record (番組) にまたがる場合は、
      該当する record を時系列順に必要な範囲だけ連結して1本の TS ファイルにする

    コピーは mirakc の record ストリームエンドポイントへの HTTP Range リクエストのみで行い、
    再エンコードは一切行わない (無劣化・高速コピー)
    mirakc に時刻範囲指定 API が存在しないため、「録画時間に対するバイトサイズの比率」で
    各 record 内の開始/終了バイト位置を近似する (VideoStream.resolveSegmentSourcePosition と同じ考え方)
    このため保存範囲の境界は数秒 (TS パケットの同期に必要な範囲) 程度前後する場合がある

    ジョブはメモリ上でのみ管理される (サーバー再起動で進行中ジョブは失われ、再開されない)
    """

    # シングルトンインスタンス
    __instance: ClassVar[TimeshiftSaveTask | None] = None


    def __new__(cls) -> TimeshiftSaveTask:
        if cls.__instance is None:
            cls.__instance = super().__new__(cls)
        return cls.__instance


    def __init__(self) -> None:
        if not hasattr(self, '_initialized'):
            self._initialized = True
            # ジョブ ID をキーにした状態管理 (挿入順 = 作成順)
            self._jobs: dict[str, _SaveJobState] = {}
            # コピー処理を 1 件ずつ逐次実行するためのキュー (I/O 競合を避ける)
            self._queue: asyncio.Queue[str] = asyncio.Queue()
            self._worker_task: asyncio.Task[None] | None = None


    async def start(self) -> None:
        """タスクを開始する。既に起動済みの場合は何もしない。"""
        if self._worker_task is not None and not self._worker_task.done():
            return
        # 前回起動時にコピーが中断された残骸の .tmp ファイルを掃除する
        self._cleanupStaleTempFiles()
        self._worker_task = asyncio.create_task(self._workerLoop())
        logging.info('[TimeshiftSaveTask] Started.')


    async def stop(self) -> None:
        """タスクを停止する。"""
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._worker_task = None
        logging.info('[TimeshiftSaveTask] Stopped.')


    def _cleanupStaleTempFiles(self) -> None:
        """ 保存先サブフォルダ内に残っている前回起動時の中断済み .tmp ファイルを削除する """
        config = Config()
        if len(config.video.recorded_folders) == 0:
            return
        folder = Path(config.video.recorded_folders[0]) / SAVE_SUBFOLDER_NAME
        if not folder.exists():
            return
        for tmp_file in folder.glob('*.ts.tmp'):
            try:
                tmp_file.unlink()
                logging.info(f'[TimeshiftSaveTask] Removed stale temp file: {tmp_file}')
            except OSError as ex:
                logging.warning(f'[TimeshiftSaveTask] Failed to remove stale temp file {tmp_file}: {ex}')


    async def enqueueFullRecord(self, recorder_id: str, record_id: int) -> schemas.TimeshiftSaveJob:
        """
        タイムシフト record (= 1番組) をまるごと保存するジョブをキューに追加する

        Args:
            recorder_id (str): mirakc 上のタイムシフトレコーダー名
            record_id (int): mirakc 上のタイムシフト record ID

        Returns:
            schemas.TimeshiftSaveJob: 作成された保存ジョブ

        Raises:
            ValueError: record が見つからない、録画時間が短すぎる、録画フォルダが未設定などの場合
        """

        # 循環インポート回避のため遅延インポート (TimeshiftRouter は本タスクをインポートしている)
        from app.routers.TimeshiftRouter import MillisecondToDatetime

        config = Config()
        if len(config.video.recorded_folders) == 0:
            raise ValueError('No recorded folder is configured.')

        # 保存時点の record 状態をスナップショットとして取得する
        # (録画中の record はこの後も size/duration が伸び続けるが、保存対象は取得時点の範囲に固定する)
        client = MirakcClient()
        record = await client.fetch_timeshift_record(recorder_id, record_id)
        if record is None:
            raise ValueError(f'Specified record was not found. (recorder_id={recorder_id}, record_id={record_id})')

        record_start = MillisecondToDatetime(record['startTime'])
        duration_seconds = record['duration'] / 1000
        file_size = record['size']
        if duration_seconds <= 0 or file_size <= 0:
            raise ValueError('The record has not been recorded enough to be saved yet.')
        record_end = record_start + timedelta(seconds=duration_seconds)

        if duration_seconds < MINIMUM_SAVE_DURATION_SECONDS:
            raise ValueError(
                f'This record is too short to save (minimum {MINIMUM_SAVE_DURATION_SECONDS} seconds required).'
            )

        title = TSInformation.formatString(record['program'].get('name', '')) or 'untitled'
        segments = [_CopySegment(record_id=record_id, start_byte=0, end_byte=file_size)]
        output_path = self._resolveOutputPath(config, title, record_start)

        return self._createAndQueueJob(
            recorder_id = recorder_id,
            record_id = record_id,
            title = title,
            is_range_cut = False,
            start_time = record_start,
            end_time = record_end,
            segments = segments,
            output_path = output_path,
        )


    async def enqueueRange(self, recorder_id: str, start_time: datetime, end_time: datetime) -> schemas.TimeshiftSaveJob:
        """
        レコーダーのリングバッファ全体から、番組の区切りとは無関係に絶対時刻で範囲を切り出して保存するジョブをキューに追加する
        指定範囲が複数の record (番組) にまたがる場合は、該当する record を時系列順に必要な範囲だけ連結して1本の TS ファイルにする

        Args:
            recorder_id (str): mirakc 上のタイムシフトレコーダー名
            start_time (datetime): 保存範囲の開始時刻 (壁時計時刻)
            end_time (datetime): 保存範囲の終了時刻 (壁時計時刻)

        Returns:
            schemas.TimeshiftSaveJob: 作成された保存ジョブ

        Raises:
            ValueError: 指定範囲に録画データがない、範囲が短すぎる、録画フォルダが未設定などの場合
        """

        # 循環インポート回避のため遅延インポート (TimeshiftRouter は本タスクをインポートしている)
        from app.routers.TimeshiftRouter import MillisecondToDatetime

        config = Config()
        if len(config.video.recorded_folders) == 0:
            raise ValueError('No recorded folder is configured.')
        if end_time <= start_time:
            raise ValueError('The specified time range is invalid (end must be after start).')

        client = MirakcClient()
        records = await client.fetch_timeshift_records(recorder_id)
        if len(records) == 0:
            raise ValueError(f'Specified recorder was not found or has no records. (recorder_id={recorder_id})')

        # 指定範囲と重なる record のみを対象とし、開始時刻の昇順に並べる
        overlapping: list[tuple[WebTimeshiftRecord, datetime, datetime]] = []
        for record in records:
            record_start = MillisecondToDatetime(record['startTime'])
            duration_seconds = record['duration'] / 1000
            if duration_seconds <= 0 or record['size'] <= 0:
                continue
            record_end = record_start + timedelta(seconds=duration_seconds)
            if record_start < end_time and record_end > start_time:
                overlapping.append((record, record_start, record_end))
        overlapping.sort(key=lambda item: item[1])

        if len(overlapping) == 0:
            raise ValueError('No recorded data was found in the specified time range.')

        # 実際にコピーする範囲は、指定範囲とリングバッファ上に残っている実データの共通部分に丸める
        effective_start = max(start_time, overlapping[0][1])
        effective_end = min(end_time, overlapping[-1][2])
        if effective_end - effective_start < timedelta(seconds=MINIMUM_SAVE_DURATION_SECONDS):
            raise ValueError(
                f'The specified range is too short to save (minimum {MINIMUM_SAVE_DURATION_SECONDS} seconds required).'
            )

        segments: list[_CopySegment] = []
        for record, record_start, record_end in overlapping:
            duration_seconds = record['duration'] / 1000
            file_size = record['size']
            # この record 内で、指定範囲と重なる部分だけを record 先頭からの経過秒数に変換する
            seg_start_seconds = max(0.0, (effective_start - record_start).total_seconds())
            seg_end_seconds = min(duration_seconds, (effective_end - record_start).total_seconds())
            if seg_end_seconds <= seg_start_seconds:
                continue
            start_byte = _AlignDownToPacketBoundary(round(seg_start_seconds / duration_seconds * file_size))
            end_byte = _AlignUpToPacketBoundary(round(seg_end_seconds / duration_seconds * file_size))
            end_byte = min(end_byte, file_size)
            start_byte = min(start_byte, end_byte)
            if end_byte > start_byte:
                segments.append(_CopySegment(record_id=record['id'], start_byte=start_byte, end_byte=end_byte))

        if len(segments) == 0:
            raise ValueError('No recorded data was found in the specified time range.')

        channel_name = await self._resolveChannelName(overlapping[0][0], recorder_id)
        title = f'{effective_start.strftime("%H:%M")}〜{effective_end.strftime("%H:%M")} {channel_name}'
        output_path = self._resolveOutputPath(config, channel_name, effective_start)

        return self._createAndQueueJob(
            recorder_id = recorder_id,
            record_id = None,
            title = title,
            is_range_cut = True,
            start_time = effective_start,
            end_time = effective_end,
            segments = segments,
            output_path = output_path,
        )


    async def _resolveChannelName(self, record: WebTimeshiftRecord, recorder_id: str) -> str:
        """ record のネットワーク/サービス ID から、保存ファイル名・タイトルに使うチャンネル名を解決する """

        # 循環インポート回避のため遅延インポート
        from app.models.Channel import Channel

        program = record['program']
        channel = await Channel.filter(network_id=program['networkId'], service_id=program['serviceId']).first()
        if channel is not None:
            return channel.name
        return recorder_id


    def _createAndQueueJob(
        self,
        recorder_id: str,
        record_id: int | None,
        title: str,
        is_range_cut: bool,
        start_time: datetime,
        end_time: datetime,
        segments: list[_CopySegment],
        output_path: Path,
    ) -> schemas.TimeshiftSaveJob:
        """ ジョブを組み立てて内部状態に登録し、コピーキューに積む (enqueueFullRecord / enqueueRange の共通処理) """

        file_size_total = sum(segment.end_byte - segment.start_byte for segment in segments)
        job = _SaveJobState(
            id = str(uuid.uuid4()),
            recorder_id = recorder_id,
            record_id = record_id,
            title = title,
            is_range_cut = is_range_cut,
            start_time = start_time,
            end_time = end_time,
            status = 'Pending',
            progress = 0.0,
            file_size_total = file_size_total,
            file_size_written = 0,
            error_message = None,
            created_at = datetime.now(start_time.tzinfo),
            segments = segments,
            output_path = output_path,
        )
        self._jobs[job.id] = job
        self._queue.put_nowait(job.id)
        logging.info(
            f'[TimeshiftSaveTask] Enqueued job {job.id} (recorder_id={recorder_id}, record_id={record_id}, '
            f'is_range_cut={is_range_cut}, segments={len(segments)}, total_bytes={file_size_total}) -> {output_path}'
        )

        return job.toSchema()


    def _resolveOutputPath(self, config: ServerSettings, label: str, time_label: datetime) -> Path:
        """
        保存先の出力ファイルパスを決定する (同名ファイルが存在する場合は連番を付与する)

        ファイルシステム上の既存ファイルだけでなく、まだコピー中/待機中の他ジョブが使っている
        出力先パスとも衝突しないようにする
        (このメソッドは enqueueFullRecord()/enqueueRange() 内で await を挟まずに呼ばれるため、他の呼び出しと競合しない)
        """

        # 循環インポート回避のため遅延インポート
        from app.routers.ReservationsRouter import sanitize_filename

        folder = Path(config.video.recorded_folders[0]) / SAVE_SUBFOLDER_NAME
        folder.mkdir(parents=True, exist_ok=True)

        # コピー未完了 (Pending/Running) の他ジョブが既に使っている出力先パス
        reserved_paths = {job.output_path for job in self._jobs.values() if job.status in ('Pending', 'Running')}

        safe_label = sanitize_filename(label)
        time_str = time_label.strftime('%Y%m%d_%H%M%S')
        base_name = f'{time_str}_{safe_label}'
        output_path = folder / f'{base_name}.ts'

        counter = 1
        while (output_path in reserved_paths or output_path.exists() or
               output_path.with_name(output_path.name + '.tmp').exists()):
            output_path = folder / f'{base_name}_{counter}.ts'
            counter += 1

        return output_path


    def getJobs(self) -> list[schemas.TimeshiftSaveJob]:
        """ 保存ジョブの一覧を作成日時の新しい順で返す """
        jobs = sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)
        return [job.toSchema() for job in jobs]


    async def _workerLoop(self) -> None:
        """ キューに積まれたジョブを 1 件ずつ逐次コピーするワーカーループ """
        while True:
            job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            if job is not None:
                await self._runJob(job)
            self._trimOldJobs()


    async def _runJob(self, job: _SaveJobState) -> None:
        """ 1件の保存ジョブを実行し、mirakc から取得したバイト範囲 (複数 record にまたがる場合は連結) を .tmp ファイルへコピーしてから確定させる """

        job.status = 'Running'
        logging.info(f'[TimeshiftSaveTask] Started copying job {job.id} ({len(job.segments)} segment(s)) -> {job.output_path}')

        tmp_path = job.output_path.with_name(job.output_path.name + '.tmp')

        try:
            # 同期 I/O (httpx.Client によるストリーミング GET) をワーカースレッドへオフロードし、
            # イベントループをブロックしないようにする
            await asyncio.to_thread(self._copySegmentsToFile, job, tmp_path)
            # コピー完了後に確定ファイル名へ rename する (同一ディレクトリ内の rename は atomic)
            # これにより RecordedScanTask は完成したファイルのみを検知する (.tmp はスキャン対象外)
            tmp_path.rename(job.output_path)
            job.status = 'Completed'
            job.progress = 1.0
            logging.info(f'[TimeshiftSaveTask] Completed job {job.id} -> {job.output_path}')
        except Exception as ex:
            job.status = 'Failed'
            job.error_message = str(ex)
            logging.error(f'[TimeshiftSaveTask] Failed job {job.id}: {ex}')
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass


    def _copySegmentsToFile(self, job: _SaveJobState, tmp_path: Path) -> None:
        """
        job.segments を時系列順に mirakc から取得し、1つの tmp_path へ連結して書き出す
        同期 I/O のため、呼び出し側で asyncio.to_thread() によりワーカースレッド上で実行すること

        Args:
            job (_SaveJobState): 対象ジョブ (進捗をここに書き込む)
            tmp_path (Path): 書き出し先の一時ファイルパス
        """

        client_wrapper = MirakcClient()
        written_total = 0

        with httpx.Client(timeout=httpx.Timeout(10.0, read=60.0)) as client, tmp_path.open('wb') as f:
            for segment in job.segments:
                url = client_wrapper.get_timeshift_record_stream_url(job.recorder_id, segment.record_id)
                bytes_needed = segment.end_byte - segment.start_byte
                # mirakc の record ストリームエンドポイントは Range の終端指定を無視し、start 位置以降を全て返す可能性があるため
                # (KonomiTV 内の他の利用箇所 (HttpRangeFile) も終端なしの 'bytes=start-' しか送っていない)、
                # 終端はヒントとして送りつつも、実際に書き込むバイト数は自前で bytes_needed に達した時点で強制的に打ち切る
                headers = {'Range': f'bytes={segment.start_byte}-{segment.end_byte - 1}'}

                with client.stream('GET', url, headers=headers) as response:
                    response.raise_for_status()
                    written_in_segment = 0
                    for chunk in response.iter_bytes(chunk_size=CHUNK_SIZE):
                        if written_in_segment >= bytes_needed:
                            break
                        remaining = bytes_needed - written_in_segment
                        if len(chunk) > remaining:
                            chunk = chunk[:remaining]
                        f.write(chunk)
                        written_in_segment += len(chunk)
                        written_total += len(chunk)
                        job.file_size_written = written_total
                        job.progress = min(1.0, written_total / job.file_size_total) if job.file_size_total > 0 else 1.0


    def _trimOldJobs(self) -> None:
        """ 保持するジョブ数が上限を超えた場合、完了/失敗した古いジョブから削除する (実行中/待機中のジョブは残す) """
        if len(self._jobs) <= MAX_RETAINED_JOBS:
            return
        removable = sorted(
            (job for job in self._jobs.values() if job.status in ('Completed', 'Failed')),
            key = lambda job: job.created_at,
        )
        excess = len(self._jobs) - MAX_RETAINED_JOBS
        for job in removable[:excess]:
            del self._jobs[job.id]
