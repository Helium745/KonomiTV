
import re
from typing import Any, Literal, NotRequired

from typing_extensions import TypedDict


class RecordingOptions(TypedDict):
    """mirakc の録画オプション (WebRecordingScheduleInput.options)"""
    contentPath: NotRequired[str]  # basedir からの相対パス; records-dir 未設定時は必須
    priority: int                  # チューナー優先度 (デフォルト 0)
    preFilters: list[str]
    postFilters: list[str]
    logFilter: NotRequired[str]


class WebRecordingSchedule(TypedDict):
    """mirakc の録画スケジュール (GET/POST /api/recording/schedules の応答)"""
    state: Literal['scheduled', 'tracking', 'recording', 'rescheduling', 'finished', 'failed']
    program: dict[str, Any]  # MirakurunProgram
    options: RecordingOptions
    tags: list[str]
    failedReason: NotRequired[dict[str, Any]]  # 失敗時のみ存在


class WebRecordingRecorder(TypedDict):
    """mirakc の実行中レコーダー (GET /api/recording/recorders の応答)"""
    programId: int
    startedAt: int  # UNIX ミリ秒
    pipeline: list[dict[str, Any]]


# --- プログラム ID ユーティリティ ---
# mirakc ProgramId = network_id * 10^10 + service_id * 10^5 + event_id

def encode_program_id(network_id: int, service_id: int, event_id: int) -> int:
    """
    NID / SID / EID を mirakc 形式の ProgramId (u64) に変換する

    Args:
        network_id (int): ネットワーク ID
        service_id (int): サービス ID
        event_id (int): イベント ID

    Returns:
        int: mirakc ProgramId
    """
    return network_id * 10_000_000_000 + service_id * 100_000 + event_id


def decode_program_id(program_id: int) -> tuple[int, int, int]:
    """
    mirakc 形式の ProgramId を (network_id, service_id, event_id) に分解する

    Args:
        program_id (int): mirakc ProgramId

    Returns:
        tuple[int, int, int]: (network_id, service_id, event_id)
    """
    network_id = program_id // 10_000_000_000
    service_id = (program_id % 10_000_000_000) // 100_000
    event_id = program_id % 100_000
    return network_id, service_id, event_id


def program_id_from_konomitv_id(konomitv_id: str) -> int:
    """
    KonomiTV 形式の番組 ID 文字列を mirakc ProgramId に変換する

    Args:
        konomitv_id (str): KonomiTV 番組 ID ("NID{nid}-SID{sid:03d}-EID{eid}" 形式)

    Returns:
        int: mirakc ProgramId

    Raises:
        ValueError: ID 文字列のフォーマットが不正な場合
    """
    m = re.match(r'NID(\d+)-SID(\d+)-EID(\d+)', konomitv_id)
    if m is None:
        raise ValueError(f'Invalid KonomiTV program ID: {konomitv_id!r}')
    return encode_program_id(int(m.group(1)), int(m.group(2)), int(m.group(3)))
