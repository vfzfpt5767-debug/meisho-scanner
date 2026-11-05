"""人に確認を頼む：AIが開示の中身を確認できずに見送った候補のうち、点数が高いもの（特に名証単独・TOB・ストップ高）を、
LINEで「このPDFを開いて〇〇を返信して」と頼む。返信が来たら、その情報を入力に加えてAIをもう一度起動する。

- 頼む：AIの判断の取り込み（ai_decision.apply_all）のとき。AIの判断ファイルの ask_human（AIが選んだもの）か、
  なければ skipped のうち「確認できず」の候補から自動で選ぶ。1晩に2件まで。締切前だけ。
- 答え：LINEで「確認 1869 5.2%」のように返信（「確認」を省いても、確認待ちがあれば答えとして読む）。
  → data/ai/line_state.json の questions に保存し、data/analyst_input.json の human_answers に足して、AIを起動する。
- TOB価格を聞いたときは、返ってきた数字を tob_prices.csv にも書く。
人がブラウザでTDnetのPDFを読むのは問題ない（自動取得はしない）。
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime

from .config import DATA_DIR, TOB_PRICES_CSV

INPUT = DATA_DIR / "analyst_input.json"
MAX_ASK_PER_NIGHT = 2
MAX_ANSWER_FIRES_PER_NIGHT = 2
UNVERIFIED = re.compile(r"確認でき|確認不可|見つから|取得でき|読めな|中身.*(不明|分から)|内容.*(不明|分から)")

# 材料ごとの「何を聞くか」
QUESTIONS = [
    (("TOB対象", "TOB対象（意見表明）", "TOB条件変更", "TOB対象（買い手側の開示から検出）"),
     "tob", "TOB価格（1株あたり何円か）"),
    (("自社株買い",), "buyback", "取得する株数の上限が発行済株式（自己株式を除く）の何%か、と取得期間"),
    (("業績予想の修正", "上方修正", "予想と実績の差異"), "revision",
     "通期の営業利益（または経常利益）の修正前→修正後の数字（上方か下方か）"),
    (("配当予想の修正", "増配", "株主還元方針の変更", "株主還元の強化"), "dividend", "年間配当の修正前→修正後（円）"),
    (("優待新設・拡充", "優待の変更"), "perk", "優待の内容（新設・拡充か、縮小か）と対象になる株数"),
    (("株式分割",), "split", "分割の比率（1株を何株に）と基準日"),
]


def _question_for(labels: list[str]) -> tuple[str, str]:
    for names, kind, q in QUESTIONS:
        if any(lb in names for lb in labels):
            return kind, q
    return "other", "良い内容（上方修正・増配など）か悪い内容か、と主な数字"


def _priority(c: dict, stuck_codes: set[str]) -> tuple:
    """頼む順：TOB → ストップ高 → 名証単独 → 点数。"""
    return (c.get("category") != "S", c.get("code") not in stuck_codes, not c.get("single"), -(c.get("score") or 0))


def pick_questions(d: dict, data: dict | None = None) -> list[dict]:
    """AIの判断から、人に確認を頼む候補を選ぶ（最大2件）。"""
    data = data if data is not None else _load_input()
    cands = {c["code"]: c for c in data.get("candidates", [])} if data else {}
    stuck = {h["code"] for h in (data or {}).get("stop_limits", []) if h.get("kind") == "stop_high"}
    out: list[dict] = []
    asked = d.get("ask_human") or []
    if not asked:
        for s in d.get("skipped", []) or []:
            code = str(s.get("code", "")).strip().upper()
            if code in cands and UNVERIFIED.search(str(s.get("reason", ""))):
                asked.append({"code": code})
        asked.sort(key=lambda a: _priority(cands.get(a["code"], {}), stuck))
    for a in asked:
        code = str(a.get("code", "")).strip().upper()
        c = cands.get(code, {})
        items = c.get("items", [])
        it = next((i for i in items if any(lb.get("cat") in ("S", "A", "CHECK") for lb in i.get("labels", []))),
                  items[0] if items else {})
        labels = [lb.get("label", "") for lb in it.get("labels", [])]
        kind, q = _question_for(labels)
        out.append({
            "code": code, "name": a.get("name") or c.get("name", ""), "title": a.get("title") or it.get("title", ""),
            "pdf": a.get("pdf") or it.get("pdf", ""), "question": a.get("question") or q, "kind": a.get("kind") or kind,
            "single": c.get("single"), "category": c.get("category"), "stuck": code in stuck,
        })
        if len(out) >= MAX_ASK_PER_NIGHT:
            break
    return out


def _load_input() -> dict:
    try:
        return json.loads(INPUT.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def ask_text(qs: list[dict]) -> str:
    lines = ["【確認のお願い】AIが開示の中身を確認できなかった候補です。PDFを開いて、数字を返信してください（締切前なら、答えでAIがもう一度判断します）。"]
    for i, q in enumerate(qs, 1):
        tag = "・".join(x for x in ("TOB" if q.get("category") == "S" else "", "ストップ高" if q.get("stuck") else "",
                                    "名証単独" if q.get("single") else "") if x)
        lines.append(f"{i}. {q['code']} {q['name']}{'（' + tag + '）' if tag else ''}\n「{q['title'][:50]}」\n"
                     f"知りたいこと：{q['question']}\nPDF：{q['pdf']}")
    ex = qs[0]["code"] if qs else "1869"
    lines.append(f"返信の例：確認 {ex} 5.2%（取得期間 11/6〜12/31）\n分からなければ返信しなくて大丈夫です。")
    return "\n".join(lines)


def register(qs: list[dict], d: dict, st: dict, now: datetime) -> list[dict]:
    """確認待ちとして line_state に保存する（同じ夜・同じ銘柄は1回、1晩2件まで）。保存したものを返す。"""
    base = d.get("base_day", "")
    qlist = st.setdefault("questions", [])
    tonight = [q for q in qlist if q.get("base_day") == base]
    have = {q["code"] for q in tonight}
    new = []
    for q in qs:
        if q["code"] in have or len(tonight) + len(new) >= MAX_ASK_PER_NIGHT:
            continue
        new.append({**q, "base_day": base, "decision": d.get("id", ""), "asked_at": now.isoformat(), "status": "open"})
    qlist += new
    st["questions"] = qlist[-60:]
    return new


def open_questions(st: dict, base_day: str) -> list[dict]:
    return [q for q in st.get("questions", []) if q.get("base_day") == base_day and q.get("status") == "open"]


ANSWER_HEAD = re.compile(r"^\s*(確認|回答|かくにん|こたえ|答え)")
CODE_RE = re.compile(r"(?<![0-9A-Z])([0-9][0-9A-Z]{3})(?![0-9A-Z])")


def match_answer(text: str, st: dict, base_day: str) -> tuple[dict | None, str]:
    """返信が確認の答えか。（どの確認への答えか, 答えの本文）。答えでなければ (None, "")。"""
    t = unicodedata.normalize("NFKC", text).strip()
    opens = open_questions(st, base_day)
    head = ANSWER_HEAD.match(t)
    if not opens:
        return None, ""
    body = t[head.end():].strip() if head else t
    for m in CODE_RE.finditer(body.upper()):
        for q in opens:
            if q["code"] == m[1]:
                rest = (body[:m.start()] + body[m.end():]).strip(" ：:、,")
                return q, rest
    if head or len(opens) == 1:
        return opens[-1], body
    return None, ""


def save_answer(q: dict, answer: str, st: dict, now: datetime) -> list[str]:
    """答えを保存し、入力（analyst_input.json）の human_answers に足す。返り値はメモ。"""
    notes = []
    for x in st.get("questions", []):
        if x.get("base_day") == q.get("base_day") and x.get("code") == q["code"]:
            x.update(status="answered", answer=answer[:300], answered_at=now.isoformat())
    data = _load_input()
    if data and data.get("base_day") == q.get("base_day"):
        ha = [h for h in data.get("human_answers", []) if h.get("code") != q["code"]]
        ha.append({"code": q["code"], "name": q.get("name", ""), "title": q.get("title", ""), "question": q["question"],
                   "answer": answer[:300], "answered_at": now.isoformat(),
                   "note": "ユーザーがブラウザでTDnetのPDFを読んで答えた内容（確認済みの情報として使ってよい）"})
        data["human_answers"] = ha
        INPUT.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if q.get("kind") == "tob":
        m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*円?", answer)
        if m:
            price = float(m[1].replace(",", ""))
            _write_tob_price(q["code"], price, q.get("name", ""))
            notes.append(f"TOB価格 {price:,.0f}円 を tob_prices.csv に書きました")
    return notes


def _write_tob_price(code: str, price: float, name: str) -> None:
    lines = TOB_PRICES_CSV.read_text(encoding="utf-8").splitlines() if TOB_PRICES_CSV.exists() else ["code,tob_price,memo"]
    lines = [ln for ln in lines if not ln.split(",")[0].strip().upper() == code]
    lines.append(f"{code},{price:g},{name}（LINEで確認）")
    TOB_PRICES_CSV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def answers_for(base_day: str) -> list[dict]:
    """このタームの答え（スキャンのたびに入力に入れ直す）。"""
    from .line_actions import load_state

    st = load_state()
    return [{"code": q["code"], "name": q.get("name", ""), "title": q.get("title", ""), "question": q["question"],
             "answer": q.get("answer", ""), "answered_at": q.get("answered_at", ""),
             "note": "ユーザーがブラウザでTDnetのPDFを読んで答えた内容（確認済みの情報として使ってよい）"}
            for q in st.get("questions", []) if q.get("base_day") == base_day and q.get("status") == "answered"]


def fire_text(q: dict, answer: str) -> str:
    return (f"人の確認の答えが届いたので再判断：{q['code']} {q.get('name', '')}「{q.get('title', '')[:40]}」"
            f"／質問：{q['question']}／答え：{answer[:200]}。"
            "入力は main の data/analyst_input.json（human_answers に入っている）。この答えを確認済みの情報として判断し、"
            "判断ファイルの trigger は \"human\" にすること。")
