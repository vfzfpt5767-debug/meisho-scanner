"""新規上場トラッカーの欄。"""
from __future__ import annotations

from datetime import date
from html import escape

from .calendar_jp import fmt_date


def _yen(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.1f}円" if v != int(v) else f"{v:,.0f}円"


def section(rows) -> str:
    out = [f'<h2 id="ipo">新規上場（10/22以降・今後の予定 {len(rows)}社）</h2>',
           '<p class="sub">名証に新しく上場する銘柄。<b>上場日の16:30から買えます</b>（その日の名証の最終気配で約定）</p>']
    if not rows:
        out.append('<p class="empty">なし</p>')
    for r in rows:
        it = r.item
        d = date.fromisoformat(it.date)
        single = ('<span class="badge single">名証単独</span>' if r.single
                  else '<span class="badge dual">重複</span>' if r.single is False
                  else '<span class="badge dual">単独/重複 不明</span>')
        kind = "IPO（新規公開）" if it.ipo else "既上場会社の名証追加上場"
        offer = f"公開価格 {_yen(it.offer_price)}" if it.offer_price else ("公開価格：資料待ち" if it.ipo else "")
        if r.status == "上場予定":
            body = f'<div class="tob">上場予定：{fmt_date(d)}。この日の16:30から買えます</div>'
        else:
            if r.first_open is not None:
                fo = f"初値あり（名証の始値 {_yen(r.first_open)}）"
            elif r.first_day_price is not None:
                fo = "初値つかず（気配のみ）"
            else:
                fo = "上場日の値段：名証の日報に見当たりません"
            gain = ""
            if it.offer_price and r.first_day_price:
                gain = f"（公開価格比 {(r.first_day_price / it.offer_price - 1) * 100:+.1f}%）"
            qo = ' <span class="badge qo">気配のみ</span>' if r.first_day_quote_only else ""
            body = (f'<div class="price">上場日 {fmt_date(d)}：{fo}<br>上場日の約定見込み（最終気配） <b>{_yen(r.first_day_price)}</b>{qo}{gain}'
                    f'<br>直近の値段 <b>{_yen(r.price_now)}</b></div>')
        links = "".join(f'<a class="btn sub" href="{escape(u)}" target="_blank" rel="noopener">{escape(k)}</a>'
                        for k, u in it.links.items() if k in ("会社概要", "価格決定", "Ⅰの部", "有価証券報告書"))
        out.append(
            f'<div class="card {"A" if r.status == "上場予定" else ""}"><div class="head"><span class="badge">{escape(r.status)}</span>'
            f'<span class="name">{escape(it.name)}</span><span class="code">{escape(it.code)}</span>{single}</div>'
            f'<div class="sub">{escape(it.market)}市場・{kind}{"・" + offer if offer else ""}<br>単独/重複：{escape(r.single_note)}</div>'
            f'{body}<div class="btns">{links}<a class="btn sub" href="https://kabutan.jp/stock/?code={escape(it.code)}" '
            f'target="_blank" rel="noopener">株探</a></div></div>')
    return "\n".join(out)
