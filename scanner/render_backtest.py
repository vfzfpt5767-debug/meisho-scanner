"""ページの「検証結果」欄と、ほかの欄に添える検証の数字（reports/backtest.json から）。"""
from __future__ import annotations

import json
from functools import lru_cache
from html import escape

from .config import GITHUB_BRANCH, GITHUB_REPOSITORY, ROOT

PATH = ROOT / "reports" / "backtest.json"
NAMES = {"S": "S", "A": "A", "CHECK": "要確認", "BAD": "悪材料", "SELF_TOB": "自社株TOB", "TOB_REL": "TOB続報", "INFO": "参考"}
ORDER = {"S": 0, "A": 1, "CHECK": 2, "BAD": 3, "SELF_TOB": 4, "TOB_REL": 5, "INFO": 6}
SCOPES = {"all": "全上場銘柄", "nagoya": "名証上場のみ", "single": "名証単独のみ"}


@lru_cache(maxsize=1)
def load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def label_stats(label: str) -> dict | None:
    """ラベルの「全上場銘柄・翌日」の成績。"""
    v = load().get("disclosures", {}).get("labels", {}).get(label, {})
    st = v.get("all", {}).get("r1")
    return st if st and st.get("n") else None


def label_note(label: str, short: bool = False) -> str:
    st = label_stats(label)
    if not st:
        return ""
    ref = "" if st["n"] >= load().get("min_n", 30) else "・参考"
    if short:
        return f"上がった割合{st['up']:.0f}%・平均{st['mean']:+.1f}%（n={st['n']:,}{ref}）"
    return f"検証：翌日に上がった割合{st['up']:.0f}%・平均{st['mean']:+.1f}%・中央値{st['median']:+.1f}%（n={st['n']:,}{ref}）"


def holding_advice(target: str) -> str:
    """何日持つのがよいか（target: TOB（S）/上方修正/優待新設・拡充/ストップ高張り付き）。"""
    h = load().get("holding", {}).get(target)
    if not h or not h.get("best"):
        return ""
    st = h["rules"][h["best"]]
    return f"{h['best_label']}（検証：平均{st['mean']:+.1f}%・中央値{st['median']:+.1f}%・n={st['n']}）"


def _cell(st: dict) -> str:
    if not st or not st.get("n"):
        return "—"
    return f"{st['up']:.0f}%／{st['mean']:+.1f}%／{st['median']:+.1f}%／{st['p10']:+.1f}%"


def _table(rows: list[tuple[str, str, dict, int]], min_n: int) -> str:
    body = "".join(
        f'<tr><td>{escape(name)}{"" if n >= min_n else "（参考）"}<br><span class="sub">{escape(sub)}</span></td>'
        f'<td>{_cell(st)}</td><td>{st.get("min", 0):+.1f}%</td><td>{n:,}</td></tr>' for name, sub, st, n in rows)
    return ('<div class="meta" style="overflow-x:auto"><table class="bt"><tr><th>材料</th>'
            '<th>上がった割合／平均／中央値／下位10%</th><th>最大の下落</th><th>n</th></tr>' + body + "</table></div>")


def section() -> str:
    r = load()
    if not r:
        return ""
    d, mn = r["disclosures"], r.get("min_n", 30)
    link = f"https://github.com/{GITHUB_REPOSITORY}/blob/{GITHUB_BRANCH}/reports/backtest.md"
    out = ['<h2 id="backtest">検証結果（過去データ）</h2>',
           f'<p class="sub">期間 {escape(r.get("period", ""))}。引け後の開示の直前の終値で買い、翌営業日の終値で評価。'
           f'中央値＝真ん中の値（大当たりに引っ張られにくい）、下位10%＝悪いときの下がり幅の目安。n＝件数。n{mn}未満は参考。'
           f'<a href="{link}" target="_blank" rel="noopener">詳しい表</a></p>']
    labels = sorted(d["labels"].items(), key=lambda kv: (ORDER.get(kv[1]["cat"], 9), -kv[1].get("all", {}).get("r1", {}).get("n", 0)))
    rows = [(lb, NAMES.get(v["cat"], ""), v["all"]["r1"], v["all"]["r1"].get("n", 0)) for lb, v in labels
            if v.get("all") and v["all"]["r1"].get("n", 0) >= 5]
    out.append("<h3>全上場銘柄（翌日）</h3>" + _table(rows, mn))

    hold = r.get("holding", {})
    if hold:
        hr = []
        for target, v in hold.items():
            for k, st in v["rules"].items():
                mark = "★" if k == v["best"] else ""
                hr.append((f"{mark}{target}", {"d1": "翌日売り", "d2": "2日後売り", "stop": "張り付きが外れた日に売り"}[k], st, st.get("n", 0)))
        out.append("<h3>何日持つのがよいか（★＝平均が一番よい）</h3>" + _table(hr, mn))
    t2 = r.get("tob_day2")
    if t2:
        out.append(f"<h3>TOBの2日目（発表翌日にストップ高張り付き：{t2['n_stuck_next_day']}件／TOB {t2['n_tob']}件）</h3>"
                   + _table([("張り付き値段で買い翌日売り", "", t2["d1"], t2["d1"].get("n", 0)),
                             ("張り付き値段で買い、外れた日に売り", "", t2["stop"], t2["stop"].get("n", 0))], mn))
    for sc in ("nagoya", "single"):
        rows = [(lb, NAMES.get(v["cat"], ""), v[sc]["r1"], v[sc]["r1"].get("n", 0)) for lb, v in labels
                if v.get(sc) and v[sc]["r1"].get("n", 0) >= 3]
        if rows:
            out.append(f"<h3>{SCOPES[sc]}（翌日・Yahooの東証終値）</h3>" + _table(rows, mn))
    nse = r.get("nse") or {}
    if nse:
        for sc in ("nagoya", "single"):
            rows = [(lb, "名証の最終気配で計算", st, st.get("n", 0)) for lb, st in
                    sorted(nse["labels"][sc].items(), key=lambda kv: -kv[1].get("n", 0)) if st.get("n", 0) >= 2]
            rows += [({"stuck": "ストップ高張り付き", "close": "ストップ高引け", "low": "ストップ安"}[k], "名証の最終気配で計算", st, st.get("n", 0))
                     for k, st in nse["stops"][sc].items() if st.get("n")]
            if rows:
                out.append(f"<h3>{SCOPES[sc]}（名証の日報 {escape(nse['period'])}・すべて参考）</h3>" + _table(rows, 10 ** 9))
    sh = r["stop_high"]["all"]
    out.append("<h3>ストップ高の翌日（全上場銘柄）</h3>" + _table(
        [("張り付き", "", sh["stuck_r1"], sh["stuck_r1"].get("n", 0)),
         ("張り付き以外のストップ高引け", "", sh["close_r1"], sh["close_r1"].get("n", 0)),
         ("ストップ安引け", "", sh["low_r1"], sh["low_r1"].get("n", 0))], mn))
    f = r.get("futures") or {}
    if f:
        out.append(f"<h3>日経平均先物の夜間の変化 → 名証上場の大型株の翌日（相関 {f['corr_large']:.2f}）</h3>" + _table(
            [(f"先物 {b['name']}", "", b, b.get("n", 0)) for b in f["buckets"]], mn))
    return "\n".join(out)


def stop_high_note() -> str:
    try:
        st = load()["stop_high"]["all"]["stuck_r1"]
        adv = holding_advice("ストップ高張り付き")
        return (f"過去の検証：張り付きの翌日は{st['up']:.0f}%で上昇、平均{st['mean']:+.1f}%・中央値{st['median']:+.1f}%（n={st['n']}）。"
                + (f"売り時の目安：{adv}" if adv else ""))
    except Exception:  # noqa: BLE001
        return ""


def futures_note(gap_pct: float | None) -> str:
    r = load()
    if gap_pct is None or not r.get("futures"):
        return ""
    g = gap_pct / 100
    for b, (lo, hi) in zip(r["futures"]["buckets"], [(-9, -0.01), (-0.01, -0.003), (-0.003, 0.003), (0.003, 0.01), (0.01, 9)]):
        if lo <= g < hi and b.get("n"):
            return f"過去の検証：先物が{b['name']}の翌日、名証上場の大型株は{b['up']:.0f}%で上昇、平均{b['mean']:+.2f}%（n={b['n']}）"
    return ""
