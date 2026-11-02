"""AIアナリストの判断（decisions/*.json）の確認・取り込み。

  python -m scanner.ai_decision preflight /tmp/input.json --trigger schedule [--test]  # AIが最初に使う（続けるか・出力先）
  python -m scanner.ai_decision validate decisions/2026-11-04-1712.json   # 形式チェック（AIが push 前に使う）
  python -m scanner.ai_decision apply                                      # Actions：未取り込みの判断を取り込む

テストモード（decisions/test/*.json または "test": true）：
- 形式チェックをして、LINEに「【テスト】AIの結論」を送るだけ
- holdings.csv・cash.csv・ページの「AIの結論」（data/ai/latest.json）は変えない

取り込み（apply）でやること：
- 形式チェック → data/ai/latest.json に保存（ページ最上部の「AIの結論」に表示）
- 「AIの注文どおりに注文した」前提で holdings.csv と cash.csv を更新（D. 保有の自動記録）
- LINEで注文の指示を送る
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from .config import BUDGET_PER_TERM, DATA_DIR, ROOT

DECISIONS = ROOT / "decisions"
TEST_DIR = DECISIONS / "test"
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


def line_text(d: dict, test: bool = False) -> str:
    from .notify import page_url

    out = [f"{'【テスト】AIの結論：' if test else '【AIの結論】'}{d.get('summary', '')}"]
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
    if test:
        out.append(f"※テストです（基準日 {d.get('base_day', '')} のデータ）。注文はしないでください。保有・現金の記録は変えていません")
    else:
        out.append("注文したら完了。違う注文をしたときはページの案内で holdings.csv / cash.csv を直してください")
    out.append(page_url())
    return "\n".join(out)


def _pending(applied: list[str]) -> list[tuple[str, Path]]:
    """（取り込み済みリストでの名前, パス）。テストは "test/ファイル名"。"""
    out = [(p.name, p) for p in sorted(DECISIONS.glob("*.json"))]
    out += [(f"test/{p.name}", p) for p in sorted(TEST_DIR.glob("*.json"))]
    return [(k, p) for k, p in out if k not in applied]


def apply_all(now: datetime | None = None, send_line: bool = True) -> list[str]:
    from .calendar_jp import now_jst, order_deadline

    now = now or now_jst()
    applied = json.loads(APPLIED.read_text(encoding="utf-8")) if APPLIED.exists() else []
    log = []
    real = 0
    for key, path in _pending(applied):
        d = json.loads(path.read_text(encoding="utf-8"))
        d.setdefault("id", path.stem)
        test = key.startswith("test/") or bool(d.get("test"))
        errs = validate(d)
        rec = {"file": key, "applied_at": now.isoformat(), "errors": errs, "notes": [], "test": test}
        if errs:
            rec["status"] = "形式エラーのため取り込みませんでした"
        elif test:
            rec["status"] = "テスト（保有・現金の記録とページは変えません）"
        else:
            deadline = order_deadline(date.fromisoformat(d["base_day"]))
            if now > deadline:
                rec["status"] = "注文締切を過ぎていたため、保有の自動記録はしませんでした"
            else:
                rec["notes"] = apply_to_portfolio(d, now)
                rec["status"] = "取り込み済み"
            d["deadline"] = d.get("deadline") or deadline.isoformat()
        d["_apply"] = rec
        if not test:
            real += 1
            LATEST.parent.mkdir(parents=True, exist_ok=True)
            if not errs:
                LATEST.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            with HISTORY.open("a", encoding="utf-8") as f:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        applied.append(key)
        log.append(f"{key}: {rec['status']} {' / '.join(rec['notes'] + errs)}")
        if send_line:
            from .notify import send

            if errs:
                msg = f"{'【テスト】' if test else '【名証スキャナー】'}AIの判断ファイル {key} に形式エラー：{errs[0]}"
            else:
                msg = line_text(d, test=test)
            log.append(send(msg, "ai_test" if test else "ai"))
    APPLIED.parent.mkdir(parents=True, exist_ok=True)
    APPLIED.write_text(json.dumps(applied[-500:], ensure_ascii=False, indent=0), encoding="utf-8")
    # Actions 用：本番の判断があったときだけページを作り直す
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"rebuild={'true' if real else 'false'}\n")
    return log


def preflight(input_path: Path, trigger: str, test: bool, now: datetime | None = None) -> tuple[bool, list[str]]:
    """AIが最初に実行する確認。（続けるか, 表示する行）。"""
    from .calendar_jp import is_business_day, now_jst

    now = now or now_jst()
    lines = [f"now={now:%Y-%m-%dT%H:%M%z}", f"mode={'test' if test else 'normal'}", f"trigger={trigger}"]
    biz = is_business_day(now.date())
    lines.append(f"business_day={'true' if biz else 'false'}")
    try:
        d = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        lines.append(f"input_error={type(e).__name__}: {e}")
        lines.append("result=STOP 入力（analyst_input.json）が読めません")
        return False, lines
    lines.append(f"base_day={d.get('base_day', '')}")
    lines.append(f"deadline={d.get('deadline', '')}")
    passed = False
    try:
        passed = now > datetime.fromisoformat(d["deadline"])
    except (KeyError, TypeError, ValueError):
        lines.append("deadline_error=入力に deadline がありません")
    lines.append(f"deadline_passed={'true' if passed else 'false'}")
    try:
        age = (now - datetime.fromisoformat(d["generated_at"])).total_seconds() / 3600
        lines.append(f"input_age_hours={age:.1f}")
        if age >= 18:
            lines.append("stale=true（data_gaps に「入力が18時間以上前」と書く）")
    except (KeyError, TypeError, ValueError):
        lines.append("input_age_hours=不明")
    sub = "decisions/test/" if test else "decisions/"
    lines.append(f"output={sub}{now:%Y-%m-%d-%H%M}")
    if test:
        lines.append("result=GO テストモード（休業日・締切に関係なく最後まで判断する）")
        return True, lines
    if passed:
        lines.append("result=STOP 締切を過ぎています")
        return False, lines
    if trigger == "schedule" and not biz:
        lines.append("result=STOP 今日は取引所の休業日（定時起動なので何もしない）")
        return False, lines
    lines.append("result=GO")
    return True, lines


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "validate":
        path = Path(sys.argv[2])
        d = json.loads(path.read_text(encoding="utf-8"))
        errs = validate(d)
        if path.resolve().parent == TEST_DIR.resolve() and d.get("test") is not True:
            errs.append('テストモードのファイルには "test": true を入れてください')
        print("OK" if not errs else "\n".join(errs))
        return 1 if errs else 0
    if len(sys.argv) >= 2 and sys.argv[1] == "preflight":
        import argparse

        ap = argparse.ArgumentParser(prog="python -m scanner.ai_decision preflight")
        ap.add_argument("input")
        ap.add_argument("--trigger", choices=("fire", "schedule"), default="schedule")
        ap.add_argument("--test", action="store_true")
        a = ap.parse_args(sys.argv[2:])
        ok, lines = preflight(Path(a.input), a.trigger, a.test)
        print("\n".join(lines))
        return 0 if ok else 3
    if len(sys.argv) >= 2 and sys.argv[1] == "apply":
        for line in apply_all():
            print(line)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
