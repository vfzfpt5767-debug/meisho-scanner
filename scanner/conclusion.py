"""「今夜の結論」：ページ最上部に出す、今夜の行動のまとめ（3行以内）。"""
from __future__ import annotations

from dataclasses import dataclass

from . import classify as C
from .scan import Card, ScanResult


@dataclass
class Line:
    kind: str  # buy / sell / single / info
    text: str


def _yen(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.1f}円" if v != int(v) else f"{v:,.0f}円"


def _man(v: float | None) -> str:
    return "—" if v is None else f"{v / 10000:,.1f}万円"


def best_buy(res: ScanResult) -> Card | None:
    """買い候補1位（S → A の順。S はTOB価格との差が大きい順）。"""
    s = [c for c in res.cards if c.category == C.S]
    if s:
        with_gap = [c for c in s if c.tob_gap_pct is not None]
        if with_gap:
            return max(with_gap, key=lambda c: c.tob_gap_pct)
        return s[0]
    a = [c for c in res.cards if c.category == C.A]
    return max(a, key=lambda c: c.score) if a else None


def buy_line(res: ScanResult) -> Line:
    c = best_buy(res)
    if c is None:
        n_check = sum(1 for x in res.cards if x.category == C.CHECK)
        if n_check:
            return Line("info", f"買い候補：S・Aはなし。要確認が{n_check}社（PDFで上方修正・増配か確認）")
        return Line("info", "買い候補：今夜の新しい好材料はなし")
    tags = [t for it in c.items for t in it.tags if t["cat"] in (C.S, C.A)]
    labels = [t["label"] for t in tags]
    reason = "・".join(dict.fromkeys(labels)) or C.LABELS.get(c.category, "")
    from .render_backtest import label_note

    main = max(tags, key=lambda t: t["score"])["label"] if tags else ""
    note = label_note(main, short=True) if main else ""
    if c.category == C.S:
        reason += f"・TOB価格との差{c.tob_gap_pct:+.1f}%" if c.tob_gap_pct is not None else "・TOB価格未入力"
    single = "【名証単独】" if c.single else ""
    if c.price and c.shares:
        money = f"終値{_yen(c.price)} → {c.shares:,}株（{_man(c.cost)}）"
    else:
        money = "株価はコンテスト画面で確認"
    bt = f"［検証：{main}は{note}］" if note else ""
    return Line("buy", f"買い候補1位：{c.name}（{c.code}）{single}［{reason}］{money}{bt}")


def lines(res: ScanResult, portfolio=None) -> list[Line]:
    out = [buy_line(res)]
    if portfolio is not None:
        out += portfolio.conclusion_lines()
    return out[:3]
