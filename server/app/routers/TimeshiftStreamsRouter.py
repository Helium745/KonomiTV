
import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import Response
from sse_starlette.sse import EventSourceResponse

from app import logging
from app.models.RecordedProgram import RecordedProgram
from app.streams.StreamEncodingOptions import (
    SplitQualityAndEncodingOptions,
    StreamQualityWithOptions,
)
from app.streams.TimeshiftRecordedProgram import BuildRecordedProgramFromTimeshiftRecord
from app.streams.VideoStream import VideoStream
from app.utils.mirakc import MirakcClient


# ルーター
router = APIRouter(
    tags = ['Streams'],
    prefix = '/api/streams/timeshift',
)


async def ValidateTimeshiftRecordID(
    recorder_id: Annotated[str, Path(description='mirakc 上のタイムシフトレコーダー名。')],
    record_id: Annotated[int, Path(description='mirakc 上のタイムシフト record ID。')],
) -> RecordedProgram:
    """ タイムシフト record のバリデーションと、視聴用の (DB 未保存の) RecordedProgram への変換 """

    mirakc_client = MirakcClient()
    record = await mirakc_client.fetch_timeshift_record(recorder_id, record_id)
    if record is None:
        logging.error(
            f'[TimeshiftStreamsRouter][ValidateTimeshiftRecordID] Specified record was not found. '
            f'[recorder_id: {recorder_id}, record_id: {record_id}]'
        )
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified record was not found',
        )

    return await BuildRecordedProgramFromTimeshiftRecord(recorder_id, record)


async def ValidateQuality(quality: Annotated[str, Path(description='映像の品質。ex: 1080p')]) -> StreamQualityWithOptions:
    """ 映像の品質のバリデーション """

    # 指定された品質が存在するか確認
    ## 品質の指定に -10bit や -24fps が付いていれば分解する
    stream_quality = SplitQualityAndEncodingOptions(quality)
    if stream_quality is None:
        logging.error(f'[TimeshiftStreamsRouter][ValidateQuality] Specified quality was not found. [quality: {quality}]')
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified quality was not found',
        )

    return stream_quality


@router.get(
    '/{recorder_id}/{record_id}/{quality}/playlist',
    summary = 'タイムシフト録画 HLS M3U8 プレイリスト API',
    response_class = Response,
    responses = {
        status.HTTP_200_OK: {
            'description': 'タイムシフト録画の HLS M3U8 プレイリスト。',
            'content': {'application/vnd.apple.mpegurl': {}},
        }
    }
)
async def TimeshiftHLSPlaylistAPI(
    recorded_program: Annotated[RecordedProgram, Depends(ValidateTimeshiftRecordID)],
    stream_quality: Annotated[StreamQualityWithOptions, Depends(ValidateQuality)],
    session_id: Annotated[str, Query(description='セッション ID（クライアント側で適宜生成したランダム値を指定する）。')],
    cache_key: Annotated[str | None, Query(description='キャッシュ制御用のキー。')] = None,
):
    """
    指定された画質に対応する、タイムシフト録画のストリーミング用 HLS M3U8 プレイリストを返す。<br>
    この M3U8 プレイリストは仮想的なもので、すべてのセグメントデータがエンコード済みとは限らない。セグメントはリクエストされ次第随時生成される。<br>
    通常の録画番組視聴 (/api/streams/video) と同じ VideoStream / VideoEncodingTask を再利用しているが、
    入力は mirakc のタイムシフトリングバッファに対する Range リクエストとなるため、シーク位置は録画時間に対する比率から近似される。
    """

    video_stream = VideoStream(
        session_id,
        recorded_program,
        stream_quality.quality,
        stream_quality.encoding_options,
        is_new_session_allowed = True,
    )

    virtual_playlist = video_stream.getVirtualPlaylist(cache_key)
    return Response(
        content = virtual_playlist,
        media_type = 'application/vnd.apple.mpegurl',
        headers = {
            'Cache-Control': 'max-age=0',
        },
    )


@router.get(
    '/{recorder_id}/{record_id}/{quality}/segment',
    summary = 'タイムシフト録画 HLS セグメント API',
    response_class = Response,
    responses = {
        status.HTTP_200_OK: {
            'description': 'HLS セグメントとして分割された MPEG-TS データ。',
            'content': {'video/mp2t': {}},
        }
    }
)
async def TimeshiftHLSSegmentAPI(
    recorded_program: Annotated[RecordedProgram, Depends(ValidateTimeshiftRecordID)],
    stream_quality: Annotated[StreamQualityWithOptions, Depends(ValidateQuality)],
    session_id: Annotated[str, Query(description='セッション ID（クライアント側で適宜生成したランダム値を指定する）。')],
    sequence: Annotated[int, Query(description='HLS セグメントの 0 スタートのシーケンス番号。')],
    cache_key: Annotated[str | None, Query(description='キャッシュ制御用のキー。')],
):
    """
    指定された画質に対応する、タイムシフト録画のストリーミング用 HLS セグメントを返す。<br>
    呼び出された時点でエンコードされていない場合は既存のエンコードタスクが終了され、<br>
    sequence の HLS セグメントが含まれる範囲から新たにエンコードタスクが開始される。
    """

    video_stream = VideoStream(session_id, recorded_program, stream_quality.quality, stream_quality.encoding_options)

    segment_data = await video_stream.getSegment(sequence)
    if segment_data is None:
        logging.error(
            f'{video_stream.log_prefix} Specified sequence segment was not found. '
            f'[sequence: {sequence}]'
        )
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified sequence segment was not found',
        )

    return Response(
        content = segment_data,
        media_type = 'video/mp2t',
        headers = {
            # キャッシュ有効期間を3時間に設定
            'Cache-Control': 'max-age=10800',
        },
    )


@router.get(
    '/{recorder_id}/{record_id}/{quality}/buffer',
    summary = 'タイムシフト録画 HLS バッファ範囲 API',
    response_class = Response,
    responses = {
        status.HTTP_200_OK: {
            'description': 'タイムシフト録画の HLS バッファ範囲が随時配信されるイベントストリーム。',
            'content': {'text/event-stream': {}},
        }
    }
)
async def TimeshiftHLSBufferAPI(
    recorded_program: Annotated[RecordedProgram, Depends(ValidateTimeshiftRecordID)],
    stream_quality: Annotated[StreamQualityWithOptions, Depends(ValidateQuality)],
    session_id: Annotated[str, Query(description='セッション ID（クライアント側で適宜生成したランダム値を指定する）。')],
):
    """
    タイムシフト録画の HLS バッファ範囲を Server-Sent Events で随時配信する。

    イベントには、
    - バッファ範囲の更新を示す **buffer_range_update**
    の1種類がある。

    どのイベントでも配信される JSON 構造は同じ。<br>
    エンコードタスクが終了した場合は、接続を終了する。
    """

    video_stream = VideoStream(session_id, recorded_program, stream_quality.quality, stream_quality.encoding_options)

    async def generator():
        """イベントストリームを出力するジェネレーター"""

        previous_buffer_range = video_stream.getBufferRange()

        yield {
            'event': 'buffer_range_update',
            'data': json.dumps({
                'begin': previous_buffer_range[0],
                'end': previous_buffer_range[1],
            }),
        }

        while True:
            buffer_range = video_stream.getBufferRange()

            if previous_buffer_range != buffer_range:
                logging.info(f'{video_stream.log_prefix} Buffer range updated. [begin: {buffer_range[0]}, end: {buffer_range[1]}]')
                yield {
                    'event': 'buffer_range_update',
                    'data': json.dumps({
                        'begin': buffer_range[0],
                        'end': buffer_range[1],
                    }),
                }

                previous_buffer_range = buffer_range

            await asyncio.sleep(0.1)

    return EventSourceResponse(generator())


@router.put(
    '/{recorder_id}/{record_id}/{quality}/keep-alive',
    summary = 'タイムシフト録画 HLS Keep-Alive API',
    status_code = status.HTTP_204_NO_CONTENT,
)
async def TimeshiftHLSKeepAliveAPI(
    recorded_program: Annotated[RecordedProgram, Depends(ValidateTimeshiftRecordID)],
    stream_quality: Annotated[StreamQualityWithOptions, Depends(ValidateQuality)],
    session_id: Annotated[str, Query(description='セッション ID（クライアント側で適宜生成したランダム値を指定する）。')],
):
    """
    タイムシフト録画のストリーミング用 HLS セグメントの生成を継続するための API 。<br>
    ストリーミングセッションを維持するために、この API はタイムシフト録画の視聴を続けている間、定期的に呼び出さなければならない。<br>
    この API が定期的に呼び出されなくなった場合、一定時間後にストリーミング用 HLS セグメントの生成が停止され、メモリ上のデータが破棄される。
    """

    video_stream = VideoStream(session_id, recorded_program, stream_quality.quality, stream_quality.encoding_options)
    video_stream.keepAlive()
