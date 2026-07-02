
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import ClassVar

from app import logging
from app.config import Config
from app.constants import JST
from app.utils.mirakc import (
    MirakcClient,
    MirakcEventClient,
    WebRecordingSchedule,
)


class AutoReservationTask:
    """
    キーワード自動予約ルールエンジン

    KonomiTV の ReservationCondition モデルに登録された検索条件を使って、
    Program DB を走査し、条件にマッチした番組を mirakc のスケジュール API に自動登録する。

    実行タイミング:
    - 起動時: 全条件の一括照合
    - mirakc SSE `epg.programs-updated` 受信時: 30 秒デバウンス後に再照合
    - 定期スキャン: program_update_interval 分ごとのセーフティスキャン

    タグ命名規則: `konomitv-condition-{condition_id}`
    """

    # シングルトンインスタンス
    __instance: ClassVar[AutoReservationTask | None] = None

    # SSE イベント受信後、何秒待ってから照合を実行するか (デバウンス間隔)
    DEBOUNCE_SECONDS: ClassVar[int] = 30


    def __new__(cls) -> AutoReservationTask:
        if cls.__instance is None:
            cls.__instance = super().__new__(cls)
        return cls.__instance


    def __init__(self) -> None:
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self._stop_event = asyncio.Event()
            self._reconcile_trigger = asyncio.Event()
            self._main_task: asyncio.Task[None] | None = None
            self._sse_task: asyncio.Task[None] | None = None


    async def start(self) -> None:
        """タスクを開始する。既に起動済みの場合は何もしない。"""
        if self._main_task is not None and not self._main_task.done():
            return
        self._stop_event.clear()
        self._reconcile_trigger.clear()
        self._main_task = asyncio.create_task(self._main_loop())
        self._sse_task = asyncio.create_task(self._sse_listener())
        logging.info('[AutoReservationTask] Started.')


    async def stop(self) -> None:
        """タスクを停止する。"""
        self._stop_event.set()
        self._reconcile_trigger.set()  # wait をアンブロックする
        for task in [self._main_task, self._sse_task]:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._main_task = None
        self._sse_task = None
        logging.info('[AutoReservationTask] Stopped.')


    async def trigger_reconcile(self) -> None:
        """外部から照合を即座にトリガーする (条件追加/更新/削除時に呼ぶ)。"""
        self._reconcile_trigger.set()


    async def _sse_listener(self) -> None:
        """
        mirakc の SSE `/events` を購読し、`epg.programs-updated` を受け取ったら
        デバウンスタイマーを起動して照合をトリガーする。
        """
        event_client = MirakcEventClient()
        debounce_task: asyncio.Task[None] | None = None

        async def debounced_trigger() -> None:
            try:
                await asyncio.sleep(self.DEBOUNCE_SECONDS)
                if not self._stop_event.is_set():
                    logging.debug('[AutoReservationTask] EPG updated, triggering reconcile after debounce.')
                    self._reconcile_trigger.set()
            except asyncio.CancelledError:
                pass

        async for event_name, _ in event_client.subscribe():
            if self._stop_event.is_set():
                break
            if event_name == 'epg.programs-updated':
                # デバウンス: 前のタイマーがあればキャンセルして再スタート
                if debounce_task is not None and not debounce_task.done():
                    debounce_task.cancel()
                debounce_task = asyncio.create_task(debounced_trigger())


    async def _main_loop(self) -> None:
        """
        照合メインループ: 起動時照合 + 定期スキャン + trigger_reconcile() 待機
        """
        # 起動時に一度フル照合
        await self._reconcile_all()

        config = Config()
        interval = config.general.program_update_interval * 60.0  # 秒に変換

        while not self._stop_event.is_set():
            try:
                # SSE トリガーか定期スキャンのどちらか早い方を待つ
                await asyncio.wait_for(
                    self._reconcile_trigger.wait(),
                    timeout = interval,
                )
            except TimeoutError:
                pass  # 定期スキャン

            if self._stop_event.is_set():
                break
            self._reconcile_trigger.clear()
            await self._reconcile_all()


    async def _reconcile_all(self) -> None:
        """
        全ての有効な自動予約条件を照合し、mirakc スケジュールを同期する。
        - マッチ番組 → スケジュールが未登録なら POST
        - 条件が無効/削除済みのタグ → DELETE /api/recording/schedules?tag=...
        """
        # 循環インポート回避のために遅延インポート
        from app.models.Program import Program
        from app.models.RecordedProgram import RecordedProgram
        from app.models.ReservationCondition import ReservationCondition
        from app.utils.mirakc import RecordingOptions, encode_program_id
        from app.utils.ProgramSearchMatcher import match_program

        client = MirakcClient()
        now = datetime.now(JST)

        # 現在の mirakc スケジュールを一括取得してキャッシュ (重複登録防止)
        try:
            existing_schedules: list[WebRecordingSchedule] = await client.fetch_schedules()
        except Exception as ex:
            logging.error(f'[AutoReservationTask] Failed to fetch schedules: {ex}')
            return

        scheduled_program_ids: set[int] = {s['program']['id'] for s in existing_schedules}

        # 既存スケジュールに付いている自動予約タグを収集 (有効条件のタグと比較して孤立タグを削除)
        existing_condition_tags: set[str] = set()
        for schedule in existing_schedules:
            for tag in schedule.get('tags', []):
                if tag.startswith('konomitv-condition-'):
                    existing_condition_tags.add(tag)

        # 全条件を取得
        try:
            conditions = await ReservationCondition.all()
        except Exception as ex:
            logging.error(f'[AutoReservationTask] Failed to fetch conditions: {ex}')
            return

        active_condition_tags: set[str] = set()

        for condition in conditions:
            tag = condition.mirakc_tag
            if not condition.is_enabled:
                # 無効化された条件: 既存スケジュールを削除
                if tag in existing_condition_tags:
                    await self._delete_by_tag(client, tag)
                continue

            active_condition_tags.add(tag)
            search_cond = condition.get_program_search_condition()
            record_settings = condition.get_record_settings()

            # duplicate_title_check_scope のための既存録画タイトルセット
            existing_titles: set[str] | None = None
            if search_cond.duplicate_title_check_scope != 'None':
                try:
                    from app.models.RecordedProgram import RecordedProgram
                    period_days = search_cond.duplicate_title_check_period_days
                    from datetime import timedelta
                    since = now - timedelta(days=period_days)
                    recorded_program_titles = await RecordedProgram.filter(
                        recorded_at__gte=since,
                    ).values_list('title', flat=True)
                    existing_titles = {str(t) for t in recorded_program_titles}
                except Exception as ex:
                    logging.warning(f'[AutoReservationTask] Failed to fetch recorded programs for duplicate check: {ex}')
                    existing_titles = set()

            # 未来番組を DB から取得 (終了時刻が現在以降)
            try:
                programs = await Program.filter(end_time__gt=now).prefetch_related('channel')
            except Exception as ex:
                logging.warning(f'[AutoReservationTask] Failed to fetch programs: {ex}')
                continue

            for program in programs:
                if not match_program(search_cond, program, existing_titles):
                    continue

                program_id = encode_program_id(program.network_id, program.service_id, program.event_id)

                # 既に登録済みならスキップ
                if program_id in scheduled_program_ids:
                    continue

                # contentPath を生成
                from app.routers.ReservationsRouter import (
                    build_content_path,
                )
                mirakc_prog_info = {
                    'name': program.title,
                    'startAt': int(program.start_time.timestamp() * 1000),
                }
                channel_name = program.channel.name if hasattr(program, 'channel') and program.channel else 'unknown'
                content_path = build_content_path(mirakc_prog_info, channel_name)

                options: RecordingOptions = {
                    'contentPath': content_path,
                    'priority': record_settings.priority,
                    'preFilters': record_settings.pre_filters,
                    'postFilters': record_settings.post_filters,
                }

                try:
                    await client.add_schedule(
                        program_id = program_id,
                        options = options,
                        tags = [tag],
                    )
                    scheduled_program_ids.add(program_id)
                    logging.info(
                        f'[AutoReservationTask] Scheduled program_id={program_id} '
                        f'"{program.title}" for condition #{condition.id}'
                    )
                except Exception as ex:
                    # EPG 上にない番組、チューナー不足などは warning 扱い
                    logging.warning(
                        f'[AutoReservationTask] Failed to add schedule for program_id={program_id}: {ex}'
                    )

        # 孤立した自動予約タグ (条件が削除された) を削除
        orphan_tags = existing_condition_tags - active_condition_tags
        for tag in orphan_tags:
            await self._delete_by_tag(client, tag)


    async def _delete_by_tag(self, client: MirakcClient, tag: str) -> None:
        """指定タグを持つ全スケジュールを mirakc から削除する。"""
        try:
            await client.delete_schedules_by_tag(tag)
            logging.info(f'[AutoReservationTask] Deleted schedules with tag: {tag}')
        except Exception as ex:
            logging.error(f'[AutoReservationTask] Failed to delete schedules with tag {tag}: {ex}')
