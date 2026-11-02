"""ストップ高・ストップ安の検出（JPXの制限値幅表で判定）。

基準値段＝前の営業日の名証の最終気配（なければ終値）。名証で約定があり前日比が出ている銘柄は「終値−前日比」を使う。
制限値幅の臨時拡大や、配当落ち・株式分割による基準値段の調整には対応していない（ずれることがある）。
出典：https://www.jpx.co.jp/equities/trading/domestic/06.html
"""
from __future__ import annotations

from dataclasses import dataclass

# (基準値段の上限（未満）, 制限値幅)
LIMIT_TABLE = [
    (100, 30), (200, 50), (500, 80), (700, 100), (1_000, 150), (1_500, 300), (2_000, 400), (3_000, 500),
    (5_000, 700), (7_000, 1_000), (10_000, 1_500), (15_000, 3_000), (20_000, 4_000), (30_000, 5_000),
    (50_000, 7_000), (70_000, 10_000), (100_000, 15_000), (150_000, 30_000), (200_000, 40_000),
    (300_000, 50_000), (500_000, 70_000), (700_000, 100_000), (1_000_000, 150_000), (1_500_000, 300_000),
    (2_000_000, 400_000), (3_000_000, 500_000), (5_000_000, 700_000), (7_000_000, 1_000_000),
    (10_000_000, 1_500_000), (15_000_000, 3_000_000), (20_000_000, 4_000_000), (30_000_000, 5_000_000),
    (50_000_000, 7_000_000),
]


def limit_width(base: float) -> float:
    for upper, w in LIMIT_TABLE:
        if base < upper:
            return w
    return 10_000_000


@dataclass
class LimitHit:
    code: str
    name: str
    kind: str  # stop_high / stop_low
    base: float
    price: float
    limit_price: float
    stuck: bool  # 張り付き（最終気配が制限値幅いっぱい、または始値＝高値＝安値＝終値）
    label: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    quote: float | None

    @property
    def change_pct(self) -> float:
        return (self.price / self.base - 1) * 100


def detect(today: dict, prev: dict) -> dict[str, LimitHit]:
    """today/prev: {コード: Quote}。ストップ高・安の銘柄を返す。"""
    out: dict[str, LimitHit] = {}
    for code, q in today.items():
        price = q.contest_price
        if price is None:
            continue
        if q.close is not None and q.change is not None:
            base = q.close - q.change
        else:
            p = prev.get(code)
            base = p.contest_price if p else None
        if not base or base <= 0:
            continue
        w = limit_width(base)
        up, down = base + w, base - w
        # 値段は制限値幅の外には出ない。外にあるのは株式分割・権利落ち・値幅の臨時拡大などなので対象外
        if price > up + 0.5 or price < down - 0.5:
            continue
        ohlc_flat = (q.open is not None and q.open == q.high == q.low == q.close)
        if price >= up:
            stuck = (q.quote is not None and q.quote >= up) or (ohlc_flat and q.close >= up)
            label = ("ストップ高張り付き（特別買い気配）" if q.quote is not None and q.quote >= up
                     else "ストップ高張り付き（始値＝高値＝安値＝終値）" if stuck else "ストップ高で引け")
            out[code] = LimitHit(code, q.name, "stop_high", base, price, up, stuck, label,
                                 q.open, q.high, q.low, q.close, q.quote)
        elif price <= down:
            stuck = (q.quote is not None and q.quote <= down) or (ohlc_flat and q.close <= down)
            label = ("ストップ安張り付き（特別売り気配）" if q.quote is not None and q.quote <= down
                     else "ストップ安張り付き" if stuck else "ストップ安で引け")
            out[code] = LimitHit(code, q.name, "stop_low", base, price, down, stuck, label,
                                 q.open, q.high, q.low, q.close, q.quote)
    return out
