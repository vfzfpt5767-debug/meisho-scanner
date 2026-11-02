"""スキャン結果をスマホ向けのHTML1ページにする。"""
from __future__ import annotations

from datetime import date, datetime
from html import escape

from . import classify as C
from .calendar_jp import fmt_date, is_business_day
from .config import GITHUB_BRANCH, GITHUB_REPOSITORY, TOB_SELL_GAP_PCT
from .scan import Card, ScanResult, TobRow

EDIT_TOB_URL = f"https://github.com/{GITHUB_REPOSITORY}/edit/{GITHUB_BRANCH}/tob_prices.csv"

SECTIONS = [
    (C.S, "S：TOB・MBOの対象", "他社によるTOB・MBOの対象。TOB価格との差が大きいほど上"),
    (C.A, "A：好材料", "上方修正・増配・自社株買い・優待・分割・提携など"),
    (C.CHECK, "要確認（タイトルだけでは良し悪しが不明）", "「業績予想の修正」など。PDFを開いて上方か下方か確認"),
    (C.INFO, "参考", "決算短信など。数値の自動計算はしていません"),
    (C.BAD, "悪材料（保有中なら売りを検討）", "下方修正・減配・増資・特別損失など"),
    (C.SELF_TOB, "自社株TOB（別扱い）", "会社が自分の株を買い戻すTOB。値上がりしにくい"),
    (C.TOB_REL, "TOB関連の続報", "TOBの結果・訂正・買い手側の開示など"),
]

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1b1f24;--muted:#5d6670;--line:#dde1e6;--accent:#0b62d6;
--s:#c62828;--a:#e65100;--check:#7b1fa2;--info:#546e7a;--bad:#37474f;--warn-bg:#fff4e5;--warn-line:#f0a020;
--tob-bg:#fff0f0;--btn:#0b62d6;--btn-text:#fff;--chip:#eef1f4}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#191e24;--text:#e8ecf0;--muted:#9aa5b1;--line:#2c333b;
--accent:#6ea8ff;--s:#ff6b6b;--a:#ffa040;--check:#d18cff;--info:#90a4ae;--bad:#b0bec5;--warn-bg:#2b2112;--warn-line:#c98a1a;
--tob-bg:#2a1414;--btn:#3d7fe0;--btn-text:#fff;--chip:#252c34}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif;
font-size:16px;line-height:1.55;-webkit-text-size-adjust:100%}
main{max-width:760px;margin:0 auto;padding:12px 16px 48px}
h1{font-size:1.25rem;margin:8px 0 4px}
h2{font-size:1.1rem;margin:28px 0 4px;padding-bottom:4px;border-bottom:2px solid var(--line)}
.sub{color:var(--muted);font-size:.85rem;margin:0 0 8px}
.meta{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:8px 0}
.deadline{font-size:1.15rem;font-weight:700;color:var(--s)}
.warn{background:var(--warn-bg);border:1px solid var(--warn-line);border-radius:12px;padding:10px 14px;margin:10px 0;font-size:.9rem}
.warn ul{margin:4px 0 0;padding-left:1.2em}
.tobbanner{background:var(--tob-bg);border:3px solid var(--s);border-radius:14px;padding:14px;margin:14px 0}
.tobbanner h2{border:none;margin:0 0 6px;color:var(--s);font-size:1.35rem}
.tobbanner .big{font-size:1.15rem;font-weight:700}
.card{background:var(--card);border:1px solid var(--line);border-left:6px solid var(--info);border-radius:12px;padding:12px 14px;margin:10px 0}
.card.S{border-left-color:var(--s)}.card.A{border-left-color:var(--a)}.card.CHECK{border-left-color:var(--check)}
.card.BAD{border-left-color:var(--bad)}
.head{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.name{font-weight:700;font-size:1.08rem}
.code{color:var(--muted);font-variant-numeric:tabular-nums}
.badge{display:inline-block;border-radius:999px;padding:1px 9px;font-size:.78rem;font-weight:700;background:var(--chip)}
.badge.single{background:#1b8a3a;color:#fff}.badge.qo{background:#8d6e00;color:#fff}.badge.dual{background:var(--chip);color:var(--muted)}
.badge.cat-S{background:var(--s);color:#fff}.badge.cat-A{background:var(--a);color:#fff}
.badge.cat-CHECK{background:var(--check);color:#fff}.badge.cat-BAD{background:var(--bad);color:var(--card)}
.price{margin:6px 0;font-variant-numeric:tabular-nums}
.price b{font-size:1.15rem}
.tob{margin:6px 0;padding:8px 10px;border-radius:10px;background:var(--tob-bg);font-weight:700}
.gap{font-size:1.25rem;color:var(--s)}
.item{border-top:1px dashed var(--line);padding-top:8px;margin-top:8px}
.item .t{font-size:.95rem}
.time{color:var(--muted);font-variant-numeric:tabular-nums;margin-right:4px}
.reason{font-size:.85rem;color:var(--muted);margin:2px 0}
.tag{display:inline-block;font-size:.75rem;border:1px solid var(--line);border-radius:6px;padding:0 6px;margin:2px 4px 2px 0}
.tag.S{border-color:var(--s);color:var(--s)}.tag.A{border-color:var(--a);color:var(--a)}
.tag.CHECK{border-color:var(--check);color:var(--check)}.tag.BAD{border-color:var(--bad)}
.btns{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
a.btn{display:inline-flex;align-items:center;justify-content:center;min-height:48px;min-width:120px;padding:10px 16px;
border-radius:10px;background:var(--btn);color:var(--btn-text);text-decoration:none;font-weight:700;font-size:1rem;flex:1 1 140px}
a.btn.sub{background:var(--chip);color:var(--text);border:1px solid var(--line)}
a.btn.edit{background:var(--s)}
a{color:var(--accent)}
details{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin:10px 0}
summary{cursor:pointer;font-weight:700;min-height:32px}
.empty{color:var(--muted);font-size:.9rem;margin:6px 0}
.row{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
.sell{color:#fff;background:var(--s);border-radius:6px;padding:0 8px;font-weight:700}
footer{color:var(--muted);font-size:.8rem;margin-top:32px}
.concl{background:var(--card);border:3px solid var(--accent);border-radius:14px;padding:12px 14px;margin:10px 0}
.concl h2{border:none;margin:0 0 6px;font-size:1.2rem}
.concl ol{margin:0;padding-left:1.3em}.concl li{margin:4px 0;font-weight:700}
.concl li.sell{color:var(--s)}.concl li.info{font-weight:400}
.concl .dl{font-size:1.1rem;font-weight:700;color:var(--s);margin-top:6px}
.alert{background:var(--s);color:#fff;border-radius:14px;padding:12px 14px;margin:10px 0;font-weight:700}
.alert ul{margin:4px 0 0;padding-left:1.2em}
"""


def _yen(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.1f}円" if v != int(v) else f"{v:,.0f}円"


def _single_badge(single: bool | None) -> str:
    if single is True:
        return '<span class="badge single">名証単独</span>'
    if single is False:
        return '<span class="badge dual">重複（東証にも上場）</span>'
    return '<span class="badge dual">単独/重複 不明</span>'


def _kabutan(code: str) -> str:
    return f"https://kabutan.jp/stock/?code={escape(code)}"


def _price_block(c: Card) -> str:
    if c.price is None:
        return f'<div class="price">本日終値：<b>—</b>（{escape(c.price_note)}）</div>'
    shares = (f"300万円で <b>{c.shares:,}株</b>（{_yen(c.cost)}）" if c.shares
              else "300万円では1単元も買えません")
    unit = f"・売買単位{c.unit:,}株" if c.unit != 100 else ""
    qo = ' <span class="badge qo">気配のみ</span>' if c.quote_only else ""
    return (f'<div class="price">本日終値（今夜の約定見込み）：<b>{_yen(c.price)}</b>{qo} '
            f'<span class="sub">（{escape(c.price_note)}{unit}）</span><br>{shares}</div>')


def _tob_block(c: Card) -> str:
    if c.category != C.S:
        return ""
    if c.tob_price and c.tob_gap_pct is not None:
        return (f'<div class="tob">TOB価格 {_yen(c.tob_price)} ／ 終値との差 '
                f'<span class="gap">{c.tob_gap_pct:+.1f}%</span></div>')
    if c.tob_price:
        return f'<div class="tob">TOB価格 {_yen(c.tob_price)}（終値が取れないため差は計算できません）</div>'
    return (f'<div class="tob">TOB価格：未入力。PDFでTOB価格を確認して書き込むと、次回から差を計算します。'
            f'<div class="btns"><a class="btn edit" href="{EDIT_TOB_URL}" target="_blank" rel="noopener">'
            f'TOB価格を書き込む（{escape(c.code)}）</a></div></div>')


def _card(c: Card) -> str:
    items = []
    for it in c.items:
        tags = "".join(f'<span class="tag {escape(t["cat"])}">{escape(t["label"])}</span>' for t in it.tags
                       if t["label"] != "その他")
        reasons = "".join(f'<div class="reason">・{escape(t["reason"])}</div>' for t in it.tags if t["reason"])
        pdf = (f'<div class="btns"><a class="btn" href="{escape(it.pdf)}" target="_blank" rel="noopener">開示PDFを開く</a></div>'
               if it.pdf else "")
        items.append(f'<div class="item"><div class="t"><span class="time">{escape(it.date)} {escape(it.time)}</span>'
                     f'{escape(it.title)}</div>{tags}{reasons}{pdf}</div>')
    cat_badge = f'<span class="badge cat-{c.category}">{escape(C.LABELS.get(c.category, c.category).split("：")[0])}</span>'
    return (f'<div class="card {c.category}" id="c-{escape(c.code)}"><div class="head">{cat_badge}'
            f'<span class="name">{escape(c.name)}</span><span class="code">{escape(c.code)}</span>'
            f'{_single_badge(c.single)}</div>'
            f'{_price_block(c)}{_tob_block(c)}{"".join(items)}'
            f'<div class="btns"><a class="btn sub" href="{_kabutan(c.code)}" target="_blank" rel="noopener">株探で見る（PTS・チャート）</a></div>'
            f'</div>')


def _tob_row(r: TobRow) -> str:
    gap = f'<span class="gap">{r.gap_pct:+.1f}%</span>' if r.gap_pct is not None else "TOB価格未入力"
    sell = f' <span class="sell">売り時（差{TOB_SELL_GAP_PCT:g}%以下）</span>' if r.sell_signal else ""
    ev = "".join(
        f'<div class="reason">{escape(e["date"][5:])} {escape(e["time"])} {escape(e["label"])}：'
        f'<a href="{escape(e["pdf"])}" target="_blank" rel="noopener">{escape(e["title"][:60])}</a></div>'
        for e in r.events[-4:])
    return (f'<div class="card S"><div class="head"><span class="name">{escape(r.name)}</span>'
            f'<span class="code">{escape(r.code)}</span>{_single_badge(r.single)}'
            f'<span class="badge">{escape(r.status)}</span></div>'
            f'<div class="price">終値 <b>{_yen(r.price)}</b>（{escape(r.price_note)}）／ TOB価格 <b>{_yen(r.tob_price)}</b>'
            f' ／ 差 {gap}{sell}</div>{ev}'
            f'<div class="btns"><a class="btn sub" href="{EDIT_TOB_URL}" target="_blank" rel="noopener">TOB価格を書き込む</a>'
            f'<a class="btn sub" href="{_kabutan(r.code)}" target="_blank" rel="noopener">株探</a></div></div>')


def render(res: ScanResult, portfolio=None, extra_top: str = "", extra_sections: str = "", extra_mid: str = "") -> str:
    gen = datetime.fromisoformat(res.generated_at)
    base = date.fromisoformat(res.base_day)
    dl = datetime.fromisoformat(res.deadline)
    holidays_between = []
    d = base
    from datetime import timedelta
    d += timedelta(days=1)
    while d < dl.date():
        if not is_business_day(d) and d.weekday() < 5:
            holidays_between.append(fmt_date(d))
        d += timedelta(days=1)
    hol = f"（{'・'.join(holidays_between)}は休業日）" if holidays_between else ""

    out = [f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<meta name="color-scheme" content="light dark">'
           f'<title>名証 夜の好材料スキャナー</title><style>{CSS}</style></head><body><main>']
    out.append('<h1>名証 夜の好材料スキャナー</h1>')
    out.append(extra_top)
    from .conclusion import lines as concl_lines

    left = dl - gen
    hours = left.total_seconds() / 3600
    left_txt = f"あと{int(hours)}時間{int((hours % 1) * 60)}分" if hours > 0 else "締切を過ぎています（次のタームは営業日16:30から）"
    head = "今朝の結論" if 4 <= gen.hour < 12 else "今夜の結論"
    lis = "".join(f'<li class="{escape(x.kind)}">{escape(x.text)}</li>' for x in concl_lines(res, portfolio))
    out.append(f'<div class="concl"><h2>{head}</h2><ol>{lis}</ol>'
               f'<div class="dl">注文締切：{fmt_date(dl.date())} 朝{dl:%H:%M}（{left_txt}）{hol}</div></div>')
    out.append(f'<div class="meta">更新：{fmt_date(gen.date())} {gen:%H:%M}<br>'
               f'約定見込み価格＝<b>{fmt_date(base)}の名証の終値</b>（出典：{escape(res.price_source or "取得できず")}）<br>'
               f'対象：{fmt_date(base)} 15:30以降の名証上場銘柄の適時開示<br>'
               f'<span class="deadline">今夜の注文締切：{fmt_date(dl.date())} 朝{dl:%H:%M}</span>{hol}<br>'
               f'<span class="sub">名証上場 {res.universe_count}銘柄（うち名証単独 {res.single_count}）を対象</span></div>')

    if res.stale_from:
        sf = datetime.fromisoformat(res.stale_from)
        out.append(f'<div class="warn"><b>今回は開示一覧を取得できなかったため、前回（{sf:%H:%M}）の結果を表示しています。</b></div>')
    if res.errors:
        out.append('<div class="warn"><b>取得できなかったもの・注意</b><ul>' +
                   "".join(f"<li>{escape(e)}</li>" for e in res.errors) + "</ul></div>")

    s_cards = [c for c in res.cards if c.category == C.S]
    if s_cards:
        lis = "".join(
            f'<li class="big"><a href="#c-{escape(c.code)}">{escape(c.name)}（{escape(c.code)}）</a>'
            + (f' 差 {c.tob_gap_pct:+.1f}%' if c.tob_gap_pct is not None else "：TOB価格未入力") + "</li>"
            for c in s_cards)
        out.append(f'<div class="tobbanner"><h2>TOB・MBOの対象が出ました</h2><ul>{lis}</ul>'
                   f'<div>PDFでTOB価格を確認し、tob_prices.csv に「コード,TOB価格」を1行書き込んでください（例：1439,2150）。'
                   f'次回の実行から差（%）を計算します。</div>'
                   f'<div class="btns"><a class="btn edit" href="{EDIT_TOB_URL}" target="_blank" rel="noopener">'
                   f'tob_prices.csv を編集する</a></div></div>')

    for cat, title, desc in SECTIONS:
        cs = [c for c in res.cards if c.category == cat]
        if cat == C.INFO:
            main_cs = [c for c in cs if c.score > 0]
            other = [c for c in cs if c.score <= 0]
        else:
            main_cs, other = cs, []
        out.append(f'<h2>{escape(title)}（{len(cs)}社）</h2><p class="sub">{escape(desc)}</p>')
        if not cs:
            out.append('<p class="empty">なし</p>')
        out += [_card(c) for c in main_cs]
        if other:
            out.append(f'<details><summary>その他の開示（{len(other)}社）</summary>' +
                       "".join(_card(c) for c in other) + "</details>")
        if cat == C.A:
            out.append(extra_mid)

    out.append(extra_sections)
    out.append(f'<h2>15:30より前の開示（{len(res.before_close)}社）</h2>'
               '<p class="sub">場中の開示は、すでに終値に反映されている可能性が高い（参考）</p>')
    if res.before_close:
        out.append('<details><summary>開く</summary>' + "".join(_card(c) for c in res.before_close) + "</details>")
    else:
        out.append('<p class="empty">なし</p>')

    out.append(f'<h2>TOBトラッカー（10/22以降、{len(res.tob_rows)}銘柄）</h2>'
               f'<p class="sub">期間中にTOBが発表された名証上場銘柄。差が{TOB_SELL_GAP_PCT:g}%以下なら「売り時」</p>')
    out += [_tob_row(r) for r in res.tob_rows] or ['<p class="empty">なし</p>']

    out.append('<footer>' + "".join(f"<p>{escape(n)}</p>" for n in res.notes) +
               '<p>データ：適時開示＝やのしんWEB-API（TDnetの一覧）、銘柄一覧・値段＝名古屋証券取引所の公開情報。'
               '売買の判断はご自身で行ってください。</p></footer>')
    out.append('</main></body></html>')
    return "\n".join(out)
