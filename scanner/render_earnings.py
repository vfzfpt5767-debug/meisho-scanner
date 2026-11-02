"""決算カレンダーの欄。"""
from __future__ import annotations

from datetime import date
from html import escape

from .calendar_jp import fmt_date


def section(days: list[date], by_day: dict, universe: dict, held: set[str], tonight: date) -> str:
    total = sum(len(v) for v in by_day.values())
    out = [f'<h2 id="earnings">決算カレンダー（今後10営業日・名証上場 {total}件）</h2>',
           '<p class="sub">JPXと名証の決算発表予定日の一覧から。予定は変わることがあります。発表は15:00〜15:30以降が多く、引け後なら今夜の材料になります</p>']
    labels = {}
    if days:
        is_today = days[0] == tonight
        labels[days[0]] = "今夜発表" if is_today else "次の営業日に発表"
        if len(days) > 1:
            labels[days[1]] = "明日発表" if is_today else "その次の営業日に発表"
    for d in days:
        evs = sorted(by_day.get(d.isoformat(), []), key=lambda e: (not universe[e.code].single, e.code))
        if not evs:
            continue
        tag = labels.get(d, "")
        head = f'{fmt_date(d)}（{len(evs)}社）' + (f' <span class="sell">{tag}</span>' if tag else "")
        items = []
        for e in evs:
            ls = universe[e.code]
            badges = ('<span class="badge single">名証単独</span>' if ls.single else "") + \
                     ('<span class="sell">保有中</span>' if e.code in held else "")
            items.append(f'<div class="reason"><b>{escape(ls.name)}</b>（{escape(e.code)}）{badges} '
                         f'{escape(e.kind)} <a href="https://kabutan.jp/stock/?code={escape(e.code)}" target="_blank" rel="noopener">株探</a></div>')
        body = "".join(items)
        if tag:
            out.append(f'<div class="card A"><div class="name">{head}</div>{body}</div>')
        else:
            out.append(f'<details><summary>{head}</summary>{body}</details>')
    if total == 0:
        out.append('<p class="empty">予定なし（または取得できず）</p>')
    return "\n".join(out)
