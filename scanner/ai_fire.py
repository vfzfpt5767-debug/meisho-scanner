"""AIアナリスト（Claude Code のスケジュールされたタスク）を API で起動する。

GitHub Secrets：ANALYST_FIRE_URL（https://api.anthropic.com/v1/claude_code/routines/trig_.../fire）、
ANALYST_FIRE_TOKEN（タスクのAPIトリガーで発行したトークン）。
Python標準ライブラリだけで書いている。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .ai_input import FIRE_REQ, FIRE_STATE


def fire(text: str) -> tuple[bool, str]:
    url = os.environ.get("ANALYST_FIRE_URL", "").strip()
    tok = os.environ.get("ANALYST_FIRE_TOKEN", "").strip()
    if not url or not tok:
        return False, "AIアナリスト未設定（Secrets に ANALYST_FIRE_URL と ANALYST_FIRE_TOKEN がない）"
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {tok}", "anthropic-version": "2023-06-01",
        "anthropic-beta": "experimental-cc-routine-2026-04-01", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.load(r)
            return True, f"起動しました：{j.get('claude_code_session_url', '')}"
    except urllib.error.HTTPError as e:
        return False, f"起動に失敗（HTTP {e.code}）"
    except Exception as e:  # noqa: BLE001
        return False, f"起動に失敗（{type(e).__name__}）"


def main() -> int:
    if not FIRE_REQ.exists():
        print("起動の依頼なし")
        return 0
    req = json.loads(FIRE_REQ.read_text(encoding="utf-8"))
    if not req.get("fire"):
        print("起動しない：", req.get("reason", ""))
        return 0
    ok, msg = fire(req.get("text", ""))
    print(msg)
    if ok:
        st = json.loads(FIRE_STATE.read_text(encoding="utf-8")) if FIRE_STATE.exists() else {}
        night = st.setdefault(req["base_day"], {"fires": 0, "keys": []})
        night["fires"] += 1
        night["keys"] = sorted(set(night["keys"]) | set(req.get("new_keys", [])))
        st = dict(sorted(st.items())[-14:])
        FIRE_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        req["fire"] = False
        req["reason"] = "起動済み"
        req.pop("text", None)
        FIRE_REQ.write_text(json.dumps(req, ensure_ascii=False, indent=1), encoding="utf-8")
    elif "未設定" not in msg:
        try:
            from .notify import send

            send(f"【名証スキャナー】AIアナリストの{msg}", "failure")
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
