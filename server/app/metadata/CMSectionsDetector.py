
from __future__ import annotations

import asyncio
import concurrent.futures
import pathlib
import re
import subprocess
import time
from typing import ClassVar, cast

import anyio
import cv2
import numpy as np
import typer
from numpy.typing import NDArray

from app import logging, schemas
from app.config import Config, LoadConfig
from app.constants import LIBRARY_PATH
from app.models.RecordedVideo import RecordedVideo
from app.utils import ShutdownProcessPoolExecutor


# 局ロゴの判定モデル: (bbox=(y0, y1, x0, x1), 平均勾配テンプレート(bbox 内を平均0に中心化した1次元ベクトル))
## ロゴの位置 (bbox) と「そこにロゴがある時の勾配パターン」をまとめて表す
LogoModel = tuple[tuple[int, int, int, int], NDArray[np.float32]]


class CMSectionsDetector:
    """
    録画 TS ファイルに含まれる CM 区間を検出するクラス
    録画ファイルと同じファイル名で .chapter.txt が保存されていればそこから CM 区間情報を取得し、
    .chapter.txt が存在しない場合は「局ロゴの表示/非表示 + 無音(ノンモン)」から自前検出する

    検出の原理:
    - 多くの民放は本編中のみ半透明の局ロゴを常時表示し、CM 中は消す
      よって「ロゴが消えている区間 = CM」と判定できる
    - ロゴマスクは局ごとの事前データ (.lgd) を使わず、動画自身のフレームから自己検出する
      (コーナー領域で多数フレームに渡り持続的にエッジがある領域 = 局ロゴ)
    - 全編を一定レートでサンプリングし、フレームごとの局ロゴ有無を「学習した勾配テンプレートとの
      コサイン類似度」で判定してロゴ有無の時系列を作る
    - 本編でも白背景/激しい動きの場面ではロゴが一瞬読めなくなるため、時系列をそのまま使わず
      一定時間窓で平滑化し「一定時間ロゴが消え続けている区間」だけを CM とみなす (単発の消失は無視)
    - 番組と CM の切れ目には必ず「ノンモン」= 約0.5秒の無音区間が挿入されるため、
      CM 区間の端をその無音位置にスナップして境界を正確にする
    """

    # 解析用フレームの解像度 (縦横比は無視して固定サイズに縮小する)
    ## 学習・判定で常に同じ歪みが掛かるため、勾配相関ベースのロゴ判定には影響しない
    ## 局ロゴは半透明で細い線が多く低解像度では潰れてしまうため、faint なロゴでも拾えるよう 640x360 を採用する
    ANALYSIS_WIDTH: ClassVar[int] = 640
    ANALYSIS_HEIGHT: ClassVar[int] = 360
    # 全編をサンプリングする際のフレームレート (0.5秒間隔)。ロゴ学習・ロゴ有無時系列の両方に使う
    SAMPLE_FPS: ClassVar[float] = 2.0

    # 無音検出 (FFmpeg silencedetect) の設定
    ## ノンモンはほぼ完全な無音のため、しきい値はかなり低め (-50dB) に取る
    SILENCE_NOISE_DB: ClassVar[float] = -50.0  # 無音とみなす音量のしきい値 (dB)
    SILENCE_MIN_DURATION: ClassVar[float] = 0.4  # 無音とみなす最短の継続時間 (秒、ノンモン ~0.5秒を想定)
    MAX_SILENCES: ClassVar[int] = 1000  # 安全のため扱う無音区間数の上限

    # 局ロゴの位置 (マスク) 自己検出の設定
    ## 四隅の帯の中で「多数フレームに渡り持続的にエッジがある領域」を局ロゴの位置とみなす
    LOGO_CANNY_LOW: ClassVar[int] = 40  # Canny エッジ検出の下側しきい値
    LOGO_CANNY_HIGH: ClassVar[int] = 120  # Canny エッジ検出の上側しきい値
    LOGO_LEARN_MAX_FRAMES: ClassVar[int] = 600  # ロゴ学習に使うフレームの最大枚数 (多すぎる場合は間引く)
    LOGO_CORNER_RATIO: ClassVar[float] = 0.30  # ロゴ探索対象とする四隅の帯の割合 (画面端から 30%)
    ## ロゴ位置は「エッジ出現割合」の絶対値ではなく、そのフレーム群での最大値に対する相対しきい値で決める
    ## (半透明ロゴだと最大でも 0.4 程度にしかならないため、固定の高いしきい値では検出できない)
    LOGO_PERSIST_FLOOR: ClassVar[float] = 0.20  # ロゴ画素とみなすエッジ出現割合の下限 (これ未満はコンテンツ由来のノイズ)
    LOGO_PERSIST_REL: ClassVar[float] = 0.60  # エッジ出現割合の最大値に対する相対しきい値
    LOGO_MIN_PIXELS: ClassVar[int] = 30  # 安定した局ロゴとみなすのに必要なロゴマスクの最小ピクセル数
    LOGO_BBOX_PADDING: ClassVar[int] = 4  # ロゴ判定用に切り出す矩形 (bbox) のマスク外側への余白

    # 局ロゴの有無判定の設定
    ## faint な半透明ロゴでも安定して判定できるよう、二値エッジの重なりではなく
    ## 「学習した平均勾配 (Sobel) テンプレート」との bbox 内コサイン類似度でロゴの有無を判定する
    ## ロゴがある時はフレームの勾配がテンプレートと強く一致し、ない時はコンテンツ由来でほぼ無相関になる
    LOGO_PRESENCE_COSINE: ClassVar[float] = 0.20  # ロゴありと判定する勾配テンプレートとのコサイン類似度のしきい値

    # ロゴ有無時系列から CM 区間を組み立てる際の設定
    SMOOTH_WINDOW_SEC: ClassVar[float] = 8.0  # ロゴ有無時系列を平滑化する時間窓 (秒)。CM は一定時間ロゴが消え続ける前提
    LOGO_ABSENT_FRACTION: ClassVar[float] = 0.35  # 平滑化後、この割合を下回るとロゴ消失(=CM候補)とみなす
    SILENCE_SNAP_TOLERANCE: ClassVar[float] = 4.0  # CM 区間の端を無音位置にスナップする許容範囲 (秒)
    CM_MERGE_GAP: ClassVar[float] = 8.0  # この間隔未満で隣り合う CM 候補は1つにまとめる (秒)
    MIN_CM_DURATION: ClassVar[float] = 30.0  # CM 区間とみなす最短の長さ (秒、本編中の一時的なロゴ消失を誤検出しないため)
    MAX_CM_DURATION: ClassVar[float] = 260.0  # CM 区間とみなす最長の長さ (秒、これを超える無ロゴ区間はロゴ無し番組の可能性が高い)

    # FFmpeg サブプロセスのタイムアウト時間 (秒)
    FFMPEG_SAMPLE_TIMEOUT: ClassVar[int] = 900  # 全編フレームサンプリング (映像デコード) のタイムアウト
    FFMPEG_SILENCE_TIMEOUT: ClassVar[int] = 600  # 無音検出 (全編音声) のタイムアウト


    def __init__(
        self,
        file_path: anyio.Path,
        duration_sec: float,
        service_id: int | None = None,
        container_format: str = 'MPEG-TS',
    ) -> None:
        """
        録画 TS ファイルに含まれる CM 区間を検出するクラスを初期化する

        Args:
            file_path (anyio.Path): 動画ファイルのパス
            duration_sec (float): 動画の再生時間(秒)
            service_id (int | None): 録画対象サービス ID (将来的な対象サービス選択用に保持する)
            container_format (str): コンテナ形式 ('MPEG-TS' / 'MPEG-4')。CM 検出は放送 TS 前提のため MPEG-TS のみ対象とする
        """

        # 動画ファイルのパス (FFmpeg への入力・DB レコード特定に使う)
        self.file_path = file_path
        # 動画の再生時間 (秒)。CM 区間の末尾クランプや妥当性フィルタに使う
        self.duration_sec = float(duration_sec)
        # 録画対象サービス ID。現状の自前検出では未使用だが、将来 FFmpeg の program 選択に使えるよう保持する
        self.service_id = service_id
        # コンテナ形式。MPEG-TS 以外は自前検出をスキップする
        self.container_format = container_format


    async def detectAndSave(self) -> None:
        """
        録画ファイルの CM 区間を検出し、データベースに保存する
        """

        start_time = time.time()
        logging.info(f'{self.file_path}: Detecting CM sections...')
        try:
            # 録画ファイルに対応するチャプターファイル (.chapter.txt) がもしあれば解析し、CM 区間情報を取得する
            ## 自前で解析すると計算コストが高いので、もしチャプターファイルがあればそれを優先的に使う
            ## .chapter.txt は Amatsukaze でエンコードした際に設定次第で自動生成される
            cm_sections = await self.__detectFromChapterFile()

            # チャプターファイルが存在しない場合、自前で解析を試みる
            ## 局ロゴの表示/非表示 + 無音(ノンモン) から CM 区間を検出する
            if not cm_sections:
                cm_sections = await self.__detectByLogoAndSilenceAnalysis()

            # 自前でも解析できなかった（解析に失敗した）or CM 区間が1つも検出されなかった場合、
            # バックグラウンド解析処理が再度実行された際の再解析を回避するために [] を設定する
            ## [] は解析したが CM 区間がなかった/検出に失敗したことを表す
            ## CM 区間解析はかなり計算コストが高い処理のため、一度解析に失敗した録画ファイルは再解析しない
            if cm_sections is None:
                cm_sections = []

            # 検出結果をログに出力
            for cm_section in cm_sections:
                logging.debug(f'{self.file_path}: CM section detected: {cm_section["start_time"]} - {cm_section["end_time"]}')

            # 検出結果をデータベースに保存
            ## ファイルパスから対応する RecordedVideo レコードを取得
            db_recorded_video = await RecordedVideo.get_or_none(file_path=str(self.file_path))
            if db_recorded_video is not None:
                # CM 区間情報を更新
                # 検出できなかった場合も必ず [] を設定する
                db_recorded_video.cm_sections = cm_sections
                await db_recorded_video.save()
                if len(cm_sections) > 0:
                    logging.info(f'{self.file_path}: Saved {len(cm_sections)} CM sections. ({time.time() - start_time:.2f} sec)')
                else:
                    logging.info(f'{self.file_path}: No CM sections detected. ({time.time() - start_time:.2f} sec)')
            else:
                logging.warning(f'{self.file_path}: RecordedVideo record not found.')

        except Exception as ex:
            logging.error(f'{self.file_path}: Error saving CM sections to DB:', exc_info=ex)


    async def __detectByLogoAndSilenceAnalysis(self) -> list[schemas.CMSection] | None:
        """
        録画ファイルの CM 区間を「局ロゴの表示/非表示 + 無音(ノンモン)」で自前検出する
        重い FFmpeg デコード・OpenCV 処理は CPU-bound のため ProcessPoolExecutor 上で実行する

        Returns:
            list[schemas.CMSection] | None: 解析できた場合は CM 区間のリスト (0件なら [])、解析不能時は None
        """

        # CM 検出は放送 TS 前提のため、MPEG-TS 以外 (エンコード済み MPEG-4 等) は対象外とする
        if self.container_format != 'MPEG-TS':
            logging.debug(f'{self.file_path}: Skipping CM detection for non-MPEG-TS container ({self.container_format}).')
            return None

        # CPU-bound な検出処理を別プロセスで実行する
        ## リクエスト切断時に ProcessPoolExecutor.__exit__() が同期的に子プロセス終了を待つとイベントループが止まるため、
        ## コンテキストマネージャーは使わず、キャンセル時だけ待機なしで終了処理に入る
        loop = asyncio.get_running_loop()
        executor = concurrent.futures.ProcessPoolExecutor(max_workers=1)
        should_wait_executor = True
        try:
            return await loop.run_in_executor(executor, self._detectByLogoAndSilenceAnalysis)
        except asyncio.CancelledError:
            should_wait_executor = False
            await ShutdownProcessPoolExecutor(executor, is_cancelled=True)
            raise
        finally:
            if should_wait_executor is True:
                await ShutdownProcessPoolExecutor(executor, is_cancelled=False)


    def _detectByLogoAndSilenceAnalysis(self) -> list[schemas.CMSection] | None:
        """
        局ロゴの表示/非表示 + 無音(ノンモン) で CM 区間を検出する (別プロセスでの実行エントリーポイント)
        ProcessPoolExecutor で実行されるエントリーポイントなので、あえて prefix のアンダースコアは1つとしている
        (別プロセスで実行されるため、__ を付けるとマングリングにより正常に実行できない)

        Returns:
            list[schemas.CMSection] | None: 解析できた場合は CM 区間のリスト (0件なら [])、解析不能時は None
        """

        # もし Config() の実行時に AssertionError が発生した場合は、LoadConfig() を実行してサーバー設定データをロードする
        ## ProcessPoolExecutor で実行した場合、自動リロードモード時にグローバル変数が引き継がれないことがあるため
        try:
            Config()
        except AssertionError:
            LoadConfig(bypass_validation=True)

        try:
            start_time = time.time()

            # 1. 全編を一定レートでサンプリングする (ロゴ学習・ロゴ有無時系列の両方に使い回す)
            frames = self.__extractSampledFrames()
            if len(frames) == 0:
                logging.warning(f'{self.file_path}: Failed to sample frames for CM detection.')
                return None

            # 2. サンプリングしたフレームから局ロゴの判定モデルを自己学習する
            ## ロゴが検出できない (ロゴ無し局・不安定) 場合は、ロゴベースの CM 検出自体が成立しないため None を返す
            learn_frames = frames
            if len(learn_frames) > self.LOGO_LEARN_MAX_FRAMES:
                stride = len(learn_frames) // self.LOGO_LEARN_MAX_FRAMES
                learn_frames = learn_frames[::stride][:self.LOGO_LEARN_MAX_FRAMES]
            logo_model = self.__learnLogoTemplate(learn_frames)
            if logo_model is None:
                logging.info(f'{self.file_path}: No stable station logo found. CM detection is not applicable.')
                return None
            (y0, y1, x0, x1), _ = logo_model
            logging.debug(f'{self.file_path}: Learned logo model from {len(learn_frames)} frames. bbox=({y0},{x0})-({y1},{x1})')

            # 3. フレームごとの局ロゴ有無を判定してロゴ有無の時系列を作る
            presence = np.array([1.0 if self.__isLogoPresent(frame, logo_model) else 0.0 for frame in frames], dtype=np.float32)

            # 4. 時系列を平滑化し、「一定時間ロゴが消え続けている区間」を CM 候補として抽出する
            raw_intervals = self.__findLogoAbsentIntervals(presence)
            if len(raw_intervals) == 0:
                logging.info(f'{self.file_path}: No sustained logo-absent interval found. Treating as no CM.')
                return []

            # 5. 全編の音声から無音 (ノンモン) 区間を検出する (CM 区間の端をこの位置にスナップして境界を正確にする)
            silence_centers = self.__detectSilences()

            # 6. CM 候補の端を無音位置にスナップし、近接区間をマージ・長さフィルタして CM 区間に整える
            cm_sections = self.__buildCMSections(raw_intervals, silence_centers)
            logging.info(
                f'{self.file_path}: CM detection finished. '
                f'[raw: {len(raw_intervals)}, silences: {len(silence_centers)}, cm_sections: {len(cm_sections)}] '
                f'({time.time() - start_time:.2f} sec)'
            )
            return cm_sections

        except Exception as ex:
            logging.error(f'{self.file_path}: Error during CM detection:', exc_info=ex)
            return None


    def __runFFmpeg(self, args: list[str], timeout: int) -> subprocess.CompletedProcess[bytes] | None:
        """
        FFmpeg を同期実行し、結果を返す共通ヘルパー

        Args:
            args (list[str]): FFmpeg に渡す引数 (実行ファイルパスは含めない)
            timeout (int): タイムアウト時間 (秒)

        Returns:
            subprocess.CompletedProcess[bytes] | None: 実行結果 (タイムアウト・例外時は None)
        """

        try:
            return subprocess.run(
                [LIBRARY_PATH['FFmpeg'], *args],
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            logging.warning(f'{self.file_path}: FFmpeg process timed out after {timeout} seconds.')
            return None
        except Exception as ex:
            logging.error(f'{self.file_path}: Failed to run FFmpeg:', exc_info=ex)
            return None


    def __extractSampledFrames(self) -> list[NDArray[np.uint8]]:
        """
        全編を SAMPLE_FPS のレートでグレースケール・縮小しながらサンプリングする
        インデックス i のフレームの時刻は i / SAMPLE_FPS 秒に対応する

        Returns:
            list[NDArray[np.uint8]]: グレースケールフレームのリスト
        """

        proc = self.__runFFmpeg([
            '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-i', str(self.file_path),
            '-an',
            '-vf', f'fps={self.SAMPLE_FPS},scale={self.ANALYSIS_WIDTH}:{self.ANALYSIS_HEIGHT}',
            '-pix_fmt', 'gray',
            '-f', 'rawvideo',
            'pipe:1',
        ], timeout=self.FFMPEG_SAMPLE_TIMEOUT)
        if proc is None or proc.returncode != 0:
            return []

        # rawvideo (gray) 出力を1フレームずつ配列に変換する
        raw = proc.stdout
        frame_size = self.ANALYSIS_WIDTH * self.ANALYSIS_HEIGHT
        frame_count = len(raw) // frame_size
        frames: list[NDArray[np.uint8]] = []
        for i in range(frame_count):
            buffer = raw[i * frame_size:(i + 1) * frame_size]
            frames.append(np.frombuffer(buffer, dtype=np.uint8).reshape(self.ANALYSIS_HEIGHT, self.ANALYSIS_WIDTH))
        return frames


    def __detectSilences(self) -> list[float]:
        """
        FFmpeg の silencedetect フィルタで全編の無音区間を検出し、その中央時刻のリストを返す

        Returns:
            list[float]: 無音区間の中央時刻 (秒) のリスト (時刻昇順)
        """

        # -map 0:a:0? で先頭の音声ストリームを対象にする (存在しない場合もエラーにしない)
        proc = self.__runFFmpeg([
            '-hide_banner', '-nostats',
            '-i', str(self.file_path),
            '-map', '0:a:0?',
            '-af', f'silencedetect=noise={self.SILENCE_NOISE_DB}dB:d={self.SILENCE_MIN_DURATION}',
            '-f', 'null', '-',
        ], timeout=self.FFMPEG_SILENCE_TIMEOUT)
        if proc is None:
            return []

        # silencedetect の結果は stderr にログ出力される
        stderr_text = proc.stderr.decode('utf-8', errors='ignore')
        silence_starts = [float(m) for m in re.findall(r'silence_start:\s*([-\d.]+)', stderr_text)]
        silence_ends = [float(m) for m in re.findall(r'silence_end:\s*([-\d.]+)', stderr_text)]

        # start と end をペアにして、その中央時刻を無音位置とする
        centers: list[float] = []
        for start_sec, end_sec in zip(silence_starts, silence_ends):
            centers.append((start_sec + end_sec) / 2)

        centers.sort()
        # 安全のため無音区間数に上限を設ける
        if len(centers) > self.MAX_SILENCES:
            centers = centers[:self.MAX_SILENCES]
        return centers


    @staticmethod
    def __sobelMagnitude(frame: NDArray[np.uint8]) -> NDArray[np.float32]:
        """
        グレースケールフレームの Sobel 勾配強度マップを計算する

        Args:
            frame (NDArray[np.uint8]): グレースケールフレーム

        Returns:
            NDArray[np.float32]: 勾配強度マップ
        """

        grad_x = cv2.Sobel(frame, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(frame, cv2.CV_32F, 0, 1, ksize=3)
        return cast(NDArray[np.float32], cv2.magnitude(grad_x, grad_y))


    def __learnLogoTemplate(self, frames: list[NDArray[np.uint8]]) -> LogoModel | None:
        """
        フレーム群から局ロゴの判定モデル (位置 bbox + 平均勾配テンプレート) を自己学習する
        四隅の帯の中で「多数フレームに渡り持続的にエッジがある領域」を局ロゴの位置とみなし、
        その位置での平均勾配 (Sobel) をロゴの見た目のテンプレートとする

        Args:
            frames (list[NDArray[np.uint8]]): 学習に使うグレースケールフレームのリスト

        Returns:
            LogoModel | None: 局ロゴの判定モデル。安定したロゴが見つからない場合は None
        """

        # 各フレームのエッジ出現を累積して「エッジ出現割合」を、勾配を累積して平均勾配テンプレートを同時に求める
        edge_accumulator = np.zeros((self.ANALYSIS_HEIGHT, self.ANALYSIS_WIDTH), dtype=np.float32)
        gradient_accumulator = np.zeros((self.ANALYSIS_HEIGHT, self.ANALYSIS_WIDTH), dtype=np.float32)
        for frame in frames:
            edge_accumulator += (cv2.Canny(frame, self.LOGO_CANNY_LOW, self.LOGO_CANNY_HIGH) > 0).astype(np.float32)
            gradient_accumulator += self.__sobelMagnitude(frame)
        edge_frequency = edge_accumulator / len(frames)
        gradient_template = gradient_accumulator / len(frames)

        # ロゴ画素とみなすしきい値は、フレーム群でのエッジ出現割合の最大値に対する相対値で決める
        ## 半透明ロゴだと最大でも 0.4 程度にしかならないため、固定の高いしきい値では検出できない
        threshold = max(self.LOGO_PERSIST_FLOOR, float(edge_frequency.max()) * self.LOGO_PERSIST_REL)
        persistent = edge_frequency >= threshold

        # 四隅の帯をそれぞれ評価し、最もロゴ候補ピクセルが多いコーナーを局ロゴの位置とみなす
        corner_y = int(self.ANALYSIS_HEIGHT * self.LOGO_CORNER_RATIO)
        corner_x = int(self.ANALYSIS_WIDTH * self.LOGO_CORNER_RATIO)
        corners: dict[str, tuple[slice, slice]] = {
            'TopLeft': (slice(0, corner_y), slice(0, corner_x)),
            'TopRight': (slice(0, corner_y), slice(self.ANALYSIS_WIDTH - corner_x, self.ANALYSIS_WIDTH)),
            'BottomLeft': (slice(self.ANALYSIS_HEIGHT - corner_y, self.ANALYSIS_HEIGHT), slice(0, corner_x)),
            'BottomRight': (slice(self.ANALYSIS_HEIGHT - corner_y, self.ANALYSIS_HEIGHT), slice(self.ANALYSIS_WIDTH - corner_x, self.ANALYSIS_WIDTH)),
        }
        best_corner: tuple[slice, slice] | None = None
        best_pixels = 0
        for region in corners.values():
            pixels = int(persistent[region].sum())
            if pixels > best_pixels:
                best_pixels = pixels
                best_corner = region

        # 安定した局ロゴとみなせるだけのピクセル数が無ければ、ロゴ無しと判断する
        if best_corner is None or best_pixels < self.LOGO_MIN_PIXELS:
            return None

        # 選んだコーナーの帯の中の持続エッジ領域を囲む矩形 (bbox) を求める (余白を少し付ける)
        corner_mask = np.zeros((self.ANALYSIS_HEIGHT, self.ANALYSIS_WIDTH), dtype=np.bool_)
        corner_mask[best_corner] = persistent[best_corner]
        rows, cols = np.where(corner_mask)
        y0 = max(0, int(rows.min()) - self.LOGO_BBOX_PADDING)
        y1 = min(self.ANALYSIS_HEIGHT, int(rows.max()) + self.LOGO_BBOX_PADDING + 1)
        x0 = max(0, int(cols.min()) - self.LOGO_BBOX_PADDING)
        x1 = min(self.ANALYSIS_WIDTH, int(cols.max()) + self.LOGO_BBOX_PADDING + 1)

        # bbox 内の平均勾配テンプレートを平均0に中心化した1次元ベクトルとして保持する
        ## コサイン類似度で判定するため、あらかじめ中心化しておく
        template_vector = gradient_template[y0:y1, x0:x1].flatten()
        template_vector = (template_vector - float(template_vector.mean())).astype(np.float32)
        return ((y0, y1, x0, x1), template_vector)


    def __isLogoPresent(self, frame: NDArray[np.uint8], logo_model: LogoModel) -> bool:
        """
        1フレームに局ロゴが表示されているかを、学習済み勾配テンプレートとのコサイン類似度で判定する
        ロゴがある時はフレームの勾配がテンプレートと強く一致し、ない時はコンテンツ由来でほぼ無相関になる

        Args:
            frame (NDArray[np.uint8]): 判定対象のグレースケールフレーム
            logo_model (LogoModel): 学習済みの局ロゴ判定モデル

        Returns:
            bool: ロゴが表示されていれば True
        """

        (y0, y1, x0, x1), template_vector = logo_model

        # 判定対象フレームの bbox 内勾配を、テンプレートと同じく平均0に中心化する
        frame_vector = self.__sobelMagnitude(frame)[y0:y1, x0:x1].flatten()
        frame_vector = frame_vector - float(frame_vector.mean())

        # コサイン類似度を計算する (分母が 0 の場合は無相関=ロゴ無しとみなす)
        denominator = float(np.linalg.norm(frame_vector)) * float(np.linalg.norm(template_vector))
        if denominator == 0.0:
            return False
        cosine_similarity = float(np.dot(frame_vector, template_vector)) / denominator
        return cosine_similarity >= self.LOGO_PRESENCE_COSINE


    def __findLogoAbsentIntervals(self, presence: NDArray[np.float32]) -> list[list[float]]:
        """
        ロゴ有無の時系列を平滑化し、「一定時間ロゴが消え続けている区間」を CM 候補として抽出する
        本編でも白背景/激しい動きで一瞬ロゴが読めなくなることがあるため、単発の消失を無視できるよう平滑化する

        Args:
            presence (NDArray[np.float32]): フレームごとのロゴ有無 (1.0=あり / 0.0=なし) の時系列

        Returns:
            list[list[float]]: ロゴ消失区間 [開始時刻, 終了時刻] (秒) のリスト
        """

        # 一定時間窓での「ロゴあり割合」に平滑化する
        window = max(1, int(self.SMOOTH_WINDOW_SEC * self.SAMPLE_FPS))
        kernel = np.ones(window, dtype=np.float32) / window
        smoothed = np.convolve(presence, kernel, mode='same')

        # ロゴあり割合が一定を下回る (=ロゴが消え続けている) サンプルを CM 候補とする
        is_absent = smoothed < self.LOGO_ABSENT_FRACTION

        # 連続する CM 候補サンプルを1つの区間にまとめる
        intervals: list[list[float]] = []
        index = 0
        sample_count = len(is_absent)
        while index < sample_count:
            if is_absent[index]:
                end_index = index
                while end_index < sample_count and is_absent[end_index]:
                    end_index += 1
                intervals.append([index / self.SAMPLE_FPS, (end_index - 1) / self.SAMPLE_FPS])
                index = end_index
            else:
                index += 1
        return intervals


    def __buildCMSections(self, raw_intervals: list[list[float]], silence_centers: list[float]) -> list[schemas.CMSection]:
        """
        ロゴ消失区間 (生) の端を無音位置にスナップし、近接区間をマージ・長さフィルタして CM 区間に整える

        Args:
            raw_intervals (list[list[float]]): ロゴ消失区間 [開始時刻, 終了時刻] (秒) のリスト
            silence_centers (list[float]): 無音区間の中央時刻 (秒) のリスト

        Returns:
            list[schemas.CMSection]: CM 区間のリスト
        """

        # 各区間の端を、許容範囲内で最も近い無音位置にスナップする (ノンモンに合わせて境界を正確にする)
        snapped: list[list[float]] = [
            [self.__snapToSilence(start, silence_centers), self.__snapToSilence(end, silence_centers)]
            for start, end in raw_intervals
        ]

        # 近接する区間 (本編側の一瞬のロゴ復帰などで分断されたもの) を1つにまとめる
        merged: list[list[float]] = []
        for interval in snapped:
            if merged and interval[0] - merged[-1][1] < self.CM_MERGE_GAP:
                merged[-1][1] = interval[1]
            else:
                merged.append(interval)

        # 長さの妥当性でフィルタして CM 区間を組み立てる
        cm_sections: list[schemas.CMSection] = []
        for start, end in merged:
            start = max(0.0, start)
            end = min(self.duration_sec, end)
            duration = end - start
            # 短すぎる区間は本編中の一時的なロゴ消失、長すぎる区間はロゴ無し番組の可能性が高いため除外する
            if self.MIN_CM_DURATION <= duration <= self.MAX_CM_DURATION:
                cm_sections.append({
                    'start_time': round(start, 3),
                    'end_time': round(end, 3),
                })
        return cm_sections


    def __snapToSilence(self, time_sec: float, silence_centers: list[float]) -> float:
        """
        指定時刻を、許容範囲内で最も近い無音位置にスナップする

        Args:
            time_sec (float): スナップ対象の時刻 (秒)
            silence_centers (list[float]): 無音区間の中央時刻 (秒) のリスト

        Returns:
            float: スナップ後の時刻 (許容範囲内に無音が無ければ元の時刻をそのまま返す)
        """

        best_time = time_sec
        best_distance = self.SILENCE_SNAP_TOLERANCE
        for center in silence_centers:
            distance = abs(center - time_sec)
            if distance < best_distance:
                best_distance = distance
                best_time = center
        return best_time


    async def __detectFromChapterFile(self) -> list[schemas.CMSection] | None:
        """
        録画ファイルに対応するチャプターファイルがもしあれば解析し、CM 区間情報を取得する

        Returns:
            list[CMSection] | None: チャプターファイルが存在し、解析に成功した場合は CM 区間のリストを返す
        """

        # チャプターファイルのパスを生成
        # 録画ファイルが hoge.ts なら hoge.chapter.txt を探す
        chapter_file_path = self.file_path.with_name(f"{self.file_path.stem}.chapter.txt")

        # チャプターファイルが存在しない場合は None を返す
        if not await chapter_file_path.exists():
            return None

        # チャプターファイルを読み込む
        try:
            async with await chapter_file_path.open(encoding='utf-8') as f:
                lines = await f.readlines()
        except Exception as ex:
            # チャプターファイルの読み込みに失敗した場合は None を返す
            logging.error(f'{chapter_file_path}: Failed to read chapter file:', exc_info=ex)
            return None

        # チャプター情報を格納するリスト
        chapters: list[tuple[int, str, float]] = []  # (番号, 名前, 時刻)
        cm_sections: list[schemas.CMSection] = []

        # 2行ずつ処理 (チャプター時刻行とチャプター名行)
        for i in range(0, len(lines), 2):
            if i + 1 >= len(lines):
                break

            time_line = lines[i].strip()
            name_line = lines[i + 1].strip()

            # チャプター行のフォーマットが不正な場合は採用しない
            # 当該行だけ飛ばすこともできるが整合性が崩れる可能性が高いため、自前で CM 区間を検出した方が確実
            if not (time_line.startswith('CHAPTER') and name_line.startswith('CHAPTER') and 'NAME' in name_line):
                return None

            try:
                # チャプター番号を取得
                chapter_num = int(time_line[7:9])
                # チャプター時刻を取得
                chapter_time = self.__timeToSeconds(time_line.split('=')[1])
                # チャプター名を取得
                chapter_name = name_line.split('=')[1]

                if chapter_time <= float(self.duration_sec):
                    chapters.append((chapter_num, chapter_name, chapter_time))
                else:
                    # チャプター時刻が動画長を超えている行は無視する
                    logging.warning(f'{chapter_file_path}: Chapter time {chapter_time} exceeds the video duration {self.duration_sec}. Skipping.')
            except Exception as ex:
                # パースに失敗した場合は採用しない
                # 当該行だけ飛ばすこともできるが整合性が崩れる可能性が高いため、自前で CM 区間を検出した方が確実
                logging.warning(f'{chapter_file_path}: Failed to parse chapter data. (line {i}-{i+1}): {time_line}, {name_line}', exc_info=ex)
                return None

        # CM 区間を検出
        current_cm_start: float | None = None

        for i, (_, name, ctime) in enumerate(chapters):
            # CM 開始位置を検出
            if name.startswith('CM') and current_cm_start is None:
                current_cm_start = ctime
            # CM 終了位置を検出
            elif not name.startswith('CM') and current_cm_start is not None:
                cm_sections.append({
                    'start_time': current_cm_start,
                    'end_time': ctime,
                })
                current_cm_start = None

        # 最後のチャプターが CM で終わっている場合、動画長を終了時刻とする
        if current_cm_start is not None:
            cm_sections.append({
                'start_time': current_cm_start,
                'end_time': float(self.duration_sec),
            })

        return cm_sections


    @staticmethod
    def __timeToSeconds(time_str: str) -> float:
        """
        時刻文字列 (HH:MM:SS.mmm) を秒単位の float に変換する

        Args:
            time_str (str): 時刻文字列 (HH:MM:SS.mmm)

        Returns:
            float: 秒単位の時刻
        """

        # 時、分、秒をそれぞれ分割
        hours, minutes, seconds = time_str.strip().split(':')
        # 時と分は整数に、秒は小数に変換して合計を返す
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)


if __name__ == "__main__":
    # デバッグ用: 録画ファイルの CM 区間を検出する
    # Usage: uv run python -m app.metadata.CMSectionsDetector /path/to/recorded_file.ts
    def main(
        file_path: pathlib.Path = typer.Argument(
            ...,
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="録画ファイルのパス",
        ),
    ) -> None:
        """
        録画ファイルの CM 区間を検出する
        """

        # 設定を読み込む (必須)
        LoadConfig(bypass_validation=True)

        # メタデータを解析
        from app.metadata.MetadataAnalyzer import MetadataAnalyzer
        analyzer = MetadataAnalyzer(file_path)
        recorded_program = analyzer.analyze()
        if recorded_program is None:
            print(f'Error: {file_path} is not a valid recorded file.')
            return

        # CMSectionsDetector を初期化
        detector = CMSectionsDetector(
            file_path = anyio.Path(recorded_program.recorded_video.file_path),
            duration_sec = recorded_program.recorded_video.duration,
            service_id = recorded_program.channel.service_id if recorded_program.channel is not None else None,
            container_format = recorded_program.recorded_video.container_format,
        )

        # CM 区間を検出
        asyncio.run(detector.detectAndSave())

    typer.run(main)
