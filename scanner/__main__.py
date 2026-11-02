"""実行: python -m scanner"""
from __future__ import annotations

import sys

from .config import DOCS_DIR
from .render import render
from .scan import run


def main() -> int:
    res = run()
    pf = None
    top, sections = "", ""
    try:
        from . import portfolio, render_portfolio

        pf = portfolio.build(res)
        top = render_portfolio.alert(pf)
        sections = render_portfolio.section(pf)
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"保有管理の計算に失敗: {type(e).__name__}: {e}")
    mid = ""
    try:
        from . import render_limits

        mid = render_limits.section(res)
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"ストップ高・安の表示に失敗: {type(e).__name__}: {e}")
    html = render(res, pf, extra_top=top, extra_sections=sections, extra_mid=mid)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")
    counts: dict[str, int] = {}
    for c in res.cards:
        counts[c.category] = counts.get(c.category, 0) + 1
    print(f"基準日 {res.base_day} / 締切 {res.deadline} / 価格: {res.price_source}")
    print("候補:", counts, "/ 15:30前:", len(res.before_close), "/ TOBトラッカー:", len(res.tob_rows))
    for e in res.errors:
        print("注意:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
