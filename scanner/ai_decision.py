"""AIアナリストの判断（decisions/*.json）の確認・取り込み。

  python -m scanner.ai_decision validate decisions/2026-11-04-1712.json   # 形式チェック（AIが push 前に使う）
  python -m scanner.ai_decision apply                                      # Actions：未取り込みの判断を取り込む

取り込み（apply）でやること：
- 形式チェック → data/ai/latest.json に保存（ページ最上部の「AIの結論」に表示）
- 「AIの注文どおりに注文した」前提で holdings.csv と cash.csv を更新（D. 保有の自動記録）
- LINEで注文の指示を送る
"""
from __future__ import annotations

import csv
import io
import json
import sys
from datetime import date, datetime
from pathlib import Path

from .config import BUDGET_PER_TERM, DATA_DIR, ROOT

DECISIONS = ROOT / "decisions"
LATEST = DATA_DIR / "ai" / "latest.json"
APPLIED = DATA_DIR / "ai" / "applied.json"
HISTORY = DATA_DIR / "ai" / "history.jsonl"
CONF = ("高", "中", "低")


def validate(d: dict) -> list[str]:
    errs = []
    for k in ("created_at", "base_day", "orders", "summary"):
        if k not in d:
            errs.append(f"{k} がありません")
    try:
        date.fromisoformat(d.get("base_day", ""))
    except ValueError:
        errs.append("base_day は YYYY-MM-DD")
    buy_total = 0.0
    for i, o in enumerate(d.get("orders", []) or []):
        p = f"orders[{i}]"
        if o.get("side") not in ("buy", "sell"):
            errs.append(f"{p}.side は buy か sell")
        if not str(o.get("code", "")).strip():
            errs.append(f"{p}.code がありません")
        try:
            sh = int(o.get("shares", 0))
            unit = int(o.get("unit", 100) or 100)
            if sh <= 0 or sh % unit:
                errs.append(f"{p}.shares は売買単位（{unit}株）の整数倍の正の数")
            pr = float(o.get("price", 0))
            if pr <= 0:
                errs.append(f"{p}.price は正の数")
            if o.get("side") == "buy":
                buy_total += sh * pr
        except (TypeError, ValueError):
            errs.append(f"{p}.shares / price が数値ではありません")
        if o.get("confidence") not in CONF:
            errs.append(f"{p}.confidence は 高・中・低 のどれか")
        if not o.get("reason"):
            errs.append(f"{p}.reason がありません")
    if buy_total > BUDGET_PER_TERM:
        errs.append(f"買いの合計 {buy_total:,.0f}円 が1タームの上限 {BUDGET_PER_TERM:,}円 を超えています")
    return errs


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    """（コメント行と見出し, データ行）。"""
    head, rows = [], []
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            if line.lstrip().startswith("#") or line.lower().startswith(("code,", "key,")):
                head.append(line)
            else:
                rows.append(next(csv.reader([line])))
    return head, rows


def _write_csv(path: Path, head: list[str], rows: list[list[str]]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow(r)
    path.write_text("\n".join(head) + "\n" + buf.getvalue(), encoding="utf-8")


def apply_to_portfolio(d: dict, now: datetime) -> list[str]:
    """注文どおりに約定した前提で holdings.csv と cash.csv を更新する。返り値はメモ。"""
    from .portfolio import CASH_CSV, HOLDINGS_CSV

    notes = []
    hh, hrows = _read_csv(HOLDINGS_CSV)
    if not hh:
        hh = ["code,shares,buy_price,buy_date,memo"]
    ch, crows = _read_csv(CASH_CSV)
    cash_row = next((r for r in crows if r and r[0].strip().lower() == "cash"), None)
    if cash_row is None:
        notes.append("cash.csv に現金残高がないので、保有の自動記録をしませんでした")
        return notes
    cash = float(cash_row[1].replace(",", ""))
    stamp = d["base_day"]  # そのタームの基準日（保有管理の「今ターム」の計算に使う）
    for o in d.get("orders", []):
        code, sh, pr = str(o["code"]).strip().upper(), int(o["shares"]), float(o["price"])
        if o["side"] == "sell":
            left = sh
            for r in hrows:
                if left <= 0 or not r or r[0].strip().upper() != code:
                    continue
                have = int(float(r[1].replace(",", "")))
                take = min(have, left)
                r[1] = str(have - take)
                left -= take
            sold = sh - left
            hrows = [r for r in hrows if not (r and r[0].strip().upper() == code and int(float(r[1])) <= 0)]
            cash += sold * pr
            notes.append(f"売り {code} {sold:,}株 × {pr:,.0f}円 を記録" + (f"（保有が{left:,}株足りません）" if left else ""))
    for o in d.get("orders", []):
        if o["side"] != "buy":
            continue
        code, sh, pr = str(o["code"]).strip().upper(), int(o["shares"]), float(o["price"])
        if sh * pr > cash + 0.5:
            notes.append(f"買い {code} は現金不足（必要 {sh * pr:,.0f}円・現金 {cash:,.0f}円）のため記録しませんでした")
            continue
        hrows.append([code, str(sh), f"{pr:g}", stamp, f"AI {d.get('id', '')}".strip()])
        cash -= sh * pr
        notes.append(f"買い {code} {sh:,}株 × {pr:,.0f}円 を記録")
    cash_row[1] = f"{cash:.0f}"
    _write_csv(HOLDINGS_CSV, hh, hrows)
    _write_csv(CASH_CSV, ch, crows)
    return notes


def line_text(d: dict) -> str:
    from .notify import page_url

    out = [f"【AIの結論】{d.get('summary', '')}"]
    dl = d.get("deadline")
    if dl:
        try:
            t = datetime.fromisoformat(dl)
            out[0] += f"（締切 {t:%m/%d %H:%M}）"
        except ValueError:
            pass
    for o in d.get("orders", []):
        side = "買い" if o["side"] == "buy" else "売り"
        out.append(f"{side}：{o['code']} {o.get('name', '')} {int(o['shares']):,}株（今日の終値{float(o['price']):,.0f}円）"
                   f" 自信度{o.get('confidence', '')}")
        out.append(f"　理由：{str(o.get('reason', ''))[:80]}")
    if not d.get("orders"):
        out.append("今夜は注文なし（見送り）")
    for s in d.get("skipped", [])[:3]:
        out.append(f"見送り：{s.get('code', '')} {s.get('name', '')} {str(s.get('reason', ''))[:40]}")
    out.append("注文したら完了。違う注文をしたときはページの案内で holdings.csv / cash.csv を直してください")
    out.append(page_url())
    return "\n".join(out)


def apply_all(now: datetime | None = None, send_line: bool = True) -> list[str]:
    from .calendar_jp import now_jst, order_deadline

    now = now or now_jst()
    applied = json.loads(APPLIED.read_text(encoding="utf-8")) if APPLIED.exists() else []
    log = []
    for path in sorted(DECISIONS.glob("*.json")):
        if path.name in applied:
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        d.setdefault("id", path.stem)
        errs = validate(d)
        rec = {"file": path.name, "applied_at": now.isoformat(), "errors": errs, "notes": []}
        if errs:
            rec["status"] = "形式エラーのため取り込みませんでした"
        else:
            deadline = order_deadline(date.fromisoformat(d["base_day"]))
            if now > deadline:
                rec["status"] = "注文締切を過ぎていたため、保有の自動記録はしませんでした"
            else:
                rec["notes"] = apply_to_portfolio(d, now)
                rec["status"] = "取り込み済み"
            d["deadline"] = d.get("deadline") or deadline.isoformat()
        d["_apply"] = rec
        LATEST.parent.mkdir(parents=True, exist_ok=True)
        if not errs:
            LATEST.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        with HISTORY.open("a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
        applied.append(path.name)
        log.append(f"{path.name}: {rec['status']} {' / '.join(rec['notes'] + errs)}")
        if send_line:
            from .notify import send

            msg = line_text(d) if not errs else f"【名証スキャナー】AIの判断ファイル {path.name} に形式エラー：{errs[0]}"
            log.append(send(msg, "ai"))
    APPLIED.parent.mkdir(parents=True, exist_ok=True)
    APPLIED.write_text(json.dumps(applied[-500:], ensure_ascii=False, indent=0), encoding="utf-8")
    return log


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "validate":
        d = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        errs = validate(d)
        print("OK" if not errs else "\n".join(errs))
        return 1 if errs else 0
    if len(sys.argv) >= 2 and sys.argv[1] == "apply":
        for line in apply_all():
            print(line)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
