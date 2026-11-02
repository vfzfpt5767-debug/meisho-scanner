"""検証用：過去の適時開示一覧（やのしんWEB-API、全上場銘柄）を1日ずつ取得して、
15:30以降の開示（と休業日の開示）のうち分類対象のものだけを data/bt/disclosures.jsonl に保存する。
3秒間隔でアクセスし、取得済みの日は飛ばす（途中で止めても続きから再開できる）。
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta

from .. import http
from ..classify import classify
from ..config import DATA_DIR

BT = DATA_DIR / "bt"
OUT = BT / "disclosures.jsonl"
DONE = BT / "fetched_days.txt"
API = "https://webapi.yanoshin.jp/webapi/tdnet/list/{d}.json"


def main(start: str, end: str) -> None:
    BT.mkdir(parents=True, exist_ok=True)
    done = set(DONE.read_text().split()) if DONE.exists() else set()
    d, e = date.fromisoformat(start), date.fromisoformat(end)
    n_days = n_keep = 0
    with OUT.open("a", encoding="utf-8") as f, DONE.open("a") as fd:
        while d <= e:
            key = f"{d:%Y%m%d}"
            if key in done or d.weekday() >= 5:  # 土日は開示がほぼないので取得しない（アクセス削減）
                d += timedelta(days=1)
                continue
            try:
                data = http.get(API.format(d=key), params={"limit": 5000}).json()
            except Exception as ex:  # noqa: BLE001
                print(key, "ERR", http.short_err(ex), flush=True)
                d += timedelta(days=1)
                continue
            for raw in data.get("items", []):
                t = raw.get("Tdnet", raw)
                pub = datetime.strptime(t["pubdate"], "%Y-%m-%d %H:%M:%S")
                weekday_close = pub.weekday() < 5 and pub.hour * 60 + pub.minute < 15 * 60 + 30
                tags = classify(t.get("title") or "", (t.get("company_code") or "")[:4])
                cats = {x.cat for x in tags}
                if weekday_close and "S" not in cats:
                    continue  # 場中の開示は対象外（休日・祝日の判定はあとで行う）
                if cats == {"INFO"} and not any(x.label == "決算短信" for x in tags):
                    continue
                f.write(json.dumps({"pub": t["pubdate"], "code": t.get("company_code"), "name": t.get("company_name"),
                                    "title": t.get("title"), "markets": t.get("markets_string"),
                                    "tags": [[x.cat, x.label] for x in tags]}, ensure_ascii=False) + "\n")
                n_keep += 1
            fd.write(key + "\n")
            fd.flush()
            f.flush()
            n_days += 1
            if n_days % 20 == 0:
                print(key, "days", n_days, "kept", n_keep, flush=True)
            d += timedelta(days=1)
    print("done days", n_days, "kept", n_keep, flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
