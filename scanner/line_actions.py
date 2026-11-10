"""LINEのボタン・返信で、保有の記録（holdings.csv・cash.csv）を直す。残高の文（data/status.json）も作る。

流れ：LINE → 中継役（Cloudflare Workers：worker/line-webhook.js）→ GitHub Actions（line-event.yml）→ このモジュール
  python -m scanner.line_actions process    # 環境変数 PAYLOAD（Workerから届いたJSON）を処理して記録を直す
  python -m scanner.line_actions send       # 処理結果をLINEで送る（記録しました：保有…、現金…円）
  python -m scanner.line_actions status     # data/status.json（「残高」への返事）を作り直す

ボタン（AIの「今夜の注文」のLINEに付ける）：
  ［指示どおり注文した］ act=done  … 自動記録のまま（参考で記録していなければ、ここで記録する）
  ［注文しなかった］     act=none  … その注文の自動記録を取り消す
  ［別の注文をした］     act=other … 自動記録を取り消し、「買い 4445 1000」「売り 6999 全部」のような返信を待つ
返信（文字）：「買い／売り・コード・株数（全部も可）・値段（なければその日の終値）」を読み取って記録する。
「ボタン」と送ると、いちばん新しいAIの注文のボタンをもう一度送る。
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from .units import fmt as ufmt
from .config import DATA_DIR

STATE = DATA_DIR / "ai" / "line_state.json"
STATUS = DATA_DIR / "status.json"
RESULT = Path(os.environ.get("LINE_RESULT_FILE", "/tmp/line_result.json"))
HELP = ("使える言葉：\n・残高\n・買い 4445 10単位（または 1000株。値段を書かなければ、その日の終値で記録）\n・売り 6999 全部\n"
        "・買い 4445 10単位 1665円\n・ボタン（いちばん新しいAIの注文のボタンをもう一度出す）")


# ---------------- 状態ファイル ----------------

def load_state() -> dict:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        st = {}
    st.setdefault("decisions", {})
    st.setdefault("seen", [])
    st.setdefault("manual", [])
    st.setdefault("awaiting", None)
    return st


def save_state(st: dict) -> None:
    st["seen"] = st["seen"][-300:]
    st["manual"] = st["manual"][-200:]
    st["decisions"] = dict(list(st["decisions"].items())[-120:])
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------- 保有と現金（差分で記録・取り消し） ----------------

def snapshot() -> tuple[list[list[str]], float | None]:
    from .ai_decision import _read_csv
    from .portfolio import CASH_CSV, HOLDINGS_CSV

    _, rows = _read_csv(HOLDINGS_CSV)
    _, crows = _read_csv(CASH_CSV)
    cash = next((float(r[1].replace(",", "")) for r in crows if r and r[0].strip().lower() == "cash"), None)
    return [list(r) for r in rows if r], cash


def make_patch(pre, post) -> dict:
    """前後の違い（取り消しに使う）。行は文字列のリストで比べる。"""
    a, b = Counter(tuple(r) for r in pre[0]), Counter(tuple(r) for r in post[0])
    return {"removed": [list(r) for r in (a - b).elements()], "added": [list(r) for r in (b - a).elements()],
            "cash_delta": (post[1] or 0) - (pre[1] or 0)}


def _write(rows: list[list[str]], cash: float | None) -> None:
    from .ai_decision import _read_csv, _write_csv
    from .portfolio import CASH_CSV, HOLDINGS_CSV

    hh, _ = _read_csv(HOLDINGS_CSV)
    _write_csv(HOLDINGS_CSV, hh or ["code,shares,buy_price,buy_date,memo"], rows)
    if cash is not None:
        ch, crows = _read_csv(CASH_CSV)
        for r in crows:
            if r and r[0].strip().lower() == "cash":
                r[1] = f"{cash:.0f}"
        _write_csv(CASH_CSV, ch, crows)


def revert_patch(patch: dict) -> list[str]:
    """記録を取り消す（あとから別の変更があっても、その注文の分だけ戻す）。"""
    rows, cash = snapshot()
    notes = []
    for r in patch.get("added", []):
        if r in rows:
            rows.remove(r)
        else:
            notes.append(f"{r[0]} の行が見つからなかったので、そのままにしました")
    rows += [list(r) for r in patch.get("removed", [])]
    if cash is not None:
        cash -= patch.get("cash_delta", 0)
    _write(rows, cash)
    return notes


def apply_orders(orders: list[dict], stamp: str, memo: str) -> tuple[dict, list[str]]:
    """orders（side・code・shares（"all"可）・price）を記録し、(patch, メモ) を返す。"""
    from .ai_decision import apply_to_portfolio

    pre = snapshot()
    fixed = []
    for o in orders:
        o = dict(o)
        if o.get("shares") == "all":
            o["shares"] = sum(int(float(r[1])) for r in pre[0] if r[0].strip().upper() == o["code"])
        if int(o["shares"]) > 0:
            fixed.append(o)
    notes = apply_to_portfolio({"base_day": stamp, "orders": fixed, "id": memo}, datetime.now()) if fixed else []
    # apply_to_portfolio のメモ欄は「AI <id>」になるので、手入力のときは書き換える
    if not memo.startswith("20"):
        rows, cash = snapshot()
        for r in rows:
            if len(r) >= 5 and r[4] == f"AI {memo}":
                r[4] = memo
        _write(rows, cash)
    return make_patch(pre, snapshot()), notes


# ---------------- 返信の読み取り ----------------

CODE_RE = re.compile(r"(?<![0-9A-Z])([0-9][0-9A-Z]{3})(?![0-9A-Z])")


def parse_orders(text: str, known_codes: set[str] | None = None) -> list[dict]:
    """「買い 4445 1000」「6999を全部売った」「買 4445 1,000株 1665円」などを読む。"""
    t = unicodedata.normalize("NFKC", text).upper().replace("，", ",")
    segs = []
    for part in re.split(r"[\n、。;；/]", t):
        marks = [m.start() for m in re.finditer(r"買|売|BUY|SELL", part)]
        # 1つの区切りに「買」「売」が2回以上あるときだけ、2回目以降の前で分ける
        cuts = [0] + [i for i in marks[1:] if part[:i].strip()] + [len(part)]
        segs += [part[a:b] for a, b in zip(cuts, cuts[1:]) if part[a:b].strip()]
    out = []
    for seg in segs:
        side = "buy" if re.search(r"買|BUY|かい|カイ", seg) else "sell" if re.search(r"売|SELL|うり|ウリ", seg) else ""
        if not side:
            continue
        seg2 = re.sub(r"(\d),(\d{3})", r"\1\2", seg)
        seg2 = re.sub(r"(\d+(?:\.\d+)?)\s*(K|千)", lambda m: str(int(float(m[1]) * 1000)), seg2)
        code = None
        for m in CODE_RE.finditer(seg2):
            c = m[1]
            nxt = seg2[m.end():m.end() + 1]
            if nxt in ("株", "円"):
                continue
            if known_codes is None or c in known_codes or not c.isdigit():
                code = c
                seg2 = seg2[:m.start()] + " " + seg2[m.end():]
                break
        if code is None:
            continue
        shares = "all" if re.search(r"全部|全株|すべて|全て|ALL|ぜんぶ", seg2) else None
        price = None
        m = re.search(r"(\d+(?:\.\d+)?)\s*円|[@＠単価]\s*(\d+(?:\.\d+)?)", seg2)
        if m:
            price = float(m[1] or m[2])
            seg2 = seg2[:m.start()] + " " + seg2[m.end():]
        if shares is None:
            mu = re.search(r"(\d+)\s*単位", seg2)
            if mu:
                from .units import unit_of

                shares = int(mu[1]) * unit_of(code)
                seg2 = seg2[:mu.start()] + " " + seg2[mu.end():]
        if shares is None:
            m = re.search(r"(\d+)\s*株", seg2)
            nums = [int(x) for x in re.findall(r"\d+", seg2)]
            if m:
                shares = int(m[1])
            elif nums:
                shares = nums[0]
                if price is None and len(nums) >= 2:
                    price = float(nums[1])
        if shares is None:
            continue
        out.append({"side": side, "code": code, "shares": shares, "price": price})
    return out


# ---------------- 値段・残高 ----------------

def _now():
    from .calendar_jp import now_jst

    return now_jst()


def latest_quotes(base: date) -> dict:
    """base 以前でいちばん新しい名証の相場表（キャッシュ）。"""
    from .config import CACHE_DIR
    from .sources.nse_prices import _load_cache

    files = sorted((CACHE_DIR / "nse_prices").glob("*.json"))
    for f in reversed(files):
        d = date.fromisoformat(f.stem[:4] + "-" + f.stem[4:6] + "-" + f.stem[6:8])
        if d <= base:
            got = _load_cache(d)
            if got:
                return got[0]
    return {}


def quick_portfolio(now=None):
    """スキャンなしで保有管理を計算する（残高の返事・記録の確認に使う）。"""
    from . import portfolio
    from .calendar_jp import base_day
    from .sources import nse_list

    now = now or _now()
    base = base_day(now)
    universe, _ = nse_list.load(refresh=False)
    ctx = SimpleNamespace(now=now, base=base, quotes=latest_quotes(base), universe=universe, limits={}, disclosures=[])
    res = SimpleNamespace(ctx=ctx, cards=[], tob_rows=[])
    return portfolio.build(res, now)


def status_text(pf=None, now=None) -> str:
    from .calendar_jp import fmt_date
    from .portfolio import TENTH_LINE_PROFIT

    now = now or _now()
    pf = pf or quick_portfolio(now)
    lines = [f"【残高】{fmt_date(now.date())} {now:%H:%M}時点"]
    if pf.holdings:
        for h in pf.holdings:
            price = f"{h.price:,.0f}円" if h.price else "値段不明"
            lines.append(f"・{h.code} {h.name} {ufmt(h.code, h.shares, h.unit)} × {price}＝{h.value:,.0f}円（損益 {h.pnl:+,.0f}円 {h.pnl_pct:+.1f}%）")
    else:
        lines.append("・保有なし")
    lines.append(f"現金：{pf.cash:,.0f}円" if pf.cash is not None else "現金：cash.csv 未入力")
    lines.append(f"保有株の評価額：{pf.market_value:,.0f}円")
    if pf.total is not None:
        gap = pf.profit - TENTH_LINE_PROFIT
        lines.append(f"合計資産：{pf.total:,.0f}円（損益 {pf.profit:+,.0f}円。10位ラインまで {'あと' + format(-gap, ',.0f') + '円' if gap < 0 else '到達'}）")
    if pf.term_open:
        lines.append(f"今タームの残り購入枠：{pf.term_left:,.0f}円（{fmt_date(pf.term_base)}夜〜のターム）")
    else:
        lines.append("今タームの残り購入枠：今は注文できない時間（次のタームは16:30から300万円）")
    lines.append("名証単独銘柄：" + ("購入済み" if pf.single_bought else "未購入（入賞条件）"))
    return "\n".join(lines)


def short_status(pf=None) -> str:
    pf = pf or quick_portfolio()
    hold = "、".join(f"{h.code} {h.name} {ufmt(h.code, h.shares, h.unit)}" for h in pf.holdings) or "なし"
    cash = f"{pf.cash:,.0f}円" if pf.cash is not None else "未入力"
    return f"保有：{hold}／現金 {cash}"


def write_status(pf=None) -> None:
    now = _now()
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps({"updated": now.isoformat(timespec="minutes"), "text": status_text(pf, now)},
                                 ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------- ボタン ----------------

STATUS_NOTE = {"record": "今は記録済みです", "ref": "参考なのでまだ記録していません",
               "custom": "今は「別の注文」の内容で記録しています", "undone": "今は記録していません"}


def enabled() -> bool:
    """中継役（Worker）の設定が済んでいるか（一度でも Worker からイベントが届いたか）。"""
    return bool(load_state().get("enabled"))


def buttons(d: dict, mode: str) -> list[dict]:
    """AIの注文のLINEに付けるボタン（mode：record 記録済み／ref 参考で未記録／custom 別の注文で記録／undone 取り消し済み）。"""
    if mode not in STATUS_NOTE:
        return []
    did = d.get("id", "")
    m = re.fullmatch(r"\d{4}-(\d{2})-(\d{2})-(\d{2})(\d{2})", did)
    when = f"{m[1]}/{m[2]} {m[3]}:{m[4]}" if m else ""
    def pb(label, act):
        return {"type": "postback", "label": label, "data": f"act={act}&id={did}", "displayText": label}
    if d.get("orders"):
        text = f"AIの注文（{when}）はどうしましたか？{STATUS_NOTE[mode]}"
        actions = [pb("指示どおり注文した", "done"), pb("注文しなかった", "none"), pb("別の注文をした", "other")]
    else:
        text = f"AIの結論（{when}）は見送りです。自分で注文したときだけ押してください"
        actions = [pb("別の注文をした", "other")]
    return [{"type": "template", "altText": "注文はどうしましたか？（ボタン）",
             "template": {"type": "buttons", "text": text[:160], "actions": actions}}]


def order_text(d: dict, mode: str = "record") -> str:
    """AIの注文を短く（例：売り ブラザー工業(6448) 7単位／買い 百五銀行(8368) 14単位）。売りが先。"""
    head = "【今夜の注文】"
    try:
        from .calendar_jp import fmt_date

        dl = datetime.fromisoformat(d["deadline"])
        head += f"締切 {fmt_date(dl.date())} {dl:%H:%M}"
    except (KeyError, TypeError, ValueError):
        pass
    lines = [head]
    for o in sorted(d.get("orders", []), key=lambda o: o.get("side") != "sell"):
        try:
            unit = int(o.get("unit") or 0)
            if not unit:
                from .units import unit_of

                unit = int(unit_of(str(o["code"])) or 100)
        except Exception:  # noqa: BLE001
            unit = 100
        n = int(o["shares"]) // unit
        side = "売り" if o.get("side") == "sell" else "買い"
        name = str(o.get("name", "")).strip()[:14]
        lines.append(f"{side} {name + '(' + str(o['code']) + ')' if name else o['code']} {n}単位")
    if mode == "ref":
        lines.append("※まだ記録していません。注文したらボタンを押してください")
    return "\n".join(lines)


def order_message(d: dict, mode: str = "record") -> dict:
    """注文とボタンを1つにまとめたLINEメッセージ（ボタンつきテンプレートは本文160文字まで。長いときは文＋クイックリプライ）。"""
    text = order_text(d, mode)
    if not enabled():
        return {"type": "text", "text": text}
    did = d.get("id", "")

    def pb(label, act):
        return {"type": "postback", "label": label, "data": f"act={act}&id={did}", "displayText": label}
    actions = [pb("指示どおり注文した", "done"), pb("注文しなかった", "none"), pb("別の注文をした", "other")]
    if len(text) <= 160:
        return {"type": "template", "altText": text[:400],
                "template": {"type": "buttons", "text": text, "actions": actions}}
    return {"type": "text", "text": text, "quickReply": {"items": [{"type": "action", "action": a} for a in actions]}}


def register_decision(d: dict, mode: str, patch: dict | None) -> None:
    """ai_decision の取り込み時に呼ぶ：その注文が記録済みかどうかと、取り消し用の差分を覚える。"""
    st = load_state()
    st["decisions"][d.get("id", "")] = {"status": "recorded" if mode == "record" else "not_recorded",
                                        "mode": mode, "patch": patch, "base_day": d.get("base_day")}
    save_state(st)


# ---------------- イベントの処理 ----------------

def _decision(did: str) -> dict | None:
    from .ai_decision import DECISIONS

    p = DECISIONS / f"{did}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    d.setdefault("id", did)
    return d


def _legacy_entry(did: str, d: dict) -> dict:
    """ボタンができる前に取り込んだ注文（差分なし）。買いの行だけ「AI <id>」のメモで戻せる。"""
    from .ai_decision import HISTORY

    mode = "record"
    if HISTORY.exists():
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            try:
                h = json.loads(line)
            except ValueError:
                continue
            if h.get("id") == did:
                mode = h.get("_apply", {}).get("mode", "record")
    if mode != "record":
        return {"status": "not_recorded", "mode": mode, "patch": None, "base_day": d.get("base_day")}
    rows, _ = snapshot()
    added = [r for r in rows if len(r) >= 5 and r[4] == f"AI {did}"]
    sold = sum(int(o["shares"]) * float(o["price"]) for o in d.get("orders", []) if o["side"] == "sell")
    bought = sum(int(r[1]) * float(r[2]) for r in added)
    return {"status": "recorded", "mode": "record", "base_day": d.get("base_day"),
            "patch": {"removed": [], "added": added, "cash_delta": sold - bought,
                      "legacy_note": "売りの取り消しは元の買値が分からないため戻せません" if sold else ""}}


def _latest_with_orders() -> dict | None:
    """いちばん新しいAIの注文のうち、このタームで注文（買い・売り）があるもの。"""
    from .ai_decision import HISTORY
    from .calendar_jp import base_day

    tb = base_day(_now()).isoformat()
    best = None
    if HISTORY.exists():
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            try:
                h = json.loads(line)
            except ValueError:
                continue
            if h.get("base_day") == tb and h.get("orders") and not h.get("_apply", {}).get("errors"):
                best = h
    return _decision(best["id"]) if best else None


def _price_for(code: str, did: str | None, now) -> float | None:
    from .calendar_jp import base_day

    if did:
        d = _decision(did)
        for o in (d or {}).get("orders", []):
            if str(o["code"]).upper() == code:
                return float(o["price"])
    q = latest_quotes(base_day(now)).get(code)
    return q.contest_price if q else None


def process(payload: dict, now=None) -> dict:
    """Worker から届いた1件を処理する。返り値：{"text": LINEで送る文, "changed": bool}。"""
    from .calendar_jp import base_day
    from .sources import nse_list

    now = now or _now()
    st = load_state()
    eid = str(payload.get("event_id") or "")
    if eid and eid in st["seen"]:
        return {"text": "", "changed": False}
    if eid:
        st["seen"].append(eid)
    st["enabled"] = True  # 中継役（Worker）から届いた＝ボタンを付けてよい
    kind = payload.get("type")
    changed = False
    msg = ""
    extra: list[dict] = []
    result_fire = ""

    if kind == "postback":
        q = dict(x.split("=", 1) for x in str(payload.get("data", "")).split("&") if "=" in x)
        act, did = q.get("act", ""), q.get("id", "")
        d = _decision(did) if act != "answer" else None
        if act == "answer":
            # 確認のお願いのボタン（例：act=answer&code=7389&k=revision&v=up&b=2026-11-10）
            msg, fire, changed = _button_answer(q, st, now)
            if fire:
                result_fire = fire
        elif d is None:
            msg = "その注文が見つかりませんでした。"
        else:
            ent = st["decisions"].get(did) or _legacy_entry(did, d)
            if act == "done" and ent["status"] == "custom":
                msg = "今は「別の注文」の内容で記録しています。直すときは「別の注文をした」を押して、もう一度返信してください。"
            elif act == "done":
                if ent["status"] in ("recorded", "confirmed"):
                    ent["status"] = "confirmed"
                    msg = "了解です。AIの注文どおりの記録のままにしました。"
                else:
                    patch, notes = apply_orders(d.get("orders", []), d["base_day"], did)
                    ent.update(status="recorded", patch=patch)
                    changed = True
                    msg = "AIの注文どおりに記録しました（" + "／".join(notes) + "）。"
            elif act in ("none", "other"):
                if ent["status"] in ("recorded", "confirmed") and ent.get("patch"):
                    notes = revert_patch(ent["patch"])
                    ent["status"] = "undone"
                    changed = True
                    msg = "AIの注文の記録を取り消しました。" + ("（" + "／".join(notes) + "）" if notes else "")
                    if ent["patch"].get("legacy_note"):
                        msg += ent["patch"]["legacy_note"]
                else:
                    msg = "この注文はもともと記録していません。"
                if act == "other":
                    st["awaiting"] = {"decision": did, "since": now.isoformat()}
                    msg += "\n実際にした注文を返信してください（例：買い 4445 10単位／売り 6999 全部／買い 4445 10単位 1665円）。"
            else:
                msg = "ボタンの内容が分かりませんでした。"
            st["decisions"][did] = ent

    elif kind == "text":
        text = str(payload.get("text", "")).strip()
        if re.fullmatch(r"(ボタン|ぼたん|BUTTON)", unicodedata.normalize("NFKC", text).upper()):
            from .ai_decision import LATEST

            try:
                d = _latest_with_orders() or json.loads(LATEST.read_text(encoding="utf-8"))
                ent = st["decisions"].get(d["id"]) or _legacy_entry(d["id"], d)
                msg = f"いちばん新しいAIの注文（{d.get('summary', '')}）のボタンです。"
                mode = {"recorded": "record", "confirmed": "record", "custom": "custom", "undone": "undone"}.get(ent["status"], "ref")
                extra = buttons(d, mode)
            except Exception:  # noqa: BLE001
                msg = "AIの注文がまだありません。"
        else:
            from .human_check import ANSWER_HEAD, match_answer

            universe, _ = nse_list.load(refresh=False)
            orders = parse_orders(text, set(universe) or None)
            q, ans = (match_answer(text, st, base_day(now).isoformat())
                      if ANSWER_HEAD.match(unicodedata.normalize("NFKC", text)) or not orders else (None, ""))
            if q is not None:
                msg, fire = _answer(q, ans, st, now)
                changed = True
                if fire:
                    result_fire = fire
            elif not orders:
                msg = "読み取れませんでした。\n" + HELP
            else:
                aw = st.get("awaiting") or {}
                did = aw.get("decision")
                missing = []
                for o in orders:
                    if o["price"] is None:
                        o["price"] = _price_for(o["code"], did, now)
                        if o["price"] is None:
                            missing.append(o["code"])
                    o["unit"] = universe[o["code"]].unit if o["code"] in universe else 100
                    o["confidence"], o["reason"] = "高", "LINEでの手入力"
                if missing:
                    msg = f"{'・'.join(missing)} の値段が分からないので、「買い {missing[0]} 1000株 1665円」のように値段も書いてください。"
                else:
                    stamp = (date.fromisoformat(st["decisions"][did]["base_day"]).isoformat()
                             if did and did in st["decisions"] and st["decisions"][did].get("base_day")
                             else base_day(now).isoformat())
                    mid = f"LINE {now:%m/%d %H:%M}"
                    patch, notes = apply_orders(orders, stamp, mid)
                    st["manual"].append({"id": mid, "text": text, "patch": patch, "at": now.isoformat()})
                    if did and did in st["decisions"]:
                        st["decisions"][did]["status"] = "custom"
                    st["awaiting"] = None
                    changed = True
                    odd = [o["code"] for o in orders if isinstance(o["shares"], int) and o["shares"] % o["unit"]]
                    msg = "記録しました（" + "／".join(notes) + "）。"
                    if odd:
                        msg += f"\n※{'・'.join(odd)} の株数が売買単位の整数倍ではありません。合っているか確認してください。"
    else:
        msg = ""
    save_state(st)
    if msg:
        msg += "\n" + short_status()
    if changed:
        write_status()
    return {"text": msg, "changed": changed, "extra": extra, "fire": result_fire}


def _button_answer(p: dict, st: dict, now) -> tuple[str, str, bool]:
    """確認のお願いのボタンが押されたとき。（返事の文, AIを起動するときの文, 記録を変えたか）。"""
    from .calendar_jp import base_day
    from .human_check import answer_for, open_questions

    code = str(p.get("code", "")).upper()
    tb = base_day(now).isoformat()
    if p.get("b") and p["b"] != tb:
        return f"{code} の確認は前のターム（{p['b']}）のものなので、受け付けを終えています。", "", False
    q = next((x for x in open_questions(st, tb) if x.get("code") == code), None)
    if q is None:
        return f"{code} の確認は、もう答え済みか受け付けを終えています。", "", False
    label, ans = answer_for(p.get("k", ""), p.get("v", ""))
    if not label:
        return "ボタンの内容が分かりませんでした。数字で返信してください（例：確認 1869 5.2%）。", "", False
    msg, fire = _answer(q, ans or label, st, now)
    return msg, fire, True


def _answer(q: dict, ans: str, st: dict, now) -> tuple[str, str]:
    """確認の答えを保存する。（返事の文, AIを起動するときの文）。「わからない」はAIを起動せずに閉じる。"""
    from .calendar_jp import base_day, order_deadline
    from .human_check import MAX_ANSWER_FIRES_PER_NIGHT, SKIP_WORDS, fire_text, save_answer, skip_question

    if SKIP_WORDS.match(unicodedata.normalize("NFKC", ans).strip(" 。、!！")):
        skip_question(q, st, now)
        return f"了解です。{q['code']} {q.get('name', '')} は確認なしで進めます（AIは起動しません）。", ""
    notes = save_answer(q, ans, st, now)
    msg = f"ありがとうございます。{q['code']} {q.get('name', '')} の答え「{ans[:60]}」を受け取りました。"
    if notes:
        msg += "（" + "／".join(notes) + "）"
    tb = base_day(now)
    fires = st.setdefault("answer_fires", {})
    n = int(fires.get(tb.isoformat(), 0))
    if now > order_deadline(tb) or q.get("base_day") != tb.isoformat():
        return msg + "\n注文の締切を過ぎているので、AIは起動しません。", ""
    if n >= MAX_ANSWER_FIRES_PER_NIGHT:
        return msg + f"\n今夜の再判断は{MAX_ANSWER_FIRES_PER_NIGHT}回に達したので、AIは起動しません。", ""
    fires[tb.isoformat()] = n + 1
    st["answer_fires"] = dict(sorted(fires.items())[-14:])
    return msg + "\nこの答えでAIにもう一度判断させます（数分でLINEに結論が届きます。締切前なら保有にも記録します）。", \
        fire_text(q, ans)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "process":
        payload = json.loads(os.environ.get("PAYLOAD", "{}") or "{}")
        r = process(payload)
        RESULT.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
        print(r["text"])
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"changed={'true' if r['changed'] else 'false'}\n")
        return 0
    if cmd == "send":
        from .notify import send

        if not RESULT.exists():
            return 0
        r = json.loads(RESULT.read_text(encoding="utf-8"))
        if r.get("text"):
            print(send(r["text"], "line_reply", r.get("extra") or None))
        return 0
    if cmd == "fire":
        # 確認の答えが来たとき、AIアナリストをもう一度起動する（push の後に1回だけ）
        if not RESULT.exists():
            return 0
        r = json.loads(RESULT.read_text(encoding="utf-8"))
        if r.get("fire"):
            from .ai_fire import fire

            ok, m = fire(r["fire"])
            print(m)
            if not ok and "未設定" not in m:
                from .notify import send

                send(f"【名証スキャナー】確認の答えでAIを起動できませんでした（{m}）。", "failure")
        return 0
    if cmd == "status":
        write_status()
        print(json.loads(STATUS.read_text(encoding="utf-8"))["text"])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
