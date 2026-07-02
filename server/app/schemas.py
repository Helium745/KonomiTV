
# Type Hints を指定できるように
# ref: https://stackoverflow.com/a/33533514/17124142
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, RootModel, computed_field
from tortoise.contrib.pydantic import PydanticModel
from typing_extensions import TypedDict

from app.utils.TSInformation import TerrestrialRegion


# 以下に定義する型定義は、必ず以下の例のように、「親モデル」->「子モデル」の順に記述すること！
# from __future__ import annotations をインポートしているので前方参照について気にする必要はない
## 悪い例: ThumbnailImageInfo -> ThumbnailTileInfo -> ThumbnailInfo -> KeyFrame -> CMSection -> RecordedVideo
## 良い例: RecordedVideo -> KeyFrame -> CMSection -> ThumbnailInfo -> ThumbnailImageInfo -> ThumbnailTileInfo

# モデルとモデルに関連する API レスポンスの構造を表す Pydantic モデル
## この Pydantic モデルに含まれていないカラムは、API レスポンス返却時に自動で除外される (パスワードなど)
## 以前は pydantic_model_creator() で自動生成していたが、だんだん実態と合わなくなってきたため手動で定義している
## PydanticModel を使うところがポイント (BaseModel だとバリデーションエラーが発生する)

# ***** チャンネル *****

class Channel(PydanticModel):
    # デフォルト値は録画番組からメタデータを取得する処理向け
    id: str
    display_channel_id: str
    network_id: int
    service_id: int
    transport_stream_id: int | None
    remocon_id: int
    channel_number: str
    type: Literal['GR', 'BS', 'CS', 'CATV', 'SKY', 'BS4K']
    name: str
    # terrestrial_regions: network_id から算出した地デジチャンネルの地域名のリスト (デバッグ用)
    # 広域放送局の場合は複数の地域名が含まれる
    # 地デジ以外のチャンネルまたは地域が特定できない場合は None
    terrestrial_regions: list[TerrestrialRegion] | None = None
    jikkyo_force: int | None = None
    is_subchannel: bool = False
    is_radiochannel: bool = False
    is_watchable: bool = False

class LiveChannel(Channel):
    # 以下はすべて動的に生成される TV ライブストリーミング用の追加カラム
    is_display: bool
    viewer_count: int
    program_present: Program | None
    program_following: Program | None

class LiveChannels(BaseModel):
    GR: list[LiveChannel]
    BS: list[LiveChannel]
    CS: list[LiveChannel]
    CATV: list[LiveChannel]
    SKY: list[LiveChannel]
    BS4K: list[LiveChannel]

# ***** 放送中/放送予定の番組 *****

class Program(PydanticModel):
    id: str
    channel_id: str
    network_id: int
    service_id: int
    event_id: int
    title: str
    description: str
    detail: dict[str, str]
    start_time: datetime
    end_time: datetime
    duration: float
    is_free: bool
    genres: list[Genre]
    video_type: str | None
    video_codec: str | None
    video_resolution: str | None
    primary_audio_type: str
    primary_audio_language: str
    primary_audio_sampling_rate: str
    secondary_audio_type: str | None
    secondary_audio_language: str | None
    secondary_audio_sampling_rate: str | None

class Programs(BaseModel):
    total: int
    programs: list[Program]

class Genre(TypedDict):
    major: str
    middle: str

# ***** 番組表 *****

class TimeTable(BaseModel):
    # チャンネルごとの番組リスト
    channels: list[TimeTableChannel]
    # 番組データの有効範囲 (日付セレクター用)
    date_range: TimeTableDateRange

class TimeTableDateRange(BaseModel):
    # 番組データの最も早い日時
    earliest: datetime
    # 番組データの最も遅い日時
    latest: datetime

class TimeTableChannel(BaseModel):
    # チャンネル情報
    channel: Channel
    # 番組リスト
    programs: list[TimeTableProgram]
    # サブチャンネルのリスト (8時間ルールに該当しないサブチャンネルのみ)
    ## 同一 TS 内のサブチャンネルが1日あたり8時間以上放送されている場合、
    ## そのサブチャンネルは独立したチャンネル列として表示され、このフィールドには含まれない
    subchannels: list[TimeTableSubchannel] | None = None

class TimeTableSubchannel(BaseModel):
    # サブチャンネルのチャンネル情報
    channel: Channel
    # サブチャンネルの番組リスト
    programs: list[TimeTableProgram]

class TimeTableProgram(Program):
    # 予約情報 (EDCB バックエンド時かつ予約がある場合のみ設定)
    reservation: TimeTableProgramReservation | None = None

class TimeTableProgramReservation(BaseModel):
    # 録画予約 ID
    id: int
    # 予約状態: 予約済み / 録画中 / 無効
    status: Literal['Reserved', 'Recording', 'Disabled']
    # 実際に録画可能かどうか: 全編録画可能 / チューナー不足のため部分的にのみ録画可能 (一部録画できない) / チューナー不足のため全編録画不可能
    # ref: https://github.com/xtne6f/EDCB/blob/work-plus-s-240212/Common/CommonDef.h#L32-L34
    # ref: https://github.com/xtne6f/EDCB/blob/work-plus-s-240212/Common/StructDef.h#L62
    recording_availability: Literal['Full', 'Partial', 'Unavailable']

# ***** 録画ファイル *****

class RecordedVideo(PydanticModel):
    # デフォルト値は録画番組からメタデータを取得する処理向け
    id: int = -1  # メタデータ取得時は ID が定まらないため -1 を設定
    status: Literal['Recording', 'Recorded', 'AnalysisFailed']
    file_path: str
    file_hash: str
    file_size: int
    file_created_at: datetime
    file_modified_at: datetime
    recording_start_time: datetime | None
    recording_end_time: datetime | None
    duration: float
    container_format: Literal['MPEG-TS', 'MPEG-4']
    video_codec: Literal['MPEG-2', 'H.264', 'H.265']
    video_codec_profile: Literal['High', 'High 10', 'Main', 'Main 10', 'Baseline', 'Constrained Baseline']
    video_scan_type: Literal['Interlaced', 'Progressive']
    video_frame_rate: float
    video_resolution_width: int
    video_resolution_height: int
    has_video_stream_changes: bool = False
    primary_audio_codec: Literal['AAC-LC']
    primary_audio_channel: Literal['Monaural', 'Stereo', '5.1ch']
    primary_audio_sampling_rate: int
    secondary_audio_codec: Literal['AAC-LC'] | None = None
    secondary_audio_channel: Literal['Monaural', 'Stereo', '5.1ch'] | None = None
    secondary_audio_sampling_rate: int | None = None
    cm_sections: list[CMSection] | None = None
    thumbnail_info: ThumbnailInfo | None = None
    created_at: datetime
    updated_at: datetime

class KeyFrame(TypedDict):
    offset: int
    dts: int

class SegmentMapEntry(TypedDict):
    sequence_index: int
    source_file_position: int
    source_start_dts: int

class CMSection(TypedDict):
    start_time: float
    end_time: float

class ThumbnailInfo(TypedDict):
    version: int
    representative: ThumbnailImageInfo
    tile: ThumbnailTileInfo

class ThumbnailImageInfo(TypedDict):
    format: Literal['WebP']
    width: int
    height: int

class ThumbnailTileInfo(TypedDict):
    format: Literal['WebP']
    image_width: int
    image_height: int
    tile_width: int
    tile_height: int
    total_tiles: int
    column_count: int
    row_count: int
    interval_sec: float

# ***** 録画番組 *****

class RecordedProgram(PydanticModel):
    # デフォルト値は録画番組からメタデータを取得する処理向け
    id: int = -1  # メタデータ取得時は ID が定まらないため -1 を設定
    recorded_video: RecordedVideo
    recording_start_margin: float = 0.0  # 取得できなかった場合のデフォルト値
    recording_end_margin: float = 0.0  # 取得できなかった場合のデフォルト値
    is_partially_recorded: bool = False
    channel: Channel | None = None  # MPEG-TS 形式かつ SDT の解析に成功した場合のみセット
    network_id: int | None = None  # MPEG-TS 形式かつ SDT の解析に成功した場合のみセット
    service_id: int | None = None  # MPEG-TS 形式かつ SDT の解析に成功した場合のみセット
    event_id: int | None = None  # MPEG-TS 形式かつ EIT の解析に成功した場合のみセット
    series_id: int | None = None  # 番組タイトル解析に成功し、かつシリーズが存在する場合のみセット
    series_broadcast_period_id: int | None = None  # 番組タイトル解析に成功し、かつシリーズが存在する場合のみセット
    title: str
    series_title: str | None = None  # 番組タイトル解析に成功した場合のみセット
    episode_number: str | None = None  # 番組タイトル解析に成功した場合のみセット
    subtitle: str | None = None  # 番組タイトル解析に成功した場合のみセット
    description: str = '番組概要を取得できませんでした。'
    detail: dict[str, str] = {}
    start_time: datetime
    end_time: datetime
    duration: float
    is_free: bool = True
    genres: list[Genre] = []
    primary_audio_type: str = '2/0モード(ステレオ)'
    primary_audio_language: str = '日本語'
    secondary_audio_type: str | None = None
    secondary_audio_language: str | None = None
    created_at: datetime
    updated_at: datetime

class RecordedPrograms(BaseModel):
    total: int
    recorded_programs: list[RecordedProgram]

# ***** シリーズ *****

class Series(PydanticModel):
    id: int
    title: str
    description: str
    genres: list[Genre]
    broadcast_periods: list[SeriesBroadcastPeriod]
    created_at: datetime
    updated_at: datetime

class SeriesList(BaseModel):
    total: int
    series_list: list[Series]

class SeriesBroadcastPeriod(PydanticModel):
    channel: Channel
    start_date: date
    end_date: date
    recorded_programs: list[RecordedProgram]

# ***** ユーザー *****

class User(PydanticModel):
    id: int
    name: str
    is_admin: bool
    niconico_user_id: int | None
    niconico_user_name: str | None
    niconico_user_premium: bool | None
    twitter_accounts: list[TwitterAccount]  # 追加カラム
    bluesky_accounts: list[BlueskyAccount]  # 追加カラム
    account_links: list[AccountLink]  # 追加カラム
    created_at: datetime
    updated_at: datetime

class AccountLink(PydanticModel):
    id: int
    twitter_account: TwitterAccount
    bluesky_account: BlueskyAccount
    created_at: datetime
    updated_at: datetime

class Users(RootModel[list[User]]):
    pass

# ***** Twitter / Bluesky 連携 *****

class TwitterAccount(PydanticModel):
    id: int
    name: str
    screen_name: str
    icon_url: str
    created_at: datetime
    updated_at: datetime

class BlueskyAccount(PydanticModel):
    id: int
    did: str
    handle: str
    name: str
    icon_url: str
    created_at: datetime
    updated_at: datetime

# モデルに関連しない API リクエストの構造を表す Pydantic モデル
## リクエストボティの JSON 構造と一致する

# ***** 録画予約 *****

# 録画予約を追加する
class ReservationAddRequest(BaseModel):
    # 録画予約を追加する番組の ID (NID32736-SID1024-EID65535 の形式)
    program_id: str
    # 録画設定
    record_settings: RecordSettings

# 録画予約を変更する
class ReservationUpdateRequest(BaseModel):
    # 録画設定
    record_settings: RecordSettings

# キーワード自動予約条件を追加する
class ReservationConditionAddRequest(BaseModel):
    # 条件が有効かどうか
    is_enabled: bool = True
    # 番組検索条件
    program_search_condition: ProgramSearchCondition
    # 録画設定
    record_settings: RecordSettings

# キーワード自動予約条件を変更する
class ReservationConditionUpdateRequest(BaseModel):
    # 条件が有効かどうか
    is_enabled: bool = True
    # 番組検索条件
    program_search_condition: ProgramSearchCondition
    # 録画設定
    record_settings: RecordSettings

# ***** ユーザー *****

class UserCreateRequest(BaseModel):
    username: str
    password: str

class UserUpdateRequest(BaseModel):
    username: str | None = None
    password: str | None = None

class UserUpdateRequestForAdmin(BaseModel):
    is_admin: bool | None = None

class AccountLinkCreateRequest(BaseModel):
    twitter_account_id: int
    bluesky_account_id: int

# ***** Twitter 連携 *****

class TwitterCookieAuthRequest(BaseModel):
    cookies_txt: str
    browser_info: BrowserEnvironmentInfoRequest | None = None

class BrowserEnvironmentInfoRequest(BaseModel):
    user_agent_data: BrowserEnvironmentUserAgentData
    navigator_platform: str
    locale: str
    timezone: str

class BrowserEnvironmentInfo(TypedDict):
    http_headers: BrowserEnvironmentHTTPHeaders  # /api/twitter/auth の HTTP リクエストヘッダーから抽出した情報
    user_agent_data: BrowserEnvironmentUserAgentData
    navigator_platform: str
    locale: str
    timezone: str

class BrowserEnvironmentHTTPHeaders(TypedDict):
    user_agent: str | None
    accept_language: str | None
    accept_languages: list[str]
    sec_ch_ua: str | None
    sec_ch_ua_mobile: str | None
    sec_ch_ua_platform: str | None

class BrowserEnvironmentUserAgentData(TypedDict):
    platform: str
    platform_version: str
    architecture: str
    bitness: str
    mobile: bool
    model: str
    wow64: bool

class BlueskyAuthRequest(BaseModel):
    handle: str
    app_password: str

# モデルに関連しない API レスポンスの構造を表す Pydantic モデル
## レスポンスボディの JSON 構造と一致する

# ***** ライブストリーム *****

class LiveStreamStatus(BaseModel):
    status: Literal['Offline', 'Standby', 'ONAir', 'Idling', 'Restart']
    detail: str
    started_at: float
    updated_at: float
    client_count: int

class LiveStreamStatuses(BaseModel):
    Restart: dict[str, LiveStreamStatus]
    Idling: dict[str, LiveStreamStatus]
    ONAir: dict[str, LiveStreamStatus]
    Standby: dict[str, LiveStreamStatus]
    Offline: dict[str, LiveStreamStatus]

# ***** 録画予約 *****

# 録画予約情報 (mirakc schedules API ベース)
class Reservation(BaseModel):
    # 録画予約 ID (mirakc ProgramId: nid * 10^10 + sid * 10^5 + eid)
    id: int
    # 録画予約番組の放送チャンネル
    channel: Channel
    # 録画予約番組の情報
    program: Program
    # 録画予約が現在進行中かどうか (mirakc state == 'recording')
    is_recording_in_progress: bool
    # 実際に録画可能かどうか: mirakc はチューナー競合を事前計算しないため常に 'Full'
    recording_availability: Literal['Full', 'Partial', 'Unavailable']
    # コメント: キーワード自動予約で追加された予約なら "キーワード自動予約" と入る
    comment: str
    # 録画予定のファイル名 (contentPath のベース名)
    scheduled_recording_file_name: str
    # 想定録画ファイルサイズ (バイト): チャンネル種別ごとの静的ビットレートテーブルから算出した推定値
    estimated_recording_file_size: int
    # 録画設定
    record_settings: RecordSettings
    # mirakc スケジュール状態 (scheduled / tracking / recording / rescheduling / finished / failed)
    state: str = 'scheduled'
    # 録画失敗理由 (mirakc failedReason から取得、失敗時のみ設定)
    failed_reason: str | None = None

# 録画予約情報のリスト
class Reservations(BaseModel):
    total: int
    reservations: list[Reservation]

# キーワード自動予約条件
class ReservationCondition(BaseModel):
    id: int
    # 条件が有効かどうか
    is_enabled: bool = True
    # このキーワード自動予約条件で登録されている録画予約の数
    reservation_count: int
    # 番組検索条件
    program_search_condition: ProgramSearchCondition
    # 録画設定
    record_settings: RecordSettings

# キーワード自動予約条件のリスト
class ReservationConditions(BaseModel):
    total: int
    reservation_conditions: list[ReservationCondition]

# 番組検索条件
class ProgramSearchCondition(BaseModel):
    # 番組検索条件が有効かどうか
    is_enabled: bool = True
    # 検索キーワード
    keyword: str = ''
    # 除外キーワード
    exclude_keyword: str = ''
    # メモ欄
    note: str = ''
    # 番組名のみを検索対象とするかどうか
    is_title_only: bool = False
    # 大文字小文字を区別するかどうか
    is_case_sensitive: bool = False
    # あいまい検索を行うかどうか
    is_fuzzy_search_enabled: bool = False
    # 正規表現検索を行うかどうか
    is_regex_search_enabled: bool = False
    # 検索対象を絞り込むチャンネル範囲のリスト
    ## None を指定すると全てのチャンネルが検索対象になる
    ## 中のチャンネル ID の順序は保証されない
    service_ranges: list[ProgramSearchConditionService] | None = None
    # 検索対象を絞り込むジャンル範囲のリスト
    ## None を指定すると全てのジャンルが検索対象になる
    genre_ranges: list[Genre] | None = None
    # genre_ranges で指定したジャンルを逆に検索対象から除外するかどうか
    is_exclude_genre_ranges: bool = False
    # 検索対象を絞り込む放送日時範囲のリスト
    ## None を指定すると全ての放送日時が検索対象になる
    date_ranges: list[ProgramSearchConditionDate] | None = None
    # date_ranges で指定した放送日時を逆に検索対象から除外するかどうか
    is_exclude_date_ranges: bool = False
    # 番組長で絞り込む最小範囲 (分)
    ## 指定しない場合は None になる
    duration_range_min: Annotated[int, Field(ge=0)] | None = None
    # 番組長で絞り込む最大範囲 (分)
    ## 指定しない場合は None になる
    duration_range_max: Annotated[int, Field(ge=0)] | None = None
    # 番組の放送種別で絞り込む: すべて / 無料のみ / 有料のみ
    broadcast_type: Literal['All', 'FreeOnly', 'PaidOnly'] = 'All'
    # キーワード自動予約で、同じ番組名の既存録画がある予約を無効化するかどうか
    ## EDCB の番組検索ではこの値は参照されず、自動予約登録時のみ使われる
    ## None: 何もしない / SameChannelOnly: 同じチャンネルのみ対象 / AllChannels: 全てのチャンネルを対象
    ## 同じチャンネルのみ対象にする: 同じチャンネルで同名の番組が既に録画されていれば、新しい予約を無効状態で登録する
    ## 全てのチャンネルを対象にする: 任意のチャンネルで同名の番組が既に録画されていれば、新しい予約を無効状態で登録する
    ## 仕様上予約自体を削除してしまうとすぐ再登録されてしまうので、無効状態で登録することで有効になるのを防いでいるらしい
    duplicate_title_check_scope: Literal['None', 'SameChannelOnly', 'AllChannels'] = 'None'
    # キーワード自動予約で既存録画を探す対象期間 (日単位)
    duplicate_title_check_period_days: Annotated[int, Field(ge=0)] = 6

# 番組検索条件のチャンネル
## KonomiTV 的にはネットワーク ID とサービス ID があればチャンネルを特定できるのだが、
## EDCB はこれらに加えてトランスポートストリーム ID も必要なため、トランスポートストリーム ID も含めている
## 通常の Channel モデルだと API リクエスト時に余計な情報を送らなければならなくため、必要な情報だけを抜き出したモデルを使っている
## チャンネル名などはこれらの ID を元に別途フロントエンド側で取得してもらう想定
class ProgramSearchConditionService(BaseModel):
    # ネットワーク ID
    network_id: int
    # トランスポートストリーム ID
    transport_stream_id: int
    # サービス ID
    service_id: int

# 番組検索条件の日付
class ProgramSearchConditionDate(BaseModel):
    # 検索開始曜日 (0: 日曜日, 1: 月曜日, 2: 火曜日, 3: 水曜日, 4: 木曜日, 5: 金曜日, 6: 土曜日)
    ## 文字列にした方がわかりやすいとも思ったが、day.js が数値で曜日を扱うため数値で統一する
    start_day_of_week: Annotated[int, Field(ge=0, le=6)]
    # 検索開始時刻 (時)
    start_hour: Annotated[int, Field(ge=0, le=23)]
    # 検索開始時刻 (分)
    start_minute: Annotated[int, Field(ge=0, le=59)]
    # 検索終了曜日 (0: 日曜日, 1: 月曜日, 2: 火曜日, 3: 水曜日, 4: 木曜日, 5: 金曜日, 6: 土曜日)
    ## 文字列にした方がわかりやすいとも思ったが、day.js が数値で曜日を扱うため数値で統一する
    end_day_of_week: Annotated[int, Field(ge=0, le=6)]
    # 検索終了時刻 (時)
    end_hour: Annotated[int, Field(ge=0, le=23)]
    # 検索終了時刻 (分)
    end_minute: Annotated[int, Field(ge=0, le=59)]

# 録画設定 (mirakc RecordingOptions に対応する KonomiTV 側の設定)
class RecordSettings(BaseModel):
    # チューナー優先度: 1 ~ 5 の数値 (mirakc options.priority にマップされる)
    priority: Annotated[int, Field(ge=1, le=5)] = 3
    # mirakc の pre-filters (設定しない場合は空リスト)
    pre_filters: list[str] = []
    # mirakc の post-filters (設定しない場合は空リスト)
    post_filters: list[str] = []

# ***** データ放送 *****

class DataBroadcastingInternetStatus(BaseModel):
    success: bool
    ip_address: str | None
    response_time_milliseconds: int | None

# ***** ニコニコ実況連携 *****

class JikkyoWebSocketInfo(BaseModel):
    # 視聴セッション維持用 WebSocket API の URL (NX-Jikkyo)
    watch_session_url: str | None
    # 視聴セッション維持用 WebSocket API の URL (ニコニコ生放送)
    nicolive_watch_session_url: str | None = None
    # 視聴セッション維持用 WebSocket API のエラー情報 (ニコニコ生放送)
    nicolive_watch_session_error: str | None = None
    # コメント受信用 WebSocket API の URL (NX-Jikkyo)
    comment_session_url: str | None
    # 現在は NX-Jikkyo のみ存在するニコニコ実況チャンネルかどうか
    is_nxjikkyo_exclusive: bool

class JikkyoComment(BaseModel):
    time: float
    type: Literal['top', 'right', 'bottom']
    size: Literal['big', 'medium', 'small']
    color: str
    author: str
    text: str

class JikkyoComments(BaseModel):
    is_success: bool
    comments: list[JikkyoComment]
    detail: str

class ThirdpartyAuthURL(BaseModel):
    authorization_url: str

# ***** Twitter 連携 *****

class Tweet(BaseModel):
    source: Literal['Twitter', 'Bluesky']
    id: str
    created_at: datetime
    user: TweetUser
    text: str
    lang: str
    via: str
    image_urls: list[str] | None
    movie_url: str | None
    retweet_count: int
    retweeted: bool
    favorite_count: int
    favorited: bool
    retweeted_tweet: Tweet | None
    quoted_tweet: Tweet | None

class TweetUser(BaseModel):
    source: Literal['Twitter', 'Bluesky']
    id: str
    name: str
    screen_name: str
    icon_url: str

class TwitterAPIResult(BaseModel):
    is_success: bool
    detail: str

class PostTweetResult(TwitterAPIResult):
    tweet_url: str
    tweet_id: str | None = None
    post_uri: str | None = None
    post_cid: str | None = None

class TimelineLoadMoreCursor(BaseModel):
    cursor_type: Literal['Older', 'Gap', 'ShowMore']
    cursor_id: str
    entry_id: str | None
    upper_created_at: datetime | None
    lower_created_at: datetime | None

class TimelineTweetsResult(TwitterAPIResult):
    tweets: list[Tweet]
    newer_cursor_id: str | None
    load_more_cursors: list[TimelineLoadMoreCursor]
    is_cursor_consumed: bool

class TwitterGraphQLAPIEndpointInfo(BaseModel):
    method: Literal['GET', 'POST']
    query_id: str
    endpoint: str
    features: dict[str, bool] | None

    @computed_field
    @property
    def path(self) -> str:
        return f'/i/api/graphql/{self.query_id}/{self.endpoint}'

# ***** ユーザー *****

class UserAccessToken(BaseModel):
    access_token: str
    token_type: str

# ***** バージョン情報 *****

class VersionInformation(BaseModel):
    version: str
    latest_version: str | None
    environment: Literal['Windows', 'Linux', 'Linux-Docker', 'Linux-ARM']
    backend: Literal['mirakc']
    encoder: Literal['FFmpeg', 'QSVEncC', 'NVEncC', 'VCEEncC', 'rkmppenc']
