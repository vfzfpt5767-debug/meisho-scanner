"""新規上場トラッカー（名証公式サイトの「新規上場会社一覧」）。

- 一覧ページは1日1回だけ取得して data/new_listings.json に保存する。
- IPO（新規公開）は「価格決定」のPDF（名証サイト）から公開価格と、東証にも上場するかを読む（1社1回だけ）。
- 上場日の値段は名証の日報から（初値＝名証の始値。約定がなければ「気配のみ」）。
"""
from __future__ import annotations

import io
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from html import unescape

from . import http
from .calendar_jp import now_jst
from .config import CONTEST_START, DATA_DIR
from .sources import nse_prices

URL = "https://www.nse.or.jp/listing/new/"
BASE = "https://www.nse.or.jp"
PATH = DATA_DIR / "new_listings.json"


@dataclass
class NewListing:
    date: str
    market: str
    code: str
    name: str
    ipo: bool
    kettei_url: str = ""
    offer_price: float | None = None
    header: str = ""
    links: dict = field(default_factory=dict)

    @property
    def d(self) -> date:
        return date.fromisoformat(self.date)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def parse(html: str) -> list[NewListing]:
    out: list[NewListing] = []
    for m in re.finditer(r"<h2>(\d{4})年</h2>(.*?)</table>", html, flags=re.S):
        year = int(m[1])
        for tr in re.findall(r"<tr>(.*?)</tr>", m[2], flags=re.S):
            tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)
            if len(tds) < 3:
                continue
            md = re.search(r"(\d{1,2})/(\d{1,2})", _text(tds[0]))
            if not md:
                continue
            cell = re.findall(r"<p[^>]*>(.*?)</p>", tds[2], flags=re.S) or [tds[2]]
            parts = [_text(x) for x in cell if _text(x)]
            code = unicodedata.normalize("NFKC", parts[0]).strip() if parts else ""
            name = parts[1] if len(parts) > 1 else _text(tds[2])
            links = {_text(t): (BASE + h if h.startswith("/") else h)
                     for h, t in re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', tr, flags=re.S) if _text(t)}
            out.append(NewListing(date=date(year, int(md[1]), int(md[2])).isoformat(), market=_text(tds[1]),
                                  code=code, name=name, ipo=("Ⅰの部" in tr), kettei_url=links.get("価格決定", ""),
                                  links=links))
    return out


def _read_kettei(url: str) -> tuple[float | None, str]:
    import pdfplumber

    r = http.get(url)
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages[:2])
    t = unicodedata.normalize("NFKC", text)
    m = re.search(r"発行価格[^\n]*?金\s*([0-9,]+)\s*円", t)
    price = float(m[1].replace(",", "")) if m else None
    h = re.search(r"\(コード番号[^)]*\)", t)
    return price, (h[0] if h else "")


def load() -> tuple[list[NewListing], str | None]:
    cached = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else None
    today = now_jst().date().isoformat()
    err = None
    if not cached or cached.get("fetched_date") != today:
        try:
            items = parse(http.get(URL).content.decode("utf-8", errors="replace"))
            if not items:
                raise ValueError("一覧を読み取れませんでした")
            old = {(x["code"], x["date"]): x for x in (cached or {}).get("items", [])}
            for it in items:
                o = old.get((it.code, it.date))
                if o and o.get("offer_price"):
                    it.offer_price, it.header = o["offer_price"], o.get("header", "")
                elif it.ipo and it.kettei_url and it.d >= CONTEST_START:
                    try:
                        it.offer_price, it.header = _read_kettei(it.kettei_url)
                    except Exception:  # noqa: BLE001
                        pass
            cached = {"fetched_date": today, "items": [vars(x) for x in items]}
            PATH.parent.mkdir(parents=True, exist_ok=True)
            PATH.write_text(json.dumps(cached, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            err = f"名証の新規上場一覧の取得に失敗: {http.short_err(e)}" + ("（前回分を使用）" if cached else "")
    if not cached:
        return [], err
    return [NewListing(**x) for x in cached["items"]], err


@dataclass
class ListingRow:
    item: NewListing
    single: bool | None
    single_note: str
    status: str  # 上場予定 / 上場済み
    first_open: float | None = None  # 初値（名証の始値）
    first_day_price: float | None = None  # 上場日の約定見込み価格（最終気配）
    first_day_quote_only: bool = False
    price_now: float | None = None


def rows(res) -> tuple[list[ListingRow], list[str]]:
    """期間中（10/22以降）と今後の新規上場を返す。"""
    ctx = res.ctx
    items, err = load()
    errs = [err] if err else []
    out: list[ListingRow] = []
    for it in items:
        if it.d < CONTEST_START:
            continue
        ls = ctx.universe.get(it.code)
        if ls is not None:
            single, note = ls.single, "名証の銘柄一覧より"
        elif it.header:
            single = "東証" not in it.header and "東京証券取引所" not in it.header
            note = f"価格決定資料の記載 {it.header} より（上場後に確定）"
        else:
            single, note = None, "上場後に確定"
        r = ListingRow(item=it, single=single, single_note=note, status="上場予定" if it.d > ctx.base else "上場済み")
        if it.d <= ctx.base:
            q_day = ctx.quotes if it.d == ctx.base else nse_prices.get_prices(it.d)[0]
            q = q_day.get(it.code)
            if q:
                r.first_open = q.open
                r.first_day_price = q.contest_price
                r.first_day_quote_only = q.quote_only
            qn = ctx.quotes.get(it.code)
            r.price_now = qn.contest_price if qn else None
        out.append(r)
    up = sorted([r for r in out if r.status == "上場予定"], key=lambda r: r.item.date)
    done = sorted([r for r in out if r.status != "上場予定"], key=lambda r: r.item.date, reverse=True)
    return up + done, errs
