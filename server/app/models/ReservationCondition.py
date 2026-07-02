
import json
from typing import cast

from tortoise import fields
from tortoise.fields import Field as TortoiseField
from tortoise.models import Model as TortoiseModel

from app import schemas


class ReservationCondition(TortoiseModel):
    """
    キーワード自動予約条件を表す Tortoise ORM モデル

    番組検索条件と録画設定を SQLite に保持し、AutoReservationTask が定期的に
    Program DB を走査してマッチした番組を mirakc のスケジュール API に登録する。
    mirakc 側のスケジュールには `konomitv-condition-{id}` タグを付与し、
    条件の削除・無効化時にそのタグで一括削除できるようにする。
    """

    class Meta(TortoiseModel.Meta):
        table: str = 'reservation_conditions'

    id = fields.IntField(pk=True)
    # 条件が有効かどうか (False のとき新規スケジュール登録は停止、既存スケジュールは削除)
    is_enabled = fields.BooleanField(default=True)
    # 番組検索条件 (schemas.ProgramSearchCondition を dict にシリアライズしたもの)
    program_search_condition = cast(
        TortoiseField[dict[str, object]],
        fields.JSONField(encoder=lambda x: json.dumps(x, ensure_ascii=False)),  # type: ignore[misc]
    )
    # 録画設定 (schemas.RecordSettings を dict にシリアライズしたもの)
    record_settings = cast(
        TortoiseField[dict[str, object]],
        fields.JSONField(encoder=lambda x: json.dumps(x, ensure_ascii=False)),  # type: ignore[misc]
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    def get_program_search_condition(self) -> schemas.ProgramSearchCondition:
        """DB の JSON から schemas.ProgramSearchCondition を復元する"""
        return schemas.ProgramSearchCondition.model_validate(self.program_search_condition)

    def get_record_settings(self) -> schemas.RecordSettings:
        """DB の JSON から schemas.RecordSettings を復元する"""
        return schemas.RecordSettings.model_validate(self.record_settings)

    @property
    def mirakc_tag(self) -> str:
        """この条件に対応する mirakc スケジュールタグ"""
        return f'konomitv-condition-{self.id}'
