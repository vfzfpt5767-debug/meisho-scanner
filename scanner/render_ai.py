"""ページ最上部の「AIの結論」（data/ai/latest.json から）。"""
from __future__ import annotations

import json
from datetime import datetime
from html import escape

from .units import fmt as ufmt
from .ai_decision import LATEST
from .config import GITHUB_BRANCH, GITHUB_REPOSITORY

EDIT = f"https://github.com/{GITHUB_REPOSITORY}/edit/{GITHUB_BRANCH}/"


def load() -> dict | None:
    try:
        return json.loads(LATEST.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def box(base_day: str) -> str:
    d = load()
    if not d:
        return ('<div class="aibox"><h2>AIの結論</h2><p class="sub">AIアナリストはまだ動いていません'
                '（設定は README の「AIアナリストの設定」）</p></div>')
    if d.get("base_day") != base_day:
        t = d.get("created_at", "")
        return (f'<div class="aibox"><h2>AIの結論</h2><p class="sub">今夜（このターム）のAIの判断はまだありません。'
                f'前回：{escape(t[:16].replace("T", " "))}「{escape(d.get("summary", ""))}」</p></div>')
    try:
        t = datetime.fromisoformat(d["created_at"])
        when = f"{t:%m/%d %H:%M}"
    except Exception:  # noqa: BLE001
        when = ""
    orders = ""
    for o in d.get("orders", []):
        side = "買い" if o["side"] == "buy" else "売り"
        cls = "buy" if o["side"] == "buy" else "sell"
        links = " ".join(f'<a href="{escape(u)}" target="_blank" rel="noopener">根拠{i + 1}</a>'
                         for i, u in enumerate(o.get("links", [])[:3]))
        orders += (f'<li class="{cls}"><b>{side}：{escape(str(o["code"]))} {escape(o.get("name", ""))} '
                   f'{escape(ufmt(o["code"], o["shares"], o.get("unit")))}</b>（今日の終値 {float(o["price"]):,.0f}円・合計 {int(o["shares"]) * float(o["price"]):,.0f}円）'
                   f' 自信度：{escape(o.get("confidence", ""))}<br><span class="sub">{escape(o.get("reason", ""))} {links}</span></li>')
    if not orders:
        orders = "<li>今夜は注文なし（見送り）</li>"
    skipped = "".join(f'<div class="reason">見送り：{escape(str(s.get("code", "")))} {escape(s.get("name", ""))}'
                      f' — {escape(s.get("reason", ""))}</div>' for s in d.get("skipped", [])[:6])
    ap = d.get("_apply", {})
    apply_note = ""
    if ap.get("notes"):
        apply_note = ('<div class="sub">保有の自動記録：' + escape("／".join(ap["notes"])) +
                      f'。AIと違う注文をしたときは <a href="{EDIT}holdings.csv" target="_blank" rel="noopener">holdings.csv</a>'
                      f' と <a href="{EDIT}cash.csv" target="_blank" rel="noopener">cash.csv</a> を直してください</div>')
    elif ap.get("status"):
        apply_note = f'<div class="sub">{escape(ap["status"])}</div>'
    md = f"https://github.com/{GITHUB_REPOSITORY}/blob/{GITHUB_BRANCH}/decisions/{escape(d.get('id', ''))}.md"
    return (f'<div class="aibox"><h2>AIの結論（{when}）</h2><div class="big">{escape(d.get("summary", ""))}</div>'
            f'<ol>{orders}</ol>{skipped}{apply_note}'
            f'<div class="sub"><a href="{md}" target="_blank" rel="noopener">AIの調査メモ</a></div></div>')
