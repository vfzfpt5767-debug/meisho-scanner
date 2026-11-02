"""LINE通知（LINE Messaging API のプッシュメッセージ）。

- チャネルアクセストークンとユーザーIDは GitHub Secrets（環境変数）から読む。コードには書かない。
    LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID
- 送るのは「夜：S・A・要確認・保有警告があるときだけ、1タームに1回」「朝：朝レポートの要約1回」「Actions失敗時」。
- 月の送信数を数え（LINEのAPIで今月の送信数も確認）、180通に達したら送らない。
- Actions の失敗時にも動くよう、Python標準ライブラリだけで書いている。
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from .config import DATA_DIR, GITHUB_REPOSITORY

JST = timezone(timedelta(hours=9))
STATE = DATA_DIR / "notify_state.json"
MONTHLY_LIMIT = 180  # 無料プランは月200通。余裕を持って180通で止める
PUSH_URL = "https://api.line.me/v2/bot/message/push"
QUOTA_URL = "https://api.line.me/v2/bot/message/quota/consumption"


def page_url() -> str:
    owner, _, repo = GITHUB_REPOSITORY.partition("/")
    return f"https://{owner}.github.io/{repo}/"


def _creds() -> tuple[str, str] | None:
    tok, uid = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip(), os.environ.get("LINE_USER_ID", "").strip()
    return (tok, uid) if tok and uid else None


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(st: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _api_usage(tok: str) -> int | None:
    req = urllib.request.Request(QUOTA_URL, headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return int(json.load(r).get("totalUsage", 0))
    except Exception:  # noqa: BLE001
        return None


def send(text: str, kind: str) -> str:
    """1通送る。結果の説明を返す（送らなかった理由も含む）。"""
    creds = _creds()
    if not creds:
        return "LINE未設定（Secrets に LINE_CHANNEL_ACCESS_TOKEN と LINE_USER_ID がない）ので送りません"
    tok, uid = creds
    now = datetime.now(JST)
    st = _load()
    month = f"{now:%Y-%m}"
    if st.get("month") != month:
        st = {**st, "month": month, "count": 0}
    used = max(int(st.get("count", 0)), _api_usage(tok) or 0)
    if used >= MONTHLY_LIMIT:
        return f"今月の送信数が{used}通（上限{MONTHLY_LIMIT}通）に達したので送りません"
    body = json.dumps({"to": uid, "messages": [{"type": "text", "text": text[:4900]}]}).encode("utf-8")
    req = urllib.request.Request(PUSH_URL, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20):
            pass
    except urllib.error.HTTPError as e:
        return f"LINE送信に失敗（HTTP {e.code}）"
    except Exception as e:  # noqa: BLE001
        return f"LINE送信に失敗（{type(e).__name__}）"
    st["count"] = used + 1
    st.setdefault("log", []).append({"at": now.isoformat(timespec="minutes"), "kind": kind})
    st["log"] = st["log"][-50:]
    _save(st)
    return f"LINEに送信しました（今月{used + 1}通目）"


def already_sent(key: str) -> bool:
    return key in _load().get("sent_keys", [])


def mark_sent(key: str) -> None:
    st = _load()
    keys = st.get("sent_keys", [])
    if key not in keys:
        keys.append(key)
    st["sent_keys"] = keys[-60:]
    _save(st)


def notify_once(key: str, text: str, kind: str) -> str:
    """同じ key では1回だけ送る（例：夜の通知はタームごとに1回）。"""
    if already_sent(key):
        return f"{key} は送信済みなので送りません"
    msg = send(text, kind)
    if msg.startswith("LINEに送信しました"):
        mark_sent(key)
    return msg


def evening_text(res, pf) -> str | None:
    """夜の通知文。S・A・要確認・保有警告がなければ None（送らない）。"""
    from . import classify as C
    from .calendar_jp import fmt_date
    from .conclusion import lines

    cards = [c for c in res.cards if c.category in (C.S, C.A, C.CHECK)]
    warned = pf.warned() if pf else []
    if not cards and not warned:
        return None
    dl = datetime.fromisoformat(res.deadline)
    out = [f"【名証スキャナー】締切 {fmt_date(dl.date())} {dl:%H:%M}"]
    out += [x.text for x in lines(res, pf)]
    n = {k: sum(1 for c in res.cards if c.category == k) for k in (C.S, C.A, C.CHECK)}
    out.append(f"S:{n[C.S]} A:{n[C.A]} 要確認:{n[C.CHECK]}")
    for c in cards[:6]:
        it = next((i for i in c.items if any(t["cat"] == c.category for t in i.tags)), c.items[-1])
        price = f" {c.price:,.0f}円" if c.price else ""
        out.append(f"・[{C.LABELS[c.category].split('：')[0]}] {c.name}({c.code}){price} {it.time} {it.title[:40]}")
    for h in warned:
        out.append(f"⚠保有 {h.name}({h.code}): {'・'.join(h.warnings)}")
    out.append(page_url())
    return "\n".join(out)


EXTRA_PER_NIGHT = 2  # 最初の通知のあとに追加で送れる数（1晩）


def _evening_items(res, pf) -> dict[str, str]:
    """通知の対象（キー→1行の説明）。キーは S:コード / A:コード / W:コード（保有警告）。"""
    from . import classify as C

    items = {}
    for c in res.cards:
        if c.category in (C.S, C.A):
            it = next((i for i in c.items if any(t["cat"] == c.category for t in i.tags)), c.items[-1])
            price = f" {c.price:,.0f}円" if c.price else ""
            shares = f" {c.shares:,}株" if c.shares else ""
            items[f"{c.category}:{c.code}"] = (f"[{'S' if c.category == C.S else 'A'}] {c.name}({c.code}){price}{shares} "
                                               f"{it.time} {it.title[:40]}")
    for h in (pf.warned() if pf else []):
        items[f"W:{h.code}"] = f"⚠保有 {h.name}({h.code}): {'・'.join(h.warnings)}"
    return items


def evening_notify(res, pf) -> str:
    """夜の通知。最初の1通（S・A・要確認・保有警告があるとき）と、そのあと新しいS・A・保有警告が出たときの追加（1晩2通まで）。

    同じ銘柄（同じ種類）は1回だけ。月180通の上限は send() で守る。
    """
    st = _load()
    night = st.setdefault("nights", {}).setdefault(res.base_day, {"first": False, "extra": 0, "keys": []})
    items = _evening_items(res, pf)
    if not night["first"]:
        text = evening_text(res, pf)
        if not text:
            return "通知する候補なし"
        msg = send(text, "evening")
        if msg.startswith("LINEに送信しました"):
            st = _load()
            night = st.setdefault("nights", {}).setdefault(res.base_day, {"first": False, "extra": 0, "keys": []})
            night["first"] = True
            night["keys"] = sorted(set(night["keys"]) | set(items))
            st["nights"] = dict(sorted(st["nights"].items())[-14:])
            _save(st)
        return msg
    new = {k: v for k, v in items.items() if k not in night["keys"]}
    if not new:
        return "新しいS・A・保有警告はないので追加通知なし"
    if night["extra"] >= EXTRA_PER_NIGHT:
        return f"今夜の追加通知は{EXTRA_PER_NIGHT}通に達したので送りません"
    from .calendar_jp import fmt_date

    dl = datetime.fromisoformat(res.deadline)
    text = "\n".join([f"【名証スキャナー・追加】締切 {fmt_date(dl.date())} {dl:%H:%M}"] + list(new.values()) + [page_url()])
    msg = send(text, "evening_extra")
    if msg.startswith("LINEに送信しました"):
        st = _load()
        night = st["nights"][res.base_day]
        night["extra"] += 1
        night["keys"] = sorted(set(night["keys"]) | set(new))
        _save(st)
    return msg


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--failure", help="Actions の実行URL（失敗通知）")
    p.add_argument("--test", action="store_true", help="テスト送信")
    a = p.parse_args()
    if a.failure:
        print(send(f"【名証スキャナー】自動実行が失敗しました。前回のページが表示されたままです。\n{a.failure}", "failure"))
    elif a.test:
        print(send(f"【名証スキャナー】テスト送信です。\n{page_url()}", "test"))


if __name__ == "__main__":
    main()
