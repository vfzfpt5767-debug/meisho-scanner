"""JPX（取引所）の営業日の判定。

休業日 = 土日・祝日・年末年始（12/31〜1/3）。
単体で実行すると GitHub Actions 用に run=true/false を出力する。
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone

import jpholiday

from .config import MARKET_CLOSE, ORDER_DEADLINE

JST = timezone(timedelta(hours=9), "JST")
WEEKDAYS_JA = "月火水木金土日"


def now_jst() -> datetime:
    return datetime.now(JST)


def is_business_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    if jpholiday.is_holiday(d):
        return False
    if (d.month, d.day) in {(12, 31), (1, 1), (1, 2), (1, 3)}:
        return False
    return True


def prev_business_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def next_business_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def base_day(now: datetime) -> date:
    """今注文すると、どの日の終値で約定するか（＝直近で大引けを迎えた営業日）。"""
    today = now.date()
    if is_business_day(today) and now.time() >= MARKET_CLOSE:
        return today
    return prev_business_day(today)


def order_deadline(base: date) -> datetime:
    """そのタームの注文締切（翌営業日の朝7:59）。"""
    nd = next_business_day(base)
    return datetime.combine(nd, ORDER_DEADLINE, tzinfo=JST)


def fmt_date(d: date) -> str:
    return f"{d.month}/{d.day}({WEEKDAYS_JA[d.weekday()]})"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--event", default="", help="GitHub のイベント名（schedule / workflow_dispatch）")
    a = p.parse_args()
    today = now_jst().date()
    biz = is_business_day(today)
    run = biz or a.event != "schedule"
    print(f"business_day={'true' if biz else 'false'}")
    print(f"run={'true' if run else 'false'}")


if __name__ == "__main__":
    main()
