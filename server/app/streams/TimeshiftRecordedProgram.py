
from datetime import datetime, timedelta
from typing import Literal

from app.constants import JST
from app.models.Channel import Channel
from app.models.RecordedProgram import RecordedProgram
from app.models.RecordedVideo import RecordedVideo
from app.utils.mirakc import TIMESHIFT_FILE_PATH_SCHEME, WebTimeshiftRecord
from app.utils.TSInformation import TSInformation


# mirakc の video.resolution 文字列から、映像の解像度とスキャン方式への変換テーブル
## 地上波/BS の MPEG-2 放送は 1080i でも物理解像度が 1440x1080 (SAR 4:3 で 1920x1080 相当に引き伸ばされる) であることが大半のため、
## 1920x1080 ではなく 1440x1080 を採用する (VideoEncodingTask 側は 1920x1080 の場合のみ特別に 1440p 品質を 1920 幅へ広げるため、
## ここで誤って 1920x1080 を返すと、実際は 1440 幅の映像を歪んだアスペクト比のまま 1920 幅にスケーリングしてしまう)
RESOLUTION_TABLE: dict[str, tuple[int, int, Literal['Interlaced', 'Progressive']]] = {
    '1080i': (1440, 1080, 'Interlaced'),
    '1080p': (1440, 1080, 'Progressive'),
    '720p': (1280, 720, 'Progressive'),
    '480i': (720, 480, 'Interlaced'),
    '480p': (720, 480, 'Progressive'),
    '240p': (320, 240, 'Progressive'),
    '180p': (320, 180, 'Progressive'),
    '2160p': (3840, 2160, 'Progressive'),
    '4320p': (7680, 4320, 'Progressive'),
}

# mirakc の video.type 文字列から KonomiTV の video_codec への変換テーブル
VIDEO_CODEC_TABLE: dict[str, Literal['MPEG-2', 'H.264', 'H.265']] = {
    'mpeg2': 'MPEG-2',
    'h264': 'H.264',
    'h265': 'H.265',
}


def MillisecondToDatetime(millisecond: int) -> datetime:
    """ mirakc から取得した UNIX ミリ秒のタイムスタンプを、タイムゾーン付き (JST) の datetime に変換する """
    return datetime.fromtimestamp(millisecond / 1000, tz=JST)


async def BuildRecordedProgramFromTimeshiftRecord(recorder_id: str, record: WebTimeshiftRecord) -> RecordedProgram:
    """
    mirakc のタイムシフト record から、VideoStream / VideoEncodingTask にそのまま渡せる
    (DB に保存しない、その場限りの) RecordedProgram / RecordedVideo のペアを構築する

    タイムシフト録画は mirakc 側のリングバッファにしか実体がなく、KonomiTV の DB には一切保存しないため、
    録画番組ページの通常の読み込み経路 (DB からの取得) とは異なり、リクエストの都度この関数でメタデータを組み立てる
    RecordedVideo.file_path には実ファイルパスの代わりに TIMESHIFT_FILE_PATH_SCHEME で始まる擬似 URI を設定し、
    HttpRangeFile 経由で mirakc の Range リクエスト対応ストリーミングエンドポイントから読み取れるようにする

    Args:
        recorder_id (str): mirakc 上のタイムシフトレコーダー名
        record (WebTimeshiftRecord): mirakc から取得したタイムシフト record

    Returns:
        RecordedProgram: DB 未保存の RecordedProgram (recorded_video / channel を紐付け済み)
    """

    program = record['program']
    start_time = MillisecondToDatetime(record['startTime'])
    duration_seconds = record['duration'] / 1000
    end_time = start_time + timedelta(seconds=duration_seconds)

    # 対応する KonomiTV 側チャンネルを検索 (見つからなくても再生自体は継続できるので None のまま進める)
    channel = await Channel.filter(network_id=program['networkId'], service_id=program['serviceId']).first()

    # 映像情報を mirakc の video フィールドから復元 (取得できない場合は地上波 1080i 相当のデフォルト値にフォールバック)
    video_info = program.get('video')
    video_codec = VIDEO_CODEC_TABLE.get(video_info['type'], 'MPEG-2') if video_info is not None else 'MPEG-2'
    resolution_key = video_info['resolution'] if video_info is not None else '1080i'
    video_resolution_width, video_resolution_height, video_scan_type = RESOLUTION_TABLE.get(resolution_key, RESOLUTION_TABLE['1080i'])
    video_codec_profile: Literal['High', 'Main'] = 'Main' if video_codec == 'H.265' else 'High'

    # 音声情報を mirakc の audio フィールドから復元
    audio_info = program.get('audio')
    primary_audio_channel: Literal['Monaural', 'Stereo', '5.1ch'] = '5.1ch' if (
        audio_info is not None and audio_info.get('componentType') == 9
    ) else 'Stereo'
    primary_audio_sampling_rate = audio_info['samplingRate'] if audio_info is not None else 48000

    title = TSInformation.formatString(program.get('name', ''))
    description = TSInformation.formatString(program.get('description', '')) if program.get('description') else '番組概要を取得できませんでした。'
    genres = TSInformation.convertARIBGenresToGenreDicts(program.get('genres', []))  # type: ignore[arg-type]

    recorded_video = RecordedVideo(
        id = -1,
        recorded_program_id = record['id'],
        status = 'Recording' if record['recording'] is True else 'Recorded',
        file_path = f'{TIMESHIFT_FILE_PATH_SCHEME}{recorder_id}/{record["id"]}',
        file_hash = '',
        file_size = record['size'],
        file_created_at = start_time,
        file_modified_at = end_time,
        recording_start_time = start_time,
        recording_end_time = None if record['recording'] is True else end_time,
        duration = duration_seconds,
        container_format = 'MPEG-TS',
        video_codec = video_codec,
        video_codec_profile = video_codec_profile,
        video_scan_type = video_scan_type,
        video_frame_rate = 29.97,  # 日本の放送波はほぼ全て 29.97fps (インターレース/プログレッシブ問わず) のため固定値とする
        video_resolution_width = video_resolution_width,
        video_resolution_height = video_resolution_height,
        has_video_stream_changes = False,
        primary_audio_codec = 'AAC-LC',
        primary_audio_channel = primary_audio_channel,
        primary_audio_sampling_rate = primary_audio_sampling_rate,
        secondary_audio_codec = None,
        secondary_audio_channel = None,
        secondary_audio_sampling_rate = None,
        key_frames = [],
        segment_map = [],
        cm_sections = None,
        thumbnail_info = None,
        created_at = start_time,
        updated_at = start_time,
    )

    recorded_program = RecordedProgram(
        id = record['id'],
        recording_start_margin = 0.0,
        recording_end_margin = 0.0,
        is_partially_recorded = record['recording'],
        network_id = program['networkId'],
        service_id = program['serviceId'],
        event_id = program['eventId'],
        series_id = None,
        series_broadcast_period_id = None,
        title = title,
        series_title = None,
        episode_number = None,
        subtitle = None,
        description = description,
        detail = {},
        start_time = start_time,
        end_time = end_time,
        duration = duration_seconds,
        is_free = program.get('isFree', True),
        genres = genres,
        primary_audio_type = '2/0モード(ステレオ)',
        primary_audio_language = '日本語',
        secondary_audio_type = None,
        secondary_audio_language = None,
        created_at = start_time,
        updated_at = start_time,
    )
    # channel は RecordedProgram 側が FK を持つ forward relation なので、通常のプロパティ代入で channel_id も追従して設定される
    recorded_program.channel = channel  # type: ignore[assignment]
    # recorded_video は RecordedVideo 側が FK を持つ forward relation で、RecordedProgram.recorded_video は related_name 経由の
    # backward relation (読み取り専用プロパティ) のため、通常の代入ではなく Tortoise が内部キャッシュに使う "_" 付き属性に直接設定する
    ## ref: tortoise.models.Model._meta._generate_lazy_fk_m2m_fields() の _ro2o_getter() 実装
    recorded_video.recorded_program = recorded_program  # type: ignore[assignment]
    recorded_program._recorded_video = recorded_video  # type: ignore[attr-defined]

    return recorded_program
