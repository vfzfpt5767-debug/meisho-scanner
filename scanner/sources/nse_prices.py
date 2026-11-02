"""名証公式の相場表PDF（日報・速報）から、全上場銘柄の値段を読む。

コンテストの約定価格は「名証の直近の終値（最終気配値段を含む）」なので、
名証の相場表がいちばん正確な情報源になる。
- 日報: https://www.nse.or.jp/market/condition/report/files/YYYYMMDD.pdf （17:00頃）
- 速報: https://www.nse.or.jp/market/condition/quick/files/sokuhou.pdf （16:00頃。最新日のみ）
読み取った結果は data/cache/nse_prices/YYYYMMDD.json に保存する。
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import asdict, dataclass
from datetime import date

from .. import http
from ..calendar_jp import now_jst
from ..config import CACHE_DIR

NIPPO_URL = "https://www.nse.or.jp/market/condition/report/files/{d:%Y%m%d}.pdf"
SOKUHOU_URL = "https://www.nse.or.jp/market/condition/quick/files/sokuhou.pdf"
CACHE = CACHE_DIR / "nse_prices"
COLS = ["open", "high", "low", "close", "change", "quote", "volume"]
CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}0$")
NUM_RE = re.compile(r"^[+\-]?[0-9][0-9,]*(\.[0-9]+)?$")
DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


@dataclass
class Quote:
    code: str
    name: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None  # 名証で約定した終値
    change: float | None = None
    quote: float | None = None  # 最終気配値段
    volume: float | None = None  # 千株

    @property
    def contest_price(self) -> float | None:
        """コンテストの約定見込み価格。最終気配があればそれ、なければ終値。"""
        return self.quote if self.quote is not None else self.close

    @property
    def is_quote(self) -> bool:
        return self.quote is not None


def _num(s: str) -> float | None:
    s = re.sub(r"^[^\d+\-]+", "", s).replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _code4(c5: str) -> str:
    return c5[:4] if c5.endswith("0") else c5


def parse_pdf(data: bytes) -> tuple[date | None, dict[str, Quote]]:
    import pdfplumber

    out: dict[str, Quote] = {}
    pdf_date: date | None = None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            first = text.split("\n", 1)[0]
            if "株券" not in first or "立会取引" not in first:
                continue
            m = DATE_RE.search(first)
            if m and pdf_date is None:
                pdf_date = date(int(m[1]), int(m[2]), int(m[3]))
            words = page.extract_words()
            unit_words = [w for w in words if w["text"] in ("円", "千株")]
            if len(unit_words) < 14:
                continue
            unit_top = min(w["top"] for w in unit_words)
            unit_words = sorted([w for w in unit_words if abs(w["top"] - unit_top) < 2], key=lambda w: w["x0"])
            halves = [unit_words[:7], unit_words[7:14]]
            code_heads = sorted([w for w in words if w["text"] == "ｺ" and w["top"] < unit_top], key=lambda w: w["x0"])
            if len(code_heads) < 2:
                continue
            mid = code_heads[1]["x0"] - 4
            lines: dict[int, list[dict]] = {}
            for w in words:
                if w["top"] <= unit_top + 2:
                    continue
                lines.setdefault(round(w["top"]), []).append(w)
            for _, ws in sorted(lines.items()):
                for hi, half in enumerate(halves):
                    hw = sorted([w for w in ws if (w["x0"] < mid) == (hi == 0)], key=lambda w: w["x0"])
                    if not hw or not CODE_RE.match(hw[0]["text"]):
                        continue
                    col_x1 = [u["x1"] for u in half]
                    first_price_x0 = half[0]["x0"] - 30
                    name_parts = [w["text"] for w in hw[1:] if w["x1"] < first_price_x0]
                    q = Quote(code=_code4(hw[0]["text"]), name=re.sub(r"^[ABCＡＢＣ●○]+", "", "".join(name_parts)))
                    for w in hw[1:]:
                        if w["x1"] < first_price_x0 or not NUM_RE.match(w["text"].lstrip("◎△□◇＃")):
                            continue
                        dist = [abs(w["x1"] - x) for x in col_x1]
                        i = dist.index(min(dist))
                        if min(dist) <= 8:
                            setattr(q, COLS[i], _num(w["text"]))
                    out[q.code] = q
    return pdf_date, out


def _save(d: date, quotes: dict[str, Quote], source: str, final: bool) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / f"{d:%Y%m%d}.json").write_text(
        json.dumps({"date": d.isoformat(), "source": source, "final": final,
                    "fetched_at": now_jst().isoformat(),
                    "quotes": {k: asdict(v) for k, v in quotes.items()}}, ensure_ascii=False, indent=0),
        encoding="utf-8",
    )


def _load_cache(d: date) -> tuple[dict[str, Quote], dict] | None:
    p = CACHE / f"{d:%Y%m%d}.json"
    if not p.exists():
        return None
    j = json.loads(p.read_text(encoding="utf-8"))
    return {k: Quote(**v) for k, v in j["quotes"].items()}, j


def get_prices(d: date) -> tuple[dict[str, Quote], str, str | None]:
    """指定日の名証の値段。(銘柄→Quote, 出典の説明, エラー)

    日報（確定版）→ 速報 → キャッシュ の順に試す。
    """
    cached = _load_cache(d)
    if cached and cached[1].get("final"):
        return cached[0], "名証 日報", None
    errs = []
    try:
        r = http.get(NIPPO_URL.format(d=d))
        if r.headers.get("content-type", "").startswith("application/pdf") or r.content[:4] == b"%PDF":
            pd, quotes = parse_pdf(r.content)
            if pd == d and len(quotes) > 100:
                _save(d, quotes, "名証 日報", True)
                return quotes, "名証 日報", None
            errs.append(f"日報の日付/件数が想定外（{pd}, {len(quotes)}件）")
    except Exception as e:  # noqa: BLE001
        errs.append(f"日報: {http.short_err(e)}")
    try:
        r = http.get(SOKUHOU_URL)
        pd, quotes = parse_pdf(r.content)
        if pd == d and len(quotes) > 100:
            _save(d, quotes, "名証 相場表（速報）", False)
            return quotes, "名証 相場表（速報）", None
        errs.append(f"速報は{pd}分でした")
    except Exception as e:  # noqa: BLE001
        errs.append(f"速報: {http.short_err(e)}")
    if cached:
        return cached[0], cached[1].get("source", "キャッシュ"), None
    return {}, "", f"名証の相場表（{d:%m/%d}）を取得できませんでした（" + "、".join(errs) + "）"
