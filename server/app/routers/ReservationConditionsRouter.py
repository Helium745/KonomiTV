
from typing import Annotated

import httpx
from fastapi import APIRouter, Body, HTTPException, Path, status

from app import logging, schemas
from app.models.ReservationCondition import ReservationCondition
from app.utils.mirakc import MirakcClient


# ルーター
router = APIRouter(
    tags = ['Reservation Conditions'],
    prefix = '/api/recording/conditions',
)


async def _count_reserved(condition_id: int) -> int:
    """
    指定した条件 ID に対応するタグを持つ mirakc スケジュール数を返す。
    mirakc への接続に失敗した場合は 0 を返す。
    """
    tag = f'konomitv-condition-{condition_id}'
    try:
        client = MirakcClient()
        schedules = await client.fetch_schedules()
        return sum(1 for s in schedules if tag in s.get('tags', []))
    except (httpx.HTTPStatusError, httpx.NetworkError, httpx.TimeoutException):
        return 0


def _condition_to_schema(
    condition: ReservationCondition,
    reservation_count: int,
) -> schemas.ReservationCondition:
    """ReservationCondition ORM → schemas.ReservationCondition に変換する"""
    return schemas.ReservationCondition(
        id = condition.id,
        is_enabled = condition.is_enabled,
        reservation_count = reservation_count,
        program_search_condition = condition.get_program_search_condition(),
        record_settings = condition.get_record_settings(),
    )


@router.get(
    '',
    summary = 'キーワード自動予約条件一覧取得 API',
    response_description = 'キーワード自動予約条件のリスト。',
    response_model = schemas.ReservationConditions,
)
async def ReservationConditionsAPI():
    """
    登録済みのキーワード自動予約条件を全件取得する。
    各条件に対応する mirakc スケジュール数も返す。
    """
    conditions = await ReservationCondition.all().order_by('id')

    # mirakc スケジュールを一括取得してタグでカウント (N+1 回避)
    tag_counts: dict[str, int] = {}
    try:
        client = MirakcClient()
        schedules = await client.fetch_schedules()
        for schedule in schedules:
            for tag in schedule.get('tags', []):
                if tag.startswith('konomitv-condition-'):
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
    except (httpx.HTTPStatusError, httpx.NetworkError, httpx.TimeoutException) as ex:
        logging.warning(f'[ReservationConditionsRouter] Failed to fetch schedules for count: {ex}')

    result = [
        _condition_to_schema(c, tag_counts.get(f'konomitv-condition-{c.id}', 0))
        for c in conditions
    ]
    return schemas.ReservationConditions(total=len(result), reservation_conditions=result)


@router.post(
    '',
    summary = 'キーワード自動予約条件登録 API',
    response_description = '登録したキーワード自動予約条件。',
    response_model = schemas.ReservationCondition,
)
async def RegisterReservationConditionAPI(
    request: Annotated[schemas.ReservationConditionAddRequest, Body(description='キーワード自動予約条件。')],
):
    """
    新しいキーワード自動予約条件を登録する。
    登録後、AutoReservationTask が直ちに照合を実行して既存番組への予約登録を試みる。
    """
    condition = await ReservationCondition.create(
        is_enabled = request.is_enabled,
        program_search_condition = request.program_search_condition.model_dump(mode='json'),
        record_settings = request.record_settings.model_dump(mode='json'),
    )

    # 照合タスクを即時トリガー
    await _trigger_reconcile()

    return _condition_to_schema(condition, 0)


@router.get(
    '/{reservation_condition_id}',
    summary = 'キーワード自動予約条件取得 API',
    response_description = 'キーワード自動予約条件。',
    response_model = schemas.ReservationCondition,
)
async def ReservationConditionAPI(
    reservation_condition_id: Annotated[int, Path(description='キーワード自動予約条件 ID。')],
):
    """
    指定したキーワード自動予約条件を取得する。
    """
    condition = await ReservationCondition.filter(id=reservation_condition_id).first()
    if condition is None:
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified reservation condition was not found',
        )
    count = await _count_reserved(condition.id)
    return _condition_to_schema(condition, count)


@router.put(
    '/{reservation_condition_id}',
    summary = 'キーワード自動予約条件更新 API',
    response_description = '更新したキーワード自動予約条件。',
    response_model = schemas.ReservationCondition,
)
async def UpdateReservationConditionAPI(
    reservation_condition_id: Annotated[int, Path(description='キーワード自動予約条件 ID。')],
    request: Annotated[schemas.ReservationConditionUpdateRequest, Body(description='キーワード自動予約条件。')],
):
    """
    キーワード自動予約条件を更新する。
    更新後、既存の自動予約スケジュール (このタグを持つもの) を全削除し、
    AutoReservationTask が新しい条件で再照合・再登録を行う。
    """
    condition = await ReservationCondition.filter(id=reservation_condition_id).first()
    if condition is None:
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified reservation condition was not found',
        )

    # 条件を更新
    condition.is_enabled = request.is_enabled
    condition.program_search_condition = request.program_search_condition.model_dump(mode='json')
    condition.record_settings = request.record_settings.model_dump(mode='json')
    await condition.save()

    # 更新後は旧タグのスケジュールを削除してから再照合 (新条件でクリーンに再登録)
    try:
        client = MirakcClient()
        await client.delete_schedules_by_tag(condition.mirakc_tag)
    except (httpx.HTTPStatusError, httpx.NetworkError, httpx.TimeoutException) as ex:
        logging.warning(f'[ReservationConditionsRouter] Failed to delete old schedules on update: {ex}')

    await _trigger_reconcile()

    return _condition_to_schema(condition, 0)


@router.delete(
    '/{reservation_condition_id}',
    summary = 'キーワード自動予約条件削除 API',
    response_description = 'キーワード自動予約条件削除結果。',
)
async def DeleteReservationConditionAPI(
    reservation_condition_id: Annotated[int, Path(description='キーワード自動予約条件 ID。')],
):
    """
    キーワード自動予約条件を削除する。
    削除と同時に、この条件が追加した mirakc スケジュール (タグ付き) も一括削除する。
    """
    condition = await ReservationCondition.filter(id=reservation_condition_id).first()
    if condition is None:
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified reservation condition was not found',
        )

    # mirakc のタグ付きスケジュールを削除
    try:
        client = MirakcClient()
        await client.delete_schedules_by_tag(condition.mirakc_tag)
    except (httpx.HTTPStatusError, httpx.NetworkError, httpx.TimeoutException) as ex:
        logging.warning(f'[ReservationConditionsRouter] Failed to delete schedules on condition delete: {ex}')

    await condition.delete()
    return {'detail': 'Reservation condition deleted successfully'}


async def _trigger_reconcile() -> None:
    """AutoReservationTask に照合をトリガーする (循環インポート回避のため遅延インポート)"""
    try:
        from app.tasks.AutoReservationTask import AutoReservationTask
        await AutoReservationTask().trigger_reconcile()
    except Exception as ex:
        logging.warning(f'[ReservationConditionsRouter] Failed to trigger reconcile: {ex}')
