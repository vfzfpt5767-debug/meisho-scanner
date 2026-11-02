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
