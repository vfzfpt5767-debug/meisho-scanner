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
    nl_rows, earnings_today = [], []
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
        from .line_actions import write_status

        write_status(pf)  # LINEで「残高」と送ったときの返事（data/status.json）
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"保有管理の計算に失敗: {type(e).__name__}: {e}")
    sim_html = ""
    try:
        from . import simulator

        sim = simulator.run_for(res, pf)
        if sim is not None:
            res.simulator = simulator.summary(sim)
            sim_html = simulator.section(sim)
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"入賞確率シミュレーターでエラー: {type(e).__name__}: {e}")
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
        earnings_today = [{"code": e.code, "name": e.name, "kind": e.kind} for e in by_day.get(days[0].isoformat(), [])] \
            if days else []
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"決算カレンダーの表示に失敗: {type(e).__name__}: {e}")
    try:
        from . import track

        track.evaluate(res.ctx)
        track.record(res)
        msg = track.weekly_adjust(res.ctx.now)
        if msg:
            print(msg)
        sections += track.section()
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"自己採点でエラー: {type(e).__name__}: {e}")
    try:
        from . import render_backtest

        sections += render_backtest.section()
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"検証結果の表示に失敗: {type(e).__name__}: {e}")
    html = render(res, pf, extra_top=top, extra_sections=sections, extra_mid=mid, morning=morning_rep,
                  after_concl=morning_html + sim_html)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")

    # AIアナリストへの入力と、起動するかどうか（実際の起動は push の後に scanner.ai_fire）
    try:
        from . import ai_input

        data = ai_input.build(res, pf, morning_rep, nl_rows, earnings_today, getattr(res, "simulator", None))
        req = ai_input.plan_fire(res, pf, data)
        print("AI:", "起動する" if req["fire"] else "起動しない", req["reason"])
        stuck = ai_input.check_stuck(res)
        if stuck:
            print("AI:", stuck)
    except Exception as e:  # noqa: BLE001
        print("AI入力の作成でエラー:", type(e).__name__, e)

    # LINE通知（朝：朝レポートの要約を1日1回）
    if morning_rep is not None and not morning_rep.stale_from:
        try:
            from datetime import datetime as _dt

            from . import morning, notify

            print("LINE:", notify.notify_once(f"morning:{res.ctx.now.date()}",
                                              morning.line_text(morning_rep, _dt.fromisoformat(res.deadline)), "morning"))
        except Exception as e:  # noqa: BLE001
            print("LINE通知でエラー:", type(e).__name__, e)
    # LINE通知（夜：最初の1通＋新しいS・A・保有警告の追加は1晩2通まで）
    if not getattr(res, "ctx", None) or res.ctx.now.hour >= 8:
        try:
            from . import notify

            print("LINE:", notify.evening_notify(res, pf))
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
