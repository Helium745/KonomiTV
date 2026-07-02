
from collections.abc import Iterator
from typing import BinaryIO

import httpx


# mirakc タイムシフト録画をエンコード入力として扱うことを示す、RecordedVideo.file_path 用の擬似スキーム
## 本来はローカルファイルパスが入るフィールドだが、タイムシフト録画は mirakc 側のリングバッファにしか実体がないため、
## この擬似 URI を "ファイルパス" として扱うことで、VideoEncodingTask 側の分岐を最小限に抑えている
TIMESHIFT_FILE_PATH_SCHEME = 'mirakc-timeshift://'


class HttpRangeFile:
    """
    mirakc の Range リクエスト対応エンドポイントを、通常のバイナリファイルであるかのように読み書きできるようにするラッパー
    VideoEncodingTask は録画ファイルを open() / seek() / read() / tell() / close() で扱うため、
    このクラスも同じインターフェイスを提供することで、入力元がローカルファイルか mirakc の HTTP ストリームかを意識せずに済む

    seek() のたびに新しい Range リクエストを張り直す単純な実装のため、細かいシークを繰り返す用途には向かないが、
    VideoEncodingTask 側のシーク頻度 (エンコード開始位置の解決時、PAT/PMT 抽出時) では許容できるオーバーヘッドに収まる
    """

    # 1回の HTTP レスポンス読み取りで受け取るチャンクサイズ
    CHUNK_SIZE = 256 * 1024  # 256KB


    def __init__(self, url: str) -> None:
        """
        Args:
            url (str): mirakc の Range リクエスト対応ストリーミングエンドポイント URL
        """

        self._url = url
        # 同期 I/O 前提 (VideoEncodingTask 側ではワーカースレッド上で呼び出される) のため、同期版の httpx.Client を使う
        self._client = httpx.Client(timeout=httpx.Timeout(10.0, read=60.0))
        self._position = 0
        self._response: httpx.Response | None = None
        self._iterator: Iterator[bytes] | None = None
        self._buffer = b''
        self._closed = False


    def _openStream(self, start_position: int) -> None:
        """ 指定バイト位置から始まる新しい Range リクエストを送り、既存の接続があれば閉じてから差し替える """

        self._closeStream()
        response = self._client.send(
            self._client.build_request('GET', self._url, headers={'Range': f'bytes={start_position}-'}),
            stream = True,
        )
        response.raise_for_status()
        self._response = response
        self._iterator = response.iter_bytes(chunk_size=self.CHUNK_SIZE)
        self._buffer = b''
        self._position = start_position


    def _closeStream(self) -> None:
        """ 現在張っている Range リクエストの接続を閉じる (次の read()/seek() で必要になれば新しく張り直される) """

        if self._response is not None:
            self._response.close()
            self._response = None
            self._iterator = None


    def seek(self, position: int, whence: int = 0) -> int:
        """
        指定バイト位置にシークする (絶対位置のみサポート)

        Args:
            position (int): シーク先のバイト位置
            whence (int): os.SEEK_SET (0) のみサポート。VideoEncodingTask 側でも絶対位置指定でしか呼ばれない

        Returns:
            int: シーク後のバイト位置
        """

        assert whence == 0, 'HttpRangeFile only supports absolute seek (whence=0).'

        # 既に同じ位置にストリームが開かれている場合は何もしない
        if self._response is not None and position == self._position:
            return position

        self._openStream(position)
        return position


    def tell(self) -> int:
        """ 現在のバイト位置を返す """
        return self._position


    def read(self, size: int = -1) -> bytes:
        """
        現在位置からバイト列を読み取る

        Args:
            size (int): 読み取るバイト数。負数の場合はストリームの終端まで読み取る

        Returns:
            bytes: 読み取ったバイト列 (要求サイズに満たない場合は終端に達したことを示す)
        """

        if self._response is None:
            self._openStream(self._position)
        assert self._iterator is not None

        chunks: list[bytes] = []
        remaining = size if size >= 0 else None
        read_bytes = 0

        while remaining is None or read_bytes < remaining:
            if self._buffer:
                take_size = len(self._buffer) if remaining is None else min(len(self._buffer), remaining - read_bytes)
                chunks.append(self._buffer[:take_size])
                self._buffer = self._buffer[take_size:]
                read_bytes += take_size
                continue

            try:
                self._buffer = next(self._iterator)
            except StopIteration:
                break

        result = b''.join(chunks)
        self._position += len(result)
        return result


    def close(self) -> None:
        """ 接続を閉じる """

        if self._closed is True:
            return
        self._closed = True
        self._closeStream()
        self._client.close()


    def __enter__(self) -> 'HttpRangeFile':
        return self


    def __exit__(self, *_: object) -> None:
        self.close()


def OpenRecordedFile(file_path: str) -> BinaryIO:
    """
    録画ファイルの file_path を開く
    file_path が TIMESHIFT_FILE_PATH_SCHEME で始まる場合は mirakc のタイムシフト録画 record への HttpRangeFile を、
    それ以外の場合は通常のローカルファイルを開いて返す

    Args:
        file_path (str): RecordedVideo.file_path (ローカルパス、または mirakc タイムシフト録画の擬似 URI)

    Returns:
        BinaryIO: 読み取り用のファイルオブジェクト (HttpRangeFile もこのインターフェイスを満たす)
    """

    if file_path.startswith(TIMESHIFT_FILE_PATH_SCHEME):
        # ローカルインポート (循環インポート回避)
        from app.utils.mirakc import MirakcClient
        recorder_id, record_id_str = file_path.removeprefix(TIMESHIFT_FILE_PATH_SCHEME).split('/', 1)
        url = MirakcClient().get_timeshift_record_stream_url(recorder_id, int(record_id_str))
        return HttpRangeFile(url)  # type: ignore[return-value]

    return open(file_path, 'rb')
