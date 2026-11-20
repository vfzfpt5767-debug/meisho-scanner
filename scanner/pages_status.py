"""GitHub Pages の公開（ページだけ）の失敗を数えて、続けて失敗したときだけLINEで1回知らせる。

scan.yml の「Pages の準備・アップロード・公開」が失敗しても（公開は60秒待って1回やり直す）、
データの取得・保存・LINEの通知は正常で、次の実行で直ることが多い（2026-11-20 6:46 の #231 は GitHub 側の一時的な不具合）。
そのため、1回目の失敗ではLINEを送らない。次の実行でも続けて失敗したときに1回だけ送る（直ったら数え直す）。

使い方（Actions から）：
    python -m scanner.pages_status ok
    python -m scanner.pages_status fail --run-url <実行のURL>
記録は data/pages_state.json。変わったときだけ書き換える（コミットは scan.yml が行う）。
Actions の失敗時にも動くよう、Python標準ライブラリだけで書いている。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from .config import DATA_DIR

JST = timezone(timedelta(hours=9))
STATE = DATA_DIR / "pages_state.json"
NOTIFY_AFTER = 2  # 続けて何回失敗したらLINEで知らせるか


def message(streak: int, run_url: str) -> str:
    return (f"【名証スキャナー】ページの公開が続けて失敗しています（{streak}回）。"
            "データの取得とLINEの通知は正常です（ページは前回の内容のまま）。"
            "GitHub 側の一時的な不具合のことが多く、急いで何かする必要はありません。"
            f"\n{run_url}")


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def record(ok: bool, run_url: str = "", now: datetime | None = None, send=None) -> tuple[bool, str]:
    """今回の公開の結果を記録する。(書き換えたか, 説明) を返す。"""
    now = now or datetime.now(JST)
    st = _load()
    streak = int(st.get("fail_streak", 0))
    if ok:
        if streak == 0:
            return False, "ページの公開：成功"
        _save({"fail_streak": 0, "notified": False, "last_ok_at": now.isoformat(timespec="minutes"),
               "last_fail_at": st.get("last_fail_at", ""), "last_fail_run": st.get("last_fail_run", "")})
        return True, f"ページの公開：成功（続けて{streak}回失敗していたのが直った）"
    streak += 1
    st.update(fail_streak=streak, last_fail_at=now.isoformat(timespec="minutes"), last_fail_run=run_url)
    note = f"ページの公開：失敗（続けて{streak}回目）"
    if streak >= NOTIFY_AFTER and not st.get("notified"):
        if send is None:
            from .notify import send
        r = send(message(streak, run_url), "pages_failure")
        note += "。LINE：" + r
        if r.startswith("LINEに送信しました"):
            st["notified"] = True
    elif streak < NOTIFY_AFTER:
        note += "。1回目なのでLINEは送らない（次の実行で直ることが多い）"
    else:
        note += "。LINEは送信済みなので送らない"
    st.setdefault("notified", False)
    _save(st)
    return True, note


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("result", choices=["ok", "fail"])
    p.add_argument("--run-url", default="")
    a = p.parse_args()
    _, note = record(a.result == "ok", a.run_url)
    print(note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
