from datetime import date, datetime

from scanner import classify as C
from scanner.calendar_jp import JST, base_day, is_business_day, order_deadline
from scanner.render import render
from scanner.scan import Card, Item, ScanResult


def cats(title, code="1234"):
    return [(t.cat, t.label) for t in C.classify(title, code)]


def test_tob_target_side():
    assert cats("アクセンチュア株式会社による当社株券等に対する公開買付けに関する賛同の意見表明及び応募推奨のお知らせ")[0][0] == C.S
    assert cats("ＭＢＯの実施及び応募の推奨に関するお知らせ")[0][0] == C.S
    assert cats("株式会社イクヨによる当社株式に対する公開買付けの開始予定に関するお知らせ")[0][0] == C.S
    # 対象会社が代わりに出す買い手側リリース（自分のコードが入っている）
    t = "アクセンチュア株式会社によるグローバルセキュリティエキスパート株式会社（証券コード：4417）の株券等に対する公開買付けの開始に関するお知らせ"
    assert cats(t, "4417")[0][0] == C.S
    assert cats(t, "9999")[0] == (C.TOB_REL, "他社へのTOB（買い手側）")
    assert C.target_codes(t) == ["4417"]


def test_tob_other():
    assert cats("自己株式の取得及び自己株式の公開買付けに関するお知らせ")[0][0] == C.SELF_TOB
    assert cats("当社株券等に対する公開買付けの結果に関するお知らせ")[0][0] == C.TOB_REL
    assert cats("当社株券等に対する公開買付けの不成立に関するお知らせ")[0][0] == C.BAD
    assert cats("（変更）「株式会社Xによる当社株式に対する公開買付けに関する賛同の意見表明及び応募推奨のお知らせ」の一部変更に関するお知らせ")[0][0] == C.TOB_REL


def test_good_bad_check():
    assert (C.A, "上方修正") in cats("通期連結業績予想の修正(上方修正)に関するお知らせ")
    assert (C.A, "増配") in cats("配当予想の修正（増配）に関するお知らせ")
    assert (C.A, "自社株買い") in cats("自己株式取得に係る事項の決定に関するお知らせ")
    assert (C.A, "株式分割") in cats("株式分割及び定款の一部変更に関するお知らせ")
    assert cats("業績予想の修正に関するお知らせ")[0][0] == C.CHECK
    assert cats("配当予想の修正に関するお知らせ")[0][0] == C.CHECK
    assert (C.BAD, "下方修正") in cats("通期業績予想の下方修正に関するお知らせ")
    assert (C.BAD, "減配・無配") in cats("期末配当予想の修正（無配）に関するお知らせ")
    assert cats("取締役に対する譲渡制限付株式報酬としての新株式発行に関するお知らせ") == [(C.INFO, "その他")]
    assert cats("自己株式の取得状況に関するお知らせ")[0] == (C.INFO, "自社株買いの経過")


def test_card_category():
    up = C.classify("業績予想の修正（上方修正）に関するお知らせ")
    chk = C.classify("業績予想の修正に関するお知らせ")
    bad = C.classify("特別損失の計上に関するお知らせ")
    assert C.card_category(up) == C.A
    assert C.card_category(chk) == C.CHECK
    assert C.card_category(bad) == C.BAD
    assert C.card_category(up + bad) == C.CHECK


def test_calendar():
    assert not is_business_day(date(2026, 11, 3))  # 文化の日
    assert not is_business_day(date(2026, 11, 23))  # 勤労感謝の日
    assert not is_business_day(date(2026, 12, 31))
    assert is_business_day(date(2026, 11, 2))
    # 11/2 23:00 → 基準日11/2、締切は11/4 7:59（11/3が祝日）
    assert base_day(datetime(2026, 11, 2, 23, 0, tzinfo=JST)) == date(2026, 11, 2)
    assert order_deadline(date(2026, 11, 2)) == datetime(2026, 11, 4, 7, 59, tzinfo=JST)
    # 11/4 朝6時 → まだ11/2の終値
    assert base_day(datetime(2026, 11, 4, 6, 0, tzinfo=JST)) == date(2026, 11, 2)
    # 金曜の夜 → 締切は月曜の朝
    assert order_deadline(date(2026, 11, 6)) == datetime(2026, 11, 9, 7, 59, tzinfo=JST)


def test_render_tob_banner():
    tag = vars(C.Tag(C.S, "TOB対象（意見表明）", 100, "理由"))
    card = Card(code="1439", name="安江工務店", single=True, division="メイン", unit=100, category=C.S, score=100,
                items=[Item("16:00", "11/07", "当社株券等に対する公開買付けに関する意見表明", "https://example.com/a.pdf", [tag])],
                price=1515, price_note="名証の終値", shares=1900, cost=2878500, tob_price=2150, tob_gap_pct=41.9)
    res = ScanResult(generated_at="2026-11-02T23:00:00+09:00", base_day="2026-11-02", deadline="2026-11-04T07:59:00+09:00",
                     price_source="名証 日報", cards=[card], before_close=[], tob_rows=[])
    html = render(res)
    assert "TOB・MBOの対象が出ました" in html
    assert "tob_prices.csv" in html and "/edit/" in html
    assert "+41.9%" in html
    assert "開示PDFを開く" in html


def test_conclusion_buy_line():
    from scanner.conclusion import buy_line

    tag = vars(C.Tag(C.S, "TOB対象", 100, ""))
    a_tag = vars(C.Tag(C.A, "上方修正", 60, ""))
    s = Card(code="1439", name="安江工務店", single=True, division="", unit=100, category=C.S, score=100,
             items=[Item("16:00", "11/07", "t", "", [tag])], price=1515, shares=1900, cost=2878500,
             tob_price=2150, tob_gap_pct=41.9)
    a = Card(code="7203", name="トヨタ", single=False, division="", unit=100, category=C.A, score=60,
             items=[Item("16:00", "11/07", "t", "", [a_tag])], price=3000, shares=1000, cost=3000000)
    res = ScanResult(generated_at="2026-11-02T23:00:00+09:00", base_day="2026-11-02",
                     deadline="2026-11-04T07:59:00+09:00", price_source="", cards=[a, s], before_close=[], tob_rows=[])
    t = buy_line(res).text
    assert "安江工務店" in t and "+41.9%" in t and "1,900株" in t and "名証単独" in t
    res.cards = [a]
    assert "トヨタ" in buy_line(res).text


def test_portfolio_swap(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scanner import portfolio as P
    from scanner.sources.nse_list import Listing
    from scanner.sources.nse_prices import Quote

    h = tmp_path / "holdings.csv"
    h.write_text("code,shares,buy_price,buy_date\n1111,1000,1000,2026-10-30\n2222,500,2000,2026-11-02\n", encoding="utf-8")
    c = tmp_path / "cash.csv"
    c.write_text("key,value\ncash,100000\n", encoding="utf-8")
    monkeypatch.setattr(P, "HOLDINGS_CSV", h)
    monkeypatch.setattr(P, "CASH_CSV", c)
    monkeypatch.setattr(P, "STATE", tmp_path / "state.json")
    bad = vars(C.Tag(C.BAD, "下方修正", -60, ""))
    good = vars(C.Tag(C.A, "上方修正", 60, ""))
    cards = [Card(code="1111", name="悪い", single=False, division="", unit=100, category=C.BAD, score=-60,
                  items=[Item("16:00", "11/02", "t", "", [bad])]),
             Card(code="3333", name="良い", single=True, division="", unit=100, category=C.A, score=60,
                  items=[Item("16:00", "11/02", "t", "", [good])], price=1200)]
    res = ScanResult(generated_at="", base_day="2026-11-02", deadline="", price_source="", cards=cards,
                     before_close=[], tob_rows=[])
    uni = {k: Listing(k, k, s, "", 100, "") for k, s in (("1111", False), ("2222", True), ("3333", True))}
    quotes = {"1111": Quote("1111", "a", quote=900), "2222": Quote("2222", "b", close=2100), "3333": Quote("3333", "c", quote=1200)}
    res.ctx = SimpleNamespace(now=datetime(2026, 11, 2, 22, 0, tzinfo=JST), quotes=quotes, universe=uni)
    pf = P.build(res)
    assert pf.holdings[0].warnings  # 悪材料の保有に警告
    assert pf.single_bought  # 2222 は単独
    assert pf.term_bought == 1_000_000  # 11/2 夜の買いは今ターム
    sells = [t for t in pf.swap if t.side == "sell"]
    buy = [t for t in pf.swap if t.side == "buy"][0]
    assert sells[0].code == "1111" and sells[0].shares == 1000
    # 残り枠200万、現金10万+90万+2222の一部売却 → 200万以内で100株単位
    assert buy.shares % 100 == 0 and buy.shares * 1200 <= 2_000_000
    assert buy.shares == 1600


def test_limits():
    from scanner.limits import detect, limit_width
    from scanner.sources.nse_prices import Quote

    assert limit_width(99) == 30 and limit_width(100) == 50 and limit_width(1879) == 400 and limit_width(47000) == 7000
    prev = {"1": Quote("1", "a", quote=1879), "2": Quote("2", "b", quote=47000), "3": Quote("3", "c", quote=3830),
            "4": Quote("4", "d", quote=343)}
    today = {"1": Quote("1", "a", open=2279, high=2279, low=2279, close=2279, change=400),
             "2": Quote("2", "b", quote=54000),
             "3": Quote("3", "c", quote=1855),  # 株式分割など（値幅の外）→ 対象外
             "4": Quote("4", "d", open=367, high=423, low=352, close=423, change=80)}
    h = detect(today, prev)
    assert h["1"].stuck and h["1"].kind == "stop_high"
    assert h["2"].stuck and "特別買い気配" in h["2"].label
    assert "3" not in h
    assert not h["4"].stuck and h["4"].label == "ストップ高で引け"


def test_newlistings_parse():
    from scanner.newlistings import parse

    html = """<h2>2026年</h2><table class="tbl"><tbody><tr><th>上場日</th></tr>
<tr><td><p><span>11/5</span></p></td><td><span>ネクスト</span></td><td><p>655A</p><p><a href="https://x">（株）LTV-X</a></p></td>
<td><a href="/listing/files/kaisya655A.pdf">会社概要</a></td><td><p><a href="/listing/files/ichi655A.pdf">Ⅰの部</a></p></td>
<td><p><a href="/listing/files/press_kettei655A.pdf">価格決定</a></p></td></tr></tbody></table>"""
    items = parse(html)
    assert len(items) == 1
    it = items[0]
    assert it.date == "2026-11-05" and it.code == "655A" and it.ipo and it.market == "ネクスト"
    assert it.kettei_url.endswith("press_kettei655A.pdf")


def test_notify_limits(tmp_path, monkeypatch):
    import io
    import json as _json

    from scanner import notify

    monkeypatch.setattr(notify, "STATE", tmp_path / "n.json")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "x")
    monkeypatch.setenv("LINE_USER_ID", "U1")
    sent = []

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        if req.full_url == notify.QUOTA_URL:
            return R(_json.dumps({"totalUsage": usage[0]}).encode())
        sent.append(req.data)
        return R(b"{}")

    usage = [5]
    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    assert notify.notify_once("evening:2026-11-02", "hi", "evening").startswith("LINEに送信しました（今月6通目")
    assert "送信済み" in notify.notify_once("evening:2026-11-02", "hi", "evening")
    usage[0] = 180
    assert "上限" in notify.send("hi", "test")
    assert len(sent) == 1


def test_morning_window():
    from scanner.morning import is_morning

    assert is_morning(datetime(2026, 11, 4, 6, 20, tzinfo=JST))
    assert is_morning(datetime(2026, 11, 4, 7, 5, tzinfo=JST))
    assert not is_morning(datetime(2026, 11, 3, 0, 25, tzinfo=JST))
    assert not is_morning(datetime(2026, 11, 4, 8, 0, tzinfo=JST))


def test_consult_text():
    from scanner.render import consult_text

    tag = vars(C.Tag(C.CHECK, "業績予想の修正", 40, ""))
    c = Card(code="2806", name="ユタカフーズ", single=False, division="", unit=100, category=C.CHECK, score=40,
             items=[Item("15:30", "11/02", "業績予想の修正に関するお知らせ", "https://example.com/x.pdf", [tag])], price=1988)
    t = consult_text(c)
    for s in ("ユタカフーズ", "2806", "1,988円", "15:30", "業績予想の修正に関するお知らせ", "https://example.com/x.pdf"):
        assert s in t


def test_evening_extra(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scanner import notify

    monkeypatch.setattr(notify, "STATE", tmp_path / "n.json")
    sent = []
    monkeypatch.setattr(notify, "send", lambda text, kind: (sent.append((kind, text)), "LINEに送信しました（テスト）")[1])
    a_tag = vars(C.Tag(C.A, "上方修正", 60, ""))
    s_tag = vars(C.Tag(C.S, "TOB対象", 100, ""))

    def res_with(*cards):
        return ScanResult(generated_at="", base_day="2026-11-04", deadline="2026-11-05T07:59:00+09:00", price_source="",
                          cards=list(cards), before_close=[], tob_rows=[])

    def card(code, cat, tag):
        return Card(code=code, name=code, single=False, division="", unit=100, category=cat, score=60,
                    items=[Item("16:00", "11/04", "t", "", [tag])], price=1000, shares=3000)

    pf = None
    a1 = card("1111", C.A, a_tag)
    assert "送信" in notify.evening_notify(res_with(a1), pf)  # 最初の1通
    assert "追加通知なし" in notify.evening_notify(res_with(a1), pf)  # 同じ銘柄は送らない
    notify.evening_notify(res_with(a1, card("2222", C.S, s_tag)), pf)  # 追加1通目
    notify.evening_notify(res_with(a1, card("3333", C.A, a_tag)), pf)  # 追加2通目
    assert "2通に達した" in notify.evening_notify(res_with(a1, card("4444", C.A, a_tag)), pf)
    assert [k for k, _ in sent] == ["evening", "evening_extra", "evening_extra"]
    assert "2222" in sent[1][1] and "1111" not in sent[1][1]


def test_ai_decision_apply(tmp_path, monkeypatch):
    import json as _json

    from scanner import ai_decision as AD
    from scanner import portfolio as P

    h = tmp_path / "holdings.csv"
    h.write_text("code,shares,buy_price,buy_date,memo\n# コメント\n1111,1000,900,2026-10-30\n", encoding="utf-8")
    c = tmp_path / "cash.csv"
    c.write_text("key,value,memo\ncash,100000,現金\nsingle_bought,no,\n", encoding="utf-8")
    monkeypatch.setattr(P, "HOLDINGS_CSV", h)
    monkeypatch.setattr(P, "CASH_CSV", c)
    dec = tmp_path / "decisions"
    dec.mkdir()
    monkeypatch.setattr(AD, "DECISIONS", dec)
    monkeypatch.setattr(AD, "LATEST", tmp_path / "ai" / "latest.json")
    monkeypatch.setattr(AD, "APPLIED", tmp_path / "ai" / "applied.json")
    monkeypatch.setattr(AD, "HISTORY", tmp_path / "ai" / "history.jsonl")
    monkeypatch.setattr("scanner.line_actions.STATE", tmp_path / "ai" / "line_state.json")
    monkeypatch.setattr("scanner.line_actions.STATUS", tmp_path / "status.json")
    d = {"version": 1, "created_at": "2026-11-04T17:12:00+09:00", "base_day": "2026-11-04", "summary": "テスト",
         "orders": [{"side": "sell", "code": "1111", "shares": 1000, "price": 1000, "confidence": "高", "reason": "警告"},
                    {"side": "buy", "code": "4445", "name": "リビンT", "shares": 600, "unit": 100, "price": 1665,
                     "confidence": "中", "reason": "上方修正"}]}
    assert AD.validate(d) == []
    bad = dict(d, orders=[dict(d["orders"][1], shares=650)])
    assert any("整数倍" in e for e in AD.validate(bad))
    (dec / "2026-11-04-1712.json").write_text(_json.dumps(d, ensure_ascii=False), encoding="utf-8")
    log = AD.apply_all(now=datetime(2026, 11, 4, 17, 20, tzinfo=JST), send_line=False)
    assert "取り込み済み" in log[0]
    rows = [r for r in h.read_text(encoding="utf-8").splitlines() if r and not r.startswith(("#", "code"))]
    assert rows == ["4445,600,1665,2026-11-04,AI 2026-11-04-1712"]
    assert "cash,101000" in c.read_text(encoding="utf-8")  # 10万 + 100万 − 99.9万
    assert "# コメント" in h.read_text(encoding="utf-8")
    # 2回目は取り込まない
    assert AD.apply_all(now=datetime(2026, 11, 4, 17, 30, tzinfo=JST), send_line=False) == []
    assert "買い：4445 リビンT 6単位（600株）" in AD.line_text(d)


def test_track(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scanner import track as T
    from scanner.sources.nse_prices import Quote

    monkeypatch.setattr(T, "RECS", tmp_path / "recs.json")
    monkeypatch.setattr("scanner.render_ai.load", lambda: None)  # リポジトリの本物のAIの判断を使わない
    a_tag = vars(C.Tag(C.A, "上方修正", 60, ""))
    card = Card(code="1111", name="テスト", single=False, division="", unit=100, category=C.A, score=60,
                items=[Item("16:00", "11/02", "t", "", [a_tag])], price=1000, shares=3000)
    res = ScanResult(generated_at="", base_day="2026-11-02", deadline="", price_source="", cards=[card],
                     before_close=[], tob_rows=[])
    T.record(res)
    T.record(res)  # 2回目は増えない
    assert len(T._load()) == 2  # rule_top と rule_sa
    ctx = SimpleNamespace(base=date(2026, 11, 4), quotes={"1111": Quote("1111", "t", quote=1100)})
    T.evaluate(ctx)
    st = T.stats()
    assert st["rule_top"]["n"] == 1 and abs(st["rule_top"]["mean"] - 10.0) < 1e-6


def test_builtin_holidays_match_jpholiday():
    import pytest

    jpholiday = pytest.importorskip("jpholiday")
    from datetime import timedelta

    from scanner.calendar_jp import _fallback_holiday

    d = date(2020, 1, 1)
    while d < date(2036, 1, 1):
        assert bool(jpholiday.is_holiday(d)) == _fallback_holiday(d), d
        d += timedelta(days=1)


def test_builtin_holidays_known_days():
    from scanner.calendar_jp import _fallback_holiday

    assert _fallback_holiday(date(2026, 11, 3))  # 文化の日
    assert _fallback_holiday(date(2026, 5, 6))  # 振替休日
    assert _fallback_holiday(date(2026, 9, 22))  # 国民の休日
    assert not _fallback_holiday(date(2026, 11, 2))


def test_preflight(tmp_path):
    import json

    from scanner.ai_decision import preflight

    p = tmp_path / "in.json"
    p.write_text(json.dumps({"generated_at": "2026-11-02T20:40:00+09:00", "base_day": "2026-11-02",
                             "deadline": "2026-11-04T07:59:00+09:00"}), encoding="utf-8")
    # 祝日（11/3）の夜でも、11/2 のタームが開いているので判断する
    ok, lines = preflight(p, "schedule", False, now=datetime(2026, 11, 3, 21, 15, tzinfo=JST), history=[])
    assert ok and "use_base_day=2026-11-02" in lines and "use_deadline=2026-11-04T07:59:00+09:00" in lines
    assert any(x.startswith("recorded=true") for x in lines)
    assert not any(x.startswith("input_mismatch") for x in lines)
    # 23:30 を過ぎたら参考扱い
    ok, lines = preflight(p, "fire", False, now=datetime(2026, 11, 3, 23, 45, tzinfo=JST), history=[])
    assert ok and any(x.startswith("recorded=false") for x in lines)
    # 11/4 の昼はタームの外
    ok, lines = preflight(p, "schedule", False, now=datetime(2026, 11, 4, 12, 0, tzinfo=JST), history=[])
    assert not ok and "タームの外" in lines[-1]
    # 11/4 の夜は新しいターム。入力が古ければ mismatch
    ok, lines = preflight(p, "schedule", False, now=datetime(2026, 11, 4, 21, 15, tzinfo=JST), history=[])
    assert ok and any(x.startswith("input_mismatch=true") for x in lines)
    ok, lines = preflight(p, "schedule", True, now=datetime(2026, 11, 3, 6, 45, tzinfo=JST))
    assert ok and "output=decisions/test/2026-11-03-0645" in lines


def test_record_window_and_watch(monkeypatch):
    import json

    from scanner import ai_decision as AD

    assert AD.recordable(datetime(2026, 11, 3, 21, 30, tzinfo=JST))
    assert AD.recordable(datetime(2026, 11, 3, 23, 30, tzinfo=JST))
    assert not AD.recordable(datetime(2026, 11, 3, 23, 31, tzinfo=JST))
    assert not AD.recordable(datetime(2026, 11, 4, 6, 50, tzinfo=JST))
    sent, fired = [], []
    monkeypatch.setattr("scanner.notify.notify_once", lambda key, text, kind: sent.append(text) or "sent")
    monkeypatch.setattr("scanner.notify.already_sent", lambda key: False)
    monkeypatch.setattr("scanner.ai_fire.fire", lambda text: fired.append(text) or (True, "起動しました"))
    now = datetime(2026, 11, 3, 22, 30, tzinfo=JST)
    got = json.dumps({"_apply": {"applied_at": "2026-11-03T21:28:00+09:00"}})
    assert AD.watch(now, got) == "今夜の判断は届いています"
    old = json.dumps({"_apply": {"applied_at": "2026-11-02T21:28:00+09:00"}})
    assert AD.watch(now, old) == "sent" and "届いていません" in sent[0] and "もう一度起動しました" in sent[0]
    assert len(fired) == 1  # 届いていなければAIをもう一度起動する
    # 遅れて動いた予備の起動（深夜2時など）は何もしない
    assert "時間" in AD.watch(datetime(2026, 11, 4, 2, 0, tzinfo=JST), old) and len(fired) == 1


def test_apply_test_mode_keeps_portfolio(tmp_path, monkeypatch):
    import json

    from scanner import ai_decision as AD
    from scanner import portfolio as P

    dec = tmp_path / "decisions"
    (dec / "test").mkdir(parents=True)
    monkeypatch.setattr(AD, "DECISIONS", dec)
    monkeypatch.setattr(AD, "TEST_DIR", dec / "test")
    for k in ("LATEST", "APPLIED", "HISTORY"):
        monkeypatch.setattr(AD, k, tmp_path / "ai" / getattr(AD, k).name)
    monkeypatch.setattr("scanner.line_actions.STATE", tmp_path / "ai" / "line_state.json")
    monkeypatch.setattr("scanner.line_actions.STATUS", tmp_path / "status.json")
    monkeypatch.setattr("scanner.line_actions.write_status", lambda pf=None: None)
    h, c = tmp_path / "holdings.csv", tmp_path / "cash.csv"
    h.write_text("code,shares,buy_price,buy_date,memo\n", encoding="utf-8")
    c.write_text("key,value\ncash,3000000\n", encoding="utf-8")
    monkeypatch.setattr(P, "HOLDINGS_CSV", h)
    monkeypatch.setattr(P, "CASH_CSV", c)
    sent, sent_extra = [], []
    monkeypatch.setattr("scanner.notify.send", lambda text, kind, extra=None: sent.append(text) or sent_extra.append(extra)
                        or "LINEに送信しました")
    d = {"version": 1, "created_at": "2026-11-03T04:00:00+09:00", "base_day": "2026-11-02", "test": True,
         "summary": "テスト", "orders": [{"side": "buy", "code": "4445", "name": "X", "shares": 100, "unit": 100,
                                          "price": 1665, "confidence": "高", "reason": "r"}]}
    (dec / "test" / "2026-11-03-0400.json").write_text(json.dumps(d), encoding="utf-8")
    before = (h.read_text(), c.read_text())
    log = AD.apply_all(now=datetime(2026, 11, 3, 4, 5, tzinfo=JST))
    assert (h.read_text(), c.read_text()) == before
    assert not AD.LATEST.exists()
    assert sent and sent[0].startswith("【テスト】AIの結論")
    assert "テスト" in log[0]
    assert AD.apply_all(now=datetime(2026, 11, 3, 4, 6, tzinfo=JST)) == []  # 2回目は取り込まない
    # 本番：23:30 より後に届いた注文は参考（記録しない）、21:30 なら記録する
    real = dict(d, test=False)
    (dec / "2026-11-03-2340.json").write_text(json.dumps(real), encoding="utf-8")
    AD.apply_all(now=datetime(2026, 11, 3, 23, 45, tzinfo=JST))
    assert (h.read_text(), c.read_text()) == before
    assert sent[-1] == "" and "まだ記録していません" in sent_extra[-1][0]["text"]  # 注文だけの1通（参考）
    (dec / "2026-11-03-2115.json").write_text(json.dumps(real), encoding="utf-8")
    AD.apply_all(now=datetime(2026, 11, 3, 21, 30, tzinfo=JST))
    assert "4445" in h.read_text() and "2833500" in c.read_text()
    assert sent_extra[-1][0]["text"].startswith("【今夜の注文】") and "買い X(4445) 1単位" in sent_extra[-1][0]["text"]
    # 見送り（注文なし）の判断はLINEを送らない
    n = len(sent)
    (dec / "2026-11-03-2200.json").write_text(json.dumps(dict(real, orders=[], summary="見送り")), encoding="utf-8")
    log = AD.apply_all(now=datetime(2026, 11, 3, 22, 0, tzinfo=JST))
    assert len(sent) == n and any("送りません" in x for x in log)


def test_line_parse_orders():
    from scanner.line_actions import parse_orders

    known = {"4445", "6999", "285A"}
    assert parse_orders("買い 4445 1000", known) == [{"side": "buy", "code": "4445", "shares": 1000, "price": None}]
    assert parse_orders("売り 6999 全部", known) == [{"side": "sell", "code": "6999", "shares": "all", "price": None}]
    assert parse_orders("４４４５を１，０００株買った　１６６５円", known) == [
        {"side": "buy", "code": "4445", "shares": 1000, "price": 1665.0}]
    assert parse_orders("6999 全部売り、買い 285A 2千", known) == [
        {"side": "sell", "code": "6999", "shares": "all", "price": None},
        {"side": "buy", "code": "285A", "shares": 2000, "price": None}]
    assert parse_orders("買 4445 1000株 @1700", known)[0]["price"] == 1700.0
    assert parse_orders("買い 4445 18単位", known)[0]["shares"] == 18 * __import__("scanner.units", fromlist=["x"]).unit_of("4445")
    assert parse_orders("こんにちは", known) == []
    assert parse_orders("残高", known) == []


def test_line_buttons_flow(tmp_path, monkeypatch):
    import json as _json

    from scanner import ai_decision as AD
    from scanner import line_actions as L
    from scanner import portfolio as P
    from scanner.sources.nse_list import Listing
    from scanner.sources.nse_prices import Quote

    h = tmp_path / "holdings.csv"
    h.write_text("code,shares,buy_price,buy_date,memo\n6999,1000,1950,2026-10-30\n", encoding="utf-8")
    c = tmp_path / "cash.csv"
    c.write_text("key,value,memo\ncash,1000000,\n", encoding="utf-8")
    dec = tmp_path / "decisions"
    dec.mkdir()
    for mod, attr, val in ((P, "HOLDINGS_CSV", h), (P, "CASH_CSV", c), (AD, "DECISIONS", dec),
                           (AD, "LATEST", tmp_path / "latest.json"), (AD, "APPLIED", tmp_path / "applied.json"),
                           (AD, "HISTORY", tmp_path / "history.jsonl"), (L, "STATE", tmp_path / "state.json"),
                           (L, "STATUS", tmp_path / "status.json")):
        monkeypatch.setattr(mod, attr, val)
    monkeypatch.setattr(L, "short_status", lambda pf=None: "保有：（省略）")
    monkeypatch.setattr(L, "write_status", lambda pf=None: None)
    monkeypatch.setattr(L, "latest_quotes", lambda base: {"7203": Quote("7203", "トヨタ", quote=3000)})
    uni = {k: Listing(k, k, False, "", 100, "") for k in ("6999", "4445", "7203")}
    monkeypatch.setattr("scanner.sources.nse_list.load", lambda refresh=False: (uni, None))
    sent = []
    monkeypatch.setattr("scanner.notify.send", lambda text, kind, extra=None: sent.append((text, extra)) or "LINEに送信しました")
    d = {"version": 1, "created_at": "2026-11-04T21:20:00+09:00", "base_day": "2026-11-04", "summary": "t",
         "orders": [{"side": "sell", "code": "6999", "shares": 1000, "price": 2000, "confidence": "高", "reason": "r"},
                    {"side": "buy", "code": "4445", "shares": 600, "unit": 100, "price": 1665, "confidence": "中", "reason": "r"}]}
    (dec / "2026-11-04-2120.json").write_text(_json.dumps(d), encoding="utf-8")
    now = datetime(2026, 11, 4, 21, 25, tzinfo=JST)
    AD.apply_all(now=now)
    assert sent[-1][0] == "" and [m["type"] for m in sent[-1][1]] == ["text"]  # 中継役がまだ動いていないときはボタンを付けない
    L.save_state({**L.load_state(), "enabled": True})
    (dec / "2026-11-04-2120.json").rename(dec / "2026-11-04-2121.json")
    d["id"] = "2026-11-04-2121"
    rows_before = h.read_text(encoding="utf-8")
    h.write_text("code,shares,buy_price,buy_date,memo\n6999,1000,1950,2026-10-30\n", encoding="utf-8")
    c.write_text("key,value,memo\ncash,1000000,\n", encoding="utf-8")
    AD.apply_all(now=now)
    assert sent[-1][0] == "" and len(sent[-1][1]) == 1  # 注文とボタンを1通にまとめる
    btn = sent[-1][1][0]
    assert btn["type"] == "template" and len(btn["template"]["actions"]) == 3
    assert btn["template"]["text"].split("\n")[1:] == ["売り 6999 10単位", "買い 4445 6単位"]  # 売りが先
    after_ai = h.read_text(encoding="utf-8")
    assert "4445,600" in after_ai and "6999" not in after_ai.split("\n", 1)[1]
    # 注文しなかった → 取り消し（元に戻る）
    r = L.process({"type": "postback", "data": "act=none&id=2026-11-04-2121", "event_id": "e1"}, now)
    assert r["changed"] and "取り消し" in r["text"]
    assert "6999,1000,1950" in h.read_text(encoding="utf-8") and "4445" not in h.read_text(encoding="utf-8")
    assert "cash,1000000" in c.read_text(encoding="utf-8")
    # 同じイベントは2回処理しない
    assert L.process({"type": "postback", "data": "act=none&id=2026-11-04-2121", "event_id": "e1"}, now)["text"] == ""
    # 指示どおり注文した → もう一度記録
    r = L.process({"type": "postback", "data": "act=done&id=2026-11-04-2121", "event_id": "e2"}, now)
    assert r["changed"] and "4445,600" in h.read_text(encoding="utf-8")
    # 別の注文をした → 取り消して返信待ち → 「買い 7203 300」（値段なし＝終値）
    r = L.process({"type": "postback", "data": "act=other&id=2026-11-04-2121", "event_id": "e3"}, now)
    assert "返信してください" in r["text"] and "4445" not in h.read_text(encoding="utf-8")
    r = L.process({"type": "text", "text": "売り 6999 全部\n買い 7203 300", "event_id": "e4"}, now)
    txt = h.read_text(encoding="utf-8")
    assert r["changed"] and "7203,300,3000,2026-11-04,LINE" in txt and "6999" not in txt.split("\n", 1)[1]
    # 現金：100万 − 6999の売りはAIの値段2000円で 200万 → 300万 − 7203 300株×3000円 = 210万
    assert "cash,2100000" in c.read_text(encoding="utf-8")
    assert "読み取れません" in L.process({"type": "text", "text": "よろしく", "event_id": "e5"}, now)["text"]


def test_units_format(monkeypatch):
    from scanner import units as U

    monkeypatch.setattr(U, "_units", lambda: {"4445": 100, "1553": 10, "1319": 1000})
    assert U.fmt("4445", 1800) == "18単位（1,800株）"
    assert U.fmt("1553", 50) == "5単位（50株）"
    assert U.fmt("1319", 3000) == "3単位（3,000株）"
    assert "整数倍ではありません" in U.fmt("4445", 150)
    assert U.fmt("9999", 300, 100) == "3単位（300株）"  # 一覧にないときは渡された単位


def test_ai_fire_skip_and_handled(tmp_path, monkeypatch):
    import json as _json

    from scanner import ai_input as AI

    monkeypatch.setattr(AI, "FIRE_STATE", tmp_path / "fire_state.json")
    monkeypatch.setattr(AI, "FIRE_REQ", tmp_path / "fire_request.json")
    monkeypatch.setattr(AI, "INPUT", tmp_path / "analyst_input.json")
    monkeypatch.setattr(AI, "_main_run_done", lambda base: True)  # 21:15の定時の判断は取り込み済み
    chk = vars(C.Tag(C.CHECK, "業績予想の修正", 40, ""))
    a_tag = vars(C.Tag(C.S, "TOB対象", 90, ""))

    def card(code, cat, tag):
        return Card(code=code, name=code, single=False, division="", unit=100, category=cat, score=40,
                    items=[Item("16:00", "11/04", "t", "", [tag])], price=1000, shares=3000)

    res = ScanResult(generated_at="2026-11-04T22:33:00+09:00", base_day="2026-11-04", deadline="", price_source="",
                     cards=[card("4445", C.CHECK, chk)], before_close=[], tob_rows=[])
    # 1) ページ作り直し（SKIP_AI）のときは、新しい候補があっても起動しない
    monkeypatch.setenv("SKIP_AI", "true")
    req = AI.plan_fire(res, None, {})
    assert not req["fire"] and "作り直し" in req["reason"]
    monkeypatch.delenv("SKIP_AI")
    # 2) AIの判断を取り込んだら、入力にあった候補は「調べ済み」→ 同じ候補では起動しない
    AI.INPUT.write_text(_json.dumps({"base_day": "2026-11-04", "candidates": [{"category": "CHECK", "code": "4445"}],
                                     "portfolio": {"holdings": []}}), encoding="utf-8")
    assert AI.mark_handled("2026-11-04") == ["CHECK:4445"]
    assert not AI.plan_fire(res, None, {})["fire"]
    # 3) 21:15のあとに新しいTOB（S）が出たら起動する。A・要確認だけでは起動しない
    res.cards.append(card("6666", C.A, vars(C.Tag(C.A, "上方修正", 60, ""))))
    assert not AI.plan_fire(res, None, {"x": 1})["fire"]
    res.cards.append(card("7203", C.S, a_tag))
    req = AI.plan_fire(res, None, {"x": 1})
    assert req["fire"] and req["new_keys"] == ["S:7203"]


def test_ai_skip_publish_and_scan_request(tmp_path, monkeypatch):
    """入力が古いとき：スキャンの依頼・入力待ち・「見送り」の作成と push（偽の origin で試す）。"""
    import json as _json
    import subprocess

    from scanner import ai_decision as AD

    def git(cwd, *a):
        return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout

    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    (work / "data").mkdir()
    (work / "data" / "analyst_input.json").write_text(_json.dumps({"base_day": "2026-11-02"}), encoding="utf-8")
    git(work, "add", ".")
    git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    git(work, "branch", "-M", "main")
    git(work, "push", "-q", "origin", "main")
    monkeypatch.setattr(AD, "ROOT", work)
    monkeypatch.setattr(AD, "DECISIONS", work / "decisions")
    now = datetime(2026, 11, 4, 21, 15, tzinfo=JST)
    # スキャンの依頼 → origin に claude/scan-request ができる
    assert "スキャンを頼みました" in AD.request_scan(now)
    assert "scan_requests/last.txt" in git(origin, "ls-tree", "-r", "--name-only", "claude/scan-request")
    # 入力が古いままなら WAIT
    ok, msg = AD.wait_input(timeout=0, interval=0, now_fn=lambda: now)
    assert not ok and msg.startswith("WAIT")
    # main の入力が新しくなれば OK
    (work / "data" / "analyst_input.json").write_text(_json.dumps({"base_day": "2026-11-04"}), encoding="utf-8")
    git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "scan")
    git(work, "push", "-q", "origin", "main")
    ok, msg = AD.wait_input(timeout=0, interval=0, now_fn=lambda: now)
    assert ok and msg.startswith("OK")
    # 見送りを作って push → claude/ai-decisions に .json と .md
    p = AD.make_skip("スキャンのデータが取れていない", now=now)
    d = _json.loads(p.read_text(encoding="utf-8"))
    assert d["orders"] == [] and d["base_day"] == "2026-11-04" and AD.validate(d) == []
    assert "push しました" in AD.publish([p, p.with_suffix(".md")])
    files = git(origin, "ls-tree", "-r", "--name-only", "claude/ai-decisions")
    assert "decisions/2026-11-04-2115.json" in files and "decisions/2026-11-04-2115.md" in files
    assert "data/analyst_input.json" in files  # main の最新の上に載っている


def test_sell_rule_and_earnings_notice(tmp_path, monkeypatch):
    import json as _json
    from types import SimpleNamespace

    from scanner import earnings as E
    from scanner import portfolio as P
    from scanner.limits import LimitHit

    bt = {"exit_rules": {
        "上方修正": {"best_days": 1, "n": 245, "rules": {"d1": {"n": 245, "mean": 2.7, "median": 1.8}}},
        "株式分割": {"best_days": 5, "n": 297, "rules": {"d5": {"n": 297, "mean": 5.1, "median": 2.8}}},
        "好材料・要確認（全体）": {"best_days": 1, "n": 9230, "rules": {"d1": {"n": 9230, "mean": 1.2, "median": 0.6}}}},
        "holding": {"ストップ高張り付き": {"rules": {"stop": {"n": 752, "mean": 12.9}}}}}
    monkeypatch.setattr("scanner.render_backtest.load", lambda: bt)
    dec = tmp_path / "decisions"
    dec.mkdir()
    (dec / "2026-11-03-2229.json").write_text(_json.dumps({"orders": [
        {"side": "buy", "code": "4445", "reason": "通期の経常利益を+34.6%上方修正（株探で確認）"}]}), encoding="utf-8")
    monkeypatch.setattr("scanner.ai_decision.DECISIONS", dec)
    h = P.Holding(code="4445", shares=1800, buy_price=1665, buy_date=date(2026, 11, 2), buy_time=None,
                  memo="AI 2026-11-03-2229")
    ctx = SimpleNamespace(base=date(2026, 11, 2), now=datetime(2026, 11, 3, 22, 0, tzinfo=JST), disclosures=[])
    text, sell = P._sell_rule(h, ctx, {})
    assert h.material == "上方修正" and sell is None and "11/4(水)の終値で売る予定" in text  # 11/3は祝日
    ctx.base = date(2026, 11, 4)
    text, sell = P._sell_rule(h, ctx, {})
    assert sell and "売り時" in text and "今夜売る" in text
    stuck = LimitHit("4445", "x", "stop_high", 1665, 2065, 2065, True, "張り付き", None, None, None, None, 2065)
    text, sell = P._sell_rule(h, ctx, {"4445": stuck})
    assert sell is None and "持ち続ける" in text
    # 株式分割は5営業日後
    h2 = P.Holding(code="7203", shares=100, buy_price=3000, buy_date=date(2026, 11, 4), buy_time=None, memo="")
    h2.memo = "AI x"
    (dec / "x.json").write_text(_json.dumps({"orders": [{"side": "buy", "code": "7203", "reason": "株式分割（1:2）"}]}),
                                encoding="utf-8")
    ctx2 = SimpleNamespace(base=date(2026, 11, 5), now=datetime(2026, 11, 5, 21, 0, tzinfo=JST), disclosures=[])
    text, sell = P._sell_rule(h2, ctx2, {})
    assert h2.material == "株式分割" and sell is None and "5営業日後" in text
    # 決算発表が近い（今日〜2営業日後）
    ep = tmp_path / "earnings.json"
    ep.write_text(_json.dumps({"events": [{"date": "2026-11-06", "code": "4445"}, {"date": "2026-11-20", "code": "7203"}]}),
                  encoding="utf-8")
    monkeypatch.setattr(E, "PATH", ep)
    got = P._earnings_soon(ctx, datetime(2026, 11, 4, 21, 0, tzinfo=JST))
    assert "4445" in got and "7203" not in got and "11/6(金)" in got["4445"] and "AIが判断" in got["4445"]


def test_ir_lookup_offline(tmp_path, monkeypatch):
    import json as _json
    from types import SimpleNamespace

    from scanner import ir

    pages = tmp_path / "ir_pages.json"
    pages.write_text(_json.dumps({"1869": {"status": "ok", "ir_news": "https://ex.co.jp/ir/"}}), encoding="utf-8")
    monkeypatch.setattr(ir, "PAGES", pages)
    html = ('<ul><li>2026年11月05日 <a href="/data/a.pdf">自己株式取得に係る事項の決定に関するお知らせ（PDF：324KB）</a></li>'
            '<li>2026年11月05日 <a href="/data/b.pdf">2027年3月期 第2四半期決算短信</a></li></ul>')
    pdf = b"%PDF-1.4 fake"

    def fake_fetch(url):
        if url.endswith("/ir/"):
            return SimpleNamespace(content=html.encode("utf-8"), url=url, encoding="utf-8")
        return SimpleNamespace(content=pdf, url=url, encoding=None)

    monkeypatch.setattr(ir, "fetch", fake_fetch)
    monkeypatch.setattr(ir, "_pdf_text", lambda data, max_chars=3500: "取得し得る株式の総数 134,000株（上限）割合 0.53%")
    r = ir.lookup("1869", "自己株式取得に係る事項の決定に関するお知らせ", "2026-11-05")
    assert r["status"] == "found" and r["url"] == "https://ex.co.jp/data/a.pdf" and "0.53%" in r["excerpt"]
    assert ir.lookup("1869", "株主優待制度の新設に関するお知らせ")["status"] == "not_found"
    assert ir.lookup("9999", "x")["status"] == "no_ir_page"


def test_ir_robots(monkeypatch):
    from types import SimpleNamespace

    from scanner import ir

    ir._robots.clear()
    texts = {"https://a.jp": (200, "User-agent: *\nDisallow: /ir/\n"), "https://b.jp": (404, ""), "https://c.jp": (503, "")}

    def fake_get(url, timeout=0, allow_redirects=True):
        host = url.rsplit("/robots.txt", 1)[0]
        code, body = texts[host]
        return SimpleNamespace(status_code=code, text=body, content=body.encode(), encoding="utf-8")

    monkeypatch.setattr(ir.http._session, "get", fake_get)
    assert ir.robots_state("https://a.jp/ir/news") == "disallow"
    assert ir.robots_state("https://a.jp/company/") == "allow"
    assert ir.robots_state("https://b.jp/ir/") == "allow"  # robots.txt がない
    assert ir.robots_state("https://c.jp/ir/") == "unreachable"  # 5xx は開かない


def test_human_check_flow(tmp_path, monkeypatch):
    import json as _json

    from scanner import human_check as H
    from scanner import line_actions as L
    from scanner.sources.nse_list import Listing

    inp = tmp_path / "analyst_input.json"
    inp.write_text(_json.dumps({"base_day": "2026-11-05", "candidates": [
        {"code": "1869", "name": "名工建設", "category": "CHECK", "single": True, "score": 53,
         "items": [{"time": "11/05 16:30", "title": "自己株式取得に係る事項の決定に関するお知らせ",
                    "pdf": "https://www.release.tdnet.info/inbs/140120261105565325.pdf",
                    "labels": [{"cat": "A", "label": "自社株買い"}]}]},
        {"code": "7203", "name": "トヨタ", "category": "CHECK", "single": False, "score": 60, "items": []}],
        "stop_limits": []}), encoding="utf-8")
    monkeypatch.setattr(H, "INPUT", inp)
    monkeypatch.setattr(H, "TOB_PRICES_CSV", tmp_path / "tob.csv")
    monkeypatch.setattr(L, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(L, "short_status", lambda pf=None: "保有：（省略）")
    monkeypatch.setattr(L, "write_status", lambda pf=None: None)
    monkeypatch.setattr("scanner.sources.nse_list.load",
                        lambda refresh=False: ({"1869": Listing("1869", "名工建設", True, "", 100, "")}, None))
    d = {"id": "2026-11-05-2120", "base_day": "2026-11-05",
         "skipped": [{"code": "7203", "reason": "小さい材料"}, {"code": "1869", "reason": "開示の中身をWebで確認できず"}]}
    qs = H.pick_questions(d)
    assert [q["code"] for q in qs] == ["1869"] and "%" in qs[0]["question"] and qs[0]["pdf"].endswith("565325.pdf")
    text = H.ask_text(qs)
    assert "確認 1869" in text and "140120261105565325.pdf" in text
    now = datetime(2026, 11, 5, 21, 30, tzinfo=JST)
    st = L.load_state()
    H.register(qs, d, st, now)
    H.register(qs, d, st, now)  # 同じ銘柄は1回だけ
    assert len(st["questions"]) == 1
    L.save_state(st)
    # 「確認 1869 0.53% 11/6〜3/31」と返信 → 答えを保存し、AIを起動する文ができる
    r = L.process({"type": "text", "text": "確認 1869 0.53%（11/6〜3/31）", "event_id": "q1"}, now)
    assert "答え" in r["text"] and r["fire"] and "0.53%" in r["fire"]
    data = _json.loads(inp.read_text(encoding="utf-8"))
    assert data["human_answers"][0]["code"] == "1869" and "0.53%" in data["human_answers"][0]["answer"]
    assert H.answers_for("2026-11-05")[0]["answer"].startswith("0.53%")
    # 確認待ちがなければ、ただの数字は答えとして扱わない
    assert L.process({"type": "text", "text": "確認 1869 1%", "event_id": "q2"}, now)["fire"] == ""


def test_human_trigger_records_after_2330(tmp_path, monkeypatch):
    import json as _json

    from scanner import ai_decision as AD
    from scanner import line_actions as L
    from scanner import portfolio as P

    dec = tmp_path / "decisions"
    dec.mkdir()
    monkeypatch.setattr(AD, "DECISIONS", dec)
    monkeypatch.setattr(AD, "TEST_DIR", dec / "test")
    for k in ("LATEST", "APPLIED", "HISTORY"):
        monkeypatch.setattr(AD, k, tmp_path / "ai" / getattr(AD, k).name)
    monkeypatch.setattr(L, "STATE", tmp_path / "ai" / "line_state.json")
    monkeypatch.setattr(L, "write_status", lambda pf=None: None)
    monkeypatch.setattr("scanner.ai_input.mark_handled", lambda b: [])
    h, c = tmp_path / "holdings.csv", tmp_path / "cash.csv"
    h.write_text("code,shares,buy_price,buy_date,memo\n", encoding="utf-8")
    c.write_text("key,value\ncash,3000000\n", encoding="utf-8")
    monkeypatch.setattr(P, "HOLDINGS_CSV", h)
    monkeypatch.setattr(P, "CASH_CSV", c)
    monkeypatch.setattr("scanner.notify.send", lambda text, kind, extra=None: "LINEに送信しました")
    now = datetime(2026, 11, 5, 23, 50, tzinfo=JST)
    st = L.load_state()
    st["questions"] = [{"base_day": "2026-11-05", "code": "1869", "status": "answered",
                        "answered_at": datetime(2026, 11, 5, 23, 40, tzinfo=JST).isoformat()}]
    L.save_state(st)
    d = {"version": 1, "created_at": now.isoformat(), "base_day": "2026-11-05", "summary": "t", "trigger": "human",
         "orders": [{"side": "buy", "code": "1869", "shares": 1000, "unit": 100, "price": 1890, "confidence": "中", "reason": "r"}]}
    (dec / "2026-11-05-2350.json").write_text(_json.dumps(d), encoding="utf-8")
    AD.apply_all(now=now)
    assert "1869,1000" in h.read_text(encoding="utf-8")  # 人の答えでの判断は23:30以降でも記録
    d2 = dict(d, trigger="fire")
    (dec / "2026-11-05-2355.json").write_text(_json.dumps(d2), encoding="utf-8")
    AD.apply_all(now=datetime(2026, 11, 5, 23, 55, tzinfo=JST))
    assert h.read_text(encoding="utf-8").count("1869,1000") == 1  # ふつうの判断は参考（記録しない）


def test_delayed_runs_stay_quiet(tmp_path, monkeypatch):
    """GitHub の時刻指定が遅れて昼・深夜に動いても、LINEやAIの起動をしない。"""
    from types import SimpleNamespace

    from scanner import ai_input as AI
    from scanner.__main__ import night_notify_ok
    from scanner.morning import is_morning

    def res_at(dt, base):
        return SimpleNamespace(ctx=SimpleNamespace(now=dt, base=base))

    assert night_notify_ok(res_at(datetime(2026, 11, 5, 21, 0, tzinfo=JST), date(2026, 11, 5)))
    assert not night_notify_ok(res_at(datetime(2026, 11, 5, 10, 30, tzinfo=JST), date(2026, 11, 4)))  # 昼：締切後
    assert not night_notify_ok(res_at(datetime(2026, 11, 6, 1, 30, tzinfo=JST), date(2026, 11, 5)))  # 深夜
    assert night_notify_ok(res_at(datetime(2026, 11, 3, 14, 0, tzinfo=JST), date(2026, 11, 2)))  # 祝日の昼はタームが開いている
    assert not is_morning(datetime(2026, 11, 6, 4, 6, tzinfo=JST)) and is_morning(datetime(2026, 11, 6, 6, 20, tzinfo=JST))

    monkeypatch.setattr(AI, "FIRE_STATE", tmp_path / "fs.json")
    monkeypatch.setattr(AI, "FIRE_REQ", tmp_path / "fr.json")
    done = {"v": False}
    monkeypatch.setattr(AI, "_main_run_done", lambda base: done["v"])
    tag = vars(C.Tag(C.S, "TOB対象", 90, ""))
    card = Card(code="7203", name="t", single=False, division="", unit=100, category=C.S, score=90,
                items=[Item("16:00", "11/05", "t", "", [tag])], price=1000, shares=3000)
    # 夕方（16:30〜21:15）は臨時起動しない：21:15の定時起動がまとめて判断する（2026-11-10 ユーザー指定）
    for when, main_done, ok in (("2026-11-05T10:30:00+09:00", True, False), ("2026-11-06T01:30:00+09:00", True, False),
                                ("2026-11-05T16:30:00+09:00", False, False), ("2026-11-05T20:40:00+09:00", False, False),
                                ("2026-11-05T22:40:00+09:00", False, False),  # 21:15の判断がまだ
                                ("2026-11-05T23:45:00+09:00", True, False), ("2026-11-05T22:40:00+09:00", True, True)):
        done["v"] = main_done
        res = ScanResult(generated_at=when, base_day="2026-11-05", deadline="", price_source="", cards=[card],
                         before_close=[], tob_rows=[])
        assert AI.plan_fire(res, None, {})["fire"] is ok, when


def test_nikkei_close_backup(monkeypatch):
    """まとめて取得した表に前日の日経平均が無い朝：日経平均だけの取得（日足→最新値）で基準日の終値を取る。"""
    import sys
    import types
    from datetime import date, datetime, timedelta, timezone

    import pandas as pd

    from scanner.morning import _nikkei_close_on

    jst = timezone(timedelta(hours=9))

    def fake(rows, meta):
        class T:
            def __init__(self, sym):
                self.history_metadata = meta

            def history(self, **kw):
                idx = pd.DatetimeIndex([pd.Timestamp(d, tz="Asia/Tokyo") for d, _ in rows])
                return pd.DataFrame({"Close": [c for _, c in rows]}, index=idx)
        monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=T))

    # 1) 日経平均だけの日足に基準日の行がある
    fake([("2026-11-06", 50000.0), ("2026-11-09", 50500.0)], {})
    assert _nikkei_close_on(date(2026, 11, 9)) == 50500.0
    # 2) 日足に無い → 最新値の時刻が基準日なら、その値（引け後は終値）
    ts = int(datetime(2026, 11, 9, 15, 30, tzinfo=jst).timestamp())
    fake([("2026-11-06", 50000.0)], {"regularMarketTime": ts, "regularMarketPrice": 50600.0})
    assert _nikkei_close_on(date(2026, 11, 9)) == 50600.0
    # 3) 最新値が別の日なら使わない（None → 呼び出し側は先物の前日比で代用し、注意を出す）
    assert _nikkei_close_on(date(2026, 11, 10)) is None


def _fake_site(pages: dict):
    """URL → HTML か PDF（bytes）の偽サイト（ネットにつながない）。"""
    from types import SimpleNamespace

    def fake_fetch(url):
        if url not in pages:
            raise AssertionError(f"想定外のURL: {url}")
        body = pages[url]
        if isinstance(body, bytes):
            return SimpleNamespace(content=body, url=url, encoding=None)
        return SimpleNamespace(content=body.encode("utf-8"), url=url, encoding="utf-8")
    return fake_fetch


def test_ir_doc_date():
    from scanner import ir

    assert ir.doc_date("2026年11月10日\n各 位\n会 社 名 株式会社ユー・エス・エス") == "2026-11-10"
    assert ir.doc_date("令和８年11月９日\n各 位") == "2026-11-09"
    assert ir.doc_date("2026 年 11 月 5 日 各位") == "2026-11-05"
    assert ir.doc_date("2026.11.10 お知らせ") == "2026-11-10"
    assert ir.doc_date("各位 会社名") == ""
    assert ir.near("2026-11-10", "2026-11-11") and not ir.near("2025-11-07", "2026-11-10")


def test_ir_lookup_rejects_last_years_document(tmp_path, monkeypatch):
    """毎年同じタイトルの資料（ニチハの「通期業績予想の修正に関するお知らせ」）で、去年の分を取り違えない。"""
    import json as _json

    from scanner import ir

    pages = tmp_path / "ir_pages.json"
    pages.write_text(_json.dumps({"7943": {"status": "ok", "ir_news": "https://n.co.jp/news/"},
                                  "1111": {"status": "ok", "ir_news": "https://m.co.jp/ir/"}}), encoding="utf-8")
    monkeypatch.setattr(ir, "PAGES", pages)
    site = {
        "https://n.co.jp/news/": ('<li>2026.11.04 <a href="/news/20261104_01.pdf">AWARD 結果発表</a></li>'
                                  '<li>2025.11.07 <a href="/news/20251107_02.pdf">通期業績予想の修正に関するお知らせ</a></li>'),
        # 日付の書いていない一覧（中身の日付で見分ける）
        "https://m.co.jp/ir/": '<a href="/a.pdf">通期業績予想の修正に関するお知らせ</a>',
        "https://m.co.jp/a.pdf": b"%PDF-1.4 old",
    }
    monkeypatch.setattr(ir, "fetch", _fake_site(site))
    monkeypatch.setattr(ir, "_pdf_text", lambda data, max_chars=3500: "2025年11月7日\n各 位\n通期業績予想の修正に関するお知らせ")
    r = ir.lookup("7943", "通期業績予想の修正に関するお知らせ", "2026-11-10")
    assert r["status"] == "not_found" and r["old_match"]["date"] == "2025-11-07"
    r = ir.lookup("1111", "通期業績予想の修正に関するお知らせ", "2026-11-10")
    assert r["status"] == "not_found" and r["old_match"]["date"] == "2025-11-07"
    # 中身の日付が今回と同じなら使う
    monkeypatch.setattr(ir, "_pdf_text", lambda data, max_chars=3500: "令和８年11月10日\n各 位\n通期業績予想の修正")
    r = ir.lookup("1111", "通期業績予想の修正に関するお知らせ", "2026-11-10")
    assert r["status"] == "found" and r["doc_date"] == "2026-11-10" and not r.get("date_unverified")


def test_ir_lookup_follows_refresh_and_home(tmp_path, monkeypatch):
    """あいちFG：一覧は /release/（0秒後に /release/2026/ へ）。そこになければトップページでも探す。"""
    import json as _json

    from scanner import ir

    pages = tmp_path / "ir_pages.json"
    pages.write_text(_json.dumps({"7389": {"status": "ok", "ir_news": "https://a.co.jp/release/",
                                           "home": "https://a.co.jp/"}}), encoding="utf-8")
    monkeypatch.setattr(ir, "PAGES", pages)
    title = "業績予想の修正、期末配当予想の修正（増配）および自己株式取得に係る事項の決定に関するお知らせ"
    site = {
        "https://a.co.jp/release/": '<meta http-equiv="refresh" content="0;URL=/release/2026/">',
        "https://a.co.jp/release/2026/": '<li>2026年11月09日 <a href="/r/news20261109.pdf">主要株主の異動に関するお知らせ</a></li>',
        "https://a.co.jp/": f'<dl><dt>2026年11月10日</dt><dd><a href="/ir/files/pdf/20261110_kessan.pdf">{title}</a></dd></dl>',
        "https://a.co.jp/ir/files/pdf/20261110_kessan.pdf": b"%PDF-1.4",
    }
    monkeypatch.setattr(ir, "fetch", _fake_site(site))
    monkeypatch.setattr(ir, "_pdf_text", lambda data, max_chars=3500: "2026年11月10日\n各 位\n業績予想の修正")
    r = ir.lookup("7389", title, "2026-11-10")
    assert r["status"] == "found" and r["url"].endswith("20261110_kessan.pdf") and "トップページ" in r["note"]
    # 一覧のページ（meta refresh の先）にあれば、そこで見つける
    site["https://a.co.jp/release/2026/"] += site["https://a.co.jp/"]
    r = ir.lookup("7389", title, "2026-11-10")
    assert r["status"] == "found" and "note" not in r


def test_ir_attach_rechecks_stale_cache(tmp_path, monkeypatch):
    """前に「見つかった」と記録した資料が去年の分なら、探し直す。"""
    import json as _json

    from scanner import ir

    pages = tmp_path / "ir_pages.json"
    pages.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ir, "PAGES", pages)
    monkeypatch.setattr(ir, "CACHE", tmp_path / "ir")
    (tmp_path / "ir").mkdir()
    title = "通期業績予想の修正に関するお知らせ"
    (tmp_path / "ir" / "2026-11-10.json").write_text(_json.dumps({f"7943|{title}": {
        "status": "found", "date": "2025-11-07", "kind": "pdf", "excerpt": "2025年11月7日", "checked": "2026-11-10T08:11"}}),
        encoding="utf-8")
    calls = []
    monkeypatch.setattr(ir, "lookup", lambda code, t, hint="": calls.append(hint) or {"status": "not_found"})
    data = {"base_day": "2026-11-10", "candidates": [
        {"code": "7943", "category": "CHECK", "items": [{"time": "11/10 16:10", "title": title,
                                                         "labels": [{"cat": "CHECK", "label": "業績予想の修正"}]}]}]}
    ir.attach_ir_checks(data)
    assert calls == ["2026-11-10"] and data["candidates"][0]["ir_check"]["status"] == "not_found"


def test_ir_find_page_prefers_dated_list(monkeypatch):
    """IRのページに一覧がない会社は、トップページのニュースリリースのページを使う。"""
    from scanner import ir

    site = {
        "https://a.co.jp/": ('<a href="/ir/">株主・投資家の皆さまへ</a><a href="/release/">ニュースリリース一覧</a>'
                             '<a href="/products/">商品</a>'),
        "https://a.co.jp/ir/": '<p>IRカレンダー 11月 第2四半期決算発表</p><a href="/ir/financialreport/">決算関連情報</a>',
        "https://a.co.jp/ir/financialreport/": '<a href="/ir/financialreport/financial/">決算短信</a>',
        "https://a.co.jp/release/": ''.join(f'<li>2026年11月{d:02d}日 <a href="/r/{d}.pdf">お知らせ{d}</a></li>' for d in range(1, 6)),
    }
    monkeypatch.setattr(ir, "fetch", _fake_site(site))
    r = ir.find_ir_page("https://a.co.jp/")
    assert r["status"] == "ok" and r["ir_news"] == "https://a.co.jp/release/" and r["score"] == 5 and r["method_v"] == 2
    # IRのページに一覧があれば、そちらを使う（トップのニュースは見に行かない）
    site["https://a.co.jp/ir/"] = ''.join(f'<li>2026.11.{d:02d} <a href="/ir/{d}.pdf">決算{d}</a></li>' for d in range(1, 4))
    r = ir.find_ir_page("https://a.co.jp/")
    assert r["ir_news"] == "https://a.co.jp/ir/" and r["score"] == 3


def _hc_setup(tmp_path, monkeypatch, portfolio):
    import json as _json

    from scanner import human_check as H
    from scanner import line_actions as L

    inp = tmp_path / "analyst_input.json"
    inp.write_text(_json.dumps({"base_day": "2026-11-10", "portfolio": portfolio, "stop_limits": [], "candidates": [
        {"code": "7389", "name": "あいちFG", "category": "A", "score": 71, "price": 1696.0, "unit": 100, "single": False,
         "items": [{"time": "11/10 16:00", "title": "業績予想の修正、期末配当予想の修正（増配）および自己株式取得",
                    "pdf": "https://www.release.tdnet.info/inbs/140120261028557901.pdf",
                    "labels": [{"cat": "CHECK", "label": "業績予想の修正"}, {"cat": "A", "label": "増配"},
                               {"cat": "A", "label": "自社株買い"}]}]},
        {"code": "7943", "name": "ニチハ", "category": "CHECK", "score": 45, "price": 2900.0, "unit": 100, "single": False,
         "items": [{"time": "11/10 16:10", "title": "通期業績予想の修正に関するお知らせ", "pdf": "https://x/b.pdf",
                    "labels": [{"cat": "CHECK", "label": "業績予想の修正"}]}]},
        {"code": "9664", "name": "御園座", "category": "A", "score": 50, "price": 1000.0, "unit": 100, "single": True,
         "items": [{"time": "11/10 15:30", "title": "自己株式の取得に関するお知らせ", "pdf": "https://x/c.pdf",
                    "labels": [{"cat": "A", "label": "自社株買い"}]}]}]}), encoding="utf-8")
    monkeypatch.setattr(H, "INPUT", inp)
    monkeypatch.setattr(L, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(L, "short_status", lambda pf=None: "保有：（省略）")
    monkeypatch.setattr(L, "write_status", lambda pf=None: None)
    return H, L


def test_ask_only_affordable(tmp_path, monkeypatch):
    """今夜の注文のあとで1単位も買えない候補は、確認を頼まない（11/10：百五を買ったあと残り枠154,500円）。"""
    H, _ = _hc_setup(tmp_path, monkeypatch, {"cash": 304000.0, "term_left": 3000000.0, "term_open": True})
    d = {"id": "2026-11-10-1712", "base_day": "2026-11-10",
         "orders": [{"side": "sell", "code": "6448", "shares": 700, "price": 4465},
                    {"side": "buy", "code": "8368", "shares": 1400, "price": 2032.5}],
         "ask_human": [{"code": "7389", "question": "上方か下方か"}, {"code": "7943", "question": "上方か下方か"},
                       {"code": "9664", "question": "何%か"}]}
    qs = H.pick_questions(d)
    assert [q["code"] for q in qs] == ["9664"]  # 1単位10万円なら残り枠154,500円で買える
    d["ask_human"][0]["replace"] = True  # 答えしだいで百五と入れ替える
    qs = H.pick_questions(d)
    assert [q["code"] for q in qs] == ["7389", "9664"] and qs[0]["replace"] and not qs[1]["replace"]
    assert "入れ替えます" in H.ask_text(qs)
    # 注文がない夜は、残り枠と現金の範囲で聞く
    qs = H.pick_questions({"base_day": "2026-11-10", "orders": [], "ask_human": [{"code": "7389"}, {"code": "7943"}]})
    assert [q["code"] for q in qs] == ["7389", "7943"]  # 現金304,000円なら、どちらも1単位は買える
    qs = H.pick_questions({"base_day": "2026-11-10", "orders": [], "ask_human": [{"code": "7943"}]},
                          dict(H._load_input(), portfolio={"cash": 200000.0, "term_left": 3000000.0}))
    assert qs == []  # 現金20万円ではニチハ（29万円）は買えない


def test_answer_buttons(tmp_path, monkeypatch):
    """確認のお願いのボタン：［上方修正］でAIを起動、［わからない］は起動せずに閉じる。"""
    from datetime import datetime as _dt

    H, L = _hc_setup(tmp_path, monkeypatch, {"cash": 3000000.0, "term_left": 3000000.0, "term_open": True})
    d = {"id": "2026-11-10-2120", "base_day": "2026-11-10", "orders": [],
         "ask_human": [{"code": "7389", "question": "上方か下方か"}, {"code": "9664", "question": "何%か"}]}
    qs = H.pick_questions(d)
    qr = H.quick_reply(qs, "2026-11-10")
    labels = [i["action"]["label"] for i in qr["items"]]
    assert labels == ["7389 上方修正", "7389 下方修正", "7389 ほぼ変わらず", "7389 わからない",
                      "9664 1%未満", "9664 1〜3%", "9664 3%以上", "9664 わからない"]
    assert all(len(x) <= 20 for x in labels) and len(qr["items"]) <= 13
    now = _dt(2026, 11, 10, 21, 30, tzinfo=JST)
    st = L.load_state()
    H.register(qs, d, st, now)
    L.save_state(st)
    data = qr["items"][0]["action"]["data"]
    r = L.process({"type": "postback", "data": data, "event_id": "b1"}, now)
    assert "上方修正" in r["fire"] and r["fire"]
    r = L.process({"type": "postback", "data": data, "event_id": "b2"}, now)
    assert "答え済み" in r["text"] and r["fire"] == ""  # 2回押しても1回だけ
    skip = qr["items"][7]["action"]["data"]
    r = L.process({"type": "postback", "data": skip, "event_id": "b3"}, now)
    assert "確認なし" in r["text"] and r["fire"] == ""
    assert {q["code"]: q["status"] for q in L.load_state()["questions"]} == {"7389": "answered", "9664": "skipped"}
    # 前のタームのボタンは受け付けない
    old = data.replace("b=2026-11-10", "b=2026-11-09")
    assert "前のターム" in L.process({"type": "postback", "data": old, "event_id": "b4"}, now)["text"]


def test_earnings_soon_skips_todays_results(tmp_path, monkeypatch):
    """夜のタームでは、今日の決算（もう発表済み）で「決算が近い」を出さない（2026-11-10 百五銀行の誤報）。"""
    import json as _json
    from types import SimpleNamespace

    from scanner import earnings as E
    from scanner import portfolio as P

    ep = tmp_path / "earnings.json"
    ep.write_text(_json.dumps({"events": [{"date": "2026-11-10", "code": "8368"}, {"date": "2026-11-11", "code": "7203"}]}),
                  encoding="utf-8")
    monkeypatch.setattr(E, "PATH", ep)
    ctx = SimpleNamespace(base=date(2026, 11, 10))
    got = P._earnings_soon(ctx, datetime(2026, 11, 10, 18, 40, tzinfo=JST))
    assert "8368" not in got and "11/11(水)（次の営業日）" in got["7203"]
    # 朝（基準日は前の営業日）：今日の決算の前に売れるので数える
    got = P._earnings_soon(ctx, datetime(2026, 11, 11, 6, 20, tzinfo=JST))
    assert "（今日）" in got["7203"] and "8368" not in got


def test_main_run_done(tmp_path, monkeypatch):
    import json as _json

    from scanner import ai_decision as AD
    from scanner import ai_input as AI

    monkeypatch.setattr(AD, "LATEST", tmp_path / "latest.json")
    assert not AI._main_run_done("2026-11-10")
    AD.LATEST.write_text(_json.dumps({"base_day": "2026-11-10", "created_at": "2026-11-10T18:41:00+09:00",
                                      "_apply": {"applied_at": "2026-11-10T18:42:34+09:00"}}), encoding="utf-8")
    assert not AI._main_run_done("2026-11-10")  # 夕方の判断だけ
    AD.LATEST.write_text(_json.dumps({"base_day": "2026-11-10", "created_at": "2026-11-10T21:15:00+09:00",
                                      "_apply": {"applied_at": "2026-11-10T21:21:00+09:00"}}), encoding="utf-8")
    assert AI._main_run_done("2026-11-10") and not AI._main_run_done("2026-11-11")


def test_order_message_long_falls_back(monkeypatch):
    """注文が多くてボタンつきの形（本文160文字まで）に入らないときは、文＋クイックリプライの1通にする。"""
    from scanner import line_actions as L

    monkeypatch.setattr(L, "enabled", lambda: True)
    orders = [{"side": "buy", "code": str(1000 + i), "name": "とても長い会社の名前ホールディングス", "shares": 100, "unit": 100}
              for i in range(6)]
    m = L.order_message({"id": "x", "deadline": "2026-11-11T07:59:00+09:00", "orders": orders})
    assert m["type"] == "text" and len(m["quickReply"]["items"]) == 3 and m["text"].count("買い") == 6
    m = L.order_message({"id": "x", "deadline": "2026-11-11T07:59:00+09:00", "orders": orders[:1]})
    assert m["type"] == "template" and m["template"]["text"].startswith("【今夜の注文】締切 11/11(水) 07:59")
