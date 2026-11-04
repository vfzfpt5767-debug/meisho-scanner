"""AIアナリストに渡す入力（data/analyst_input.json）を作り、起動（fire）するか決める。

起動の条件（夜）：新しいS・A・要確認・保有警告が出たとき。1晩に最大3回。
起動は GitHub Actions の「AIアナリストを起動」ステップ（python -m scanner.ai_fire）が、push の後に行う。
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from . import classify as C
from .config import BUDGET_PER_TERM, DATA_DIR
from .render_backtest import label_stats

INPUT = DATA_DIR / "analyst_input.json"
FIRE_REQ = DATA_DIR / "ai" / "fire_request.json"
FIRE_STATE = DATA_DIR / "ai" / "fire_state.json"
MAX_FIRES_PER_NIGHT = 3

RULES = {
    "prefer": "検証で上がった割合60%以上かつ平均+2%以上の材料を優先する",
    "unverified": "PDFや報道で内容を確認できないものは買わない",
    "single": "名証単独銘柄が未購入なら、条件を満たす安全な候補も提案する",
    "budget": f"1ターム（営業日16:30〜翌営業日7:59）の買いは合計{BUDGET_PER_TERM:,}円まで。売りは無制限",
    "unit": "株数は売買単位（unit）の整数倍",
    "price": "約定価格は名証の直近の終値（最終気配を含む）。price がその値",
    "search": "Web検索は1銘柄2〜3回まで。調査は短く",
}


def _bt(label: str) -> dict | None:
    st = label_stats(label)
    if not st:
        return None
    return {k: round(st[k], 2) for k in ("up", "mean", "median", "p10") if k in st} | {"n": st["n"]}


def _card(c) -> dict:
    return {
        "code": c.code, "name": c.name, "category": c.category, "score": c.score, "single": c.single,
        "price": c.price, "price_note": c.price_note, "quote_only": c.quote_only, "unit": c.unit,
        "shares_for_budget": c.shares, "tob_price": c.tob_price, "tob_gap_pct": c.tob_gap_pct,
        "model_prob": getattr(c, "model_prob", None),
        "items": [{"time": f"{it.date} {it.time}", "title": it.title, "pdf": it.pdf,
                   "labels": [{"cat": t["cat"], "label": t["label"], "backtest_next_day": _bt(t["label"])}
                              for t in it.tags if t["label"] != "その他"]} for it in c.items],
    }


def build(res, pf=None, morning=None, new_listings=None, earnings_today=None, simulator=None) -> dict:
    ctx = res.ctx
    cards = [c for c in res.cards if c.category in (C.S, C.A, C.CHECK)][:12]
    limits = sorted(ctx.limits.values(), key=lambda h: (h.kind != "stop_high", not h.stuck, -h.change_pct))[:10]
    out = {
        "generated_at": res.generated_at, "base_day": res.base_day, "deadline": res.deadline,
        "price_source": res.price_source, "rules": RULES,
        "candidates": [_card(c) for c in cards],
        "other_after_close_count": sum(1 for c in res.cards if c.category not in (C.S, C.A, C.CHECK)),
        "stop_limits": [{"code": h.code, "name": h.name, "kind": h.kind, "stuck": h.stuck, "label": h.label,
                         "price": h.price, "base": h.base, "change_pct": round(h.change_pct, 2),
                         "single": (ctx.universe[h.code].single if h.code in ctx.universe else None),
                         "unit": (ctx.universe[h.code].unit if h.code in ctx.universe else 100)} for h in limits],
        "tob_tracker": [{"code": r.code, "name": r.name, "status": r.status, "price": r.price, "tob_price": r.tob_price,
                         "gap_pct": r.gap_pct} for r in res.tob_rows],
        "errors": res.errors,
    }
    if pf is not None:
        out["portfolio"] = {
            "cash": pf.cash, "total": pf.total, "profit": pf.profit, "term_left": pf.term_left,
            "term_open": pf.term_open, "single_bought": pf.single_bought,
            "holdings": [{"code": h.code, "name": h.name, "shares": h.shares, "buy_price": h.buy_price,
                          "buy_date": h.buy_date.isoformat(), "price": h.price, "pnl_pct": round(h.pnl_pct, 2),
                          "single": h.single, "quote_only": h.quote_only, "unit": h.unit,
                          "warnings": h.warnings, "notices": h.notices, "cautions": h.cautions, "sell_rule": h.sell_rule,
                          "material": h.material} for h in pf.holdings],
        }
    if morning is not None:
        out["morning"] = {"direction": morning.direction, "futures_gap_pct": morning.futures_gap_pct,
                          "indices": [vars(x) for x in morning.indices],
                          "adr_top": [vars(a) for a in morning.adrs if a.traded and a.gap_pct is not None][:8]}
    if new_listings:
        out["new_listings"] = [{"code": r.item.code, "name": r.item.name, "date": r.item.date, "status": r.status,
                                "single": r.single, "offer_price": r.item.offer_price,
                                "first_day_price": r.first_day_price, "price_now": r.price_now} for r in new_listings[:5]]
    if earnings_today:
        out["earnings_today"] = earnings_today
    if simulator:
        out["simulator"] = simulator
    INPUT.parent.mkdir(parents=True, exist_ok=True)
    INPUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return out


def keys_of(res, pf) -> dict[str, str]:
    """起動のきっかけになる項目（キー→説明）。"""
    keys = {}
    for c in res.cards:
        if c.category in (C.S, C.A, C.CHECK):
            keys[f"{c.category}:{c.code}"] = f"{C.LABELS[c.category].split('：')[0]} {c.name}({c.code})"
    for h in (pf.warned() if pf else []):
        keys[f"W:{h.code}"] = f"保有警告 {h.name}({h.code})"
    for h in (pf.holdings if pf else []):
        if h.notices:
            keys[f"N:{h.code}"] = f"保有の注意（決算が近い） {h.name}({h.code})"
    return keys


def plan_fire(res, pf, data: dict) -> dict:
    """新しいキーがあれば起動の依頼を data/ai/fire_request.json に書く（実際の起動は push の後）。"""
    now = datetime.fromisoformat(res.generated_at)
    st = json.loads(FIRE_STATE.read_text(encoding="utf-8")) if FIRE_STATE.exists() else {}
    night = st.get(res.base_day, {"fires": 0, "keys": []})
    keys = keys_of(res, pf)
    new = {k: v for k, v in keys.items() if k not in night["keys"]}
    req = {"fire": False, "base_day": res.base_day, "new_keys": sorted(new), "reason": ""}
    if os.environ.get("SKIP_AI") == "true":
        req["reason"] = "AIの判断の取り込み・LINEの記録のあとのページ作り直しなので起動しない"
    elif 4 <= now.hour < 8:
        req["reason"] = "朝はAIを起動しない（夜に注文して寝る運用。夜21:15に定時起動）"
    elif not new:
        req["reason"] = "新しいS・A・要確認・保有警告なし"
    elif night["fires"] >= MAX_FIRES_PER_NIGHT:
        req["reason"] = f"今夜の起動は{MAX_FIRES_PER_NIGHT}回に達した"
    else:
        req.update(fire=True, reason="新しい項目: " + "、".join(new.values()))
        text = json.dumps(data, ensure_ascii=False, default=str)
        req["text"] = ("起動理由：" + req["reason"] + "\n入力（data/analyst_input.json と同じ）：\n" + text)[:60000]
    FIRE_REQ.parent.mkdir(parents=True, exist_ok=True)
    FIRE_REQ.write_text(json.dumps(req, ensure_ascii=False, indent=1), encoding="utf-8")
    return req


def check_stuck(res) -> str:
    """AIを起動したのに1時間以上たっても判断が届かないとき、LINEで1回だけ知らせる。"""
    from datetime import timedelta

    from .ai_decision import LATEST
    from .notify import notify_once

    st = json.loads(FIRE_STATE.read_text(encoding="utf-8")) if FIRE_STATE.exists() else {}
    night = st.get(res.base_day, {})
    if not night.get("last_fire_at"):
        return ""
    try:
        latest = json.loads(LATEST.read_text(encoding="utf-8"))
        if latest.get("base_day") == res.base_day and latest.get("created_at", "") >= night["last_fire_at"][:16]:
            return ""
    except Exception:  # noqa: BLE001
        pass
    now = datetime.fromisoformat(res.generated_at)
    if now - datetime.fromisoformat(night["last_fire_at"]) < timedelta(hours=1):
        return ""
    return notify_once(f"ai_stuck:{res.base_day}:{night['last_fire_at']}",
                       "【名証スキャナー】AIアナリストを起動しましたが、1時間たっても判断が届いていません。"
                       "claude.ai/code の実行履歴を確認してください。ページのスキャン結果は通常どおり見られます。", "failure")


def mark_handled(base_day: str) -> list[str]:
    """AIの判断を取り込んだとき、そのときの入力にあった候補（S・A・要確認・保有警告）を「調べ済み」にする。

    これで、あとのスキャンが同じ銘柄でもう一度AIを起動しない（新しい開示が出たときだけ起動する）。
    """
    try:
        data = json.loads(INPUT.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if data.get("base_day") != base_day:
        return []
    keys = {f"{c['category']}:{c['code']}" for c in data.get("candidates", []) if c.get("category") in (C.S, C.A, C.CHECK)}
    keys |= {f"W:{h['code']}" for h in data.get("portfolio", {}).get("holdings", []) if h.get("warnings")}
    keys |= {f"N:{h['code']}" for h in data.get("portfolio", {}).get("holdings", []) if h.get("notices")}
    st = json.loads(FIRE_STATE.read_text(encoding="utf-8")) if FIRE_STATE.exists() else {}
    night = st.setdefault(base_day, {"fires": 0, "keys": []})
    night["keys"] = sorted(set(night["keys"]) | keys)
    st = dict(sorted(st.items())[-14:])
    FIRE_STATE.parent.mkdir(parents=True, exist_ok=True)
    FIRE_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    return sorted(keys)
