"""入賞確率シミュレーター。

過去の「名証上場銘柄の夜ごとの候補と翌日の騰落」（reports/sim_nights.json）から夜をランダムに選び、
残りの売買回数ぶん繰り返して、10位ライン（元手300万円＋最終日の10位の見込み。config.TOP10_LINE_FINAL_EST）を超える確率を戦略ごとに比べる。
今の10位（公式サイトの途中経過。config.TOP10_LINE_NOW）を超える確率も出す。
毎晩、その夜の終値で買って翌営業日の終値で売る前提。1タームに買えるのは300万円まで（資産が300万円未満ならその額まで）。
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, datetime, time

from .calendar_jp import is_business_day, next_business_day
from .config import BUDGET_PER_TERM, CONTEST_END, ROOT, STRATEGY_MODE, TOP10_LINE_DATE, TOP10_LINE_FINAL_EST, TOP10_LINE_NOW

NIGHTS = ROOT / "reports" / "sim_nights.json"
TARGET = 3_000_000 + TOP10_LINE_FINAL_EST
TARGET_NOW = 3_000_000 + TOP10_LINE_NOW
RUNS = 20_000
GOOD = ("S", "A", "STUCK")
STRATEGIES = {
    "aggressive": "毎晩1位候補に全額（要確認だけの夜も買う）",
    "split": "毎晩上位2銘柄に半分ずつ",
    "selective": "S・A・ストップ高張り付きがある夜だけ1位に全額、ない夜は休む",
    "selective_split": "S・A・張り付きがある夜だけ、上位2銘柄に半分ずつ",
}


@dataclass
class SimResult:
    nights_left: int
    start_assets: float
    probs: dict[str, float]
    medians: dict[str, float]
    best: str
    advice: str
    probs_now: dict[str, float] | None = None  # 今の10位（途中経過）を超える確率


def nights_left(now: datetime, base: date) -> int:
    """今のタームを含めて、あと何回「買って翌営業日に売る」ができるか（最後は11/30の前の営業日に買う）。"""
    term_open = not (is_business_day(now.date()) and time(8, 0) <= now.time() < time(16, 30))
    d = base if term_open else next_business_day(base)
    n = 0
    while d < CONTEST_END:
        if next_business_day(d) <= CONTEST_END:
            n += 1
        d = next_business_day(d)
    return n


def _night_return(strategy: str, cands: list[dict]) -> float:
    if not cands:
        return 0.0
    if strategy in ("selective", "selective_split"):
        cands = [c for c in cands if c["kind"] in GOOD]
        if not cands:
            return 0.0
    if strategy in ("split", "selective_split") and len(cands) >= 2:
        return (cands[0]["r1"] + cands[1]["r1"]) / 2
    return cands[0]["r1"]


def simulate(start_assets: float, n_nights: int, runs: int = RUNS, seed: int = 7) -> SimResult:
    nights = json.loads(NIGHTS.read_text(encoding="utf-8"))
    rng = random.Random(seed)
    probs, medians, probs_now = {}, {}, {}
    for st in STRATEGIES:
        finals = []
        hit = hit_now = 0
        for _ in range(runs):
            a = start_assets
            for _ in range(n_nights):
                r = _night_return(st, rng.choice(nights)["cands"])
                a += min(BUDGET_PER_TERM, a) * r
            finals.append(a)
            hit += a >= TARGET
            hit_now += a >= TARGET_NOW
        finals.sort()
        probs[st] = hit / runs * 100
        probs_now[st] = hit_now / runs * 100
        medians[st] = finals[len(finals) // 2]
    best = max(probs, key=lambda k: (probs[k], medians[k]))
    if start_assets >= TARGET:
        advice = "すでに10位ラインを超えています。守り（好材料がない夜は休む）で十分"
        best = "selective"
    elif STRATEGY_MODE == "top10":
        # 勝負モード（2026-11-12 ユーザー指定）：休む夜を作らない。確率が低くても、一番高くなるやり方を続ける
        best = "aggressive"
        advice = (f"勝負モード：毎晩、大きく上がる可能性が一番高い候補に300万円の枠いっぱい（休む夜を作らない）。"
                  f"10位ラインの見込みに届く確率 {probs[best]:.1f}%・今の10位を超える確率 {probs_now[best]:.1f}%")
    elif probs[best] < 0.5:
        advice = f"どの戦略でも10位ラインに届く確率は低い（最大{probs[best]:.1f}%）。一番ましなのは「{STRATEGIES[best]}」"
    else:
        advice = f"今夜は「{STRATEGIES[best]}」（10位ラインを超える確率 {probs[best]:.1f}%）"
    return SimResult(n_nights, start_assets, probs, medians, best, advice, probs_now)


def run_for(res, pf) -> SimResult | None:
    if not NIGHTS.exists():
        return None
    ctx = res.ctx
    start = pf.total if (pf is not None and pf.total) else 3_000_000
    n = nights_left(ctx.now, ctx.base)
    if n <= 0:
        return None
    return simulate(start, n)


def section(r: SimResult) -> str:
    from html import escape

    rows = "".join(
        f'<div class="reason"><b>{r.probs[k]:.1f}%</b>{"（★おすすめ）" if k == r.best else ""}：{escape(v)}'
        f'<span class="sub">（今の10位 {TARGET_NOW / 10000:,.0f}万円を超える確率 {(r.probs_now or {}).get(k, 0):.1f}%・'
        f'最終資産の中央値 {r.medians[k] / 10000:,.0f}万円）</span></div>' for k, v in STRATEGIES.items())
    return (f'<h2 id="sim">入賞確率シミュレーター</h2><div class="meta"><div class="big"><b>{escape(r.advice)}</b></div>'
            f'<div class="sub">今の資産 {r.start_assets / 10000:,.0f}万円・残り{r.nights_left}回の売買で、10位ラインの見込み（{TARGET / 10000:,.0f}万円）を'
            f'超える確率（{RUNS:,}回のシミュレーション）。過去の名証上場銘柄の候補と翌日の騰落から計算。'
            f'1タームの買いは300万円までなので、1晩の利益は最大でも「300万円×その日の騰落」</div>{rows}</div>')


def summary(r: SimResult) -> dict:
    return {"nights_left": r.nights_left, "start_assets": r.start_assets, "target": TARGET, "best": r.best,
            "best_label": STRATEGIES[r.best], "advice": r.advice, "mode": STRATEGY_MODE,
            "target_now": TARGET_NOW, "target_now_date": TOP10_LINE_DATE,
            "probs": {STRATEGIES[k]: round(v, 2) for k, v in r.probs.items()},
            "probs_now": {STRATEGIES[k]: round(v, 2) for k, v in (r.probs_now or {}).items()}}
