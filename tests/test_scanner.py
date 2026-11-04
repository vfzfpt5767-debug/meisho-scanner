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
    sent = []
    monkeypatch.setattr("scanner.notify.send", lambda text, kind, extra=None: sent.append(text) or "LINEに送信しました")
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
    assert sent[-1].startswith("【参考（起きていれば）】")
    (dec / "2026-11-03-2115.json").write_text(json.dumps(real), encoding="utf-8")
    AD.apply_all(now=datetime(2026, 11, 3, 21, 30, tzinfo=JST))
    assert "4445" in h.read_text() and "2833500" in c.read_text()
    assert sent[-1].startswith("【AIの結論】")


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
    assert sent[-1][1] is None  # 中継役がまだ動いていないときはボタンを付けない
    L.save_state({**L.load_state(), "enabled": True})
    (dec / "2026-11-04-2120.json").rename(dec / "2026-11-04-2121.json")
    d["id"] = "2026-11-04-2121"
    rows_before = h.read_text(encoding="utf-8")
    h.write_text("code,shares,buy_price,buy_date,memo\n6999,1000,1950,2026-10-30\n", encoding="utf-8")
    c.write_text("key,value,memo\ncash,1000000,\n", encoding="utf-8")
    AD.apply_all(now=now)
    btn = sent[-1][1][0]
    assert btn["type"] == "template" and len(btn["template"]["actions"]) == 3
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
    chk = vars(C.Tag(C.CHECK, "業績予想の修正", 40, ""))
    a_tag = vars(C.Tag(C.A, "上方修正", 60, ""))

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
    # 3) 新しい開示（新しい候補）が出たら、今までどおり起動する
    res.cards.append(card("7203", C.A, a_tag))
    req = AI.plan_fire(res, None, {"x": 1})
    assert req["fire"] and req["new_keys"] == ["A:7203"]


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
