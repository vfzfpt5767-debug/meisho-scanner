"""名証の上場銘柄一覧（名証公式サイトの上場会社検索のデータ）。

単独上場かどうか・売買単位も公式データから取る。1日1回だけ取得してキャッシュする。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .. import http
from ..calendar_jp import now_jst
from ..config import DATA_DIR

API = "https://www.nse.or.jp/api/stock/search.json"
PATH = DATA_DIR / "universe.json"
DIVISIONS = {1: "プレミア", 2: "メイン", 3: "ネクスト", 4: "その他(ETF等)"}


@dataclass
class Listing:
    code: str  # 4文字コード
    name: str
    single: bool  # 名証単独上場
    division: str
    unit: int  # 売買単位
    industry: str


def _code4(c5: str) -> str:
    return c5[:4] if len(c5) == 5 and c5.endswith("0") else c5


def _fetch_all(single_only: bool) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        params = {
            "stockCode": "", "stockName_j": "", "listedSingle": "1" if single_only else "",
            "industryCode": "", "listedClose": "", "tradingUnit": "",
            "dispType": "stockCode", "dispOrder": "ASC", "dispCount": "100", "dispPage": str(page),
        }
        d = http.get(API, params=params).json()
        rows += d.get("stock", [])
        total = int(d["list"][0]["listTotal"]) if d.get("list") else 0
        if page * 100 >= total or not d.get("stock"):
            break
        page += 1
    return rows


def load(*, refresh: bool = True) -> tuple[dict[str, Listing], str | None]:
    """{4文字コード: Listing} と、エラー（あれば）を返す。"""
    cached = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else None
    today = now_jst().date().isoformat()
    err = None
    if refresh and (not cached or cached.get("fetched_date") != today):
        try:
            rows = _fetch_all(False)
            singles = {r["stockCode"] for r in _fetch_all(True)}
            if len(rows) < 200:
                raise ValueError(f"件数が少なすぎます（{len(rows)}件）")
            cached = {"fetched_date": today, "source": API, "rows": rows, "single_codes": sorted(singles)}
            PATH.parent.mkdir(parents=True, exist_ok=True)
            PATH.write_text(json.dumps(cached, ensure_ascii=False, indent=0), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            err = f"名証の上場銘柄一覧の取得に失敗: {http.short_err(e)}" + ("（前回分を使用）" if cached else "")
    if not cached:
        return {}, err or "名証の上場銘柄一覧がありません"
    singles = set(cached.get("single_codes", []))
    out: dict[str, Listing] = {}
    for r in cached["rows"]:
        c5 = r["stockCode"]
        try:
            unit = int(str(r.get("tradingUnit") or "100").replace(",", ""))
        except ValueError:
            unit = 100
        out[_code4(c5)] = Listing(
            code=_code4(c5),
            name=r.get("stockName_j", ""),
            single=(c5 in singles) or r.get("simpleListing") == 1,
            division=DIVISIONS.get(r.get("listedDivision"), "?"),
            unit=unit or 100,
            industry=r.get("industryName_j", ""),
        )
    return out, err
