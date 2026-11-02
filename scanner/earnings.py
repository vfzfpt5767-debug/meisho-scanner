"""決算カレンダー：今後10営業日の名証上場銘柄の決算発表予定日。

- JPX「決算発表予定日」のExcel（東証上場会社）
- 名証「決算発表日程」のExcel（名証記者クラブで発表する会社。名証単独上場会社を含む）
どちらも1日1回（17:10以降に1回更新）だけ取得して data/earnings.json に保存する。
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time

from . import http
from .calendar_jp import is_business_day, next_business_day, now_jst
from .config import DATA_DIR

JPX_PAGE = "https://www.jpx.co.jp/listing/event-schedules/financial-announcement/index.html"
NSE_PAGE = "https://www.nse.or.jp/listing/schedule/"
PATH = DATA_DIR / "earnings.json"


@dataclass
class Event:
    date: str
    code: str
    name: str
    kind: str = ""
    sources: list[str] = field(default_factory=list)


def _xlsx_links(page_url: str, base: str) -> list[str]:
    html = http.get(page_url).content.decode("utf-8", errors="replace")
    links = re.findall(r'href="([^"]+\.xlsx)"', html)
    out = []
    for h in links:
        u = h if h.startswith("http") else base + (h if h.startswith("/") else "/" + h)
        if u not in out:
            out.append(u)
    return out


def _code(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return str(int(v))
    s = str(v).strip().upper()
    return s[:4] if len(s) == 5 and s.endswith("0") else s


def _parse_xlsx(data: bytes, source: str) -> list[Event]:
    import warnings

    import openpyxl

    warnings.filterwarnings("ignore", module="openpyxl")
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: list[Event] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            if not row or not isinstance(row[0], (datetime, date)):
                continue
            d = row[0].date() if isinstance(row[0], datetime) else row[0]
            code = _code(row[1] if len(row) > 1 else None)
            if not re.fullmatch(r"[0-9][0-9A-Z]{3}", code):
                continue
            name = str(row[2] or "").replace("㈱", "").strip() if len(row) > 2 else ""
            kind = str(row[7] or "") if source == "JPX" and len(row) > 7 else ""
            out.append(Event(d.isoformat(), code, name, kind, [source]))
    return out


def load() -> tuple[list[Event], str | None]:
    cached = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else None
    now = now_jst()
    need = (not cached or cached.get("fetched_date") != now.date().isoformat()
            or (now.time() >= time(17, 10) and cached.get("fetched_time", "00:00") < "17:10"))
    err = None
    if need:
        try:
            events: dict[tuple[str, str], Event] = {}
            files = [(u, "JPX") for u in _xlsx_links(JPX_PAGE, "https://www.jpx.co.jp")]
            files += [(u, "名証") for u in _xlsx_links(NSE_PAGE, "https://www.nse.or.jp")]
            for url, src in files:
                for e in _parse_xlsx(http.get(url).content, src):
                    k = (e.date, e.code)
                    if k in events:
                        if src not in events[k].sources:
                            events[k].sources.append(src)
                        events[k].kind = events[k].kind or e.kind
                    else:
                        events[k] = e
            if not events:
                raise ValueError("予定を読み取れませんでした")
            cached = {"fetched_date": now.date().isoformat(), "fetched_time": f"{now:%H:%M}",
                      "files": [u for u, _ in files], "events": [vars(e) for e in events.values()]}
            PATH.parent.mkdir(parents=True, exist_ok=True)
            PATH.write_text(json.dumps(cached, ensure_ascii=False, indent=0), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            err = f"決算発表予定の取得に失敗: {http.short_err(e)}" + ("（前回分を使用）" if cached else "")
    if not cached:
        return [], err
    return [Event(**x) for x in cached["events"]], err


def upcoming(universe: dict, today: date, n_days: int = 10) -> tuple[list[date], dict[str, list[Event]], list[str]]:
    """今日（休業日なら次の営業日）から n 営業日分の、名証上場銘柄の予定。"""
    events, err = load()
    days = []
    d = today if is_business_day(today) else next_business_day(today)
    while len(days) < n_days:
        days.append(d)
        d = next_business_day(d)
    keep = {x.isoformat() for x in days}
    by_day: dict[str, list[Event]] = {}
    for e in events:
        if e.date in keep and e.code in universe:
            by_day.setdefault(e.date, []).append(e)
    return days, by_day, [err] if err else []
