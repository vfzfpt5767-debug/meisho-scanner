"""自己採点：すべての推奨と翌営業日の結果を記録し、的中率を出す。1週間ごとに点数の重みを少しだけ見直す。

- 記録するもの（タームごと・銘柄ごとに1回）：
  rule_top＝「今夜の結論」の買い候補1位、rule_sa＝S・Aのすべて、ai＝AIアナリストの「買い」
- 結果：翌営業日の名証の値段（最終気配を含む）÷ 推奨時の値段 − 1
- 重みの見直し（data/live_adjust.json）：前回から7日以上たったら、件数5以上のラベルについて
  「実際の平均 − 検証の平均」×10点 × n/(n+50) を足す（±10点まで）。件数が少ないうちはほとんど変わらない。
"""
from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta

from . import classify as C
from .calendar_jp import next_business_day
from .config import DATA_DIR

RECS = DATA_DIR / "track" / "recs.json"
ADJUST = DATA_DIR / "live_adjust.json"
SOURCES = {"rule_top": "ルールの買い候補1位", "rule_sa": "ルールのS・A（すべて）", "ai": "AIアナリストの買い"}


def _load() -> list[dict]:
    try:
        return json.loads(RECS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _save(recs: list[dict]) -> None:
    RECS.parent.mkdir(parents=True, exist_ok=True)
    RECS.write_text(json.dumps(recs, ensure_ascii=False, indent=0), encoding="utf-8")


def _main_label(c) -> str:
    tags = [t for it in c.items for t in it.tags if t["cat"] == c.category]
    return max(tags, key=lambda t: t["score"])["label"] if tags else ""


def record(res) -> None:
    from .conclusion import best_buy

    recs = _load()
    have = {(r["base_day"], r["source"], r["code"]) for r in recs}

    def add(source, code, name, cat, label, price):
        if price and (res.base_day, source, code) not in have:
            recs.append({"base_day": res.base_day, "source": source, "code": code, "name": name, "category": cat,
                         "label": label, "price": price, "result": None})
            have.add((res.base_day, source, code))

    top = best_buy(res)
    if top:
        add("rule_top", top.code, top.name, top.category, _main_label(top), top.price)
    for c in res.cards:
        if c.category in (C.S, C.A):
            add("rule_sa", c.code, c.name, c.category, _main_label(c), c.price)
    try:
        from .render_ai import load as ai_load

        d = ai_load()
        if d and d.get("base_day") == res.base_day:
            for o in d.get("orders", []):
                if o.get("side") == "buy":
                    add("ai", str(o["code"]), o.get("name", ""), "AI", "", float(o["price"]))
    except Exception:  # noqa: BLE001
        pass
    _save(recs)


def evaluate(ctx) -> None:
    """結果が出ていない記録に、翌営業日の値段で結果を入れる。"""
    from .sources import nse_prices

    recs = _load()
    cache: dict = {}
    changed = False
    for r in recs:
        if r["result"] is not None:
            continue
        nd = next_business_day(date.fromisoformat(r["base_day"]))
        if nd > ctx.base:
            continue  # 翌営業日の終値がまだ出ていない
        if nd not in cache:
            cache[nd] = ctx.quotes if nd == ctx.base else nse_prices.get_prices(nd)[0]
        q = cache[nd].get(r["code"])
        if q and q.contest_price:
            r["result"] = q.contest_price / r["price"] - 1
            r["next_day"] = nd.isoformat()
            changed = True
    if changed:
        _save(recs)


def stats() -> dict:
    out = {}
    for src in SOURCES:
        xs = [r["result"] for r in _load() if r["source"] == src and r["result"] is not None]
        if xs:
            out[src] = {"n": len(xs), "up": sum(1 for x in xs if x > 0) / len(xs) * 100,
                        "mean": statistics.fmean(xs) * 100, "median": statistics.median(xs) * 100}
        else:
            out[src] = {"n": 0}
    return out


def weekly_adjust(now: datetime) -> str:
    """7日ごとに、実際の結果で点数を少しだけ見直す。"""
    from .render_backtest import label_stats

    cur = json.loads(ADJUST.read_text(encoding="utf-8")) if ADJUST.exists() else {"updated": "", "adjust": {}}
    if cur.get("updated"):
        last = datetime.fromisoformat(cur["updated"])
        if now - last < timedelta(days=7):
            return ""
    by_label: dict[str, list[float]] = {}
    for r in _load():
        if r["source"] == "rule_sa" and r["result"] is not None and r["label"]:
            by_label.setdefault(r["label"], []).append(r["result"])
    adj = {}
    for lb, xs in by_label.items():
        bt = label_stats(lb)
        if len(xs) < 5 or not bt:
            continue
        n = len(xs)
        delta = (statistics.fmean(xs) * 100 - bt["mean"]) * 10 * n / (n + 50)
        adj[lb] = round(max(-10.0, min(10.0, delta)), 1)
    cur = {"updated": now.isoformat(), "adjust": adj,
           "note": "実際の平均−検証の平均（%）×10点×n/(n+50)。±10点まで。件数5以上のラベルのみ"}
    ADJUST.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"重みを見直しました（{len(adj)}ラベル）"


def section() -> str:
    from html import escape

    st = stats()
    rows = "".join(
        f'<div class="row"><span>{escape(SOURCES[k])}</span><b>'
        + (f"的中率 {v['up']:.0f}%・平均 {v['mean']:+.2f}%・中央値 {v['median']:+.2f}%（{v['n']}件）" if v["n"] else "まだ結果なし")
        + "</b></div>" for k, v in st.items())
    recent = [r for r in _load() if r["result"] is not None][-8:][::-1]
    lst = "".join(f'<div class="reason">{escape(r["base_day"][5:])} {escape(SOURCES[r["source"]])}：{escape(r["name"])}'
                  f'（{escape(r["code"])}）{r["result"] * 100:+.1f}%</div>' for r in recent)
    adj = json.loads(ADJUST.read_text(encoding="utf-8")) if ADJUST.exists() else {}
    adj_txt = ""
    if adj.get("adjust"):
        adj_txt = '<div class="sub">点数の見直し：' + escape("、".join(f"{k} {v:+g}点" for k, v in adj["adjust"].items())) + "</div>"
    return (f'<h2 id="track">自己採点（推奨の翌日の結果）</h2><div class="meta">{rows}'
            f'<div class="sub">的中率＝翌営業日の名証の値段が推奨時より上がった割合。1週間ごとに点数を少しだけ見直します</div>'
            f'{adj_txt}</div>{lst}')
