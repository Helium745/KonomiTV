
import json
import re
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Query
from pydantic import TypeAdapter
from tortoise import connections

from app import schemas
from app.constants import JST
from app.models.Channel import Channel
from app.models.Program import Program
from app.utils import NormalizeToJSTDatetime, ParseDatetimeStringToJST
from app.utils.TSInformation import TSInformation


# ルーター
router = APIRouter(
    tags = ['Programs'],
    prefix = '/api/programs',
)

TimeTableSubchannelGroupKey = tuple[Literal['TS', 'BSService'], int, int]


def GetTimeTableChannelSortKey(channel_row: dict[str, Any]) -> tuple[int, int, int, int, str]:
    """
    番組表で利用するチャンネル並び替えキーを取得する

    Args:
        channel_row (dict[str, Any]): channels テーブルから取得したチャンネル行

    Returns:
        tuple[int, int, int, int, str]: 並び替え用キー
    """

    channel_number = str(channel_row['channel_number'])
    matched_channel_number = re.fullmatch(r'(\d+)(?:-(\d+))?', channel_number)

    # 想定外のチャンネル番号は末尾に回し、DB に残っている文字列表現で順序を安定させる
    if matched_channel_number is None:
        return (
            999999,
            999999,
            999999,
            int(channel_row['service_id']),
            channel_number,
        )

    base_channel_number = int(matched_channel_number.group(1))
    branch_number = int(matched_channel_number.group(2) or '0')

    # 地上波では同一局にチャンネルが複数ある場合、枝番を優先して並び替える
    ## 単純な文字列ソートだと 031-1, 031-2, 032-1 の順になり、同じ局のサブチャンネルが離れてしまう
    if channel_row['type'] == 'GR':
        remocon_id = base_channel_number // 10
        service_number = base_channel_number % 10
        return (
            remocon_id,
            branch_number,
            service_number,
            int(channel_row['service_id']),
            channel_number,
        )

    # 地デジ以外は従来通り3桁番号を主キーにしつつ、念のため枝番つき番号も自然な順序にする
    return (
        base_channel_number,
        branch_number,
        0,
        int(channel_row['service_id']),
        channel_number,
    )


def GetTimeTableSubchannelGroupKey(channel_row: dict[str, Any]) -> TimeTableSubchannelGroupKey | None:
    """
    番組表でサブチャンネルを同じ列へ入れるためのグループキーを取得する

    Args:
        channel_row (dict[str, Any]): channels テーブルから取得したチャンネル行

    Returns:
        TimeTableSubchannelGroupKey | None: サブチャンネル結合用キー (判定できない場合は None)
    """

    # TSID がある場合は放送波の単位をそのまま使う
    ## サブチャンネルは同一 TS 内の別サービスなので、EDCB や録画メタデータで TSID が取れている環境では
    ## NID+TSID がもっとも情報量の多い結合条件になる
    if channel_row['transport_stream_id'] is not None:
        return ('TS', int(channel_row['network_id']), int(channel_row['transport_stream_id']))

    # TSID がない BS は、既知のマルチ編成だけサービス ID から親サービスへ寄せる
    ## Mirakurun の /api/services は TSID を返さないため、(NID, None) を同じ TS と見なすと
    ## BS 全体のうち TSID が欠けている局が同じ列グループに入ってしまう
    if channel_row['type'] == 'BS':
        parent_service_id = TSInformation.calculateSubchannelParentServiceID('BS', int(channel_row['service_id']))
        if parent_service_id is not None:
            return ('BSService', int(channel_row['network_id']), parent_service_id)
        return ('BSService', int(channel_row['network_id']), int(channel_row['service_id']))

    # TSID もサービス ID からの親子判定もないチャンネルは、誤結合を避けるため単独扱いにする
    return None



@router.post(
    '/search',
    summary = '番組検索 API',
    response_description = '検索結果の番組情報のリスト。',
    response_model = schemas.Programs,
)
async def ProgramSearchAPI(
    program_search_condition: Annotated[schemas.ProgramSearchCondition, Body(description='番組検索条件。')],
):
    """
    番組情報を検索する。KonomiTV の番組 DB (mirakc から取得済み) を対象に検索する。
    """

    import re as _re
    from datetime import datetime

    now = datetime.now(JST)

    # 対象チャンネルの絞り込み
    if program_search_condition.service_ranges is not None:
        service_filter = {(r.network_id, r.service_id) for r in program_search_condition.service_ranges}
        channel_ids = [
            ch.id for ch in await Channel.filter(is_watchable=True).values_list('id', 'network_id', 'service_id')  # type: ignore[misc]
            if (ch[1], ch[2]) in service_filter
        ]
        programs_qs = Program.filter(channel_id__in=channel_ids, end_time__gt=now)
    else:
        programs_qs = Program.filter(channel_id__in=[
            ch.id for ch in await Channel.filter(is_watchable=True).only('id')
        ], end_time__gt=now)

    # 有料/無料絞り込み
    if program_search_condition.broadcast_type == 'FreeOnly':
        programs_qs = programs_qs.filter(is_free=True)
    elif program_search_condition.broadcast_type == 'PaidOnly':
        programs_qs = programs_qs.filter(is_free=False)

    # 番組長の絞り込み
    if program_search_condition.duration_range_min is not None:
        programs_qs = programs_qs.filter(duration__gte=program_search_condition.duration_range_min * 60.0)
    if program_search_condition.duration_range_max is not None:
        programs_qs = programs_qs.filter(duration__lte=program_search_condition.duration_range_max * 60.0)

    programs = await programs_qs.order_by('start_time').prefetch_related('channel')

    # キーワードフィルター (Python 側)
    keyword = program_search_condition.keyword.strip()
    exclude_keyword = program_search_condition.exclude_keyword.strip()
    results: list[schemas.Program] = []
    for program in programs:
        # キーワードマッチ
        if keyword:
            targets = [program.title] if program_search_condition.is_title_only else [program.title, program.description]
            if program_search_condition.is_regex_search_enabled:
                flags = 0 if program_search_condition.is_case_sensitive else _re.IGNORECASE
                matched = any(_re.search(keyword, t, flags) for t in targets)
            elif program_search_condition.is_case_sensitive:
                matched = any(keyword in t for t in targets)
            else:
                matched = any(keyword.lower() in t.lower() for t in targets)
            if not matched:
                continue

        # 除外キーワード
        if exclude_keyword:
            targets = [program.title] if program_search_condition.is_title_only else [program.title, program.description]
            if program_search_condition.is_case_sensitive:
                excluded = any(exclude_keyword in t for t in targets)
            else:
                excluded = any(exclude_keyword.lower() in t.lower() for t in targets)
            if excluded:
                continue

        # ジャンル絞り込み (major/middle でマッチ)
        if program_search_condition.genre_ranges is not None:
            genre_match = any(
                any(g['major'] == r['major'] and (not r.get('middle') or g['middle'] == r['middle'])
                    for r in program_search_condition.genre_ranges)
                for g in program.genres
            )
            if program_search_condition.is_exclude_genre_ranges:
                if genre_match:
                    continue
            else:
                if not genre_match:
                    continue

        # 放送日時範囲絞り込み
        if program_search_condition.date_ranges is not None:
            start_jst = program.start_time.astimezone(JST)
            day = start_jst.weekday()  # 0=月 → 6=日 (Python) → 変換: 月=1 … 日=0 (JS/ARIB)
            js_day = (day + 1) % 7
            h, m = start_jst.hour, start_jst.minute
            date_match = False
            for dr in program_search_condition.date_ranges:
                start_mins = dr.start_day_of_week * 1440 + dr.start_hour * 60 + dr.start_minute
                end_mins = dr.end_day_of_week * 1440 + dr.end_hour * 60 + dr.end_minute
                prog_mins = js_day * 1440 + h * 60 + m
                if start_mins <= end_mins:
                    if start_mins <= prog_mins <= end_mins:
                        date_match = True
                        break
                else:  # 週をまたぐ範囲
                    if prog_mins >= start_mins or prog_mins <= end_mins:
                        date_match = True
                        break
            if program_search_condition.is_exclude_date_ranges:
                if date_match:
                    continue
            else:
                if not date_match:
                    continue

        schema_program = schemas.Program(
            id = program.id,
            channel_id = program.channel_id,
            network_id = program.network_id,
            service_id = program.service_id,
            event_id = program.event_id,
            title = program.title,
            description = program.description,
            detail = program.detail,
            start_time = NormalizeToJSTDatetime(program.start_time),
            end_time = NormalizeToJSTDatetime(program.end_time),
            duration = program.duration,
            is_free = program.is_free,
            genres = program.genres,
            video_type = program.video_type,
            video_codec = program.video_codec,
            video_resolution = program.video_resolution,
            primary_audio_type = program.primary_audio_type,
            primary_audio_language = program.primary_audio_language,
            primary_audio_sampling_rate = program.primary_audio_sampling_rate,
            secondary_audio_type = program.secondary_audio_type,
            secondary_audio_language = program.secondary_audio_language,
            secondary_audio_sampling_rate = program.secondary_audio_sampling_rate,
        )
        results.append(schema_program)

    return schemas.Programs(total=len(results), programs=results)

@router.get(
    '/timetable',
    summary = '番組表 API',
    response_description = '番組表データ。チャンネルごとの番組リストと日付範囲を含む。',
    response_model = schemas.TimeTable,
)
async def TimeTableAPI(
    start_time: Annotated[datetime | None, Query(description='取得開始日時 (ISO8601 形式)。省略時は現在時刻。')] = None,
    end_time: Annotated[datetime | None, Query(description='取得終了日時 (ISO8601 形式)。省略時は DB に存在する最終日時。')] = None,
    channel_type: Annotated[Literal['GR', 'BS', 'CS', 'CATV', 'SKY', 'BS4K'] | None, Query(description='チャンネル種別。省略時は全種別。')] = None,
    pinned_channel_ids: Annotated[str | None, Query(description='チャンネル ID のカンマ区切りリスト (ピン留めチャンネル用)。指定時は channel_type より優先される。')] = None,
):
    """
    番組表データを取得する。<br>
    チャンネルごとの番組リストと、番組データの有効日付範囲を含む。<br>
    EDCB バックエンド時は各番組の予約情報も含む。
    """

    # 現在時刻
    now = datetime.now(JST)

    # 開始時刻のデフォルト値: 現在時刻
    if start_time is None:
        start_time = now

    # タイムゾーンが指定されていない場合は JST として扱う
    start_time = NormalizeToJSTDatetime(start_time)

    # データベースの生のコネクションを取得
    connection = connections.get('default')

    # 番組データの日付範囲を取得 (日付セレクター用)
    date_range_result = await connection.execute_query_dict(
        'SELECT MIN(start_time) AS earliest, MAX(end_time) AS latest FROM programs'
    )
    earliest_str = date_range_result[0]['earliest'] if date_range_result else None
    latest_str = date_range_result[0]['latest'] if date_range_result else None

    # 日付文字列を datetime に変換 (SQLite は文字列で保存されている)
    if earliest_str:
        earliest = ParseDatetimeStringToJST(earliest_str)
    else:
        earliest = now

    if latest_str:
        latest = ParseDatetimeStringToJST(latest_str)
    else:
        latest = now + timedelta(days=7)

    # 終了時刻のデフォルト値: DB に存在する最終日時
    if end_time is None:
        end_time = latest

    # タイムゾーンが指定されていない場合は JST として扱う
    end_time = NormalizeToJSTDatetime(end_time)

    # チャンネル ID リストをパース
    target_channel_ids: list[str] | None = None
    if pinned_channel_ids is not None and pinned_channel_ids.strip() != '':
        target_channel_ids = [cid.strip() for cid in pinned_channel_ids.split(',') if cid.strip()]

    # チャンネル情報を raw SQL で取得 (Tortoise ORM のオーバーヘッドを回避)
    if target_channel_ids is not None:
        # 指定されたチャンネル ID のチャンネルのみ取得
        placeholders = ','.join(['?' for _ in target_channel_ids])
        channels_query = f"""
            SELECT
                id, display_channel_id, network_id, service_id, transport_stream_id,
                remocon_id, channel_number, type, name, jikkyo_force,
                is_subchannel, is_radiochannel, is_watchable
            FROM channels
            WHERE id IN ({placeholders}) AND is_watchable = 1
        """
        channels_result = await connection.execute_query_dict(channels_query, target_channel_ids)
        # 指定された順序でソート
        channel_id_order = {cid: idx for idx, cid in enumerate(target_channel_ids)}
        channels_result.sort(key=lambda c: channel_id_order.get(c['id'], float('inf')))  # type: ignore[arg-type]
    elif channel_type is not None:
        # 指定されたチャンネル種別のチャンネルのみ取得
        channels_query = """
            SELECT
                id, display_channel_id, network_id, service_id, transport_stream_id,
                remocon_id, channel_number, type, name, jikkyo_force,
                is_subchannel, is_radiochannel, is_watchable
            FROM channels
            WHERE type = ? AND is_watchable = 1
            ORDER BY channel_number, remocon_id
        """
        channels_result = await connection.execute_query_dict(channels_query, [channel_type])
    else:
        # 全チャンネル取得
        channels_query = """
            SELECT
                id, display_channel_id, network_id, service_id, transport_stream_id,
                remocon_id, channel_number, type, name, jikkyo_force,
                is_subchannel, is_radiochannel, is_watchable
            FROM channels
            WHERE is_watchable = 1
            ORDER BY channel_number, remocon_id
        """
        channels_result = await connection.execute_query_dict(channels_query)

    # チャンネルがない場合は空のレスポンスを返す
    if not channels_result:
        return schemas.TimeTable(
            channels=[],
            date_range=schemas.TimeTableDateRange(earliest=earliest, latest=latest),
        )

    # チャンネル行データの真偽値を変換 (SQLite では 0/1)
    for channel_row in channels_result:
        channel_row['is_subchannel'] = bool(channel_row['is_subchannel'])
        channel_row['is_radiochannel'] = bool(channel_row['is_radiochannel'])
        channel_row['is_watchable'] = bool(channel_row['is_watchable'])

    # ピン留め指定がない通常番組表では、枝番つき地デジ局のサブチャンネルが同じ局の近くに並ぶ順序に直す
    ## pinned_channel_ids 指定時はユーザーが設定した順番そのものが表示順なので、番組表側の自動ソートは挟まない
    if target_channel_ids is None:
        channels_result.sort(key=GetTimeTableChannelSortKey)

    # サブチャンネル結合用キーごとにチャンネルをグループ化
    ## TSID がないチャンネルは同じ TS と断定できないため、GetTimeTableSubchannelGroupKey() で
    ## Mirakurun の BS だけ既知の SID 親子関係へ寄せ、それ以外は単独扱いにする
    grouped_channels: dict[TimeTableSubchannelGroupKey, list[dict[str, Any]]] = {}
    for channel_row in channels_result:
        group_key = GetTimeTableSubchannelGroupKey(channel_row)
        if group_key is None:
            continue
        if group_key not in grouped_channels:
            grouped_channels[group_key] = []
        grouped_channels[group_key].append(channel_row)

    # サブチャンネル放送時間の集計を1つのクエリで取得
    # チャンネル ID ごとに一括取得し、Python 側でサブチャンネル結合用キーへ積み直す
    ## SQL 側で transport_stream_id ごとに GROUP BY すると、NULL TSID 同士が同じグループに入り、
    ## Mirakurun バックエンドで BS の別トランスポンダを同一 TS と誤判定してしまう
    subchannel_durations_query = """
        SELECT
            c.id,
            c.type,
            c.network_id,
            p.service_id,
            c.transport_stream_id,
            DATE(p.start_time, '-4 hours') AS broadcast_date,
            SUM(p.duration) AS total_duration
        FROM programs p
        INNER JOIN channels c ON p.channel_id = c.id
        WHERE c.is_subchannel = 1
        GROUP BY c.id, c.type, c.network_id, p.service_id, c.transport_stream_id, broadcast_date
    """
    subchannel_durations_result = await connection.execute_query_dict(subchannel_durations_query)

    # サブチャンネル放送時間を結合用キー -> サービス ID -> 日付 -> 放送時間 の形式に整理
    subchannel_durations_by_group: dict[TimeTableSubchannelGroupKey, dict[int, dict[str, float]]] = {}
    for row in subchannel_durations_result:
        group_key = GetTimeTableSubchannelGroupKey(row)
        if group_key is None:
            continue
        service_id = row['service_id']
        broadcast_date = row['broadcast_date']
        total_duration = row['total_duration'] or 0

        if group_key not in subchannel_durations_by_group:
            subchannel_durations_by_group[group_key] = {}
        if service_id not in subchannel_durations_by_group[group_key]:
            subchannel_durations_by_group[group_key][service_id] = {}
        subchannel_durations_by_group[group_key][service_id][broadcast_date] = total_duration

    # 8時間ルールに基づいて独立サブチャンネルを判定
    # 閾値: 8時間 = 28800秒
    INDEPENDENT_SUBCHANNEL_THRESHOLD = 8 * 3600
    independent_subchannels_by_group: dict[TimeTableSubchannelGroupKey, set[int]] = {}
    for group_key, durations_by_service in subchannel_durations_by_group.items():
        independent_subchannels: set[int] = set()
        for service_id, daily_durations in durations_by_service.items():
            # いずれかの日で閾値以上の放送時間があれば独立チャンネルとして判定
            for duration in daily_durations.values():
                if duration >= INDEPENDENT_SUBCHANNEL_THRESHOLD:
                    independent_subchannels.add(service_id)
                    break
        independent_subchannels_by_group[group_key] = independent_subchannels

    # 番組情報を取得するためのチャンネル ID リストを構築
    channel_ids_for_query = [c['id'] for c in channels_result]

    # 番組情報を取得
    programs_placeholders = ','.join(['?' for _ in channel_ids_for_query])
    programs_query = f"""
        SELECT *
        FROM programs
        WHERE
            channel_id IN ({programs_placeholders})
            AND (
                -- 指定期間内に開始する番組
                (start_time >= ? AND start_time < ?)
                OR
                -- 指定期間内に終了する番組 (開始は期間前でも可)
                (end_time > ? AND end_time <= ?)
                OR
                -- 期間をまたぐ番組 (開始は期間前、終了は期間後)
                (start_time < ? AND end_time > ?)
            )
        ORDER BY channel_id, start_time
    """

    programs_params: list[Any] = [
        *channel_ids_for_query,
        start_time, end_time,  # 期間内に開始
        start_time, end_time,  # 期間内に終了
        start_time, end_time,  # 期間をまたぐ
    ]

    programs_result = await connection.execute_query_dict(programs_query, programs_params)

    # 予約情報 (将来 mirakc schedules から取得予定)
    reservations_by_program_id: dict[str, dict[str, Any]] = {}
    reservations_by_channel_time: dict[str, list[dict[str, Any]]] = {}

    # チャンネルごとに番組をグループ化
    programs_by_channel: dict[str, list[dict[str, Any]]] = {c['id']: [] for c in channels_result}
    for program_row in programs_result:
        channel_id = program_row['channel_id']
        if channel_id in programs_by_channel:
            # JSON フィールドをデコード
            program_row['detail'] = json.loads(program_row['detail']) if program_row['detail'] else {}
            program_row['genres'] = json.loads(program_row['genres']) if program_row['genres'] else []

            # SQLite から取得した番組開始・終了時刻を JST aware datetime に正規化する
            ## DB には基本的に UTC+9 を保存しているが、将来のデータ混在に備えてタイムゾーンなしでも JST を補う
            program_start_time = ParseDatetimeStringToJST(program_row['start_time'])
            program_end_time = ParseDatetimeStringToJST(program_row['end_time'])
            program_row['start_time'] = program_start_time.isoformat()
            program_row['end_time'] = program_end_time.isoformat()

            # 真偽値を変換 (SQLite では 0/1)
            program_row['is_free'] = bool(program_row['is_free'])

            # 予約情報を追加
            program_id = program_row['id']
            if program_id in reservations_by_program_id:
                program_row['reservation'] = reservations_by_program_id[program_id]
            else:
                # 予約の EID が一致しない場合でも、同一チャンネルかつ同一時間帯で重なる予約があれば暫定的に紐付ける
                ## スポーツ中継の延長などで EIT[p/f] と EIT[schedule] が一時的にずれるケースを救済する
                fallback_reservation = None
                fallback_reservations = reservations_by_channel_time.get(channel_id, [])
                if fallback_reservations:
                    best_overlap_seconds = 0
                    best_overlap_index = -1
                    for index, fallback in enumerate(fallback_reservations):
                        overlap_start_time = max(program_start_time, fallback['start_time'])
                        overlap_end_time = min(program_end_time, fallback['end_time'])
                        overlap_seconds = (overlap_end_time - overlap_start_time).total_seconds()
                        if overlap_seconds > best_overlap_seconds:
                            best_overlap_seconds = overlap_seconds
                            best_overlap_index = index

                    if best_overlap_index >= 0:
                        # pop() で採用済み要素をリストから除外し、以降の番組に同一予約が二重割当されることを防ぐ
                        fallback_reservation = fallback_reservations.pop(best_overlap_index)['reservation']

                program_row['reservation'] = fallback_reservation

            programs_by_channel[channel_id].append(program_row)

    # レスポンスを構築
    result_channels: list[dict[str, Any]] = []

    for channel_row in channels_result:
        group_key = GetTimeTableSubchannelGroupKey(channel_row)
        independent_subchannels = independent_subchannels_by_group.get(group_key, set()) if group_key is not None else set()

        # このチャンネルがサブチャンネルかつ独立サブチャンネルでない場合はスキップ
        # (メインチャンネルの subchannels に含める)
        if channel_row['is_subchannel'] and channel_row['service_id'] not in independent_subchannels:
            continue

        # 番組リスト
        programs_list = programs_by_channel.get(channel_row['id'], [])

        # サブチャンネルのリストを収集 (8時間未満のサブチャンネルのみ)
        subchannels: list[dict[str, Any]] | None = None
        if not channel_row['is_subchannel'] and group_key is not None:
            # メインチャンネルの場合、同じ結合用キーに属するサブチャンネル番組を収集
            grouped_channel_list = grouped_channels.get(group_key, [])
            for sub_channel_row in grouped_channel_list:
                # サブチャンネルかつ独立サブチャンネルでない場合のみ
                if sub_channel_row['is_subchannel'] and sub_channel_row['service_id'] not in independent_subchannels:
                    # サブチャンネルの SID がメインチャンネルの SID より小さい場合はスキップ
                    # サブチャンネルは必ずメインチャンネルより大きい SID を持つため、
                    # SID が小さい場合はこのメインチャンネルのサブチャンネルではない
                    # (例: 放送大学ラジオ 531 のサブチャンネルとして放送大学テレビ SD 232 が紐づかないようにする)
                    if sub_channel_row['service_id'] < channel_row['service_id']:
                        continue
                    sub_programs = programs_by_channel.get(sub_channel_row['id'], [])
                    if sub_programs:
                        if subchannels is None:
                            subchannels = []
                        # チャンネル情報と番組リストを含める
                        subchannels.append({
                            'channel': sub_channel_row,
                            'programs': sub_programs,
                        })

        result_channels.append({
            'channel': channel_row,
            'programs': programs_list,
            'subchannels': subchannels,
        })

    # Pydantic v2 は Rust バックエンドにより高速化されているため、モデルを直接返す
    # result_channels のみを TypeAdapter で一括バリデートし、date_range は直接構築する
    channels_adapter = TypeAdapter(list[schemas.TimeTableChannel])
    validated_channels = channels_adapter.validate_python(result_channels)
    return schemas.TimeTable(
        channels=validated_channels,
        date_range=schemas.TimeTableDateRange(earliest=earliest, latest=latest),
    )
