
# Type Hints を指定できるように
# ref: https://stackoverflow.com/a/33533514/17124142
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from app import logging
from app.config import Config
from app.constants import QUALITY_TYPES, VIDEO_ENCODE_CACHE_DIR
from app.streams.StreamEncodingOptions import StreamEncodingOptions


# 書き込み中に異常終了した一時ファイルを安全に削除するまでの猶予時間 (秒)
## 進行中の書き込みを誤って削除しないよう、この時間より新しい .tmp-* ファイルはクリーンアップ対象から除外する
TEMP_FILE_STALE_THRESHOLD_SECONDS: float = 10 * 60  # 10分


def BuildCacheSegmentDirectory(
    recorded_video_id: int,
    file_hash: str,
    quality: QUALITY_TYPES,
    encoding_options: StreamEncodingOptions,
) -> Path:
    """
    録画番組・画質・エンコードオプションの組み合わせに対応するキャッシュディレクトリのパスを組み立てる
    file_hash を階層に含めることで、録画ファイルが差し替えられた場合は自動的に別ディレクトリになり、
    古い方は CleanupExpiredCacheFiles() の TTL 経過による自動削除に任せられる

    Args:
        recorded_video_id (int): 録画ファイルの RecordedVideo.id
        file_hash (str): 録画ファイルの RecordedVideo.file_hash
        quality (QUALITY_TYPES): 映像の品質
        encoding_options (StreamEncodingOptions): ベース画質に追加するエンコードオプション

    Returns:
        Path: キャッシュディレクトリのパス
    """

    return VIDEO_ENCODE_CACHE_DIR / str(recorded_video_id) / file_hash / f'{quality}{encoding_options.buildSuffix()}'


def BuildCacheSegmentPath(
    recorded_video_id: int,
    file_hash: str,
    quality: QUALITY_TYPES,
    encoding_options: StreamEncodingOptions,
    segment_sequence: int,
) -> Path:
    """
    HLS セグメント単位のキャッシュファイルパスを決定的に組み立てる

    Args:
        recorded_video_id (int): 録画ファイルの RecordedVideo.id
        file_hash (str): 録画ファイルの RecordedVideo.file_hash
        quality (QUALITY_TYPES): 映像の品質
        encoding_options (StreamEncodingOptions): ベース画質に追加するエンコードオプション
        segment_sequence (int): HLS セグメントのシーケンス番号

    Returns:
        Path: キャッシュファイルのパス
    """

    directory = BuildCacheSegmentDirectory(recorded_video_id, file_hash, quality, encoding_options)
    return directory / f'{segment_sequence:08d}.ts'


async def ReadCachedSegment(
    recorded_video_id: int,
    file_hash: str,
    quality: QUALITY_TYPES,
    encoding_options: StreamEncodingOptions,
    segment_sequence: int,
) -> bytes | None:
    """
    ディスクキャッシュからエンコード済み HLS セグメントを読み込む
    キャッシュが存在しない場合や読み込みに失敗した場合は、再エンコードにフォールバックできるよう None を返す

    Args:
        recorded_video_id (int): 録画ファイルの RecordedVideo.id
        file_hash (str): 録画ファイルの RecordedVideo.file_hash
        quality (QUALITY_TYPES): 映像の品質
        encoding_options (StreamEncodingOptions): ベース画質に追加するエンコードオプション
        segment_sequence (int): HLS セグメントのシーケンス番号

    Returns:
        bytes | None: エンコード済み HLS セグメントの MPEG-TS データ (キャッシュミス/読み込み失敗時は None)
    """

    cache_path = BuildCacheSegmentPath(recorded_video_id, file_hash, quality, encoding_options, segment_sequence)

    def Read() -> bytes | None:
        try:
            return cache_path.read_bytes()
        except FileNotFoundError:
            # 単純なキャッシュミス (未エンコード or 既に TTL で削除済み) なのでログは出さない
            return None
        except OSError as ex:
            logging.warning(f'Failed to read video encode cache: {cache_path}', exc_info=ex)
            return None

    return await asyncio.to_thread(Read)


async def WriteCachedSegment(
    recorded_video_id: int,
    file_hash: str,
    quality: QUALITY_TYPES,
    encoding_options: StreamEncodingOptions,
    segment_sequence: int,
    data: bytes,
) -> None:
    """
    エンコード済み HLS セグメントをディスクキャッシュへ書き込む
    一時ファイルへ書き込んでから Path.replace() でアトミックに確定することで、
    他の視聴セッションが読み込み中のキャッシュファイルを不完全な状態で見せてしまう事態を防ぐ
    書き込みに失敗しても再生自体は継続できるべきなので、例外は投げずログのみ残す

    Args:
        recorded_video_id (int): 録画ファイルの RecordedVideo.id
        file_hash (str): 録画ファイルの RecordedVideo.file_hash
        quality (QUALITY_TYPES): 映像の品質
        encoding_options (StreamEncodingOptions): ベース画質に追加するエンコードオプション
        segment_sequence (int): HLS セグメントのシーケンス番号
        data (bytes): エンコード済み HLS セグメントの MPEG-TS データ
    """

    cache_path = BuildCacheSegmentPath(recorded_video_id, file_hash, quality, encoding_options, segment_sequence)
    # 同じセグメントを複数の視聴セッションがほぼ同時にエンコードした場合でも一時ファイル名が衝突しないよう、
    ## uuid4 を含めておく (Path.replace() での確定自体は後勝ちで上書きされるだけで実害はない)
    tmp_path = cache_path.with_name(f'{cache_path.name}.tmp-{uuid.uuid4().hex}')

    def Write() -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_bytes(data)
        tmp_path.replace(cache_path)

    try:
        await asyncio.to_thread(Write)
    except OSError as ex:
        logging.warning(f'Failed to write video encode cache: {cache_path}', exc_info=ex)
        # 書き込み失敗時に一時ファイルが残っていれば削除を試みる (次回のクリーンアップ処理に任せず即座に片付ける)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def CleanupExpiredCacheFilesSync(retention_hours: int) -> tuple[int, int]:
    """
    保持期限を過ぎたキャッシュファイルと、書き込み中に異常終了した一時ファイルの残骸を削除する
    削除後、空になったディレクトリもボトムアップで掃除する
    同期処理のため、呼び出し側で asyncio.to_thread() 経由で実行すること

    Args:
        retention_hours (int): キャッシュファイルの保持期限 (時間)

    Returns:
        tuple[int, int]: (削除したファイル数, 削除したディレクトリ数)
    """

    if not VIDEO_ENCODE_CACHE_DIR.exists():
        return (0, 0)

    now = time.time()
    retention_seconds = retention_hours * 60 * 60
    deleted_file_count = 0
    deleted_directory_count = 0

    # キャッシュファイル本体 (*.ts) のうち、保持期限を過ぎたものを削除する
    for cache_file in VIDEO_ENCODE_CACHE_DIR.rglob('*.ts'):
        try:
            if (now - cache_file.stat().st_mtime) > retention_seconds:
                cache_file.unlink()
                deleted_file_count += 1
        except OSError as ex:
            logging.warning(f'Failed to delete expired video encode cache: {cache_file}', exc_info=ex)

    # 書き込み中に異常終了した一時ファイルの残骸を削除する
    ## 進行中の書き込みを誤って削除しないよう、一定時間以上経過したものだけを対象にする
    for tmp_file in VIDEO_ENCODE_CACHE_DIR.rglob('*.tmp-*'):
        try:
            if (now - tmp_file.stat().st_mtime) > TEMP_FILE_STALE_THRESHOLD_SECONDS:
                tmp_file.unlink()
                deleted_file_count += 1
        except OSError as ex:
            logging.warning(f'Failed to delete stale video encode cache temp file: {tmp_file}', exc_info=ex)

    # 空になったディレクトリを、深い階層から順にボトムアップで削除する
    ## quality ディレクトリ → file_hash ディレクトリ → recorded_video_id ディレクトリの順で空なら消える
    all_directories = sorted(
        (directory for directory in VIDEO_ENCODE_CACHE_DIR.rglob('*') if directory.is_dir()),
        key = lambda directory: len(directory.parts),
        reverse = True,
    )
    for directory in all_directories:
        try:
            directory.rmdir()
            deleted_directory_count += 1
        except OSError:
            # 空でない (まだ有効なキャッシュが残っている) ディレクトリなので何もしない
            pass

    return (deleted_file_count, deleted_directory_count)


async def CleanupExpiredCacheFiles() -> None:
    """
    サーバー設定の保持期限に基づき、期限切れの録画視聴エンコードキャッシュを削除する
    起動時と定期実行タスクの両方から呼び出される
    """

    retention_hours = Config().video.encode_cache_retention_hours
    deleted_file_count, deleted_directory_count = await asyncio.to_thread(
        CleanupExpiredCacheFilesSync, retention_hours,
    )
    if deleted_file_count > 0 or deleted_directory_count > 0:
        logging.info(
            f'Video encode cache cleanup completed. '
            f'[deleted_files: {deleted_file_count}, deleted_directories: {deleted_directory_count}]'
        )
