"""実行: python -m scanner"""
from __future__ import annotations

import sys

from .config import DOCS_DIR
from .render import render
from .scan import run


def main() -> int:
    res = run()
    html = render(res)
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
