"""朝レポートの欄。"""
from __future__ import annotations

from datetime import datetime
from html import escape


def _yen(v):
    return "—" if v is None else f"{v:,.0f}円"


def section(m, deadline: datetime) -> str:
    out = ['<h2 id="morning">朝レポート</h2>',
           f'<div class="tobbanner"><div class="big">注文締切 {deadline:%m/%d} 朝7:59 まで。前日の終値で買えます</div>'
           f'<div class="big">{escape(m.direction)}</div>'
           f'<div class="sub">米国市場 {escape(m.us_date)} の終値時点（Yahoo Finance）</div></div>']
    if m.stale_from:
        out.append('<div class="warn">海外指標が取れなかったため、前回の朝レポートを表示しています</div>')
    for e in m.errors:
        out.append(f'<div class="warn">{escape(e)}</div>')
    rows = "".join(
        f'<div class="row"><span>{escape(r.label)}</span><b>'
        + (f"{r.last:,.2f}（{r.change_pct:+.2f}%）" if r.last is not None else "—") + "</b></div>"
        for r in m.indices)
    out.append(f'<div class="meta">{rows}</div>')
    out.append('<p class="sub">名証上場の大型株のADR（米国で取引される日本株）から計算した理論価格と、前日の名証の値段との差。'
               '+1%以上なら前日終値で先回りする候補。換算比率は過去の値から推定した目安です</p>')
    stale = [a for a in m.adrs if a.gap_pct is not None and not a.traded]
    for a in m.adrs:
        if a.gap_pct is None or not a.traded:
            continue
        cls = "A" if a.traded and a.gap_pct >= 1 else ("BAD" if a.traded and a.gap_pct <= -1 else "")
        note = "" if a.traded else '<span class="badge">米国で取引なし（参考外）</span>'
        single = '<span class="badge single">名証単独</span>' if a.single else ""
        out.append(f'<div class="card {cls}"><div class="head"><span class="name">{escape(a.name)}</span>'
                   f'<span class="code">{escape(a.code)}</span>{single}{note}</div>'
                   f'<div class="price">ADR {escape(a.adr)} {a.adr_change_pct:+.1f}% → 理論価格 {_yen(a.implied)} ／ '
                   f'前日の名証 {_yen(a.jp_price)} ／ 差 <b>{a.gap_pct:+.1f}%</b></div></div>')
    if stale:
        out.append(f'<details><summary>米国で取引がなかったADR（{len(stale)}銘柄・参考外）</summary>'
                   + "".join(f'<div class="reason">{escape(a.name)}（{escape(a.code)}）ADR {escape(a.adr)}</div>' for a in stale)
                   + "</details>")
    return "\n".join(out)
