"""株数を「単位」で書く（コンテストの注文画面は単位で入力する。売買単位100株なら1,800株＝18単位）。

売買単位は名証の銘柄一覧（data/universe.json の tradingUnit）の値を使う。
"""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _units() -> dict[str, int]:
    try:
        from .sources import nse_list

        universe, _ = nse_list.load(refresh=False)
        return {c: ls.unit for c, ls in universe.items()}
    except Exception:  # noqa: BLE001
        return {}


def unit_of(code: str, fallback: int | None = None) -> int:
    u = _units().get(str(code).strip().upper())
    return u or fallback or 100


def fmt(code: str, shares: int | float, unit: int | None = None) -> str:
    """「18単位（1,800株）」。単位で割り切れないときは注意を付ける。"""
    u = unit_of(code, unit)
    sh = int(shares)
    if sh % u:
        return f"{sh / u:g}単位（{sh:,}株。※売買単位{u:,}株の整数倍ではありません）"
    return f"{sh // u:,}単位（{sh:,}株）"
