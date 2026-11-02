"""検証の集計：材料ごとの「当日終値→翌営業日終値」「当日終値→2営業日後終値」、ストップ高の翌日、先物と翌日の関係。

出力：
- reports/backtest.md（読みやすいまとめ）
- reports/backtest.json（ページの「検証結果」欄で使う）
- scanner/weights.json（点数の重み。件数30以上のラベルだけ）
"""
from __future__ import annotations

import json
import pickle
import statistics
from collections import defaultdict
from datetime import datetime, time

from ..classify import BAD, card_category
from ..classify import Tag
from ..config import ROOT
from ..limits import limit_width
from .fetch import BT, OUT
from .prices import LARGE

REPORTS = ROOT / "reports"
WEIGHTS = ROOT / "scanner" / "weights.json"
MIN_N = 30  # これ未満は「参考」


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "up": sum(1 for x in xs if x > 0) / len(xs) * 100, "mean": statistics.fmean(xs) * 100,
            "median": statistics.median(xs) * 100}


def _load_prices():
    return pickle.loads((BT / "prices.pkl").read_bytes())


def _returns(df, pub: datetime):
    """開示時刻 pub に対して、直前の終値（エントリー）→翌営業日・2営業日後の終値の騰落率。"""
    if df is None or len(df) < 3:
        return None
    idx = [d.date() for d in df.index]
    d0 = pub.date()
    # 15:30以降なら当日の終値がエントリー。休日なら直前の営業日
    cutoff = d0 if pub.time() >= time(15, 30) else None
    pos = None
    for i in range(len(idx) - 1, -1, -1):
        if idx[i] < d0 or (cutoff and idx[i] == d0):
            pos = i
            break
    if pos is None or pos + 1 >= len(idx):
        return None
    c = df["Close"].values
    if c[pos] <= 0:
        return None
    r1 = c[pos + 1] / c[pos] - 1
    r2 = c[pos + 2] / c[pos] - 1 if pos + 2 < len(idx) else None
    return str(idx[pos]), r1, r2


def disclosures(prices) -> dict:
    groups: dict[tuple[str, str], dict] = {}
    for line in OUT.open(encoding="utf-8"):
        x = json.loads(line)
        c5 = (x.get("code") or "").strip()
        code = c5[:4] if len(c5) == 5 and c5.endswith("0") else c5
        pub = datetime.strptime(x["pub"], "%Y-%m-%d %H:%M:%S")
        df = prices.get(f"{code}.T")
        r = _returns(df, pub)
        if r is None:
            continue
        entry, r1, r2 = r
        if abs(r1) > 1.0:  # 株式併合などの異常値
            continue
        g = groups.setdefault((code, entry), {"tags": [], "r1": r1, "r2": r2, "nagoya": "名" in (x.get("markets") or "")})
        g["tags"] += [Tag(c, lb, 0, "") for c, lb in x["tags"]]
    by_label: dict[str, dict[str, list]] = defaultdict(lambda: {"r1": [], "r2": [], "r1_nagoya": []})
    by_cat: dict[str, dict[str, list]] = defaultdict(lambda: {"r1": [], "r2": []})
    for (_code, _entry), g in groups.items():
        labels = {t.label for t in g["tags"] if t.label != "その他"}
        for lb in labels:
            by_label[lb]["r1"].append(g["r1"])
            if g["r2"] is not None:
                by_label[lb]["r2"].append(g["r2"])
            if g["nagoya"]:
                by_label[lb]["r1_nagoya"].append(g["r1"])
        cat = card_category(g["tags"])
        by_cat[cat]["r1"].append(g["r1"])
        if g["r2"] is not None:
            by_cat[cat]["r2"].append(g["r2"])
    cat_of = {}
    for (_c, _e), g in groups.items():
        for t in g["tags"]:
            cat_of.setdefault(t.label, t.cat)
    labels = {lb: {"cat": cat_of.get(lb, ""), "r1": _stats(v["r1"]), "r2": _stats(v["r2"]),
                   "r1_nagoya": _stats(v["r1_nagoya"])} for lb, v in by_label.items()}
    cats = {c: {"r1": _stats(v["r1"]), "r2": _stats(v["r2"])} for c, v in by_cat.items()}
    return {"labels": labels, "cats": cats, "n_groups": len(groups)}


def stop_high(prices) -> dict:
    stuck_r1, stuck_r2, close_r1, close_r2, low_r1 = [], [], [], [], []
    for t, df in prices.items():
        if not t.endswith(".T") or len(df) < 5:
            continue
        o, h, lo, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
        for i in range(1, len(c) - 1):
            base = c[i - 1]
            if base <= 0:
                continue
            w = limit_width(base)
            if abs(c[i] - (base + w)) < 0.51:
                flat = o[i] == h[i] == lo[i] == c[i]
                (stuck_r1 if flat else close_r1).append(c[i + 1] / c[i] - 1)
                if i + 2 < len(c):
                    (stuck_r2 if flat else close_r2).append(c[i + 2] / c[i] - 1)
            elif abs(c[i] - (base - w)) < 0.51:
                low_r1.append(c[i + 1] / c[i] - 1)
    return {"stuck": {"r1": _stats(stuck_r1), "r2": _stats(stuck_r2)},
            "close": {"r1": _stats(close_r1), "r2": _stats(close_r2)},
            "stop_low": {"r1": _stats(low_r1)}}


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
        if not rets:
            continue
        rows.append((gap, statistics.fmean(rets), nk_c[t1] / nk_c[t] - 1))
    if len(rows) < 10:
        return {}
    gaps, avg_r, nk_r = zip(*rows)
    buckets = [("−1%以下", -9, -0.01), ("−1%〜−0.3%", -0.01, -0.003), ("−0.3%〜+0.3%", -0.003, 0.003),
               ("+0.3%〜+1%", 0.003, 0.01), ("+1%以上", 0.01, 9)]
    out = {"n": len(rows), "corr_large": _corr(gaps, avg_r), "corr_nikkei": _corr(gaps, nk_r), "buckets": []}
    for name, lo, hi in buckets:
        xs = [r for g, r, _ in rows if lo <= g < hi]
        out["buckets"].append({"name": name, **_stats(xs)})
    return out


def _corr(a, b) -> float:
    try:
        return statistics.correlation(list(a), list(b))
    except Exception:  # noqa: BLE001
        return float("nan")


def weights(labels: dict) -> dict:
    """件数30以上の好材料・要確認ラベルに、翌日の平均騰落率から点数をつける。"""
    w = {}
    for lb, v in labels.items():
        st = v["r1"]
        if v["cat"] not in ("A", "CHECK") or st.get("n", 0) < MIN_N:
            continue
        w[lb] = int(max(5, min(90, round(30 + 10 * st["mean"]))))
    return w


def _fmt(st: dict) -> str:
    if not st or not st.get("n"):
        return "— | — | — | 0"
    return f"{st['up']:.0f}% | {st['mean']:+.2f}% | {st['median']:+.2f}% | {st['n']}"


def main() -> None:
    prices = _load_prices()
    days = sorted(set(l.strip() for l in (BT / "fetched_days.txt").read_text().split()))
    d = disclosures(prices)
    s = stop_high(prices)
    f = futures(prices)
    w = weights(d["labels"])
    REPORTS.mkdir(exist_ok=True)
    fmt = lambda x: f"{x[:4]}/{x[4:6]}/{x[6:]}"  # noqa: E731
    result = {"period": f"{fmt(days[0])}〜{fmt(days[-1])}" if days else "", "generated": datetime.now().isoformat(timespec="minutes"),
              "disclosures": d, "stop_high": s, "futures": f, "weights": w, "min_n": MIN_N}
    (REPORTS / "backtest.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    WEIGHTS.write_text(json.dumps(w, ensure_ascii=False, indent=1), encoding="utf-8")
    (REPORTS / "backtest.md").write_text(markdown(result), encoding="utf-8")
    print(markdown(result))


def markdown(r: dict) -> str:
    d, s, f = r["disclosures"], r["stop_high"], r["futures"]
    p = r["period"]
    L = ["# 検証結果（バックテスト）", "",
         f"- 期間：{p}（平日のみ。開示一覧＝やのしんWEB-API、全上場銘柄。株価＝Yahoo Financeの東証の終値）",
         f"- 作成：{r['generated']}",
         "- 買い方：開示の直前の終値（15:30以降の開示ならその日の終値）で買い、翌営業日の終値・2営業日後の終値で評価。コンテストの「引け後の材料をその夜に終値で買う」と同じ形",
         f"- **件数（n）が{r['min_n']}未満のものは「参考」**（偶然の影響が大きい）",
         "- 注意：株価は東証の終値。名証の最終気配とは少し違うことがある。同じ銘柄・同じ日に複数の開示があれば1件にまとめ、各ラベルに1回ずつ数えた",
         "", f"## 材料（ラベル）ごと：{d['n_groups']:,}件（銘柄×日）", "",
         "| ラベル | 区分 | 翌日 上昇確率 | 翌日 平均 | 翌日 中央値 | n | 2日後 上昇確率 | 2日後 平均 | 2日後 中央値 | n | 名証上場のみ 翌日平均(n) |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    order = {"S": 0, "A": 1, "CHECK": 2, "BAD": 3, "SELF_TOB": 4, "TOB_REL": 5, "INFO": 6}
    for lb, v in sorted(d["labels"].items(), key=lambda kv: (order.get(kv[1]["cat"], 9), -kv[1]["r1"].get("n", 0))):
        ref = "" if v["r1"].get("n", 0) >= r["min_n"] else "（参考）"
        ng = v["r1_nagoya"]
        ngs = f"{ng['mean']:+.2f}% ({ng['n']})" if ng.get("n") else "—"
        L.append(f"| {lb}{ref} | {v['cat']} | {_fmt(v['r1'])} | {_fmt(v['r2'])} | {ngs} |")
    L += ["", "## 区分（カード）ごと", "", "| 区分 | 翌日 上昇確率 | 翌日 平均 | 翌日 中央値 | n | 2日後 上昇確率 | 2日後 平均 | 2日後 中央値 | n |",
          "|---|---|---|---|---|---|---|---|---|"]
    for c, v in sorted(d["cats"].items(), key=lambda kv: order.get(kv[0], 9)):
        L.append(f"| {c} | {_fmt(v['r1'])} | {_fmt(v['r2'])} |")
    L += ["", "## ストップ高で引けた銘柄の翌日（開示のあった銘柄の範囲）", "",
          "| 種類 | 翌日 上昇確率 | 翌日 平均 | 翌日 中央値 | n | 2日後 上昇確率 | 2日後 平均 | 2日後 中央値 | n |",
          "|---|---|---|---|---|---|---|---|---|",
          f"| 張り付き（始値＝高値＝安値＝終値） | {_fmt(s['stuck']['r1'])} | {_fmt(s['stuck']['r2'])} |",
          f"| ストップ高で引け（張り付き以外） | {_fmt(s['close']['r1'])} | {_fmt(s['close']['r2'])} |",
          f"| ストップ安で引け | {_fmt(s['stop_low']['r1'])} | — | — | — | 0 |",
          "", "注意：Yahooの値段は約定した値段だけ。売買が成立しないまま特別買い気配で終わった日（コンテストでは最終気配で買える）は、ここに含まれていない。",
          "Yahooの株価は株式分割で過去分が調整されているため、分割前の一部は判定がずれることがある。"]
    if f:
        L += ["", "## 日経平均先物（CME・円建て）の夜間の変化と、名証上場の大型株の翌日", "",
              f"- 対象：名証上場の大型株{len(LARGE)}銘柄の平均。n={f['n']}日",
              f"- 相関（先物と前日の日経平均の差 ↔ 大型株の翌日平均）：{f['corr_large']:.2f}／日経平均の翌日：{f['corr_nikkei']:.2f}",
              "", "| 先物と日経平均の差 | 翌日 上昇確率 | 翌日 平均 | 翌日 中央値 | n |", "|---|---|---|---|---|"]
        for b in f["buckets"]:
            L.append(f"| {b['name']} | {_fmt(b)} |")
    if r["weights"]:
        L += ["", "## 点数の重み（scanner/weights.json に反映）", "",
              "件数30以上の好材料・要確認ラベルだけ、点数＝30＋10×翌日平均(%)（5〜90に制限）。区分（S・A・要確認など）は変えていません。", "",
              "| ラベル | 新しい点数 |", "|---|---|"]
        L += [f"| {k} | {v} |" for k, v in sorted(r["weights"].items(), key=lambda kv: -kv[1])]
    return "\n".join(x for x in L if x is not None) + "\n"


if __name__ == "__main__":
    main()
