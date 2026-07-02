
import re
import unicodedata
from datetime import datetime

from app import schemas
from app.constants import JST


def _normalize_text(text: str) -> str:
    """
    全角→半角 / 大文字→小文字 の正規化を行い、あいまい検索に対応する。
    unicodedata.normalize を使ってNFKCを適用し、さらに小文字化する。
    """
    return unicodedata.normalize('NFKC', text).lower()


def _keyword_match(keyword: str, targets: list[str], condition: schemas.ProgramSearchCondition) -> bool:
    """
    キーワードが検索対象文字列リストのいずれかにマッチするか判定する。

    Args:
        keyword (str): 検索キーワード
        targets (list[str]): 検索対象文字列のリスト
        condition (ProgramSearchCondition): 検索条件 (case/fuzzy/regex フラグ参照)

    Returns:
        bool: マッチした場合 True
    """
    if not keyword:
        return True

    if condition.is_regex_search_enabled:
        flags = 0 if condition.is_case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(keyword, flags)
            return any(pattern.search(t) for t in targets)
        except re.error:
            return False

    if condition.is_fuzzy_search_enabled:
        norm_kw = _normalize_text(keyword)
        return any(norm_kw in _normalize_text(t) for t in targets)

    if condition.is_case_sensitive:
        return any(keyword in t for t in targets)

    kw_lower = keyword.lower()
    return any(kw_lower in t.lower() for t in targets)


def match_program(
    condition: schemas.ProgramSearchCondition,
    program: 'Program',
    existing_recorded_titles: set[str] | None = None,
) -> bool:
    """
    番組が自動予約条件にマッチするかを判定する。

    Args:
        condition (schemas.ProgramSearchCondition): 番組検索条件
        program (Program): 判定対象の番組 (Tortoise ORM モデル)
        existing_recorded_titles (set[str] | None): 既存録画タイトルのセット
            (duplicate_title_check_scope 処理用。None の場合はチェックをスキップ)

    Returns:
        bool: マッチした場合 True
    """

    # キーワード検索
    keyword = condition.keyword.strip()
    if keyword:
        targets = [program.title] if condition.is_title_only else [program.title, program.description]
        if not _keyword_match(keyword, targets, condition):
            return False

    # 除外キーワード
    exclude_keyword = condition.exclude_keyword.strip()
    if exclude_keyword:
        targets = [program.title] if condition.is_title_only else [program.title, program.description]
        if _keyword_match(exclude_keyword, targets, condition):
            return False

    # サービス範囲絞り込み
    if condition.service_ranges is not None:
        allowed = {(r.network_id, r.service_id) for r in condition.service_ranges}
        if (program.network_id, program.service_id) not in allowed:
            return False

    # ジャンル絞り込み
    if condition.genre_ranges is not None:
        genre_match = any(
            any(g['major'] == r['major'] and (not r.get('middle') or g['middle'] == r['middle'])
                for r in condition.genre_ranges)
            for g in program.genres
        )
        if condition.is_exclude_genre_ranges:
            if genre_match:
                return False
        else:
            if not genre_match:
                return False

    # 放送日時範囲絞り込み
    if condition.date_ranges is not None:
        start_jst: datetime = program.start_time.astimezone(JST)
        # Python の weekday(): 0=月〜6=日、JS/ARIB: 0=日〜6=土
        js_day = (start_jst.weekday() + 1) % 7
        h, m = start_jst.hour, start_jst.minute
        prog_mins = js_day * 1440 + h * 60 + m

        date_match = False
        for dr in condition.date_ranges:
            start_mins = dr.start_day_of_week * 1440 + dr.start_hour * 60 + dr.start_minute
            end_mins = dr.end_day_of_week * 1440 + dr.end_hour * 60 + dr.end_minute
            if start_mins <= end_mins:
                if start_mins <= prog_mins <= end_mins:
                    date_match = True
                    break
            else:
                # 週をまたぐ範囲 (例: 土23時〜日1時)
                if prog_mins >= start_mins or prog_mins <= end_mins:
                    date_match = True
                    break

        if condition.is_exclude_date_ranges:
            if date_match:
                return False
        else:
            if not date_match:
                return False

    # 番組長絞り込み (分単位)
    duration_min = program.duration / 60.0
    if condition.duration_range_min is not None and duration_min < condition.duration_range_min:
        return False
    if condition.duration_range_max is not None and duration_min > condition.duration_range_max:
        return False

    # 有料/無料絞り込み
    if condition.broadcast_type == 'FreeOnly' and not program.is_free:
        return False
    if condition.broadcast_type == 'PaidOnly' and program.is_free:
        return False

    # 重複番組タイトルチェック
    if existing_recorded_titles is not None and condition.duplicate_title_check_scope != 'None':
        if program.title in existing_recorded_titles:
            return False

    return True


# 循環インポート回避のため遅延インポート用型アノテーション
from app.models.Program import Program  # noqa: E402
