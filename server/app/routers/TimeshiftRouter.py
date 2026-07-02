
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app import logging, schemas
from app.constants import JST
from app.models.Channel import Channel
from app.utils.mirakc import MirakcClient, WebTimeshiftRecord, WebTimeshiftRecorder
from app.utils.TSInformation import TSInformation


# ルーター
router = APIRouter(
    tags = ['Timeshift'],
    prefix = '/api/timeshift',
)


def MillisecondToDatetime(millisecond: int) -> datetime:
    """ mirakc から取得した UNIX ミリ秒のタイムスタンプを、タイムゾーン付き (JST) の datetime に変換する """
    return datetime.fromtimestamp(millisecond / 1000, tz=JST)


async def ConvertToChannelSchema(network_id: int, service_id: int) -> schemas.Channel | None:
    """
    ネットワーク ID とサービス ID から対応する KonomiTV 側のチャンネルを取得し、スキーマへ変換する
    タイムシフト録画は DB の RecordedProgram と異なりチャンネルへの FK を持たないため、都度検索する

    Args:
        network_id (int): ネットワーク ID
        service_id (int): サービス ID

    Returns:
        schemas.Channel | None: 対応するチャンネルが見つかった場合のみそのスキーマ、見つからない場合は None
    """

    channel = await Channel.filter(network_id=network_id, service_id=service_id).first()
    if channel is None:
        return None
    return schemas.Channel.model_validate(channel, from_attributes=True)


async def ConvertToTimeshiftRecorderSchema(recorder_id: str, recorder: WebTimeshiftRecorder) -> schemas.TimeshiftRecorder:
    """ mirakc の WebTimeshiftRecorder を KonomiTV の TimeshiftRecorder スキーマへ変換する """

    channel_schema = await ConvertToChannelSchema(recorder['service']['networkId'], recorder['service']['serviceId'])
    return schemas.TimeshiftRecorder(
        recorder_id = recorder_id,
        channel = channel_schema,
        network_id = recorder['service']['networkId'],
        service_id = recorder['service']['serviceId'],
        is_recording = recorder['recording'],
        current_record_id = recorder.get('currentRecordId'),
        total_records = recorder['numRecords'],
        start_time = MillisecondToDatetime(recorder['startTime']),
        end_time = MillisecondToDatetime(recorder['endTime']),
        duration = recorder['duration'] / 1000,
    )


async def ConvertToTimeshiftRecordSchema(recorder_id: str, record: WebTimeshiftRecord) -> schemas.TimeshiftRecord:
    """ mirakc の WebTimeshiftRecord を KonomiTV の TimeshiftRecord スキーマへ変換する """

    program = record['program']
    channel_schema = await ConvertToChannelSchema(program['networkId'], program['serviceId'])
    start_time = MillisecondToDatetime(record['startTime'])
    duration_seconds = record['duration'] / 1000

    return schemas.TimeshiftRecord(
        id = record['id'],
        recorder_id = recorder_id,
        channel = channel_schema,
        network_id = program['networkId'],
        service_id = program['serviceId'],
        event_id = program['eventId'],
        title = TSInformation.formatString(program.get('name', '')),
        description = TSInformation.formatString(program.get('description', '')) if program.get('description') else '番組概要を取得できませんでした。',
        genres = TSInformation.convertARIBGenresToGenreDicts(program.get('genres', [])),  # type: ignore
        is_free = program.get('isFree', True),
        is_recording = record['recording'],
        start_time = start_time,
        end_time = start_time + timedelta(seconds=duration_seconds),
        duration = duration_seconds,
        file_size = record['size'],
    )


async def ValidateRecorderID(recorder_id: Annotated[str, Path(description='mirakc 上のタイムシフトレコーダー名。')]) -> WebTimeshiftRecorder:
    """ タイムシフトレコーダー名のバリデーション """

    mirakc_client = MirakcClient()
    recorder = await mirakc_client.fetch_timeshift_recorder(recorder_id)
    if recorder is None:
        logging.error(f'[TimeshiftRouter][ValidateRecorderID] Specified recorder_id was not found. [recorder_id: {recorder_id}]')
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified recorder_id was not found',
        )

    return recorder


@router.get(
    '/recorders',
    summary = 'タイムシフトレコーダー一覧 API',
    response_description = 'タイムシフトレコーダーのリスト。',
    response_model = schemas.TimeshiftRecorders,
)
async def TimeshiftRecordersAPI():
    """
    mirakc 上で動作しているすべてのタイムシフトレコーダーを取得する。<br>
    タイムシフトレコーダーは、mirakc の `config.yml` で `timeshift` が設定されているチャンネルごとに存在する。
    """

    mirakc_client = MirakcClient()
    recorders = await mirakc_client.fetch_timeshift_recorders()

    timeshift_recorders = [
        await ConvertToTimeshiftRecorderSchema(recorder['name'], recorder)
        for recorder in recorders
    ]

    return {
        'total': len(timeshift_recorders),
        'timeshift_recorders': timeshift_recorders,
    }


@router.get(
    '/recorders/{recorder_id}',
    summary = 'タイムシフトレコーダー API',
    response_description = 'タイムシフトレコーダー。',
    response_model = schemas.TimeshiftRecorder,
)
async def TimeshiftRecorderAPI(
    recorder_id: Annotated[str, Path(description='mirakc 上のタイムシフトレコーダー名。')],
    recorder: Annotated[WebTimeshiftRecorder, Depends(ValidateRecorderID)],
):
    """
    指定されたタイムシフトレコーダーを取得する。
    """

    return await ConvertToTimeshiftRecorderSchema(recorder_id, recorder)


@router.get(
    '/recorders/{recorder_id}/records',
    summary = 'タイムシフト record 一覧 API',
    response_description = 'タイムシフト record のリスト。',
    response_model = schemas.TimeshiftRecords,
)
async def TimeshiftRecorderRecordsAPI(
    recorder_id: Annotated[str, Path(description='mirakc 上のタイムシフトレコーダー名。')],
    _: Annotated[WebTimeshiftRecorder, Depends(ValidateRecorderID)],
):
    """
    指定されたタイムシフトレコーダーのリングバッファに残っているすべての record を、開始時刻が新しい順に取得する。<br>
    リングバッファはサイズ固定のため、古い record は新しい録画で上書きされ次第 mirakc 側で自動的に消えていく。
    """

    mirakc_client = MirakcClient()
    records = await mirakc_client.fetch_timeshift_records(recorder_id)

    timeshift_records = [
        await ConvertToTimeshiftRecordSchema(recorder_id, record)
        for record in records
    ]
    timeshift_records.sort(key=lambda timeshift_record: timeshift_record.start_time, reverse=True)

    return {
        'total': len(timeshift_records),
        'timeshift_records': timeshift_records,
    }


@router.get(
    '/recorders/{recorder_id}/records/{record_id}',
    summary = 'タイムシフト record API',
    response_description = 'タイムシフト record。',
    response_model = schemas.TimeshiftRecord,
)
async def TimeshiftRecorderRecordAPI(
    recorder_id: Annotated[str, Path(description='mirakc 上のタイムシフトレコーダー名。')],
    record_id: Annotated[int, Path(description='mirakc 上のタイムシフト record ID。')],
    _: Annotated[WebTimeshiftRecorder, Depends(ValidateRecorderID)],
):
    """
    指定されたタイムシフト record を取得する。
    """

    mirakc_client = MirakcClient()
    record = await mirakc_client.fetch_timeshift_record(recorder_id, record_id)
    if record is None:
        logging.error(
            f'[TimeshiftRouter][TimeshiftRecorderRecordAPI] Specified record_id was not found. '
            f'[recorder_id: {recorder_id}, record_id: {record_id}]'
        )
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified record_id was not found',
        )

    return await ConvertToTimeshiftRecordSchema(recorder_id, record)
