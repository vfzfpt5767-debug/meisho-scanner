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
    morning_rep = None
    morning_html = ""
    from .morning import is_morning

    if res.ctx.now and is_morning(res.ctx.now):
        try:
            from datetime import datetime as _dt

            from . import morning, render_morning

            morning_rep = morning.build(res)
            morning_html = render_morning.section(morning_rep, _dt.fromisoformat(res.deadline))
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"朝レポートの作成に失敗: {type(e).__name__}: {e}")
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
    try:
        from . import newlistings, render_newlistings

        nl_rows, nl_errs = newlistings.rows(res)
        res.errors += nl_errs
        mid += render_newlistings.section(nl_rows)
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"新規上場トラッカーの表示に失敗: {type(e).__name__}: {e}")
    try:
        from . import earnings, render_earnings

        today = res.ctx.now.date()
        days, by_day, e_errs = earnings.upcoming(res.ctx.universe, today)
        res.errors += e_errs
        held = {h.code for h in pf.holdings} if pf else set()
        tonight = today  # 今日が営業日なら「今夜」、休業日なら「次の営業日」と表示
        sections += render_earnings.section(days, by_day, res.ctx.universe, held, tonight)
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"決算カレンダーの表示に失敗: {type(e).__name__}: {e}")
    html = render(res, pf, extra_top=top, extra_sections=sections, extra_mid=mid, morning=morning_rep,
                  after_concl=morning_html)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")

    # LINE通知（朝：朝レポートの要約を1日1回）
    if morning_rep is not None and not morning_rep.stale_from:
        try:
            from datetime import datetime as _dt

            from . import morning, notify

            print("LINE:", notify.notify_once(f"morning:{res.ctx.now.date()}",
                                              morning.line_text(morning_rep, _dt.fromisoformat(res.deadline)), "morning"))
        except Exception as e:  # noqa: BLE001
            print("LINE通知でエラー:", type(e).__name__, e)
    # LINE通知（夜：S・A・要確認・保有警告があるときだけ、1タームに1回）
    if not getattr(res, "ctx", None) or res.ctx.now.hour >= 8:
        try:
            from . import notify

            text = notify.evening_text(res, pf)
            print("LINE:", notify.notify_once(f"evening:{res.base_day}", text, "evening") if text else "通知する候補なし")
        except Exception as e:  # noqa: BLE001
            print("LINE通知でエラー:", type(e).__name__, e)
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
