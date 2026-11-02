"""控えめなHTTPアクセス（同じサイトには間隔をあけ、失敗したら少し待って再試行）。"""
from __future__ import annotations

import time
from urllib.parse import urlparse

import requests

from .config import USER_AGENT

MIN_INTERVAL_SEC = 3.0
_last_access: dict[str, float] = {}
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ja"})


def get(url: str, *, params: dict | None = None, timeout: float = 60, retries: int = 2) -> requests.Response:
    host = urlparse(url).netloc
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        wait = MIN_INTERVAL_SEC - (time.monotonic() - _last_access.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        try:
            r = _session.get(url, params=params, timeout=timeout)
            _last_access[host] = time.monotonic()
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            _last_access[host] = time.monotonic()
            last_err = e
            if attempt < retries:
                time.sleep(5 * (attempt + 1))
    assert last_err is not None
    raise last_err


def short_err(e: Exception) -> str:
    """エラーを短い日本語にする。"""
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"サーバーがエラーを返しました（HTTP {e.response.status_code}）"
    if isinstance(e, requests.Timeout):
        return "時間切れ（応答なし）"
    if isinstance(e, requests.ConnectionError):
        return "接続できませんでした"
    return f"{type(e).__name__}: {str(e)[:80]}"
