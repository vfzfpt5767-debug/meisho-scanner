"""夜の好材料スキャン本体：データを集めて、候補（カード）を作る。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from . import classify as C
from . import tob as tobmod
from .calendar_jp import JST, base_day, now_jst, order_deadline
from .config import BUDGET_PER_TERM, CONTEST_START, DATA_DIR, MARKET_CLOSE, TOB_SELL_GAP_PCT
from .sources import nse_list, nse_prices, yahoo, yanoshin
from .sources.nse_list import Listing
from .sources.yanoshin import Disclosure

LAST_SCAN = DATA_DIR / "last_scan.json"


@dataclass
class Item:
    time: str
    date: str
    title: str
    pdf: str
    tags: list[dict]


@dataclass
class Card:
    code: str
    name: str
    single: bool | None  # True=名証単独, False=重複, None=不明
    division: str
    unit: int
    category: str
    score: int
    items: list[Item]
    price: float | None = None
    price_note: str = ""
    nse_close: float | None = None
    nse_quote: float | None = None
    shares: int | None = None
    cost: float | None = None
    tob_price: float | None = None
    tob_gap_pct: float | None = None


@dataclass
class TobRow:
    code: str
    name: str
    single: bool | None
    first_date: str
    status: str
    events: list[dict]
    price: float | None
    price_note: str
    tob_price: float | None
    gap_pct: float | None
    sell_signal: bool


@dataclass
class ScanResult:
    generated_at: str
    base_day: str
    deadline: str
    price_source: str
    cards: list[Card]
    before_close: list[Card]
    tob_rows: list[TobRow]
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    universe_count: int = 0
    single_count: int = 0
    stale_from: str = ""  # 前回の結果を使っている場合、その時刻


def _shares(price: float | None, unit: int) -> tuple[int | None, float | None]:
    if not price or price <= 0:
        return None, None
    lots = int(BUDGET_PER_TERM // (price * unit))
    return lots * unit, lots * unit * price


def run(now: datetime | None = None) -> ScanResult:
    now = now or now_jst()
    base = base_day(now)
    deadline = order_deadline(base)
    errors: list[str] = []
    notes: list[str] = []

    # 1) 名証の上場銘柄一覧
    universe, err = nse_list.load()
    if err:
        errors.append(err)

    def listing(code: str) -> Listing | None:
        return universe.get(code)

    def is_nagoya(code: str, d: Disclosure | None) -> bool:
        if code in universe:
            return True
        return bool(d and d.nagoya)

    def single_of(code: str, d: Disclosure | None) -> bool | None:
        if code in universe:
            return universe[code].single
        if d is not None and d.markets:
            return "名" in d.markets and "東" not in d.markets
        return None

    # 2) 適時開示（期間の初めから今日まで。過去分はキャッシュ）
    start = min(CONTEST_START, base - timedelta(days=7))
    disclosures, errs = yanoshin.fetch_range(start, now.date())
    errors += errs
    window_start = datetime.combine(base, MARKET_CLOSE, tzinfo=JST)
    after = [d for d in disclosures if d.pubdate >= window_start and d.pubdate <= now + timedelta(minutes=5)
             and is_nagoya(d.code, d)]
    before = [d for d in disclosures if d.pubdate.date() == base and d.pubdate < window_start
              and is_nagoya(d.code, d)]

    # 買い手側の開示から、名証上場の「TOB対象」を見つける（対象会社の開示がまだ無い場合に備える）
    for d in [d for d in disclosures if d.pubdate >= window_start]:
        for tg in C.classify(d.title, d.code):
            if tg.cat == C.TOB_REL and tg.label.startswith("他社へのTOB"):
                for tc in C.target_codes(d.title):
                    if tc != d.code and tc in universe:
                        after.append(Disclosure(id=d.id + "-t" + tc, pubdate=d.pubdate, code5=tc + "0" if len(tc) == 4 else tc,
                                                name=universe[tc].name, title=d.title, pdf_url=d.pdf_url,
                                                markets="", extra={"from_acquirer": d.name}))

    # 3) 名証の相場表（約定見込み価格）
    quotes, price_src, err = nse_prices.get_prices(base)
    if err:
        errors.append(err)

    tob_prices, warns = tobmod.load_tob_prices()
    errors += warns

    def make_cards(ds: list[Disclosure]) -> list[Card]:
        groups: dict[str, list[Disclosure]] = {}
        for d in ds:
            groups.setdefault(d.code, []).append(d)
        cards = []
        for code, g in groups.items():
            items, all_tags = [], []
            for d in sorted(g, key=lambda x: x.pubdate):
                tags = C.classify(d.title, code)
                if d.extra.get("from_acquirer"):
                    tags = [C.Tag(C.S, "TOB対象（買い手側の開示から検出）", 100,
                                  f"{d.extra['from_acquirer']}がこの会社へのTOBを発表。対象会社側の開示も確認してください")]
                all_tags += tags
                items.append(Item(time=f"{d.pubdate:%H:%M}", date=f"{d.pubdate:%m/%d}", title=d.title, pdf=d.pdf_url,
                                  tags=[asdict(t) for t in tags]))
            ls = listing(code)
            cat = C.card_category(all_tags)
            card = Card(code=code, name=(ls.name if ls else g[0].name), single=single_of(code, g[0]),
                        division=ls.division if ls else "", unit=ls.unit if ls else 100,
                        category=cat, score=C.card_score(all_tags), items=items)
            cards.append(card)
        return cards

    cards = make_cards(after)
    before_cards = make_cards(before)

    # 4) 値段・株数
    need_yahoo = []
    for c in cards + before_cards:
        q = quotes.get(c.code)
        if q and q.contest_price:
            c.price = q.contest_price
            c.nse_close, c.nse_quote = q.close, q.quote
            if q.quote is not None and q.close is not None and q.quote != q.close:
                c.price_note = f"最終気配 {q.quote:,.0f}円（名証での約定 {q.close:,.0f}円）"
            elif q.quote is not None:
                c.price_note = "名証の最終気配"
            else:
                c.price_note = "名証の終値"
        elif c.single is False:
            need_yahoo.append(c)
        else:
            c.price_note = "株価はコンテスト画面で確認"
    if need_yahoo:
        yc, err = yahoo.closes(sorted({c.code for c in need_yahoo}), base)
        if err:
            errors.append(err)
        for c in need_yahoo:
            if c.code in yc:
                c.price = yc[c.code]
                c.price_note = "東証の終値（名証の相場表が取れないため代用）"
            else:
                c.price_note = "株価はコンテスト画面で確認"
    for c in cards + before_cards:
        c.shares, c.cost = _shares(c.price, c.unit)
        if c.category == C.S:
            c.tob_price = tob_prices.get(c.code)
            if c.tob_price and c.price:
                c.tob_gap_pct = (c.tob_price / c.price - 1) * 100

    # 並び順：S は「TOB価格未入力」→ 差が大きい順。ほかは点数順
    def sort_key(c: Card):
        if c.category == C.S:
            return (0 if c.tob_gap_pct is None else 1, -(c.tob_gap_pct or 0), -c.score)
        return (0, -c.score, c.items[0].time)

    cards.sort(key=sort_key)
    before_cards.sort(key=sort_key)

    # 5) TOBトラッカー
    tob_rows: list[TobRow] = []
    try:
        entries = tobmod.update(disclosures, is_nagoya, lambda c: universe[c].name if c in universe else c)
        for e in sorted(entries.values(), key=lambda x: x.first_date, reverse=True):
            q = quotes.get(e.code)
            price = q.contest_price if q else None
            tp = tob_prices.get(e.code)
            gap = (tp / price - 1) * 100 if (tp and price) else None
            tob_rows.append(TobRow(code=e.code, name=e.name, single=single_of(e.code, None), first_date=e.first_date,
                                   status=e.status, events=e.events, price=price,
                                   price_note=("名証" if q else "株価はコンテスト画面で確認"), tob_price=tp, gap_pct=gap,
                                   sell_signal=(gap is not None and gap <= TOB_SELL_GAP_PCT)))
    except Exception as e:  # noqa: BLE001
        errors.append(f"TOBトラッカーの更新に失敗: {type(e).__name__}: {e}")

    single_count = sum(1 for x in universe.values() if x.single)
    notes.append("TDnetのPDF/XBRLは自動では読みません（TDnetのrobots.txtが自動取得を拒否しているため）。"
                 "分類はタイトルの文言から判定しています。修正率・TOB価格などはPDFで確認してください。")

    res = ScanResult(
        generated_at=now.isoformat(), base_day=base.isoformat(), deadline=deadline.isoformat(),
        price_source=price_src, cards=cards, before_close=before_cards, tob_rows=tob_rows,
        errors=errors, notes=notes, universe_count=len(universe), single_count=single_count,
    )

    # 開示一覧がまったく取れなかったときは、同じタームの前回の結果を使う
    today_failed = any(f"（{now:%m/%d}）の取得に失敗" in e and "前回取得分" not in e for e in errs)
    if today_failed and not cards and LAST_SCAN.exists():
        prev = json.loads(LAST_SCAN.read_text(encoding="utf-8"))
        if prev.get("base_day") == res.base_day:
            res.cards = [_card_from(x) for x in prev.get("cards", [])]
            res.stale_from = prev.get("generated_at", "")
    else:
        LAST_SCAN.write_text(json.dumps(asdict(res), ensure_ascii=False, indent=0, default=str), encoding="utf-8")
    return res


def _card_from(d: dict) -> Card:
    d = dict(d)
    d["items"] = [Item(**i) for i in d.get("items", [])]
    return Card(**d)


def base_label(res: ScanResult) -> date:
    return date.fromisoformat(res.base_day)
