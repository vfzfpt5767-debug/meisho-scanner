"""保有管理：holdings.csv（保有銘柄）と cash.csv（現金残高）から、評価額・損益・警告・入れ替え案を作る。"""
from __future__ import annotations

import csv
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time

from . import classify as C
from .calendar_jp import base_day, is_business_day, prev_business_day
from .config import (BUDGET_PER_TERM, DATA_DIR, ROOT, TOB_SELL_GAP_PCT)
from .conclusion import Line, best_buy

HOLDINGS_CSV = ROOT / "holdings.csv"
CASH_CSV = ROOT / "cash.csv"
STATE = DATA_DIR / "portfolio_state.json"
START_CAPITAL = 3_000_000
TENTH_LINE_PROFIT = 4_290_000  # 昨年10位の利益（+429万円）


@dataclass
class Holding:
    code: str
    shares: int
    buy_price: float
    buy_date: date
    buy_time: time | None
    memo: str = ""
    # 以下は計算結果
    name: str = ""
    single: bool | None = None
    unit: int = 100
    price: float | None = None
    price_note: str = ""
    quote_only: bool = False
    warnings: list[str] = field(default_factory=list)  # 赤い警告（売り推奨）
    cautions: list[str] = field(default_factory=list)  # 黄色の注意

    @property
    def cost(self) -> float:
        return self.shares * self.buy_price

    @property
    def value(self) -> float:
        return self.shares * (self.price if self.price is not None else self.buy_price)

    @property
    def pnl(self) -> float:
        return self.value - self.cost

    @property
    def pnl_pct(self) -> float:
        return (self.value / self.cost - 1) * 100 if self.cost else 0.0

    def term_base(self) -> date:
        """この買いがどのタームのものか（そのタームの基準日）。"""
        d = self.buy_date
        if self.buy_time is not None and self.buy_time < time(8, 0):
            return prev_business_day(d)  # 朝0:00〜7:59の注文は前の営業日からのターム
        return d if is_business_day(d) else prev_business_day(d)


@dataclass
class Trade:
    side: str  # sell / buy
    code: str
    name: str
    shares: int
    price: float
    reason: str

    @property
    def amount(self) -> float:
        return self.shares * self.price


@dataclass
class Portfolio:
    holdings: list[Holding]
    cash: float | None
    term_base: date
    term_open: bool
    term_bought: float
    single_bought: bool
    single_codes_ever: list[str]
    errors: list[str]
    swap: list[Trade] = field(default_factory=list)
    swap_note: str = ""

    @property
    def market_value(self) -> float:
        return sum(h.value for h in self.holdings)

    @property
    def total(self) -> float | None:
        return None if self.cash is None else self.cash + self.market_value

    @property
    def profit(self) -> float | None:
        return None if self.total is None else self.total - START_CAPITAL

    @property
    def term_left(self) -> float:
        return max(0.0, BUDGET_PER_TERM - self.term_bought)

    @property
    def registered(self) -> bool:
        return bool(self.holdings) or self.cash is not None

    def warned(self) -> list[Holding]:
        return [h for h in self.holdings if h.warnings]

    def conclusion_lines(self) -> list[Line]:
        out: list[Line] = []
        w = self.warned()
        if w:
            out.append(Line("sell", "保有中の売り推奨：" + "、".join(f"{h.name}（{h.code}）［{h.warnings[0]}］" for h in w)))
        elif self.holdings:
            p = self.profit
            ptxt = f"／損益{p / 10000:+,.1f}万円" if p is not None else ""
            out.append(Line("info", f"保有中の売り推奨：なし（{len(self.holdings)}銘柄{ptxt}）"))
        if self.single_bought:
            out.append(Line("info", "単独銘柄：購入済み（入賞条件クリア）"))
        else:
            out.append(Line("sell", "単独銘柄：未購入（入賞条件。11/30までに名証単独銘柄を1回買う）"))
        return out


def _n(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").strip()


def _num(s: str) -> float:
    return float(re.sub(r"[,円株\s]", "", _n(s)))


def _parse_date(s: str) -> tuple[date, time | None]:
    s = _n(s).replace("/", "-").replace(".", "-")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?$", s)
    if not m:
        m2 = re.match(r"^(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?$", s)  # 年なし（11/2）
        if not m2:
            raise ValueError(s)
        y = 2026
        mo, d, hh, mm = m2.groups()
    else:
        y, mo, d, hh, mm = int(m[1]), m[2], m[3], m[4], m[5]
    t = time(int(hh), int(mm)) if hh is not None else None
    return date(int(y), int(mo), int(d)), t


def _rows(path) -> list[list[str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig")
    return [r for r in csv.reader(text.splitlines()) if r and "".join(r).strip() and not _n(r[0]).startswith("#")]


def load_holdings() -> tuple[list[Holding], list[str]]:
    out, errs = [], []
    for i, r in enumerate(_rows(HOLDINGS_CSV), start=1):
        if _n(r[0]).lower() in ("code", "コード"):
            continue
        try:
            d, t = _parse_date(r[3])
            out.append(Holding(code=_n(r[0]).upper(), shares=int(_num(r[1])), buy_price=_num(r[2]),
                               buy_date=d, buy_time=t, memo=_n(r[4]) if len(r) > 4 else ""))
        except (ValueError, IndexError):
            errs.append(f"holdings.csv の{i}行目を読めませんでした（{','.join(r)}）。例: 6999,1000,2002,2026-11-02")
    return out, errs


def load_cash() -> tuple[float | None, bool, list[str]]:
    cash, single, errs = None, False, []
    for r in _rows(CASH_CSV):
        key = _n(r[0]).lower()
        val = _n(r[1]) if len(r) > 1 else ""
        if key == "cash":
            try:
                cash = _num(val)
            except ValueError:
                errs.append(f"cash.csv の現金残高を読めませんでした（{val}）。例: cash,3000000")
        elif key == "single_bought":
            single = val.lower() in ("yes", "y", "true", "1", "はい", "済", "購入済み")
    return cash, single, errs


def build(res, now: datetime | None = None) -> Portfolio:
    ctx = res.ctx
    now = now or ctx.now
    quotes, universe = ctx.quotes, ctx.universe
    holdings, errs = load_holdings()
    cash, single_flag, e2 = load_cash()
    errs += e2

    # 単独銘柄を一度でも持ったことがあるか（売った後も覚えておく）
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    ever = set(state.get("single_codes_ever", []))

    bad_codes: dict[str, str] = {}
    check_codes: dict[str, str] = {}
    for c in res.cards:
        for it in c.items:
            for t in it.tags:
                if t["cat"] == C.BAD:
                    bad_codes.setdefault(c.code, f"悪材料：{t['label']}")
                elif t["cat"] == C.CHECK:
                    check_codes.setdefault(c.code, f"要確認の開示：{t['label']}")
    tob = {r.code: r for r in res.tob_rows}
    limits = getattr(ctx, "limits", {}) or {}

    for h in holdings:
        ls = universe.get(h.code)
        q = quotes.get(h.code)
        h.name = ls.name if ls else (q.name if q else h.code)
        h.single = ls.single if ls else None
        h.unit = ls.unit if ls else 100
        if q and q.contest_price:
            h.price, h.quote_only = q.contest_price, q.quote_only
            h.price_note = "名証の最終気配" if q.quote is not None else "名証の終値"
        else:
            h.price_note = "株価不明（買値で計算）"
        if h.single:
            ever.add(h.code)
        if h.code in bad_codes:
            h.warnings.append(bad_codes[h.code])
        r = tob.get(h.code)
        if r and r.sell_signal:
            h.warnings.append(f"TOB価格に到達（差{r.gap_pct:+.1f}%）")
        lim = limits.get(h.code)
        if lim and lim.kind == "stop_low":
            h.warnings.append("ストップ安")
        if h.code in check_codes:
            h.cautions.append(check_codes[h.code])
        if h.single and h.quote_only:
            h.cautions.append("名証単独で今日は気配のみ（約定なし）。最終日11/30の評価額がブレやすいので注意")
        if r and not r.sell_signal:
            h.cautions.append("TOB対象（TOB価格に届くまで保有の候補）")

    state["single_codes_ever"] = sorted(ever)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    term = base_day(now)
    term_open = not (is_business_day(now.date()) and time(8, 0) <= now.time() < time(16, 30))
    bought = sum(h.cost for h in holdings if h.term_base() == term) if term_open else 0.0
    pf = Portfolio(holdings=holdings, cash=cash, term_base=term, term_open=term_open, term_bought=bought,
                   single_bought=single_flag or bool(ever), single_codes_ever=sorted(ever), errors=errs)
    _swap(pf, res)
    return pf


def _swap(pf: Portfolio, res) -> None:
    """「売り→買い」の入れ替え案。売買単位の整数倍、今タームの残り枠の範囲内で計算する。"""
    c = best_buy(res)
    if c is None or not c.price:
        pf.swap_note = "今夜の買い候補（S・A）がないため、入れ替え案はありません"
        return
    if pf.cash is None:
        pf.swap_note = "cash.csv に現金残高を書くと、入れ替え案を計算します"
        return
    if not pf.term_open:
        pf.swap_note = "今は注文できない時間です（次のタームは16:30から）"
        return
    lot = c.price * c.unit
    budget = pf.term_left
    trades: list[Trade] = []
    cash = pf.cash
    # 1) 警告が出ている保有銘柄は全部売る
    for h in pf.warned():
        if h.code == c.code or not h.price:
            continue
        trades.append(Trade("sell", h.code, h.name, h.shares, h.price, h.warnings[0]))
        cash += h.shares * h.price
    # 2) まだ枠いっぱいに買えないなら、ほかの保有銘柄を損益率の低い順に必要な分だけ売る
    target = min(budget, cash)
    if cash < budget:
        others = sorted([h for h in pf.holdings if h not in pf.warned() and h.code != c.code and h.price],
                        key=lambda h: h.pnl_pct)
        for h in others:
            need = budget - cash
            if need < lot:
                break
            units = min(h.shares // h.unit, math.ceil(need / (h.price * h.unit)))
            if units <= 0:
                continue
            n = units * h.unit
            trades.append(Trade("sell", h.code, h.name, n, h.price, f"資金を作るため（損益{h.pnl_pct:+.1f}%）"))
            cash += n * h.price
        target = min(budget, cash)
    n_buy = int(target // lot) * c.unit
    if n_buy <= 0:
        pf.swap_note = f"今タームの残り枠（{budget:,.0f}円）または現金では{c.name}を1単元も買えません"
        pf.swap = [t for t in trades if t.reason and not t.reason.startswith("資金")]
        return
    trades.append(Trade("buy", c.code, c.name, n_buy, c.price, f"買い候補1位（{C.LABELS.get(c.category, '')}）"))
    pf.swap = trades
    pf.swap_note = (f"買い {n_buy:,}株 × {c.price:,.0f}円 = {n_buy * c.price:,.0f}円"
                    f"（今タームの残り枠 {budget:,.0f}円、売却後の現金 {cash:,.0f}円）")
