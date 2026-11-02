"""検証の集計。

- 材料（ラベル）ごとの「当日終値→翌営業日終値」「当日終値→2営業日後終値」
  （上がった割合・平均・中央値・下位10%・最大の下落・件数）。全銘柄／名証上場のみ／名証単独のみ
- 何日持つのがよいか：TOB・上方修正・優待新設・ストップ高張り付きについて
  「翌日売り」「2日後売り」「張り付きが続く間は持ち、外れた日の終値で売る」を比較
- TOBの2日目：発表翌日にストップ高張り付きで引けた銘柄を、その値段で買って翌日売る
- ストップ高で引けた銘柄の翌日、日経平均先物と名証上場の大型株の翌日

出力：reports/backtest.md・reports/backtest.json・scanner/weights.json
"""
from __future__ import annotations

import json
import pickle
import statistics
from collections import defaultdict
from datetime import datetime, time

from ..calendar_jp import now_jst
from ..classify import Tag, card_category
from ..config import ROOT
from ..limits import limit_width
from .fetch import BT, OUT
from .prices import LARGE

REPORTS = ROOT / "reports"
WEIGHTS = ROOT / "scanner" / "weights.json"
MIN_N = 30  # これ未満は「参考」
MAX_HOLD = 10  # 「張り付きが続く間は持つ」の最長日数
SCOPES = {"all": "全上場銘柄", "nagoya": "名証上場のみ", "single": "名証単独のみ"}
HOLD_TARGETS = ["TOB（S）", "上方修正", "優待新設・拡充", "ストップ高張り付き"]
RULES = {"d1": "翌日の終値で売る", "d2": "2日後の終値で売る", "stop": "張り付きが続く間は持ち、外れた日の終値で売る"}


def _stats(xs: list[float]) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    p10 = s[max(0, int(len(s) * 0.1) - 1)] if len(s) >= 10 else s[0]
    return {"n": len(xs), "up": sum(1 for x in xs if x > 0) / len(xs) * 100, "mean": statistics.fmean(xs) * 100,
            "median": statistics.median(xs) * 100, "p10": p10 * 100, "min": s[0] * 100}


def _load_prices():
    return pickle.loads((BT / "prices.pkl").read_bytes())


class Series:
    """1銘柄の日足（配列で持つ）。"""

    def __init__(self, df):
        self.dates = [d.date() for d in df.index]
        self.o, self.h, self.l, self.c = (df[k].values for k in ("Open", "High", "Low", "Close"))

    def entry_pos(self, pub: datetime) -> int | None:
        """開示時刻に対する「直前の終値」の位置（15:30以降なら当日、休日・場中なら前の営業日）。"""
        d0 = pub.date()
        after_close = pub.time() >= time(15, 30)
        for i in range(len(self.dates) - 1, -1, -1):
            if self.dates[i] < d0 or (after_close and self.dates[i] == d0):
                return i
        return None

    def stuck_high(self, i: int) -> bool:
        """i日目がストップ高張り付き（始値＝高値＝安値＝終値＝前日終値＋制限値幅）か。"""
        if i <= 0 or i >= len(self.c):
            return False
        base = self.c[i - 1]
        if base <= 0:
            return False
        up = base + limit_width(base)
        return abs(self.c[i] - up) < 0.51 and self.o[i] == self.h[i] == self.l[i] == self.c[i]

    def stop_close(self, i: int) -> bool:
        base = self.c[i - 1] if i > 0 else 0
        return base > 0 and abs(self.c[i] - (base + limit_width(base))) < 0.51

    def ret(self, i: int, j: int) -> float | None:
        if j >= len(self.c) or self.c[i] <= 0:
            return None
        return self.c[j] / self.c[i] - 1

    def ret_follow(self, i: int) -> float | None:
        """i日目の終値で買い、翌日以降、張り付きが続く間は持ち、外れた日の終値で売る。"""
        for k in range(1, MAX_HOLD + 1):
            j = i + k
            if j >= len(self.c):
                return None
            if not self.stuck_high(j):
                return self.ret(i, j)
        return self.ret(i, i + MAX_HOLD)


def _scope_flags(markets: str) -> dict:
    m = markets or ""
    return {"all": True, "nagoya": "名" in m, "single": "名" in m and "東" not in m}


def analyze_disclosures(series: dict[str, Series]) -> dict:
    groups: dict[tuple[str, int], dict] = {}
    markets_of: dict[str, str] = {}
    for line in OUT.open(encoding="utf-8"):
        x = json.loads(line)
        c5 = (x.get("code") or "").strip()
        code = c5[:4] if len(c5) == 5 and c5.endswith("0") else c5
        if x.get("markets"):
            markets_of[code] = x["markets"]
        s = series.get(code)
        if s is None:
            continue
        pos = s.entry_pos(datetime.strptime(x["pub"], "%Y-%m-%d %H:%M:%S"))
        if pos is None or pos + 1 >= len(s.c):
            continue
        r1 = s.ret(pos, pos + 1)
        if r1 is None or abs(r1) > 1.0:  # 株式併合などの異常値
            continue
        g = groups.setdefault((code, pos), {"code": code, "pos": pos, "tags": [], "r1": r1, "r2": s.ret(pos, pos + 2),
                                            "scope": _scope_flags(x.get("markets"))})
        g["tags"] += [Tag(c, lb, 0, "") for c, lb in x["tags"]]

    labels: dict[str, dict] = {}
    cat_of: dict[str, str] = {}
    acc = {sc: defaultdict(lambda: {"r1": [], "r2": []}) for sc in SCOPES}
    cats = {sc: defaultdict(lambda: {"r1": [], "r2": []}) for sc in SCOPES}
    for g in groups.values():
        lbs = {t.label for t in g["tags"] if t.label != "その他"}
        for t in g["tags"]:
            cat_of.setdefault(t.label, t.cat)
        cat = card_category(g["tags"])
        for sc, on in g["scope"].items():
            if not on:
                continue
            for lb in lbs:
                acc[sc][lb]["r1"].append(g["r1"])
                acc[sc][lb]["r2"].append(g["r2"])
            cats[sc][cat]["r1"].append(g["r1"])
            cats[sc][cat]["r2"].append(g["r2"])
    for sc in SCOPES:
        for lb, v in acc[sc].items():
            labels.setdefault(lb, {"cat": cat_of.get(lb, "")})[sc] = {"r1": _stats(v["r1"]), "r2": _stats(v["r2"])}
    cat_stats = {sc: {c: {"r1": _stats(v["r1"]), "r2": _stats(v["r2"])} for c, v in cats[sc].items()} for sc in SCOPES}
    return {"labels": labels, "cats": cat_stats, "n_groups": len(groups), "groups": list(groups.values()),
            "markets_of": markets_of}


def holding_rules(groups: list[dict], series: dict[str, Series]) -> dict:
    """何日持つのがよいか。対象ごとに3つのルールを比べる。"""
    events: dict[str, list[tuple[str, int]]] = {k: [] for k in HOLD_TARGETS}
    for g in groups:
        cats = {t.cat for t in g["tags"]}
        lbs = {t.label for t in g["tags"]}
        if "S" in cats:
            events["TOB（S）"].append((g["code"], g["pos"]))
        if "上方修正" in lbs:
            events["上方修正"].append((g["code"], g["pos"]))
        if "優待新設・拡充" in lbs:
            events["優待新設・拡充"].append((g["code"], g["pos"]))
    for code, s in series.items():
        for i in range(1, len(s.c) - 1):
            if s.stuck_high(i):
                events["ストップ高張り付き"].append((code, i))
    out = {}
    for target, evs in events.items():
        rs = {"d1": [], "d2": [], "stop": []}
        for code, i in evs:
            s = series[code]
            rs["d1"].append(s.ret(i, i + 1))
            rs["d2"].append(s.ret(i, i + 2))
            rs["stop"].append(s.ret_follow(i))
        st = {k: _stats(v) for k, v in rs.items()}
        valid = {k: v for k, v in st.items() if v.get("n", 0) > 0}
        best = max(valid, key=lambda k: valid[k]["mean"]) if valid else ""
        out[target] = {"rules": st, "best": best, "best_label": RULES.get(best, "")}
    return out


def tob_day2(groups: list[dict], series: dict[str, Series]) -> dict:
    """TOB発表の翌日にストップ高張り付きで引けた銘柄を、その値段で買った場合。"""
    r_next, r_follow, n_tob, n_stuck = [], [], 0, 0
    for g in groups:
        if "S" not in {t.cat for t in g["tags"]}:
            continue
        n_tob += 1
        s, i = series[g["code"]], g["pos"]
        if s.stuck_high(i + 1):
            n_stuck += 1
            r_next.append(s.ret(i + 1, i + 2))
            r_follow.append(s.ret_follow(i + 1))
    return {"n_tob": n_tob, "n_stuck_next_day": n_stuck,
            "d1": _stats(r_next), "stop": _stats(r_follow)}


def stop_high(series: dict[str, Series], markets_of: dict[str, str]) -> dict:
    res = {sc: {"stuck_r1": [], "stuck_r2": [], "close_r1": [], "close_r2": [], "low_r1": []} for sc in SCOPES}
    for code, s in series.items():
        flags = _scope_flags(markets_of.get(code, ""))
        for i in range(1, len(s.c) - 1):
            base = s.c[i - 1]
            if base <= 0:
                continue
            w = limit_width(base)
            kind = None
            if abs(s.c[i] - (base + w)) < 0.51:
                kind = "stuck" if s.stuck_high(i) else "close"
            elif abs(s.c[i] - (base - w)) < 0.51:
                kind = "low"
            if not kind:
                continue
            for sc, on in flags.items():
                if not on:
                    continue
                res[sc][f"{kind}_r1"].append(s.ret(i, i + 1))
                if kind != "low":
                    res[sc][f"{kind}_r2"].append(s.ret(i, i + 2))
    return {sc: {k: _stats(v) for k, v in d.items()} for sc, d in res.items()}


def futures(prices) -> dict:
    nk, fut = prices.get("^N225"), prices.get("NIY=F")
    if nk is None or fut is None or not len(nk) or not len(fut):
        return {}
    nk_c = {d.date(): v for d, v in zip(nk.index, nk["Close"].values)}
    fu_c = {d.date(): v for d, v in zip(fut.index, fut["Close"].values)}
    days = sorted(nk_c)
    large = {c: {d.date(): v for d, v in zip(prices[f"{c}.T"].index, prices[f"{c}.T"]["Close"].values)}
             for c in LARGE if f"{c}.T" in prices and len(prices[f"{c}.T"])}
    rows = []
    for i in range(len(days) - 1):
        t, t1 = days[i], days[i + 1]
        if t not in fu_c:
            continue
        gap = fu_c[t] / nk_c[t] - 1
        rets = [v[t1] / v[t] - 1 for v in large.values() if t in v and t1 in v and v[t] > 0]
        if rets:
            rows.append((gap, statistics.fmean(rets), nk_c[t1] / nk_c[t] - 1))
    if len(rows) < 10:
        return {}
    gaps, avg_r, nk_r = zip(*rows)
    buckets = [("−1%以下", -9, -0.01), ("−1%〜−0.3%", -0.01, -0.003), ("−0.3%〜+0.3%", -0.003, 0.003),
               ("+0.3%〜+1%", 0.003, 0.01), ("+1%以上", 0.01, 9)]
    out = {"n": len(rows), "corr_large": _corr(gaps, avg_r), "corr_nikkei": _corr(gaps, nk_r), "buckets": []}
    for name, lo, hi in buckets:
        out["buckets"].append({"name": name, **_stats([r for g, r, _ in rows if lo <= g < hi])})
    return out


def _corr(a, b) -> float:
    try:
        return statistics.correlation(list(a), list(b))
    except Exception:  # noqa: BLE001
        return float("nan")


def nse_period() -> dict:
    """名証の日報（コンテストと同じ値段＝最終気配）で計算する。サーバーにある約30営業日分だけ。

    名証単独の銘柄はYahooに株価がないので、名証単独の集計はこちらで行う。
    """
    from ..config import CACHE_DIR, DATA_DIR
    from ..limits import detect
    from ..sources.nse_prices import Quote

    files = sorted((CACHE_DIR / "nse_prices").glob("*.json"))
    days, quotes = [], {}
    for f in files:
        j = json.loads(f.read_text(encoding="utf-8"))
        dd = datetime.fromisoformat(j["date"]).date()
        days.append(dd)
        quotes[dd] = {k: Quote(**v) for k, v in j["quotes"].items()}
    if len(days) < 3:
        return {}
    uni = json.loads((DATA_DIR / "universe.json").read_text(encoding="utf-8"))
    singles = {c[:4] if len(c) == 5 and c.endswith("0") else c for c in uni.get("single_codes", [])}
    price = {dd: {k: q.contest_price for k, q in qs.items()} for dd, qs in quotes.items()}

    def r(code, i, j):
        if j >= len(days):
            return None
        a, b = price[days[i]].get(code), price[days[j]].get(code)
        if not a or not b:
            return None
        if j == i + 1 and abs(b - a) > limit_width(a) + 0.5:
            return None  # 1日の値幅制限を超える＝株式分割・併合などの調整なので除外
        return b / a - 1

    acc = {"nagoya": defaultdict(list), "single": defaultdict(list)}
    seen = set()
    for line in OUT.open(encoding="utf-8"):
        x = json.loads(line)
        c5 = (x.get("code") or "").strip()
        code = c5[:4] if len(c5) == 5 and c5.endswith("0") else c5
        pub = datetime.strptime(x["pub"], "%Y-%m-%d %H:%M:%S")
        if pub.date() < days[0] or code not in price[days[0]]:
            continue
        pos = None
        for i in range(len(days) - 1, -1, -1):
            if days[i] < pub.date() or (pub.time() >= time(15, 30) and days[i] == pub.date()):
                pos = i
                break
        if pos is None:
            continue
        r1 = r(code, pos, pos + 1)
        if r1 is None:
            continue
        for lb in {lb for _c, lb in x["tags"] if lb != "その他"}:
            if (code, pos, lb) in seen:
                continue
            seen.add((code, pos, lb))
            acc["nagoya"][lb].append(r1)
            if code in singles:
                acc["single"][lb].append(r1)
    stops = {"nagoya": defaultdict(list), "single": defaultdict(list)}
    for i in range(1, len(days) - 1):
        for code, h in detect(quotes[days[i]], quotes[days[i - 1]]).items():
            kind = ("stuck" if h.stuck else "close") if h.kind == "stop_high" else "low"
            v = r(code, i, i + 1)
            stops["nagoya"][kind].append(v)
            if code in singles:
                stops["single"][kind].append(v)
    return {"period": f"{days[0]:%Y/%m/%d}〜{days[-1]:%Y/%m/%d}", "n_days": len(days),
            "labels": {sc: {lb: _stats(v) for lb, v in d.items()} for sc, d in acc.items()},
            "stops": {sc: {k: _stats(v) for k, v in d.items()} for sc, d in stops.items()}}


def weights(labels: dict) -> dict:
    """件数30以上の好材料・要確認ラベルに、翌日の平均騰落率から点数をつける（全上場銘柄の結果）。"""
    w = {}
    for lb, v in labels.items():
        st = v.get("all", {}).get("r1", {})
        if v["cat"] not in ("A", "CHECK") or st.get("n", 0) < MIN_N:
            continue
        w[lb] = int(max(5, min(90, round(30 + 10 * st["mean"]))))
    return w


def main() -> None:
    prices = _load_prices()
    series = {t[:-2]: Series(df) for t, df in prices.items() if t.endswith(".T") and len(df) >= 5}
    days = sorted(set((BT / "fetched_days.txt").read_text().split()))
    d = analyze_disclosures(series)
    hold = holding_rules(d["groups"], series)
    t2 = tob_day2(d["groups"], series)
    sh = stop_high(series, d["markets_of"])
    fu = futures(prices)
    nse = nse_period()
    w = weights(d["labels"])
    REPORTS.mkdir(exist_ok=True)
    fmt = lambda x: f"{x[:4]}/{x[4:6]}/{x[6:]}"  # noqa: E731
    result = {"period": f"{fmt(days[0])}〜{fmt(days[-1])}" if days else "",
              "generated": now_jst().strftime("%Y-%m-%d %H:%M JST"), "min_n": MIN_N,
              "disclosures": {"labels": d["labels"], "cats": d["cats"], "n_groups": d["n_groups"]},
              "holding": hold, "tob_day2": t2, "stop_high": sh, "futures": fu, "nse": nse, "weights": w}
    (REPORTS / "backtest.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    WEIGHTS.write_text(json.dumps(w, ensure_ascii=False, indent=1), encoding="utf-8")
    md = markdown(result)
    (REPORTS / "backtest.md").write_text(md, encoding="utf-8")
    print(md)


# ---------------- 表の文字 ----------------

def _f(st: dict, full: bool = True) -> str:
    if not st or not st.get("n"):
        return "— | — | — | — | — | 0" if full else "— | — | — | 0"
    if full:
        return (f"{st['up']:.0f}% | {st['mean']:+.2f}% | {st['median']:+.2f}% | {st['p10']:+.1f}% | "
                f"{st['min']:+.1f}% | {st['n']}")
    return f"{st['up']:.0f}% | {st['mean']:+.2f}% | {st['median']:+.2f}% | {st['n']}"


def _ref(st: dict, min_n: int) -> str:
    return "" if st.get("n", 0) >= min_n else "（参考）"


ORDER = {"S": 0, "A": 1, "CHECK": 2, "BAD": 3, "SELF_TOB": 4, "TOB_REL": 5, "INFO": 6}
HEAD_FULL = "上がった割合 | 平均 | 中央値 | 下位10% | 最大の下落 | n"
SEP_FULL = "---|---|---|---|---|---"


def markdown(r: dict) -> str:
    d, sh, fu, mn = r["disclosures"], r["stop_high"], r["futures"], r["min_n"]
    L = ["# 検証結果（バックテスト）", "",
         f"- 期間：{r['period']}（平日のみ。開示一覧＝やのしんWEB-API、全上場銘柄。株価＝Yahoo Financeの東証の終値）",
         f"- 作成：{r['generated']}",
         "- 買い方：開示の直前の終値（15:30以降の開示ならその日の終値）で買う。コンテストの「引け後の材料をその夜に終値で買う」と同じ形",
         "- 用語：**中央値**＝真ん中の値（大当たりに引っ張られにくい）。**下位10%**＝悪いほうから10%目の値（悪いときはこれくらい下がる）。**最大の下落**＝いちばん悪かった1件",
         f"- **件数（n）が{mn}未満のものは「参考」**（偶然の影響が大きい）",
         "- 注意：株価は東証の終値。名証の最終気配とは少し違うことがある。同じ銘柄・同じ日に複数の開示があれば1件にまとめ、各ラベルに1回ずつ数えた。"
         "Yahooの値段は約定した値段だけなので、売買が成立しないまま特別買い気配で終わった日は張り付きとして数えられていない",
         "", f"## 1. 材料（ラベル）ごと・全上場銘柄（{d['n_groups']:,}件＝銘柄×日）", "",
         f"| ラベル | 区分 | 翌日：{HEAD_FULL} | 2日後：上がった割合 | 平均 | 中央値 | n |",
         f"|---|---|{SEP_FULL}|---|---|---|---|"]
    labels = sorted(d["labels"].items(), key=lambda kv: (ORDER.get(kv[1]["cat"], 9), -kv[1].get("all", {}).get("r1", {}).get("n", 0)))
    for lb, v in labels:
        a = v.get("all")
        if not a:
            continue
        L.append(f"| {lb}{_ref(a['r1'], mn)} | {v['cat']} | {_f(a['r1'])} | {_f(a['r2'], False)} |")
    for sc in ("nagoya", "single"):
        L += ["", f"## 2. 材料ごと・{SCOPES[sc]}（翌日）", "",
              "開示時点の上場取引所欄で判定（名証単独＝「名」を含み「東」を含まない）。件数が少ないので参考程度に。", "",
              f"| ラベル | 区分 | {HEAD_FULL} |", f"|---|---|{SEP_FULL}|"]
        rows = [(lb, v) for lb, v in labels if v.get(sc)]
        for lb, v in rows:
            L.append(f"| {lb}{_ref(v[sc]['r1'], mn)} | {v['cat']} | {_f(v[sc]['r1'])} |")
        if not rows:
            L.append("| なし | | | | | | | |")
    L += ["", "## 3. 区分（カード）ごと（翌日）", "", f"| 区分 | 範囲 | {HEAD_FULL} |", f"|---|---|{SEP_FULL}|"]
    for sc in SCOPES:
        for c, v in sorted(d["cats"][sc].items(), key=lambda kv: ORDER.get(kv[0], 9)):
            L.append(f"| {c} | {SCOPES[sc]} | {_f(v['r1'])} |")
    L += ["", "## 4. 何日持つのがよいか", "",
          f"「張り付きが続く間は持つ」は、翌日以降ストップ高張り付き（始値＝高値＝安値＝終値）が続く限り持ち、外れた日の終値で売る（最長{MAX_HOLD}日）。", "",
          f"| 対象 | 売り方 | {HEAD_FULL} |", f"|---|---|{SEP_FULL}|"]
    for target, v in r["holding"].items():
        for k, st in v["rules"].items():
            mark = " **← 一番よい（平均）**" if k == v["best"] else ""
            L.append(f"| {target} | {RULES[k]}{mark} | {_f(st)} |")
    t2 = r["tob_day2"]
    L += ["", "## 5. TOBの2日目", "",
          f"TOB発表（{t2['n_tob']}件）のうち、発表翌日にストップ高張り付きで引けたのは{t2['n_stuck_next_day']}件。その張り付き値段で買った場合：", "",
          f"| 売り方 | {HEAD_FULL} |", f"|---|{SEP_FULL}|",
          f"| 翌日の終値で売る | {_f(t2['d1'])} |",
          f"| 張り付きが続く間は持つ | {_f(t2['stop'])} |"]
    L += ["", "## 6. ストップ高で引けた銘柄の翌日", "", f"| 種類 | 範囲 | {HEAD_FULL} |", f"|---|---|{SEP_FULL}|"]
    for sc in SCOPES:
        s = sh[sc]
        L += [f"| 張り付き | {SCOPES[sc]} | {_f(s['stuck_r1'])} |",
              f"| ストップ高引け（張り付き以外） | {SCOPES[sc]} | {_f(s['close_r1'])} |",
              f"| ストップ安引け | {SCOPES[sc]} | {_f(s['low_r1'])} |"]
    L.append("\n名証上場・名証単独の判定は、期間中に開示のあった銘柄だけ（開示の上場取引所欄）。")
    if fu:
        L += ["", "## 7. 日経平均先物（CME・円建て）の夜間の変化と、名証上場の大型株の翌日", "",
              f"- 対象：名証上場の大型株{len(LARGE)}銘柄の平均。n={fu['n']}日",
              f"- 相関（先物と前日の日経平均の差 ↔ 大型株の翌日平均）：{fu['corr_large']:.2f}／日経平均の翌日：{fu['corr_nikkei']:.2f}",
              "", f"| 先物と日経平均の差 | {HEAD_FULL} |", f"|---|{SEP_FULL}|"]
        L += [f"| {b['name']} | {_f(b)} |" for b in fu["buckets"]]
    nse = r.get("nse") or {}
    if nse:
        L += ["", f"## 名証の日報で計算（{nse['period']}・{nse['n_days']}営業日）", "",
              "コンテストと同じ値段（名証の最終気配）で計算。名証単独の銘柄はYahooに株価がないため、名証単独はここだけで集計。"
              "名証のサイトに残っている約30営業日分しかないので、**すべて参考**。", "",
              f"| ラベル／種類 | 範囲 | 翌日：{HEAD_FULL} |", f"|---|---|{SEP_FULL}|"]
        for sc in ("nagoya", "single"):
            for lb, st in sorted(nse["labels"][sc].items(), key=lambda kv: -kv[1].get("n", 0)):
                L.append(f"| {lb} | {SCOPES[sc]} | {_f(st)} |")
            for k, name in (("stuck", "ストップ高張り付き（特別買い気配を含む）"), ("close", "ストップ高引け"), ("low", "ストップ安")):
                if nse["stops"][sc].get(k):
                    L.append(f"| {name} | {SCOPES[sc]} | {_f(nse['stops'][sc][k])} |")
    if r["weights"]:
        L += ["", "## 8. 点数の重み（scanner/weights.json に反映）", "",
              "件数30以上の好材料・要確認ラベルだけ、点数＝30＋10×翌日平均(%)（5〜90に制限）。区分（S・A・要確認など）は変えていない。", "",
              "| ラベル | 新しい点数 |", "|---|---|"]
        L += [f"| {k} | {v} |" for k, v in sorted(r["weights"].items(), key=lambda kv: -kv[1])]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
