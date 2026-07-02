
from typing import Any

import httpx

from app.constants import HTTPX_CLIENT
from app.utils.mirakc.models import (
    RecordingOptions,
    WebRecordingRecorder,
    WebRecordingSchedule,
    WebTimeshiftRecord,
    WebTimeshiftRecorder,
)


def _get_api_url(endpoint: str) -> str:
    """mirakc API のエンドポイント URL を生成する (GetMirakcAPIEndpointURL のラッパー)"""
    from app.utils import GetMirakcAPIEndpointURL
    return GetMirakcAPIEndpointURL(endpoint)


class MirakcClient:
    """
    mirakc Web API REST クライアント

    全メソッドは async。HTTP リクエストには HTTPX_CLIENT() を使用し、
    URL 生成には GetMirakcAPIEndpointURL() を通じて config.general.mirakc_url を参照する。
    """

    # --- チャンネル / サービス / 番組 ---

    async def fetch_services(self) -> list[dict[str, Any]]:
        """GET /api/services → サービス一覧"""
        async with HTTPX_CLIENT() as client:
            response = await client.get(_get_api_url('/api/services'), timeout=10)
            response.raise_for_status()
            return response.json()

    async def fetch_service(self, service_id: int) -> dict[str, Any]:
        """GET /api/services/{id} → サービス情報"""
        async with HTTPX_CLIENT() as client:
            response = await client.get(_get_api_url(f'/api/services/{service_id}'), timeout=5)
            response.raise_for_status()
            return response.json()

    async def fetch_service_logo(self, service_id: int) -> bytes | None:
        """
        GET /api/services/{id}/logo → ロゴ画像バイナリ
        ロゴが存在しない場合 (HTTP 503) は None を返す
        """
        try:
            async with HTTPX_CLIENT() as client:
                response = await client.get(_get_api_url(f'/api/services/{service_id}/logo'), timeout=5)
            if response.status_code == 200:
                return response.content
            return None
        except (httpx.NetworkError, httpx.TimeoutException):
            return None

    async def fetch_programs(self) -> list[dict[str, Any]]:
        """GET /api/programs → 番組一覧 (全サービス)"""
        async with HTTPX_CLIENT() as client:
            response = await client.get(_get_api_url('/api/programs'), timeout=60)
            response.raise_for_status()
            return response.json()

    async def fetch_program(self, program_id: int) -> dict[str, Any] | None:
        """GET /api/programs/{id} → 番組情報、存在しない場合は None"""
        try:
            async with HTTPX_CLIENT() as client:
                response = await client.get(_get_api_url(f'/api/programs/{program_id}'), timeout=5)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (httpx.NetworkError, httpx.TimeoutException):
            return None

    async def fetch_tuners(self) -> list[dict[str, Any]]:
        """GET /api/tuners → チューナー一覧"""
        async with HTTPX_CLIENT() as client:
            response = await client.get(_get_api_url('/api/tuners'), timeout=5)
            response.raise_for_status()
            return response.json()

    # --- 録画スケジュール (予約) ---

    async def fetch_schedules(self) -> list[WebRecordingSchedule]:
        """GET /api/recording/schedules → 録画スケジュール一覧"""
        async with HTTPX_CLIENT() as client:
            response = await client.get(_get_api_url('/api/recording/schedules'), timeout=10)
            response.raise_for_status()
            return response.json()

    async def fetch_schedule(self, program_id: int) -> WebRecordingSchedule | None:
        """GET /api/recording/schedules/{program_id} → 録画スケジュール、存在しない場合は None"""
        try:
            async with HTTPX_CLIENT() as client:
                response = await client.get(_get_api_url(f'/api/recording/schedules/{program_id}'), timeout=5)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (httpx.NetworkError, httpx.TimeoutException):
            return None

    async def add_schedule(
        self,
        program_id: int,
        options: RecordingOptions,
        tags: list[str] | None = None,
    ) -> WebRecordingSchedule:
        """
        POST /api/recording/schedules → 録画スケジュールを追加する

        Args:
            program_id (int): mirakc ProgramId
            options (RecordingOptions): 録画オプション (contentPath / priority / filters)
            tags (list[str] | None): タグ一覧

        Returns:
            WebRecordingSchedule: 作成された録画スケジュール

        Raises:
            httpx.HTTPStatusError: mirakc がエラーを返した場合
        """
        body = {
            'programId': program_id,
            'options': options,
            'tags': tags or [],
        }
        async with HTTPX_CLIENT() as client:
            response = await client.post(
                _get_api_url('/api/recording/schedules'),
                json=body,
                timeout=10,
            )
            response.raise_for_status()
            return response.json()

    async def delete_schedule(self, program_id: int) -> bool:
        """
        DELETE /api/recording/schedules/{program_id} → 録画スケジュールを削除する

        Returns:
            bool: 削除成功なら True、存在しない場合は False
        """
        async with HTTPX_CLIENT() as client:
            response = await client.delete(_get_api_url(f'/api/recording/schedules/{program_id}'), timeout=5)
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    async def delete_schedules_by_tag(self, tag: str) -> None:
        """DELETE /api/recording/schedules?tag={tag} → タグに一致するスケジュールを一括削除"""
        async with HTTPX_CLIENT() as client:
            response = await client.delete(
                _get_api_url('/api/recording/schedules'),
                params={'tag': tag},
                timeout=10,
            )
            response.raise_for_status()

    # --- 録画中レコーダー ---

    async def fetch_recorders(self) -> list[WebRecordingRecorder]:
        """GET /api/recording/recorders → 実行中レコーダー一覧"""
        async with HTTPX_CLIENT() as client:
            response = await client.get(_get_api_url('/api/recording/recorders'), timeout=5)
            response.raise_for_status()
            return response.json()

    async def stop_recorder(self, program_id: int) -> bool:
        """
        DELETE /api/recording/recorders/{program_id} → スケジュールを維持したまま録画を停止する

        Returns:
            bool: 停止成功なら True、存在しない場合は False
        """
        async with HTTPX_CLIENT() as client:
            response = await client.delete(_get_api_url(f'/api/recording/recorders/{program_id}'), timeout=5)
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    # --- タイムシフト録画 ---

    async def fetch_timeshift_recorders(self) -> list[WebTimeshiftRecorder]:
        """GET /api/timeshift → タイムシフトレコーダー一覧"""
        async with HTTPX_CLIENT() as client:
            response = await client.get(_get_api_url('/api/timeshift'), timeout=10)
            response.raise_for_status()
            return response.json()

    async def fetch_timeshift_recorder(self, recorder: str) -> WebTimeshiftRecorder | None:
        """GET /api/timeshift/{recorder} → タイムシフトレコーダー、存在しない場合は None"""
        try:
            async with HTTPX_CLIENT() as client:
                response = await client.get(_get_api_url(f'/api/timeshift/{recorder}'), timeout=5)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (httpx.NetworkError, httpx.TimeoutException):
            return None

    async def fetch_timeshift_records(self, recorder: str) -> list[WebTimeshiftRecord]:
        """GET /api/timeshift/{recorder}/records → タイムシフト record 一覧"""
        async with HTTPX_CLIENT() as client:
            response = await client.get(_get_api_url(f'/api/timeshift/{recorder}/records'), timeout=10)
            response.raise_for_status()
            return response.json()

    async def fetch_timeshift_record(self, recorder: str, record_id: int) -> WebTimeshiftRecord | None:
        """GET /api/timeshift/{recorder}/records/{record} → タイムシフト record、存在しない場合は None"""
        try:
            async with HTTPX_CLIENT() as client:
                response = await client.get(_get_api_url(f'/api/timeshift/{recorder}/records/{record_id}'), timeout=5)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (httpx.NetworkError, httpx.TimeoutException):
            return None

    # --- タイムシフト ストリーム URL ヘルパー ---

    def get_timeshift_record_stream_url(self, recorder: str, record_id: int) -> str:
        """
        /api/timeshift/{recorder}/records/{record}/stream の URL を生成する
        (実際の接続・Range リクエストは VideoEncodingTask 側で行う)

        Args:
            recorder (str): mirakc タイムシフトレコーダー名
            record_id (int): タイムシフト record ID

        Returns:
            str: オンデマンドストリーム URL
        """
        return _get_api_url(f'/api/timeshift/{recorder}/records/{record_id}/stream')

    # --- ライブストリーム URL ヘルパー ---

    def get_service_stream_url(self, service_id: int, decode: bool = True) -> str:
        """
        /api/services/{id}/stream の URL を生成する (実際の接続は LiveEncodingTask 側で行う)

        Args:
            service_id (int): mirakc 形式のサービス ID
            decode (bool): デスクランブルするか (デフォルト True)

        Returns:
            str: ストリーム URL
        """
        url = _get_api_url(f'/api/services/{service_id}/stream')
        if not decode:
            url += '?decode=0'
        return url
