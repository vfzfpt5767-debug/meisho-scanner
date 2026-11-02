"""予備の株価（名証の相場表が取れなかったときだけ使う）。

東証にも上場している銘柄だけ、yfinance で「コード.T」の終値を取る。
名証単独の銘柄は Yahoo にデータがないため取れない。
"""
from __future__ import annotations

from datetime import date, timedelta


def closes(codes: list[str], d: date) -> tuple[dict[str, float], str | None]:
    if not codes:
        return {}, None
    try:
        import yfinance as yf

        tickers = [f"{c}.T" for c in codes]
        df = yf.download(tickers, start=d - timedelta(days=7), end=d + timedelta(days=1),
                         progress=False, auto_adjust=False, group_by="ticker", threads=False)
        out: dict[str, float] = {}
        for c, t in zip(codes, tickers):
            try:
                s = df[t]["Close"] if len(tickers) > 1 else df["Close"]
                s = s.dropna()
                s = s[[ix.date() == d for ix in s.index]]
                if len(s):
                    out[c] = float(s.iloc[-1])
            except Exception:  # noqa: BLE001
                continue
        return out, None
    except Exception as e:  # noqa: BLE001
        return {}, f"Yahoo（予備の株価）の取得に失敗: {type(e).__name__}"
