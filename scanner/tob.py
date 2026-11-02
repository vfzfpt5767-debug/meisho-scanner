"""TOBトラッカー：期間中にTOBが発表された名証上場銘柄の一覧と、TOB価格との差。

TOB価格はPDFを自動で読まないので、ユーザーが tob_prices.csv に書き込む。
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field

from .classify import BAD, S, TOB_REL, Tag, classify, norm, target_codes
from .config import DATA_DIR, TOB_PRICES_CSV
from .sources.yanoshin import Disclosure

PATH = DATA_DIR / "tob_tracker.json"


@dataclass
class TobEntry:
    code: str
    name: str
    first_date: str
    status: str = "進行中"
    events: list[dict] = field(default_factory=list)  # {date, time, title, pdf, label}


def load_tob_prices() -> tuple[dict[str, float], list[str]]:
    """tob_prices.csv を読む。書式の間違いは警告として返す。"""
    prices: dict[str, float] = {}
    warns: list[str] = []
    if not TOB_PRICES_CSV.exists():
        return prices, warns
    text = TOB_PRICES_CSV.read_text(encoding="utf-8-sig")
    for i, row in enumerate(csv.reader(text.splitlines()), start=1):
        if not row or not "".join(row).strip() or row[0].strip().lower() == "code":
            continue
        code = unicodedata.normalize("NFKC", row[0]).strip().upper()
        raw = unicodedata.normalize("NFKC", row[1] if len(row) > 1 else "")
        num = re.sub(r"[,円\s]", "", raw)
        try:
            prices[code] = float(num)
        except ValueError:
            warns.append(f"tob_prices.csv の{i}行目を読めませんでした（{','.join(row)}）。例: 1439,2150,メモ")
    return prices, warns


def _load() -> dict[str, TobEntry]:
    if not PATH.exists():
        return {}
    raw = json.loads(PATH.read_text(encoding="utf-8"))
    return {k: TobEntry(**v) for k, v in raw.items()}


def _save(entries: dict[str, TobEntry]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps({k: asdict(v) for k, v in sorted(entries.items())}, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def _add_event(e: TobEntry, d: Disclosure, label: str) -> None:
    if any(ev.get("id") == d.id for ev in e.events):
        return
    e.events.append({"id": d.id, "date": f"{d.pubdate:%Y-%m-%d}", "time": f"{d.pubdate:%H:%M}",
                     "title": d.title, "pdf": d.pdf_url, "label": label})
    e.events.sort(key=lambda x: (x["date"], x["time"]))


def update(disclosures: list[Disclosure], is_nagoya, name_of) -> dict[str, TobEntry]:
    """開示一覧からトラッカーを更新して保存する。

    is_nagoya(code, disclosure|None) -> bool, name_of(code) -> str
    """
    entries = _load()
    for d in disclosures:
        tags: list[Tag] = classify(d.title, d.code)
        for tg in tags:
            if tg.cat == S and is_nagoya(d.code, d):
                e = entries.setdefault(d.code, TobEntry(d.code, d.name, f"{d.pubdate:%Y-%m-%d}"))
                _add_event(e, d, tg.label)
            elif tg.cat == TOB_REL and tg.label.startswith("他社へのTOB"):
                for tc in target_codes(d.title):
                    if tc != d.code and is_nagoya(tc, None):
                        e = entries.setdefault(tc, TobEntry(tc, name_of(tc), f"{d.pubdate:%Y-%m-%d}"))
                        _add_event(e, d, f"買い手側の開示（{d.name}）")
            elif d.code in entries and (tg.cat in (TOB_REL, BAD)) and "公開買付" in norm(d.title):
                e = entries[d.code]
                _add_event(e, d, tg.label)
                if tg.label == "TOB結果":
                    e.status = "終了（結果発表済み）"
                elif tg.label.startswith("TOB不成立"):
                    e.status = "不成立・撤回"
    _save(entries)
    return entries
