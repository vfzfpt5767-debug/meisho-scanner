"""検証用：開示のあった銘柄と指数の日足（Yahoo Finance）を取得して data/bt/prices.pkl に保存する。
50銘柄ずつ、間隔をあけて取得する。
"""
from __future__ import annotations

import json
import pickle
import sys
import time

from .fetch import BT, OUT

EXTRA = ["^N225", "NIY=F", "NKD=F"]
LARGE = ["7203", "8306", "8316", "8604", "4502", "7751", "6501", "6702", "5401", "5802", "9022", "6902",
         "6586", "4063", "8031", "7259", "5332", "4062", "5334", "6448", "5938", "5020", "6504", "7011"]


def codes() -> list[str]:
    out = set(LARGE)
    for line in OUT.open(encoding="utf-8"):
        c = (json.loads(line).get("code") or "").strip()
        if len(c) == 5 and c.endswith("0"):
            out.add(c[:4])
    return sorted(out)


def main(start: str, end: str) -> None:
    import pandas as pd
    import yfinance as yf

    path = BT / "prices.pkl"
    store = pickle.loads(path.read_bytes()) if path.exists() else {}
    tickers = [f"{c}.T" for c in codes()] + EXTRA
    todo = [t for t in tickers if t not in store]
    print("tickers", len(tickers), "todo", len(todo), flush=True)
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        for attempt in range(3):
            try:
                df = yf.download(chunk, start=start, end=end, interval="1d", progress=False, group_by="ticker",
                                 threads=False, auto_adjust=False)
                break
            except Exception as e:  # noqa: BLE001
                print("retry", e, flush=True)
                time.sleep(20)
        else:
            continue
        for t in chunk:
            try:
                sub = df[t][["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
            except Exception:  # noqa: BLE001
                sub = pd.DataFrame()
            store[t] = sub
        path.write_bytes(pickle.dumps(store))
        print(i + len(chunk), "/", len(todo), flush=True)
        time.sleep(4)
    print("done", sum(1 for v in store.values() if len(v)), "with data", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
