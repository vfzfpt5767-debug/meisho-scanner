"""予測モデル：引け後の開示で、翌営業日に上がる確率（ロジスティック回帰）。

- 学習：2025/01〜2025/12、採点：2026/01〜（時系列に沿った検証。未来のデータで学習しない）
- 比べる相手：ルールの点数（学習期間だけで作り直した重み）
- 採用の条件：採点期間で「AUC」と「毎晩の1位候補の平均騰落率」の両方がルールより良いこと
- 結果：reports/model.md、係数：scanner/model.json（adopted が true のときだけ使う。今は不採用）

特徴量：材料ラベル・区分・開示時刻・売買代金（時価総額の代わり）・直近5日/20日の騰落・出来高の変化・
市場（グロース・札証/福証）・ストップ高（張り付き）・前夜の日経平均先物の変化・曜日
（「前夜の先物」は開示日の前日の夜の動き＝判断の時点で分かっている値。名証単独はYahooに株価がないため学習に入らない）
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import date, datetime

from ..calendar_jp import now_jst
from ..classify import Tag, card_category
from ..config import ROOT
from .analyze import MIN_N, Series, _load_prices
from .fetch import OUT

MODEL = ROOT / "scanner" / "model.json"
REPORT = ROOT / "reports" / "model.md"
SPLIT = date(2026, 1, 1)
CATS = ["S", "A", "CHECK", "BAD", "SELF_TOB", "TOB_REL", "INFO"]


def _futures_prev(prices) -> dict[date, float]:
    """各日 d について「d の前日の夜」の先物の変化（前日の先物 ÷ 前日の日経平均 − 1）。"""
    nk, fu = prices.get("^N225"), prices.get("NIY=F")
    if nk is None or fu is None:
        return {}
    nk_c = {d.date(): v for d, v in zip(nk.index, nk["Close"].values)}
    fu_c = {d.date(): v for d, v in zip(fu.index, fu["Close"].values)}
    days = sorted(nk_c)
    out = {}
    for i in range(1, len(days)):
        p = days[i - 1]
        if p in fu_c and nk_c[p] > 0:
            out[days[i]] = fu_c[p] / nk_c[p] - 1
    return out


def build_events():
    prices = _load_prices()
    series = {t[:-2]: Series(df) for t, df in prices.items() if t.endswith(".T") and len(df) >= 25}
    vols = {t[:-2]: df["Volume"].values for t, df in prices.items() if t.endswith(".T") and len(df) >= 25}
    fut = _futures_prev(prices)
    groups: dict[tuple[str, int], dict] = {}
    for line in OUT.open(encoding="utf-8"):
        x = json.loads(line)
        c5 = (x.get("code") or "").strip()
        code = c5[:4] if len(c5) == 5 and c5.endswith("0") else c5
        s = series.get(code)
        if s is None:
            continue
        pub = datetime.strptime(x["pub"], "%Y-%m-%d %H:%M:%S")
        pos = s.entry_pos(pub)
        if pos is None or pos < 21 or pos + 1 >= len(s.c):
            continue
        r1 = s.ret(pos, pos + 1)
        if r1 is None or abs(r1) > 1.0:
            continue
        g = groups.setdefault((code, pos), {"code": code, "pos": pos, "date": s.dates[pos], "tags": [], "r1": r1,
                                            "hour": pub.hour + pub.minute / 60, "weekday_pub": pub.weekday(),
                                            "markets": x.get("markets") or "", "name": x.get("name") or ""})
        g["tags"] += [Tag(c, lb, 0, "") for c, lb in x["tags"]]
        g["hour"] = min(g["hour"], pub.hour + pub.minute / 60)
    events = []
    for g in groups.values():
        s, v, i = series[g["code"]], vols[g["code"]], g["pos"]
        c = s.c
        turnover = statistics.fmean(c[i - 19:i + 1] * v[i - 19:i + 1])
        v5, v20 = statistics.fmean(v[i - 4:i + 1]), statistics.fmean(v[i - 19:i + 1])
        g.update({
            "cat": card_category(g["tags"]), "labels": sorted({t.label for t in g["tags"] if t.label != "その他"}),
            "log_turnover": math.log10(max(turnover, 1.0)), "ret5": c[i] / c[i - 5] - 1, "ret20": c[i] / c[i - 20] - 1,
            "vol_chg": math.log((v5 + 1) / (v20 + 1)), "stuck": s.stuck_high(i), "stop": s.stop_close(i),
            "fut_prev": fut.get(g["date"], 0.0), "nagoya": "名" in g["markets"],
            "growth": g["name"].startswith(("Ｇ－", "G-")), "local": any(m in g["markets"] for m in ("札", "福")),
        })
        events.append(g)
    return events


def featurize(e: dict, label_list: list[str]) -> list[float]:
    f = [1.0 if e["cat"] == c else 0.0 for c in CATS]
    f += [1.0 if lb in e["labels"] else 0.0 for lb in label_list]
    h = e["hour"]
    f += [1.0 if abs(h - 15.5) < 0.01 else 0.0, 1.0 if h >= 18 else 0.0, 1.0 if h < 15.5 else 0.0]
    f += [e["log_turnover"], max(-0.5, min(0.5, e["ret5"])), max(-1, min(1, e["ret20"])), max(-3, min(3, e["vol_chg"])),
          float(e["stuck"]), float(e["stop"]), max(-0.05, min(0.05, e["fut_prev"])) * 100,
          float(e["growth"]), float(e["local"]), float(e["nagoya"])]
    f += [1.0 if e["date"].weekday() == k else 0.0 for k in range(5)]
    return f


FEATURE_TAIL = ["15:30ちょうど", "18時以降", "15:30前（休日など）", "売買代金(log10)", "直近5日の騰落", "直近20日の騰落",
                "出来高の変化(5日/20日,log)", "ストップ高張り付き", "ストップ高引け", "前夜の先物(%)", "グロース", "札証・福証",
                "名証上場"] + [f"曜日{'月火水木金'[k]}" for k in range(5)]


def _auc(y: list[int], s: list[float]) -> float:
    pairs = sorted(zip(s, y))
    n1 = sum(y)
    n0 = len(y) - n1
    if not n1 or not n0:
        return float("nan")
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg = (i + j + 1) / 2
        rank_sum += avg * sum(1 for k in range(i, j) if pairs[k][1] == 1)
        i = j
    return (rank_sum - n1 * (n1 + 1) / 2) / (n1 * n0)


def _top1(events: list[dict], scores: list[float], pool) -> dict:
    by_day = defaultdict(list)
    for e, sc in zip(events, scores):
        if pool(e):
            by_day[e["date"]].append((sc, e["r1"]))
    picks = [max(v)[1] for v in by_day.values() if v]
    if not picks:
        return {"n": 0}
    return {"n": len(picks), "up": sum(1 for r in picks if r > 0) / len(picks) * 100,
            "mean": statistics.fmean(picks) * 100, "median": statistics.median(picks) * 100}


def main() -> None:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    events = build_events()
    train = [e for e in events if e["date"] < SPLIT]
    test = [e for e in events if e["date"] >= SPLIT]
    cnt = defaultdict(int)
    for e in train:
        for lb in e["labels"]:
            cnt[lb] += 1
    label_list = sorted(lb for lb, n in cnt.items() if n >= MIN_N)
    Xtr = [featurize(e, label_list) for e in train]
    Xte = [featurize(e, label_list) for e in test]
    ytr = [1 if e["r1"] > 0 else 0 for e in train]
    yte = [1 if e["r1"] > 0 else 0 for e in test]
    sc = StandardScaler().fit(Xtr)
    clf = LogisticRegression(C=0.3, max_iter=2000).fit(sc.transform(Xtr), ytr)
    p_te = list(clf.predict_proba(sc.transform(Xte))[:, 1])

    # ルールの点数（学習期間だけで作った重み）
    lab_mean = defaultdict(list)
    for e in train:
        for lb in e["labels"]:
            lab_mean[lb].append(e["r1"])
    wt = {lb: 30 + 10 * statistics.fmean(v) * 100 for lb, v in lab_mean.items() if len(v) >= MIN_N}
    base_cat = {"S": 100, "A": 50, "CHECK": 30, "BAD": -50, "SELF_TOB": 0, "TOB_REL": 0, "INFO": 0}

    def rule_score(e):
        pos = sorted((wt.get(lb, 0) for lb in e["labels"]), reverse=True)
        return base_cat.get(e["cat"], 0) + (pos[0] if pos else 0) + 5 * max(0, len(pos) - 1)

    r_te = [rule_score(e) for e in test]
    cand = lambda e: e["cat"] in ("S", "A", "CHECK")  # noqa: E731
    cand_ng = lambda e: cand(e) and e["nagoya"]  # noqa: E731
    res = {
        "train": {"n": len(train), "period": f"{min(e['date'] for e in train)}〜{max(e['date'] for e in train)}"},
        "test": {"n": len(test), "period": f"{min(e['date'] for e in test)}〜{max(e['date'] for e in test)}",
                 "base_up": sum(yte) / len(yte) * 100},
        "auc": {"model": _auc(yte, p_te), "rule": _auc(yte, r_te)},
        "top1_all": {"model": _top1(test, p_te, cand), "rule": _top1(test, r_te, cand)},
        "top1_nagoya": {"model": _top1(test, p_te, cand_ng), "rule": _top1(test, r_te, cand_ng)},
    }
    # 確率の当たり具合（採点期間）
    bins = defaultdict(list)
    for p, e in zip(p_te, test):
        bins[min(int(p * 10), 9)].append(e["r1"])
    res["calibration"] = [{"bin": f"{b * 10}〜{b * 10 + 10}%", "n": len(v), "up": sum(1 for r in v if r > 0) / len(v) * 100,
                           "mean": statistics.fmean(v) * 100} for b, v in sorted(bins.items())]
    m, r = res["top1_all"]["model"], res["top1_all"]["rule"]
    adopted = bool(res["auc"]["model"] > res["auc"]["rule"] and m.get("mean", -9) > r.get("mean", -9))
    res["adopted"] = adopted

    # 採用・不採用にかかわらず、全期間で学習し直した係数を保存（adopted のときだけ使う）
    Xall = Xtr + Xte
    sc_all = StandardScaler().fit(Xall)
    clf_all = LogisticRegression(C=0.3, max_iter=2000).fit(sc_all.transform(Xall), ytr + yte)
    names = CATS + [f"ラベル:{lb}" for lb in label_list] + FEATURE_TAIL
    model = {"adopted": adopted, "created": now_jst().strftime("%Y-%m-%d %H:%M JST"), "label_list": label_list,
             "feature_names": names, "mean": list(sc_all.mean_), "scale": list(sc_all.scale_),
             "coef": list(clf_all.coef_[0]), "intercept": float(clf_all.intercept_[0]), "eval": res}
    MODEL.write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    coefs = sorted(zip(names, clf.coef_[0]), key=lambda kv: -abs(kv[1]))[:15]
    REPORT.write_text(markdown(res, coefs), encoding="utf-8")
    print(markdown(res, coefs))


def _st(st: dict) -> str:
    if not st or not st.get("n"):
        return "— | — | — | 0"
    return f"{st['up']:.0f}% | {st['mean']:+.2f}% | {st['median']:+.2f}% | {st['n']}"


def markdown(r: dict, coefs) -> str:
    L = ["# 予測モデルの検証（翌営業日に上がる確率）", "",
         "- モデル：ロジスティック回帰（特徴量を標準化。正則化 C=0.3）",
         f"- 学習期間：{r['train']['period']}（{r['train']['n']:,}件）／ 採点期間：{r['test']['period']}（{r['test']['n']:,}件）",
         "- 時系列に沿った検証：学習期間のデータだけでモデルとルールの重みを作り、その後の期間で採点した",
         f"- 採点期間で翌日に上がった割合（全体）：{r['test']['base_up']:.1f}%",
         "", "## 1. 並べる力（AUC：0.5＝でたらめ、1.0＝完璧）", "",
         "| 方法 | AUC |", "|---|---|",
         f"| 予測モデル | {r['auc']['model']:.3f} |", f"| ルールの点数 | {r['auc']['rule']:.3f} |",
         "", "## 2. 毎晩の1位候補だけ買った場合（S・A・要確認の中から、その晩いちばん点数／確率が高いもの）", "",
         "| 候補の範囲 | 方法 | 上がった割合 | 平均 | 中央値 | 夜の数 |", "|---|---|---|---|---|---|",
         f"| 全上場銘柄 | 予測モデル | {_st(r['top1_all']['model'])} |",
         f"| 全上場銘柄 | ルールの点数 | {_st(r['top1_all']['rule'])} |",
         f"| 名証上場のみ | 予測モデル | {_st(r['top1_nagoya']['model'])} |",
         f"| 名証上場のみ | ルールの点数 | {_st(r['top1_nagoya']['rule'])} |",
         "", "## 3. 確率の当たり具合（採点期間）", "", "| モデルの確率 | 件数 | 実際に上がった割合 | 平均 |", "|---|---|---|---|"]
    L += [f"| {b['bin']} | {b['n']:,} | {b['up']:.0f}% | {b['mean']:+.2f}% |" for b in r["calibration"]]
    L += ["", "## 4. 影響の大きい特徴量（学習期間のモデル。プラス＝上がりやすい）", "", "| 特徴量 | 係数 |", "|---|---|"]
    L += [f"| {n} | {c:+.3f} |" for n, c in coefs]
    L += ["", "## 結論", "",
          ("**採用**：採点期間でAUCと「毎晩の1位候補の平均」の両方がルールより良かったため、ページとAIアナリストに確率を表示する。"
           if r["adopted"] else
           "**不採用**：採点期間でルールの点数を上回れなかったため（AUCか「毎晩の1位候補の平均」のどちらかが負けた）、ページとAIアナリストはルールの点数を使う。"
           "全体の並べる力（AUC）はモデルが上でも、実際に使う「毎晩の1位だけ買う」ではルールの方が成績が良かった。"),
          "", "注意：株価はYahooの東証終値。名証単独の銘柄は株価がないため学習に入っていない。時価総額の代わりに直近20日の売買代金を使った。"]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
