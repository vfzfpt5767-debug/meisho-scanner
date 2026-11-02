"""適時開示の一覧（やのしんWEB-API経由）。

TDnet本体は robots.txt で自動取得を拒否しているため、本ツールは
TDnetのPDF/XBRLを自動では読まない（一覧のメタデータだけを使う）。
名証上場の開示と、TOBに関する開示だけをキャッシュに残す。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .. import http
from ..calendar_jp import JST, now_jst
from ..config import CACHE_DIR

API = "https://webapi.yanoshin.jp/webapi/tdnet/list/{d}.json"
CACHE = CACHE_DIR / "tdnet"


@dataclass
class Disclosure:
    id: str
    pubdate: datetime
    code5: str
    name: str
    title: str
    pdf_url: str
    markets: str
    has_xbrl: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def code(self) -> str:
        """4桁（英字入りは4文字）の証券コード。"""
        c = self.code5
        return c[:4] if len(c) == 5 and c.endswith("0") else c

    @property
    def nagoya(self) -> bool:
        return "名" in (self.markets or "")


def _strip_redirect(url: str | None) -> str:
    if not url:
        return ""
    if "rd.php?" in url:
        return url.split("rd.php?", 1)[1]
    return url


def _parse_item(raw: dict) -> Disclosure:
    t = raw.get("Tdnet", raw)
    return Disclosure(
        id=str(t.get("id", "")),
        pubdate=datetime.strptime(t["pubdate"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST),
        code5=str(t.get("company_code", "")).strip(),
        name=(t.get("company_name") or "").strip(),
        title=(t.get("title") or "").strip(),
        pdf_url=_strip_redirect(t.get("document_url")),
        markets=t.get("markets_string") or "",
        has_xbrl=bool(t.get("url_xbrl")),
    )


def _keep(raw: dict) -> bool:
    t = raw.get("Tdnet", raw)
    title = t.get("title") or ""
    return "名" in (t.get("markets_string") or "") or "公開買付" in title or "ＭＢＯ" in title or "MBO" in title


def fetch_day(d: date, *, force: bool = False) -> tuple[list[Disclosure], str | None]:
    """指定日の開示を返す。(一覧, エラーメッセージ or None)

    過去の日でキャッシュが「完了」なら再取得しない。当日分は毎回取得する。
    取得に失敗したらキャッシュを使い、エラーメッセージを返す。
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{d:%Y%m%d}.json"
    cached = None
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("complete") and not force:
            return [_parse_item(x) for x in cached["items"]], None
    err = None
    try:
        r = http.get(API.format(d=f"{d:%Y%m%d}"), params={"limit": 5000})
        data = r.json()
        items = [x for x in data.get("items", []) if _keep(x)]
        fetched = now_jst()
        complete = fetched >= datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=JST) + timedelta(hours=1)
        path.write_text(
            json.dumps(
                {"date": f"{d:%Y-%m-%d}", "fetched_at": fetched.isoformat(), "complete": complete,
                 "total_all_markets": data.get("total_count"), "items": items},
                ensure_ascii=False, indent=0,
            ),
            encoding="utf-8",
        )
        return [_parse_item(x) for x in items], None
    except Exception as e:  # noqa: BLE001
        err = f"適時開示一覧（{d:%m/%d}）の取得に失敗: {http.short_err(e)}"
        if cached:
            return [_parse_item(x) for x in cached["items"]], err + "（前回取得分を表示）"
        return [], err


def fetch_range(start: date, end: date) -> tuple[list[Disclosure], list[str]]:
    out: list[Disclosure] = []
    errs: list[str] = []
    d = start
    while d <= end:
        items, err = fetch_day(d)
        out.extend(items)
        if err:
            errs.append(err)
        d += timedelta(days=1)
    # 同じIDの重複を除く
    seen: set[str] = set()
    uniq = []
    for x in sorted(out, key=lambda x: x.pubdate):
        if x.id in seen:
            continue
        seen.add(x.id)
        uniq.append(x)
    return uniq, errs
