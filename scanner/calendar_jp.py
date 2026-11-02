"""JPX（取引所）の営業日の判定。

休業日 = 土日・祝日・年末年始（12/31〜1/3）。
単体で実行すると GitHub Actions 用に run=true/false を出力する。
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone

try:  # 入っていれば使う。なくても下の予備の判定で動く（AIのルーチン環境など）
    import jpholiday
except ImportError:  # pragma: no cover
    jpholiday = None

from .config import MARKET_CLOSE, ORDER_DEADLINE

JST = timezone(timedelta(hours=9), "JST")
WEEKDAYS_JA = "月火水木金土日"


def now_jst() -> datetime:
    return datetime.now(JST)


def _nth_monday(y: int, m: int, n: int) -> date:
    first = date(y, m, 1)
    return first + timedelta(days=(7 - first.weekday()) % 7 + 7 * (n - 1))


def _base_holidays(y: int) -> set[date]:
    """祝日法の祝日（振替休日・国民の休日を除く）。2020年以降の法律に合わせている。"""
    eq = int((y - 1980) / 4)
    hs = {
        date(y, 1, 1), date(y, 2, 11), date(y, 4, 29), date(y, 5, 3), date(y, 5, 4), date(y, 5, 5),
        date(y, 11, 3), date(y, 11, 23),
        _nth_monday(y, 1, 2),  # 成人の日
        _nth_monday(y, 9, 3),  # 敬老の日
        date(y, 3, int(20.8431 + 0.242194 * (y - 1980)) - eq),  # 春分の日（1980〜2099年の近似式）
        date(y, 9, int(23.2488 + 0.242194 * (y - 1980)) - eq),  # 秋分の日
    }
    if y >= 2020:
        hs.add(date(y, 2, 23))  # 天皇誕生日
    special = {2020: (date(2020, 7, 23), date(2020, 7, 24), date(2020, 8, 10)),  # 東京五輪の特例
               2021: (date(2021, 7, 22), date(2021, 7, 23), date(2021, 8, 8))}
    if y in special:
        hs.update(special[y])  # 海の日・スポーツの日・山の日
    else:
        hs.update({_nth_monday(y, 7, 3), _nth_monday(y, 10, 2), date(y, 8, 11)})
    return hs


def _fallback_holiday(d: date) -> bool:
    """jpholiday が使えないときの祝日判定（振替休日・国民の休日を含む）。"""
    hs = _base_holidays(d.year) | _base_holidays(d.year - 1) | _base_holidays(d.year + 1)
    if d in hs:
        return True
    # 振替休日：日曜の祝日のあと、最初の祝日でない日
    p = d - timedelta(days=1)
    while p in hs:
        if p.weekday() == 6:
            return True
        p -= timedelta(days=1)
    # 国民の休日：前後の日が祝日にはさまれた平日
    return d.weekday() != 6 and (d - timedelta(days=1)) in hs and (d + timedelta(days=1)) in hs


def is_holiday(d: date) -> bool:
    if jpholiday is not None:
        return bool(jpholiday.is_holiday(d))
    return _fallback_holiday(d)


def is_business_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    if is_holiday(d):
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
    print(f"holiday_source={'jpholiday' if jpholiday is not None else 'builtin'}")


if __name__ == "__main__":
    main()
