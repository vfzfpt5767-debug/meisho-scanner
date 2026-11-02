"""保有管理の表示（最上部の赤い警告と、保有管理の欄）。"""
from __future__ import annotations

from html import escape

from .calendar_jp import fmt_date
from .config import GITHUB_BRANCH, GITHUB_REPOSITORY
from .portfolio import START_CAPITAL, TENTH_LINE_PROFIT, Portfolio

EDIT = f"https://github.com/{GITHUB_REPOSITORY}/edit/{GITHUB_BRANCH}/"


def _yen(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.1f}円" if v != int(v) else f"{v:,.0f}円"


def _man(v: float | None, sign: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v / 10000:+,.1f}万円" if sign else f"{v / 10000:,.1f}万円"


def alert(pf: Portfolio) -> str:
    w = pf.warned()
    if not w:
        return ""
    lis = "".join(f"<li>{escape(h.name)}（{escape(h.code)}）：{escape('・'.join(h.warnings))}</li>" for h in w)
    return f'<div class="alert">保有銘柄に警告があります（売りを検討）<ul>{lis}</ul></div>'


def section(pf: Portfolio) -> str:
    out = ['<h2 id="portfolio">保有管理</h2>']
    for e in pf.errors:
        out.append(f'<div class="warn">{escape(e)}</div>')
    if not pf.registered:
        out.append('<p class="empty">holdings.csv と cash.csv がまだ空です。下のボタンから書き込むと、評価額・損益・入れ替え案を表示します。</p>')
    total, profit = pf.total, pf.profit
    gap10 = None if profit is None else profit - TENTH_LINE_PROFIT
    term = (f"{_yen(pf.term_left)}（{fmt_date(pf.term_base)}夜〜のターム。今ターム購入済み {_yen(pf.term_bought)}）"
            if pf.term_open else "今は注文できない時間（次のタームは16:30から。300万円）")
    single = ("購入済み（" + "、".join(pf.single_codes_ever) + "）" if pf.single_codes_ever else "購入済み（cash.csvで指定）") \
        if pf.single_bought else '<b style="color:var(--s)">未購入（入賞条件）</b>'
    rows = [
        ("現金", _yen(pf.cash) if pf.cash is not None else "cash.csv 未入力"),
        ("保有株の評価額", _yen(pf.market_value)),
        ("合計資産", _yen(total)),
        ("損益（元手300万円から）", f"{_man(profit, True)}（{(profit / START_CAPITAL * 100):+.1f}%）" if profit is not None else "—"),
        ("昨年10位ライン（+429万円）との差", _man(gap10, True) if gap10 is not None else "—"),
        ("今タームの残り購入枠", term),
        ("名証単独銘柄", single),
    ]
    out.append('<div class="meta">' + "".join(
        f'<div class="row"><span>{escape(k)}</span><b>{v if k == "名証単独銘柄" else escape(v)}</b></div>' for k, v in rows) + "</div>")

    for h in pf.holdings:
        cls = "card BAD" if h.warnings else "card"
        qo = ' <span class="badge qo">気配のみ</span>' if h.quote_only else ""
        single_b = ('<span class="badge single">名証単独</span>' if h.single
                    else '<span class="badge dual">重複</span>' if h.single is False else "")
        warn = "".join(f'<div class="tob" style="color:var(--s)">警告：{escape(x)}</div>' for x in h.warnings)
        caut = "".join(f'<div class="reason">注意：{escape(x)}</div>' for x in h.cautions)
        out.append(
            f'<div class="{cls}"><div class="head"><span class="name">{escape(h.name)}</span>'
            f'<span class="code">{escape(h.code)}</span>{single_b}</div>'
            f'<div class="price">{h.shares:,}株 ／ 買値 {_yen(h.buy_price)}（{fmt_date(h.buy_date)}）<br>'
            f'今の値段 <b>{_yen(h.price)}</b>{qo} <span class="sub">（{escape(h.price_note)}）</span><br>'
            f'評価額 {_yen(h.value)} ／ 損益 <b>{_man(h.pnl, True)}（{h.pnl_pct:+.1f}%）</b></div>{warn}{caut}</div>')

    if pf.swap:
        lis = "".join(
            f'<li><b>{"売る" if t.side == "sell" else "買う"}</b>：{escape(t.name)}（{escape(t.code)}） '
            f'{t.shares:,}株 × {_yen(t.price)} = {_yen(t.amount)} <span class="sub">［{escape(t.reason)}］</span></li>'
            for t in pf.swap)
        out.append(f'<div class="meta"><b>入れ替え案（売り→買い）</b><ol>{lis}</ol>'
                   f'<div class="sub">{escape(pf.swap_note)}。売買単位の整数倍・今タームの残り枠内で計算。売るのは無制限、買いは枠を使います</div></div>')
    elif pf.swap_note:
        out.append(f'<p class="sub">入れ替え案：{escape(pf.swap_note)}</p>')

    out.append(f'<div class="btns"><a class="btn sub" href="{EDIT}holdings.csv" target="_blank" rel="noopener">holdings.csv を編集</a>'
               f'<a class="btn sub" href="{EDIT}cash.csv" target="_blank" rel="noopener">cash.csv を編集</a></div>')
    return "\n".join(out)
