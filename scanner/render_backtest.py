"""ページの「検証結果」欄（reports/backtest.json から）。"""
from __future__ import annotations

import json
from html import escape

from .config import GITHUB_BRANCH, GITHUB_REPOSITORY, ROOT

PATH = ROOT / "reports" / "backtest.json"
NAMES = {"S": "S", "A": "A", "CHECK": "要確認", "BAD": "悪材料", "SELF_TOB": "自社株TOB", "TOB_REL": "TOB続報", "INFO": "参考"}


def _cell(st: dict) -> str:
    if not st or not st.get("n"):
        return "—"
    return f"{st['up']:.0f}%／{st['mean']:+.1f}%"


def section() -> str:
    if not PATH.exists():
        return ""
    r = json.loads(PATH.read_text(encoding="utf-8"))
    d, s, f = r["disclosures"], r["stop_high"], r.get("futures") or {}
    min_n = r.get("min_n", 30)
    link = f"https://github.com/{GITHUB_REPOSITORY}/blob/{GITHUB_BRANCH}/reports/backtest.md"
    out = ['<h2 id="backtest">検証結果（過去データ）</h2>',
           f'<p class="sub">期間 {escape(r.get("period", ""))}。引け後の開示の直前の終値で買い、翌営業日／2営業日後の終値で評価。'
           f'表示は「上昇した割合／平均騰落率」。n＝件数。n{min_n}未満は参考。'
           f'<a href="{link}" target="_blank" rel="noopener">詳しい表</a></p>']
    rows = []
    order = {"S": 0, "A": 1, "CHECK": 2, "BAD": 3, "SELF_TOB": 4, "TOB_REL": 5, "INFO": 6}
    for lb, v in sorted(d["labels"].items(), key=lambda kv: (order.get(kv[1]["cat"], 9), -kv[1]["r1"].get("n", 0))):
        n = v["r1"].get("n", 0)
        if n < 5:
            continue
        ref = "" if n >= min_n else "（参考）"
        rows.append(f'<tr><td>{escape(lb)}{ref}<br><span class="sub">{NAMES.get(v["cat"], "")}</span></td>'
                    f'<td>{_cell(v["r1"])}</td><td>{_cell(v["r2"])}</td><td>{n:,}</td></tr>')
    out.append('<div class="meta" style="overflow-x:auto"><table class="bt"><tr><th>材料</th><th>翌日</th><th>2日後</th><th>n</th></tr>'
               + "".join(rows) + "</table></div>")
    sh = s["stuck"]["r1"]
    sc = s["close"]["r1"]
    out.append(f'<div class="meta"><b>ストップ高の翌日</b><br>張り付き：{_cell(sh)}（n={sh.get("n", 0)}）<br>'
               f'張り付き以外のストップ高引け：{_cell(sc)}（n={sc.get("n", 0)}）</div>')
    if f:
        bl = "".join(f'<div class="row"><span>先物 {escape(b["name"])}</span><b>{_cell(b)}（n={b.get("n", 0)}）</b></div>'
                     for b in f["buckets"])
        out.append(f'<div class="meta"><b>日経平均先物の夜間の変化 → 名証上場の大型株の翌日</b>（相関 {f["corr_large"]:.2f}）{bl}</div>')
    return "\n".join(out)
