
import asyncio
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import httpx

from app import logging


def _get_events_url() -> str:
    """mirakc の SSE エンドポイント URL を生成する (/events は /api 配下ではない)"""
    from app.config import Config
    return str(Config().general.mirakc_url).rstrip('/') + '/events'


class MirakcEventClient:
    """
    mirakc SSE イベント購読クライアント

    mirakc の `GET /events` エンドポイント (NOT /api 配下) に接続し、
    サーバー送信イベント (SSE) をリアルタイムに受信する。

    SSE フォーマット:
        event: <event_name>\\n
        data: <json_payload>\\n
        \\n

    自動再接続: 切断時に指数バックオフで再接続を試みる (最大 60 秒間隔)。
    停止: stop() を呼び出すか、run() の呼び出し元タスクをキャンセルする。
    """

    def __init__(self) -> None:
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """イベント購読を停止する"""
        self._stop_event.set()

    async def subscribe(self) -> AsyncIterator[tuple[str, Any]]:
        """
        SSE イベントを非同期イテレーターとして返す

        Yields:
            tuple[str, Any]: (event_name, json_payload) のペア
        """
        backoff = 1.0
        headers = {'Accept': 'text/event-stream', 'Cache-Control': 'no-cache'}

        while not self._stop_event.is_set():
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream('GET', _get_events_url(), headers=headers) as response:
                        response.raise_for_status()
                        logging.info('[MirakcEventClient] Connected to mirakc SSE /events')
                        backoff = 1.0  # 接続成功時にバックオフをリセット

                        event_name: str = ''
                        data_lines: list[str] = []

                        async for raw_line in response.aiter_lines():
                            if self._stop_event.is_set():
                                return

                            line = raw_line.rstrip('\r')

                            if line.startswith('event:'):
                                event_name = line[len('event:'):].strip()
                            elif line.startswith('data:'):
                                data_lines.append(line[len('data:'):].strip())
                            elif line == '':
                                # 空行 = イベント区切り
                                if event_name and data_lines:
                                    raw_data = '\n'.join(data_lines)
                                    try:
                                        payload = json.loads(raw_data)
                                    except json.JSONDecodeError:
                                        payload = raw_data
                                    yield (event_name, payload)
                                event_name = ''
                                data_lines = []

            except asyncio.CancelledError:
                return
            except Exception as ex:
                if self._stop_event.is_set():
                    return
                logging.warning(f'[MirakcEventClient] SSE connection lost: {ex}. Reconnecting in {backoff:.0f}s...')
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=backoff,
                    )
                    return  # stop() が呼ばれた
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 60.0)

    async def run(
        self,
        handlers: dict[str, Callable[[Any], Coroutine[Any, Any, None]]],
    ) -> None:
        """
        SSE イベントを受信し、対応するハンドラーに振り分けるディスパッチループ

        Args:
            handlers (dict[str, Callable]): イベント名 → async ハンドラー関数のマッピング
                                            ハンドラーは (payload: Any) → Coroutine を受け取る。
                                            '*' を指定すると全イベントに対して呼ばれる。
        """
        async for event_name, payload in self.subscribe():
            # 個別ハンドラーを実行
            if event_name in handlers:
                try:
                    await handlers[event_name](payload)
                except Exception as ex:
                    logging.error(f'[MirakcEventClient] Handler error for {event_name!r}: {ex}', exc_info=ex)
            # ワイルドカードハンドラーを実行
            if '*' in handlers:
                try:
                    await handlers['*']((event_name, payload))
                except Exception as ex:
                    logging.error(f'[MirakcEventClient] Wildcard handler error for {event_name!r}: {ex}', exc_info=ex)
