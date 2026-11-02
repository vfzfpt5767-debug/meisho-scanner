"""ストップ高・ストップ安の欄。"""
from __future__ import annotations

from html import escape

from .classify import classify, norm


def _yen(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.1f}円" if v != int(v) else f"{v:,.0f}円"


def _related(code: str, disclosures, base) -> list:
    """その銘柄の最近（基準日の前営業日以降）の開示。"""
    return [d for d in disclosures if d.code == code and (base - d.pubdate.date()).days <= 3][-3:]


def section(res) -> str:
    ctx = res.ctx
    hits = ctx.limits
    tob_rows = {r.code: r for r in res.tob_rows}
    highs = sorted([h for h in hits.values() if h.kind == "stop_high"],
                   key=lambda h: (not h.stuck, -h.change_pct))
    lows = sorted([h for h in hits.values() if h.kind == "stop_low"], key=lambda h: (not h.stuck, h.change_pct))
    out = [f'<h2 id="limits">ストップ高（{len(highs)}銘柄）・ストップ安（{len(lows)}銘柄）</h2>',
           '<p class="sub">名証の日報とJPXの制限値幅表で判定。張り付き（最終気配がストップ高）を最上位に表示。'
           '制限値幅の臨時拡大や配当落ちの調整には対応していません</p>']
    if not hits:
        out.append('<p class="empty">なし</p>')
    for h in highs + lows:
        ls = ctx.universe.get(h.code)
        single = ('<span class="badge single">名証単独</span>' if ls and ls.single
                  else '<span class="badge dual">重複</span>' if ls else "")
        cat = "S" if h.kind == "stop_high" else "BAD"
        note = ('<div class="tob">現実ではほぼ買えないが、コンテストではこの値段（最終気配）で買える</div>'
                if h.kind == "stop_high" and h.stuck else "")
        tob = ""
        tp = ctx.tob_prices.get(h.code) or (tob_rows[h.code].tob_price if h.code in tob_rows else None)
        if tp:
            tob = f'<div class="tob">TOB価格 {_yen(tp)} ／ 差 <span class="gap">{(tp / h.price - 1) * 100:+.1f}%</span></div>'
        elif h.code in tob_rows:
            tob = '<div class="tob">TOB対象（TOB価格未入力）</div>'
        rel = ""
        for d in _related(h.code, ctx.disclosures, ctx.base):
            tags = [t.label for t in classify(d.title, d.code) if t.label != "その他"]
            if not tags and "公開買付" not in norm(d.title):
                tags = []
            rel += (f'<div class="reason">{d.pubdate:%m/%d %H:%M} {escape(d.title[:60])}'
                    f'{"［" + escape("・".join(tags)) + "］" if tags else ""} '
                    f'<a href="{escape(d.pdf_url)}" target="_blank" rel="noopener">PDF</a></div>')
        ohlc = (f"始{_yen(h.open)} 高{_yen(h.high)} 安{_yen(h.low)} 終{_yen(h.close)}" if h.close is not None
                else "名証での約定なし（気配のみ）")
        out.append(
            f'<div class="card {cat}"><div class="head"><span class="badge cat-{cat}">{escape(h.label)}</span>'
            f'<span class="name">{escape(ls.name if ls else h.name)}</span><span class="code">{escape(h.code)}</span>{single}</div>'
            f'<div class="price">値段 <b>{_yen(h.price)}</b>（基準値段 {_yen(h.base)} から {h.change_pct:+.1f}%、'
            f'制限値幅いっぱい {_yen(h.limit_price)}）<br><span class="sub">{ohlc}／最終気配 {_yen(h.quote)}</span></div>'
            f'{note}{tob}{rel}'
            f'<div class="btns"><a class="btn sub" href="https://kabutan.jp/stock/?code={escape(h.code)}" '
            f'target="_blank" rel="noopener">株探で見る</a></div></div>')
    return "\n".join(out)
