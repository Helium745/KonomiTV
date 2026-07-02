
import os
import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Body, HTTPException, Path, status

from app import logging, schemas
from app.constants import JST
from app.models.Channel import Channel
from app.models.Program import Program
from app.utils.mirakc import (
    MirakcClient,
    WebRecordingSchedule,
    decode_program_id,
    program_id_from_konomitv_id,
)


# ルーター
router = APIRouter(
    tags = ['Reservations'],
    prefix = '/api/recording/reservations',
)

# チャンネル種別ごとのおおよそのビットレート (Mbps)
# 録画ファイルサイズ推定に使用
_BITRATE_TABLE: dict[str, int] = {
    'GR': 17,
    'BS': 24,
    'CS': 16,
    'SKY': 16,
    'CATV': 16,
    'BS4K': 70,
}


def sanitize_filename(name: str) -> str:
    """
    ファイル名として使用できない文字を置換する。
    全角文字は残し、OS 予約文字のみ置換する。
    """
    name = unicodedata.normalize('NFC', name)
    # Windows のファイル名禁止文字と半角スラッシュ
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    name = name.strip()
    return name[:80] if len(name) > 80 else name or 'untitled'


def build_content_path(program: dict[str, Any], channel_name: str) -> str:
    """
    mirakc の contentPath (basedir からの相対パス) を生成する。

    Args:
        program (dict): mirakc MirakurunProgram オブジェクト
        channel_name (str): チャンネル名

    Returns:
        str: 相対パス (例: "テスト番組_NHK総合_20240101_120000.m2ts")
    """
    title = sanitize_filename(program.get('name', 'untitled'))
    ch_name = sanitize_filename(channel_name)
    # startAt は UNIX ミリ秒
    start_at_ms = program.get('startAt', 0)
    start_dt = datetime.fromtimestamp(start_at_ms / 1000, tz=UTC).astimezone(JST)
    time_str = start_dt.strftime('%Y%m%d_%H%M%S')
    return f'{title}_{ch_name}_{time_str}.m2ts'


def _estimate_file_size(channel_type: str, duration_seconds: float) -> int:
    """
    チャンネル種別と番組長から録画ファイルサイズを推定する (バイト単位)。

    Args:
        channel_type (str): チャンネル種別 (GR / BS / CS / ...)
        duration_seconds (float): 番組長 (秒)

    Returns:
        int: 推定ファイルサイズ (バイト)
    """
    bitrate_mbps = _BITRATE_TABLE.get(channel_type, 17)
    return int(bitrate_mbps * 1_000_000 / 8 * duration_seconds)


async def _decode_schedule(
    schedule: WebRecordingSchedule,
    channels_by_nid_sid: dict[tuple[int, int], Channel] | None = None,
    programs_by_id: dict[str, Program] | None = None,
) -> schemas.Reservation:
    """
    mirakc の WebRecordingSchedule を schemas.Reservation に変換する。

    Args:
        schedule (WebRecordingSchedule): mirakc スケジュール
        channels_by_nid_sid: NID+SID → Channel のキャッシュ辞書 (None のとき個別 DB ルックアップ)
        programs_by_id: program_id → Program のキャッシュ辞書 (None のとき個別 DB ルックアップ)

    Returns:
        schemas.Reservation
    """
    mirakc_program = schedule['program']
    program_id_int: int = mirakc_program['id']
    network_id, service_id, event_id = decode_program_id(program_id_int)
    program_id_str = f'NID{network_id}-SID{service_id:03d}-EID{event_id}'

    # DB からチャンネル情報を取得
    if channels_by_nid_sid is not None:
        channel = channels_by_nid_sid.get((network_id, service_id))
    else:
        channel = await Channel.filter(network_id=network_id, service_id=service_id, is_watchable=True).first()

    # DB からチャンネルが見つからなければ、mirakc のサービス情報からフォールバックチャンネルを生成
    if channel is None:
        mirakc_service = mirakc_program.get('service', {}) or {}
        channel_type: str = mirakc_service.get('channel', {}).get('type', 'GR')
        channel_schema = schemas.Channel(
            id = f'NID{network_id}-SID{service_id:03d}',
            display_channel_id = f'NID{network_id}-SID{service_id:03d}',
            network_id = network_id,
            service_id = service_id,
            transport_stream_id = None,
            remocon_id = 0,
            channel_number = '000',
            type = channel_type if channel_type in ('GR', 'BS', 'CS', 'CATV', 'SKY', 'BS4K') else 'GR',  # type: ignore[arg-type]
            name = mirakc_service.get('name', f'NID{network_id} SID{service_id}'),
            is_watchable = False,
        )
    else:
        channel_schema = schemas.Channel.model_validate(channel, from_attributes=True)

    # DB から番組情報を取得
    if programs_by_id is not None:
        program = programs_by_id.get(program_id_str)
    else:
        program = await Program.filter(id=program_id_str).first()

    # DB から番組が見つからなければ mirakc の program フィールドからフォールバック番組を生成
    if program is None:
        start_at_ms = mirakc_program.get('startAt', 0)
        duration_ms = mirakc_program.get('duration', 0)
        start_dt = datetime.fromtimestamp(start_at_ms / 1000, tz=UTC).astimezone(JST) if start_at_ms else datetime.now(JST)
        duration_sec = duration_ms / 1000.0 if duration_ms else 0.0
        end_dt = datetime.fromtimestamp((start_at_ms + duration_ms) / 1000, tz=UTC).astimezone(JST) if (start_at_ms and duration_ms) else start_dt
        program_schema = schemas.Program(
            id = program_id_str,
            channel_id = channel_schema.id,
            network_id = network_id,
            service_id = service_id,
            event_id = event_id,
            title = mirakc_program.get('name', '不明な番組'),
            description = mirakc_program.get('description', ''),
            detail = {},
            start_time = start_dt,
            end_time = end_dt,
            duration = duration_sec,
            is_free = mirakc_program.get('isFree', True),
            genres = [],
            video_type = None,
            video_codec = None,
            video_resolution = None,
            primary_audio_type = '',
            primary_audio_language = '',
            primary_audio_sampling_rate = '',
            secondary_audio_type = None,
            secondary_audio_language = None,
            secondary_audio_sampling_rate = None,
        )
    else:
        program_schema = schemas.Program.model_validate(program, from_attributes=True)

    # コメント: konomitv-condition-* タグがあれば自動予約
    tags: list[str] = schedule.get('tags', [])
    is_auto = any(t.startswith('konomitv-condition-') for t in tags)
    comment = 'キーワード自動予約' if is_auto else ''

    # 録画ファイル名
    content_path: str = schedule['options'].get('contentPath', '')
    scheduled_file_name = os.path.basename(content_path) if content_path else ''

    # 推定ファイルサイズ
    ch_type = channel_schema.type if channel else 'GR'
    duration_s = program_schema.duration
    estimated_size = _estimate_file_size(ch_type, duration_s)

    # 録画設定
    opts = schedule['options']
    record_settings = schemas.RecordSettings(
        priority = max(1, min(5, opts.get('priority', 3))),
        pre_filters = opts.get('preFilters', []),
        post_filters = opts.get('postFilters', []),
    )

    # failedReason → 文字列化
    failed_reason: str | None = None
    raw_failed = schedule.get('failedReason')
    if raw_failed:
        failed_reason = raw_failed.get('type', 'unknown')
        if 'message' in raw_failed:
            failed_reason = f'{failed_reason}: {raw_failed["message"]}'

    return schemas.Reservation(
        id = program_id_int,
        channel = channel_schema,
        program = program_schema,
        is_recording_in_progress = schedule['state'] == 'recording',
        recording_availability = 'Full',  # mirakc は事前計算しないため常に Full
        comment = comment,
        scheduled_recording_file_name = scheduled_file_name,
        estimated_recording_file_size = estimated_size,
        record_settings = record_settings,
        state = schedule['state'],
        failed_reason = failed_reason,
    )


@router.get(
    '',
    summary = '録画予約情報一覧取得 API',
    response_description = '録画予約情報のリスト。',
    response_model = schemas.Reservations,
)
async def ReservationsAPI():
    """
    mirakc から全録画スケジュールを取得し、KonomiTV の Reservation 形式に変換して返す。
    """
    client = MirakcClient()
    try:
        schedules = await client.fetch_schedules()
    except httpx.HTTPStatusError as ex:
        logging.error(f'[ReservationsRouter][ReservationsAPI] Failed to fetch schedules: {ex}')
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='Failed to fetch schedules from mirakc')
    except (httpx.NetworkError, httpx.TimeoutException) as ex:
        logging.error(f'[ReservationsRouter][ReservationsAPI] mirakc unreachable: {ex}')
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='mirakc is not available')

    # 一括 DB ルックアップでパフォーマンス向上
    channels = await Channel.filter(is_watchable=True).all()
    channels_by_nid_sid: dict[tuple[int, int], Channel] = {
        (ch.network_id, ch.service_id): ch for ch in channels
    }

    # 番組情報を一括取得 (スケジュール分だけ)
    program_ids: list[str] = []
    for s in schedules:
        nid, sid, eid = decode_program_id(s['program']['id'])
        program_ids.append(f'NID{nid}-SID{sid:03d}-EID{eid}')
    programs_list = await Program.filter(id__in=program_ids).all()
    programs_by_id: dict[str, Program] = {p.id: p for p in programs_list}

    reservations: list[schemas.Reservation] = []
    for schedule in schedules:
        try:
            reservation = await _decode_schedule(schedule, channels_by_nid_sid, programs_by_id)
            reservations.append(reservation)
        except Exception as ex:
            logging.warning(f'[ReservationsRouter][ReservationsAPI] Failed to decode schedule: {ex}')

    # 番組開始時刻でソート
    reservations.sort(key=lambda r: r.program.start_time)

    return schemas.Reservations(total=len(reservations), reservations=reservations)


@router.post(
    '',
    summary = '録画予約追加 API',
    response_description = '追加した録画予約情報。',
    response_model = schemas.Reservation,
)
async def AddReservationAPI(
    reservation_req: Annotated[schemas.ReservationAddRequest, Body(description='録画予約情報。')],
):
    """
    指定した番組を mirakc の録画スケジュールに追加する。
    予約 ID は program_id (NID...-SID...-EID... 形式) から自動算出される mirakc ProgramId を使用する。
    """
    # KonomiTV 形式の program_id を mirakc ProgramId に変換
    try:
        mirakc_program_id = program_id_from_konomitv_id(reservation_req.program_id)
    except ValueError:
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = f'Invalid program_id format: {reservation_req.program_id}',
        )

    # DB から番組情報を取得して contentPath のファイル名を生成
    program = await Program.filter(id=reservation_req.program_id).first()
    channel = await Channel.filter(
        network_id = mirakc_program_id // 10_000_000_000,
        service_id = (mirakc_program_id % 10_000_000_000) // 100_000,
        is_watchable = True,
    ).first()
    channel_name = channel.name if channel else 'unknown'

    # mirakc の MirakurunProgram 形式で program 情報を組み立てる (contentPath 生成用)
    if program is not None:
        mirakc_program_info: dict[str, Any] = {
            'name': program.title,
            'startAt': int(program.start_time.timestamp() * 1000),
        }
    else:
        mirakc_program_info = {'name': 'unknown', 'startAt': 0}

    content_path = build_content_path(mirakc_program_info, channel_name)

    # mirakc RecordingOptions を組み立て
    from app.utils.mirakc import RecordingOptions
    options: RecordingOptions = {
        'contentPath': content_path,
        'priority': reservation_req.record_settings.priority,
        'preFilters': reservation_req.record_settings.pre_filters,
        'postFilters': reservation_req.record_settings.post_filters,
    }

    client = MirakcClient()

    # 既に同じ programId のスケジュールが存在するか確認
    existing = await client.fetch_schedule(mirakc_program_id)
    if existing is not None:
        raise HTTPException(
            status_code = status.HTTP_409_CONFLICT,
            detail = 'A recording schedule for this program already exists',
        )

    try:
        schedule = await client.add_schedule(
            program_id = mirakc_program_id,
            options = options,
            tags = [],
        )
    except httpx.HTTPStatusError as ex:
        logging.error(f'[ReservationsRouter][AddReservationAPI] Failed to add schedule: {ex}')
        if ex.response.status_code == 404:
            raise HTTPException(
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail = 'Program not found in mirakc EPG. Please wait for EPG to be updated.',
            )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'mirakc returned error: {ex.response.status_code}')
    except (httpx.NetworkError, httpx.TimeoutException) as ex:
        logging.error(f'[ReservationsRouter][AddReservationAPI] mirakc unreachable: {ex}')
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='mirakc is not available')

    return await _decode_schedule(schedule)


@router.get(
    '/{reservation_id}',
    summary = '録画予約情報取得 API',
    response_description = '録画予約情報。',
    response_model = schemas.Reservation,
)
async def ReservationAPI(
    reservation_id: Annotated[int, Path(description='録画予約 ID (mirakc ProgramId)。')],
):
    """
    指定した録画予約情報を取得する。
    """
    client = MirakcClient()
    try:
        schedule = await client.fetch_schedule(reservation_id)
    except (httpx.NetworkError, httpx.TimeoutException) as ex:
        logging.error(f'[ReservationsRouter][ReservationAPI] mirakc unreachable: {ex}')
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='mirakc is not available')

    if schedule is None:
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified reservation was not found',
        )

    return await _decode_schedule(schedule)


@router.put(
    '/{reservation_id}',
    summary = '録画予約更新 API',
    response_description = '更新した録画予約情報。',
    response_model = schemas.Reservation,
)
async def UpdateReservationAPI(
    reservation_id: Annotated[int, Path(description='録画予約 ID (mirakc ProgramId)。')],
    reservation_req: Annotated[schemas.ReservationUpdateRequest, Body(description='録画予約情報。')],
):
    """
    録画予約の設定を更新する。
    mirakc は PUT を持たないため、既存スケジュールを削除して再登録する。
    tags は保持されるため、自動予約との紐付きは失われない。
    """
    client = MirakcClient()

    # 現在のスケジュールを取得 (tags 保持のため)
    try:
        existing = await client.fetch_schedule(reservation_id)
    except (httpx.NetworkError, httpx.TimeoutException) as ex:
        logging.error(f'[ReservationsRouter][UpdateReservationAPI] mirakc unreachable: {ex}')
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='mirakc is not available')

    if existing is None:
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified reservation was not found',
        )

    existing_tags = existing.get('tags', [])
    existing_content_path = existing['options'].get('contentPath', '')

    # 削除してから再登録 (tags を保持)
    from app.utils.mirakc import RecordingOptions
    new_options: RecordingOptions = {
        'contentPath': existing_content_path,
        'priority': reservation_req.record_settings.priority,
        'preFilters': reservation_req.record_settings.pre_filters,
        'postFilters': reservation_req.record_settings.post_filters,
    }

    try:
        await client.delete_schedule(reservation_id)
        schedule = await client.add_schedule(
            program_id = reservation_id,
            options = new_options,
            tags = existing_tags,
        )
    except httpx.HTTPStatusError as ex:
        logging.error(f'[ReservationsRouter][UpdateReservationAPI] Failed to update schedule: {ex}')
        # 削除成功後の再登録失敗: best-effort で旧設定での復旧を試みる
        try:
            await client.add_schedule(reservation_id, existing['options'], existing_tags)
        except Exception:
            pass
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'mirakc returned error: {ex.response.status_code}')
    except (httpx.NetworkError, httpx.TimeoutException) as ex:
        logging.error(f'[ReservationsRouter][UpdateReservationAPI] mirakc unreachable: {ex}')
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='mirakc is not available')

    return await _decode_schedule(schedule)


@router.delete(
    '/{reservation_id}',
    summary = '録画予約削除 API',
    response_description = '録画予約削除結果。',
)
async def DeleteReservationAPI(
    reservation_id: Annotated[int, Path(description='録画予約 ID (mirakc ProgramId)。')],
):
    """
    指定した録画予約を削除する。
    """
    client = MirakcClient()
    try:
        deleted = await client.delete_schedule(reservation_id)
    except (httpx.NetworkError, httpx.TimeoutException) as ex:
        logging.error(f'[ReservationsRouter][DeleteReservationAPI] mirakc unreachable: {ex}')
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='mirakc is not available')

    if not deleted:
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified reservation was not found',
        )

    return {'detail': 'Reservation deleted successfully'}
