"""AIアナリストの判断（decisions/*.json）の確認・取り込み。

  python -m scanner.ai_decision preflight /tmp/input.json --trigger schedule [--test]  # AIが最初に使う（続けるか・出力先）
  python -m scanner.ai_decision validate decisions/2026-11-04-1712.json   # 形式チェック（AIが push 前に使う）
  python -m scanner.ai_decision apply                                      # Actions：未取り込みの判断を取り込む

保有の自動記録は「注文締切前」かつ「16:30〜23:30 に届いた」本番の判断だけ。
それ以外（深夜・朝・昼）は「参考（起きていれば）」としてLINEで送るだけで、holdings.csv・cash.csv は変えない。

  python -m scanner.ai_decision watch                                      # Actions（22:30）：夜の判断が届いていなければLINE
  python -m scanner.ai_decision request-scan                               # AI：入力が古いとき、スキャンを頼む
  python -m scanner.ai_decision wait-input                                 # AI：入力が新しくなるまで最長100秒待つ（exit 0＝新しい／4＝まだ）
  python -m scanner.ai_decision skip --reason "…"                         # AI：「今夜は見送り」を作って push（最後の手段）
  python -m scanner.ai_decision publish decisions/….json                   # AI：判断ファイル（.md も）を push

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
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .units import fmt as ufmt
from .config import AI_NIGHT_RUN, BUDGET_PER_TERM, DATA_DIR, RECORD_FROM, RECORD_UNTIL, ROOT

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
    rep = d.get("replaces")
    if rep is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{4}", str(rep)):
        errs.append("replaces は入れ替える前の判断のID（例：2026-11-13-2118）")
    ask = d.get("ask_human", [])
    if ask is not None and (not isinstance(ask, list) or any(not isinstance(a, dict) or not a.get("code") for a in ask)):
        errs.append("ask_human は [{\"code\": \"1869\", \"question\": \"…\"}] のような形にしてください")
    return errs


def _human_ok(d: dict, now: datetime) -> bool:
    """人の確認の答えで起動された判断か（締切前なら 23:30 以降でも記録してよい）。"""
    if d.get("trigger") != "human":
        return False
    try:
        from .line_actions import load_state

        for q in load_state().get("questions", []):
            if q.get("base_day") == d.get("base_day") and q.get("status") == "answered" and q.get("answered_at"):
                if (now - datetime.fromisoformat(q["answered_at"])).total_seconds() <= 3 * 3600:
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


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
            notes.append(f"売り {code} {ufmt(code, sold)} × {pr:,.0f}円 を記録" + (f"（保有が{left:,}株足りません）" if left else ""))
    for o in d.get("orders", []):
        if o["side"] != "buy":
            continue
        code, sh, pr = str(o["code"]).strip().upper(), int(o["shares"]), float(o["price"])
        if sh * pr > cash + 0.5:
            notes.append(f"買い {code} は現金不足（必要 {sh * pr:,.0f}円・現金 {cash:,.0f}円）のため記録しませんでした")
            continue
        hrows.append([code, str(sh), f"{pr:g}", stamp, f"AI {d.get('id', '')}".strip()])
        cash -= sh * pr
        notes.append(f"買い {code} {ufmt(code, sh)} × {pr:,.0f}円 を記録")
    cash_row[1] = f"{cash:.0f}"
    _write_csv(HOLDINGS_CSV, hh, hrows)
    _write_csv(CASH_CSV, ch, crows)
    return notes


HEAD = {"record": "【AIの結論】", "ref": "【参考（起きていれば）】AIの結論：",
        "late": "【締切後・参考】AIの結論：", "test": "【テスト】AIの結論："}


def recordable(now: datetime) -> bool:
    """この時刻に届いた判断を保有に自動記録するか（16:30〜23:30）。"""
    return RECORD_FROM <= now.time().replace(tzinfo=None) <= RECORD_UNTIL


def line_text(d: dict, mode: str = "record") -> str:
    """mode：record（記録した本番）/ ref（23:30以降などの参考）/ late（締切後）/ test。"""
    from .notify import page_url

    out = [f"{HEAD[mode]}{d.get('summary', '')}"]
    dl = d.get("deadline")
    if dl:
        try:
            t = datetime.fromisoformat(dl)
            out[0] += f"（締切 {t:%m/%d %H:%M}）"
        except ValueError:
            pass
    for o in d.get("orders", []):
        side = "買い" if o["side"] == "buy" else "売り"
        out.append(f"{side}：{o['code']} {o.get('name', '')} {ufmt(o['code'], o['shares'], o.get('unit'))}（今日の終値{float(o['price']):,.0f}円）"
                   f" 自信度{o.get('confidence', '')}")
        out.append(f"　理由：{str(o.get('reason', ''))[:80]}")
    if not d.get("orders"):
        out.append("今夜は見送り（注文なし）")
    for s in d.get("skipped", [])[:3]:
        out.append(f"見送り：{s.get('code', '')} {s.get('name', '')} {str(s.get('reason', ''))[:40]}")
    if mode == "test":
        out.append(f"※テストです（基準日 {d.get('base_day', '')} のデータ）。注文はしないでください。保有・現金の記録は変えていません")
    elif mode == "ref":
        out.append(f"※{RECORD_UNTIL:%H:%M}を過ぎた（または夜以外の）指示なので、保有・現金にはまだ記録していません。"
                   "起きていて注文したときは、ボタン（またはページの holdings.csv / cash.csv）で記録してください")
    elif mode == "late":
        out.append("※注文締切を過ぎているので、保有・現金には記録していません")
    elif d.get("orders"):
        out.append("寝る前に注文してください。違う注文・注文しなかったときは、ボタン（またはページの holdings.csv / cash.csv）で直してください")
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
                rec["mode"] = "late"
                rec["status"] = "注文締切を過ぎていたため、保有の自動記録はしませんでした"
            elif not recordable(now) and not _human_ok(d, now):
                rec["mode"] = "ref"
                rec["status"] = (f"参考（{RECORD_FROM:%H:%M}〜{RECORD_UNTIL:%H:%M}の外に届いた指示）のため、"
                                 "保有の自動記録はしませんでした")
            else:
                from .line_actions import make_patch, replace_decision, snapshot

                ok, why = (True, "")
                if d.get("replaces"):
                    # 前の判断（ユーザー未確認）の自動記録を取り消してから、こちらを記録する（2026-11-13：
                    # 21:20の注文が記録済みのため、あとのTOB（岐阜造園）を「枠不足」で見送ってしまった）
                    ok, why = replace_decision(str(d["replaces"]), d["id"])
                if not ok:
                    rec["mode"] = "ref"
                    rec["notes"].append(f"入れ替えできません（{why}）")
                    rec["status"] = "入れ替え前の注文に返事済みのため、参考として送りました（保有には記録していません）"
                else:
                    rec["mode"] = "record"
                    if why:
                        rec["notes"].append(why)
                    pre = snapshot()
                    rec["notes"] += apply_to_portfolio(d, now)
                    rec["patch"] = make_patch(pre, snapshot())
                    rec["status"] = "取り込み済み（保有に記録）"
            d["deadline"] = d.get("deadline") or deadline.isoformat()
        d["_apply"] = rec
        if not test and not errs:
            from .ai_input import mark_handled

            mark_handled(d["base_day"])  # 調べ済みの候補では、あとのスキャンからAIを起動しない
        if not test and not errs and rec.get("mode") in ("record", "ref"):
            from .line_actions import register_decision

            register_decision(d, rec["mode"], rec.pop("patch", None))
        rec.pop("patch", None)
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
            log.append(_send_line(d, rec, errs, test, key, now))
    APPLIED.parent.mkdir(parents=True, exist_ok=True)
    APPLIED.write_text(json.dumps(applied[-500:], ensure_ascii=False, indent=0), encoding="utf-8")
    if real:
        try:
            from .line_actions import write_status

            write_status()
        except Exception as e:  # noqa: BLE001
            log.append(f"残高の文の作成に失敗: {type(e).__name__}: {e}")
    # Actions 用：本番の判断があったときだけページを作り直す
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"rebuild={'true' if real else 'false'}\n")
    return log


def _send_line(d: dict, rec: dict, errs: list[str], test: bool, key: str, now: datetime) -> str:
    """AIの結論のLINE。売り・買いの注文があるときだけ、注文とボタンを1通にまとめて送る（2026-11-10 ユーザー指定）。
    見送り（注文なし）・締切後は送らない（LINE_NO_ORDER=True なら見送りも今までの形で送る）。"""
    from .config import LINE_ASK_HUMAN, LINE_NO_ORDER
    from .notify import send

    mode = rec.get("mode", "")
    if errs:
        return send(f"{'【テスト】' if test else '【名証スキャナー】'}AIの判断ファイル {key} に形式エラー：{errs[0]}",
                    "ai_test" if test else "failure")
    if test:
        return send(line_text(d, "test"), "ai_test")
    if mode not in ("record", "ref"):
        return "締切後の判断なので、LINEは送りません"
    extra: list[dict] = []
    if d.get("orders"):
        from .line_actions import order_message

        extra.append(order_message(d, mode))
    elif LINE_NO_ORDER:
        from .line_actions import buttons, enabled

        extra = [{"type": "text", "text": line_text(d, mode)[:4900]}] + ((buttons(d, mode) or []) if enabled() else [])
    if LINE_ASK_HUMAN and extra:
        ask = _ask_human(d, now, mode)
        if ask:
            extra.append(ask)  # 最後のメッセージに付けたボタン（クイックリプライ）だけがLINEに出る
    if not extra:
        return "注文がない（見送り）ので、LINEは送りません"
    return send("", "ai", extra)


def _ask_human(d: dict, now: datetime, mode: str) -> dict | None:
    """見送った候補のうち確認できなかったものを、LINEで人に確認を頼むメッセージ（締切前だけ・1晩2件まで）。
    答えのボタン（クイックリプライ）付き。頼むものがなければ None。"""
    if mode not in ("record", "ref"):
        return None
    try:
        from .human_check import ask_text, pick_questions, quick_reply, register
        from .line_actions import enabled, load_state, save_state

        if not enabled():
            return None  # 中継役（返信を受け取るしくみ）がまだ動いていない
        qs = pick_questions(d)
        if not qs:
            return None
        st = load_state()
        new = register(qs, d, st, now)
        save_state(st)
        if not new:
            return None
        msg = {"type": "text", "text": ask_text(new)[:4900]}
        qr = quick_reply(new, d.get("base_day", ""))
        if qr:
            msg["quickReply"] = qr
        return msg
    except Exception:  # noqa: BLE001
        return None


def _term_history(base_day: str) -> list[dict]:
    """このタームにすでに取り込んだ本番の判断（main の data/ai/history.jsonl。なければ手元）。"""
    import subprocess

    text = ""
    try:
        text = subprocess.run(["git", "show", "origin/main:data/ai/history.jsonl"], cwd=ROOT, capture_output=True,
                              text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        pass
    if not text and HISTORY.exists():
        text = HISTORY.read_text(encoding="utf-8")
    out = []
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("base_day") == base_day and not d.get("_apply", {}).get("errors"):
            out.append(d)
    return out


def preflight(input_path: Path, trigger: str, test: bool, now: datetime | None = None,
              history: list[dict] | None = None) -> tuple[bool, list[str]]:
    """AIが最初に実行する確認。（続けるか, 表示する行）。"""
    from .calendar_jp import base_day, is_business_day, now_jst, order_deadline

    now = now or now_jst()
    tb = base_day(now)
    td = order_deadline(tb)
    lines = [f"now={now:%Y-%m-%dT%H:%M%z}", f"mode={'test' if test else 'normal'}", f"trigger={trigger}",
             f"business_day={'true' if is_business_day(now.date()) else 'false'}",
             f"term_base_day={tb.isoformat()}", f"term_deadline={td.isoformat()}"]
    try:
        d = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        d = {}
        lines.append(f"input_error={type(e).__name__}: {e}（入力なしで判断し、注文は出さず見送りにする）")
    term_open = now <= td
    lines.append(f"term_open={'true' if term_open else 'false'}")
    if d:
        lines.append(f"input_base_day={d.get('base_day', '')}")
        try:
            age = (now - datetime.fromisoformat(d["generated_at"])).total_seconds() / 3600
            lines.append(f"input_age_hours={age:.1f}")
        except (KeyError, TypeError, ValueError):
            lines.append("input_age_hours=不明")
        if not test and d.get("base_day") != tb.isoformat():
            lines.append("input_mismatch=true（入力が今のタームのものではない。スキャンが動いていない可能性）")
            lines.append("next=入力が古いので、①`python -m scanner.ai_decision request-scan` でスキャンを頼み、"
                         "②`python -m scanner.ai_decision wait-input` を exit 0 になるまで最大6回くり返し（1回約100秒）、"
                         "③新しくなったら入力を取り直して preflight からやり直す。"
                         "④6回待っても古いままなら `python -m scanner.ai_decision skip --reason \"スキャンのデータが取れていない\"` "
                         "で見送りの判断を出して終わる（これで必ずLINEが1通届く）")
    if test:
        tb_s = d.get("base_day") or tb.isoformat()
        lines.append(f"use_base_day={tb_s}")
        lines.append(f"use_deadline={d.get('deadline') or order_deadline(date.fromisoformat(tb_s)).isoformat()}")
    else:
        lines.append(f"use_base_day={tb.isoformat()}")
        lines.append(f"use_deadline={td.isoformat()}")
        lines.append(f"recorded={'true' if recordable(now) and term_open else 'false'}"
                     f"（{RECORD_FROM:%H:%M}〜{RECORD_UNTIL:%H:%M}に届いた注文だけ保有に記録。false なら参考として送られる）")
        hist = _term_history(tb.isoformat()) if history is None else history
        from .line_actions import load_state, user_status

        lst = load_state()["decisions"]
        free_cash, free_term = 0.0, 0.0
        replaceable = []
        for h in hist:
            ap = h.get("_apply", {})
            od = "、".join(f"{'買い' if o['side'] == 'buy' else '売り'}{o['code']} {ufmt(o['code'], o['shares'], o.get('unit'))}"
                          for o in h.get("orders", [])) or "注文なし"
            us = user_status(h.get("id", ""))
            lines.append(f"term_decision={h.get('id', '')}：{od}（{ap.get('status', '')}" + (f"／{us}" if us else "") + "）")
            ent = lst.get(h.get("id", "")) or {}
            if ent.get("status") == "recorded" and ent.get("patch") and h.get("orders"):
                replaceable.append(h["id"])
                free_cash -= float(ent["patch"].get("cash_delta", 0))
                free_term += sum(float(r[1]) * float(r[2]) for r in ent["patch"].get("added", [])
                                 if len(r) >= 4 and r[3] == tb.isoformat())
        if not hist:
            lines.append("term_decision=なし（このタームの判断はまだない）")
        if replaceable:
            pf = d.get("portfolio", {}) if d else {}
            try:
                lines.append(f"if_replaced={','.join(replaceable)} を取り消したときの今タームの残り枠="
                             f"{float(pf.get('term_left', 0)) + free_term:,.0f}円・現金={float(pf.get('cash', 0)) + free_cash:,.0f}円"
                             "（入れ替えるときはこの額で計算し、判断ファイルに \"replaces\": \"<ID>\" を書く）")
            except (TypeError, ValueError):
                pass
    sub = "decisions/test/" if test else "decisions/"
    lines.append(f"output={sub}{now:%Y-%m-%d-%H%M}")
    if test:
        lines.append("result=GO テストモード（休業日・締切に関係なく最後まで判断する）")
        return True, lines
    if not term_open:
        lines.append("result=STOP 今は注文できるタームの外です（営業日の 7:59〜15:30）")
        return False, lines
    lines.append("result=GO 判断ファイルを必ず1つ出す（見送りでも）")
    return True, lines


def watch(now: datetime | None = None, history_text: str | None = None) -> str:
    """22:30：今夜（21:00以降）の本番の判断が届いていなければLINEで知らせる。"""
    from .calendar_jp import base_day, now_jst, order_deadline

    now = now or now_jst()
    tb = base_day(now)
    if now > order_deadline(tb):
        return "注文できるタームの外なので確認しない"
    t = now.time().replace(tzinfo=None)
    if not (datetime.strptime("21:45", "%H:%M").time() <= t <= RECORD_UNTIL):
        return "確認する時間（21:45〜23:30）の外なので何もしない（遅れて動いた予備の起動など）"
    since = datetime.combine(now.date(), AI_NIGHT_RUN, tzinfo=now.tzinfo) - timedelta(minutes=15)
    if history_text is None:
        history_text = HISTORY.read_text(encoding="utf-8") if HISTORY.exists() else ""
    for line in history_text.splitlines():
        try:
            ap = json.loads(line).get("_apply", {})
            if datetime.fromisoformat(ap["applied_at"]) >= since:
                return "今夜の判断は届いています"
        except (ValueError, KeyError, TypeError):
            continue
    from .notify import already_sent, notify_once

    key = f"ai_night_missing:{now.date()}"
    if already_sent(key):
        return "今夜はすでに知らせて、AIの再起動も済んでいます"
    # 予備：AIをもう一度起動する（入力が古ければ、AIがスキャンを頼んで待つ。23:30までに届けば保有に記録される）
    refire = ""
    try:
        from .ai_fire import fire

        ok, msg = fire(f"再起動：{AI_NIGHT_RUN:%H:%M}の定時起動の判断が {now:%H:%M} までに届いていないため。"
                       "入力は main の data/analyst_input.json を使い、指示書どおり必ず判断ファイルを1つ出すこと。")
        refire = "AIをもう一度起動しました（数分で判断が届くはずです）。" if ok else f"AIの再起動はできませんでした（{msg}）。"
    except Exception as e:  # noqa: BLE001
        refire = f"AIの再起動はできませんでした（{type(e).__name__}）。"
    return notify_once(key,
                       f"【名証スキャナー】{AI_NIGHT_RUN:%H:%M}のAIの判断が届いていません。{refire}"
                       "届かなければ今夜は見送るか、ページの「今夜の結論」を見て判断してください。", "failure")


SCAN_REQUEST_BRANCH = "claude/scan-request"
DECISION_BRANCH = "claude/ai-decisions"


def _git(*args: str, check: bool = True) -> str:
    import subprocess

    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=120)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} に失敗：{(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def request_scan(now: datetime | None = None) -> str:
    """claude/scan-request に push してスキャンを頼む（GitHub Actions の scan-request が main で scan を起動）。"""
    import tempfile

    from .calendar_jp import now_jst

    now = now or now_jst()
    _git("fetch", "-q", "origin", "main")
    with tempfile.TemporaryDirectory() as tmp:
        _git("worktree", "add", "-q", "-f", "--detach", tmp, "origin/main")
        try:
            req = Path(tmp) / "scan_requests" / "last.txt"
            req.parent.mkdir(exist_ok=True)
            req.write_text(f"{now.isoformat()} AIアナリストがスキャンを依頼\n", encoding="utf-8")
            import subprocess

            def g(*a):
                r = subprocess.run(["git", "-C", tmp, *a], capture_output=True, text=True, timeout=120)
                if r.returncode != 0:
                    raise RuntimeError(f"git {' '.join(a)} に失敗：{(r.stderr or r.stdout).strip()[:300]}")
            g("add", "scan_requests/last.txt")
            g("-c", "user.name=AI analyst", "-c", "user.email=ai-analyst@users.noreply.github.com",
              "commit", "-q", "-m", f"scan request {now:%Y-%m-%d %H:%M}")
            for i in range(4):
                try:
                    g("push", "-q", "-f", "origin", f"HEAD:refs/heads/{SCAN_REQUEST_BRANCH}")
                    break
                except RuntimeError:
                    if i == 3:
                        raise
                    import time

                    time.sleep(2 ** (i + 1))
        finally:
            _git("worktree", "remove", "-f", tmp, check=False)
    return f"スキャンを頼みました（{SCAN_REQUEST_BRANCH} に push）。2〜4分で main の入力が新しくなります"


def wait_input(timeout: int = 100, interval: int = 20, now_fn=None) -> tuple[bool, str]:
    """main の data/analyst_input.json が今のタームのものになるまで待つ（最長 timeout 秒）。"""
    import time

    from .calendar_jp import base_day, now_jst

    now_fn = now_fn or now_jst
    tb = base_day(now_fn()).isoformat()
    end = time.monotonic() + timeout
    last = ""
    while True:
        try:
            _git("fetch", "-q", "origin", "main")
            d = json.loads(_git("show", "origin/main:data/analyst_input.json"))
            last = f"input_base_day={d.get('base_day')} generated_at={d.get('generated_at', '')[:16]}"
            if d.get("base_day") == tb:
                return True, f"OK 入力が新しくなりました（{last}）。入力を取り直して preflight からやり直してください"
        except Exception as e:  # noqa: BLE001
            last = f"読めませんでした：{e}"
        if time.monotonic() + interval > end:
            return False, f"WAIT まだ古いままです（{last}・今のターム {tb}）。もう一度 wait-input を実行してください"
        time.sleep(interval)


def make_skip(reason: str, trigger: str = "schedule", now: datetime | None = None) -> Path:
    """「今夜は見送り」の判断ファイルを作る（入力が古い・取れないときの最後の手段）。"""
    from .calendar_jp import base_day, now_jst, order_deadline

    now = now or now_jst()
    tb = base_day(now)
    path = DECISIONS / f"{now:%Y-%m-%d-%H%M}.json"
    d = {"version": 1, "created_at": now.isoformat(timespec="seconds"), "base_day": tb.isoformat(),
         "deadline": order_deadline(tb).isoformat(), "trigger": trigger,
         "summary": f"今夜は見送り（{reason}）", "orders": [], "skipped": [],
         "notes": "AIアナリストが入力を確認できなかったため、注文を出さずに見送りにしました。ページの「今夜の結論」も確認してください。",
         "data_gaps": [reason]}
    errs = validate(d)
    if errs:
        raise ValueError("見送りの判断ファイルが形式チェックを通りません：" + " / ".join(errs))
    DECISIONS.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    path.with_suffix(".md").write_text(f"# {d['summary']}\n\n- 理由：{reason}\n- 作成：{d['created_at']}\n", encoding="utf-8")
    return path


def publish(paths: list[Path]) -> str:
    """判断ファイル（.json と .md）を claude/ai-decisions に push する（main の最新の上に載せる）。"""
    import shutil
    import tempfile

    for p in paths:
        if p.suffix == ".json":
            errs = validate(json.loads(p.read_text(encoding="utf-8")))
            if errs:
                raise ValueError(f"{p.name} が形式チェックを通りません：" + " / ".join(errs))
    _git("fetch", "-q", "origin", "main")
    import subprocess

    with tempfile.TemporaryDirectory() as tmp:
        _git("worktree", "add", "-q", "-f", "--detach", tmp, "origin/main")
        try:
            def g(*a):
                r = subprocess.run(["git", "-C", tmp, *a], capture_output=True, text=True, timeout=120)
                if r.returncode != 0:
                    raise RuntimeError(f"git {' '.join(a)} に失敗：{(r.stderr or r.stdout).strip()[:300]}")
            files = []
            for p in paths:
                rel = p.resolve().relative_to(ROOT.resolve())
                dst = Path(tmp) / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                files.append(str(rel))
            g("add", *files)
            g("-c", "user.name=AI analyst", "-c", "user.email=ai-analyst@users.noreply.github.com",
              "commit", "-q", "-m", f"AI decision {paths[0].stem}")
            for i in range(4):
                try:
                    g("push", "-q", "-f", "origin", f"HEAD:refs/heads/{DECISION_BRANCH}")
                    break
                except RuntimeError:
                    if i == 3:
                        raise
                    import time

                    time.sleep(2 ** (i + 1))
        finally:
            _git("worktree", "remove", "-f", tmp, check=False)
    return f"push しました（{DECISION_BRANCH}）：" + "、".join(p.name for p in paths)


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
    if len(sys.argv) >= 2 and sys.argv[1] == "request-scan":
        print(request_scan())
        return 0
    if len(sys.argv) >= 2 and sys.argv[1] == "wait-input":
        ok, msg = wait_input(int(sys.argv[2]) if len(sys.argv) >= 3 else 100)
        print(msg)
        return 0 if ok else 4
    if len(sys.argv) >= 2 and sys.argv[1] == "skip":
        import argparse

        ap = argparse.ArgumentParser(prog="python -m scanner.ai_decision skip")
        ap.add_argument("--reason", default="スキャンのデータが取れていない")
        ap.add_argument("--trigger", choices=("fire", "schedule"), default="schedule")
        a = ap.parse_args(sys.argv[2:])
        p = make_skip(a.reason, a.trigger)
        print(f"作成：{p.relative_to(ROOT)}")
        print(publish([p, p.with_suffix(".md")]))
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == "publish":
        paths = [Path(x) for x in sys.argv[2:]]
        paths += [p.with_suffix(".md") for p in paths if p.suffix == ".json" and p.with_suffix(".md").exists()
                  and p.with_suffix(".md") not in paths]
        print(publish(paths))
        return 0
    if len(sys.argv) >= 2 and sys.argv[1] == "watch":
        print(watch())
        return 0
    if len(sys.argv) >= 2 and sys.argv[1] == "apply":
        for line in apply_all():
            print(line)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
