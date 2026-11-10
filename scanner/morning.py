"""朝レポート：前夜の米国株・為替・日経平均先物と、名証上場の大型株のADRから当日の方向を推定する。

データは yfinance（Yahoo Finance）。
ADRの「何株分か」は銘柄ごとに違うので、過去の「ADR終値×ドル円÷日本の終値」の中央値で換算比率を推定し、
最新のADR価格から日本株の理論価格を出して、前日の名証の値段との差（%）を計算する。
"""
from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field

from .calendar_jp import now_jst
from .config import DATA_DIR

PATH = DATA_DIR / "morning.json"

INDICES = [
    ("^GSPC", "S&P500"), ("^IXIC", "ナスダック"), ("^SOX", "SOX指数（半導体）"), ("JPY=X", "ドル円"),
    ("NIY=F", "日経平均先物（CME・円建て）"), ("^N225", "日経平均"),
]
# ADR → 日本のコード（名証に上場しているものだけ表示する）
ADRS = {
    "TM": "7203", "MUFG": "8306", "SMFG": "8316", "NMR": "8604", "TAK": "4502", "CAJPY": "7751",
    "HTHIY": "6501", "FJTSY": "6702", "NPSCY": "5401", "SMTOY": "5802", "CJPRY": "9022", "DNZOY": "6902",
    "MKTAY": "6586", "SHECY": "4063", "MITSY": "8031", "ASEKY": "7259", "TOTDY": "5332", "IBIDF": "4062",
    "NGKSY": "5334", "BRTHY": "6448", "JSGRY": "5938", "JXHLY": "5020", "FELTY": "6504", "MHVYF": "7011",
}


@dataclass
class IndexRow:
    symbol: str
    label: str
    last: float | None
    change_pct: float | None
    date: str


@dataclass
class AdrRow:
    adr: str
    code: str
    name: str
    jp_price: float | None  # 前日の名証の値段
    implied: float | None  # ADRから計算した理論価格（円）
    gap_pct: float | None
    adr_change_pct: float | None
    traded: bool
    single: bool | None = None


@dataclass
class Morning:
    generated_at: str
    us_date: str
    indices: list[IndexRow]
    adrs: list[AdrRow]
    futures_gap_pct: float | None
    direction: str
    errors: list[str] = field(default_factory=list)
    stale_from: str = ""

    def summary_line(self) -> str:
        top = [a for a in self.adrs if a.gap_pct is not None and a.gap_pct >= 1.0 and a.traded]
        s = f"朝：{self.direction}"
        if top:
            s += f"。先回り候補1位 {top[0].name}（{top[0].code}）ADR理論価格 {top[0].gap_pct:+.1f}%"
        return s


def _nikkei_close_on(base):
    """基準日の日経平均の終値を、日経平均だけの取得で探す（まとめて取得した表に前日分が無い朝のための予備）。

    1) ^N225 だけの日足（日本時間の日付）に基準日の行があればその終値
    2) 無ければ、Yahoo の最新値（regularMarketPrice）の時刻が基準日なら、その値（引け後は終値と同じ）
    見つからなければ None。
    """
    try:
        from datetime import datetime, timedelta, timezone

        import yfinance as yf

        t = yf.Ticker("^N225")
        h = t.history(period="5d", interval="1d", auto_adjust=False)
        s = h["Close"].dropna() if h is not None and len(h) else None
        if s is not None:
            s = s[[d.date() == base for d in s.index]]
            if len(s):
                return float(s.iloc[-1])
        meta = getattr(t, "history_metadata", None) or {}
        ts, px = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
        if ts is not None and px:
            jst = timezone(timedelta(hours=9))
            d = ts.astimezone(jst) if isinstance(ts, datetime) else datetime.fromtimestamp(int(ts), jst)
            if d.date() == base:
                return float(px)
    except Exception:  # noqa: BLE001
        pass
    return None


def _series(df, sym):
    try:
        s = df[sym]["Close"].dropna()
        return s
    except Exception:  # noqa: BLE001
        return None


def build(res) -> Morning:
    ctx = res.ctx
    now = now_jst()
    errors: list[str] = []
    try:
        import yfinance as yf

        syms = [s for s, _ in INDICES] + list(ADRS) + [f"{c}.T" for c in ADRS.values()]
        df = yf.download(syms, period="2mo", interval="1d", progress=False, group_by="ticker",
                         threads=False, auto_adjust=False)
    except Exception as e:  # noqa: BLE001
        df = None
        errors.append(f"海外指標（Yahoo）の取得に失敗: {type(e).__name__}")
    if df is None or len(df) == 0:
        prev = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else None
        if prev:
            m = _from_dict(prev)
            m.errors = errors + ["海外指標が取れなかったため、前回の朝レポートを表示しています"]
            m.stale_from = prev.get("generated_at", "")
            return m
        return Morning(now.isoformat(), "", [], [], None, "海外指標を取得できませんでした", errors)

    idx_rows = []
    spx = _series(df, "^GSPC")
    us_date = spx.index[-1].date() if spx is not None and len(spx) else None
    for sym, label in INDICES:
        s = _series(df, sym)
        if s is None or len(s) < 2:
            idx_rows.append(IndexRow(sym, label, None, None, ""))
            errors.append(f"{label} のデータがありません")
            continue
        idx_rows.append(IndexRow(sym, label, float(s.iloc[-1]), float((s.iloc[-1] / s.iloc[-2] - 1) * 100),
                                 f"{s.index[-1].date()}"))

    # 日経平均先物と日経平均（基準日の終値）の差
    fut, nk = _series(df, "NIY=F"), _series(df, "^N225")
    futures_gap = None
    if fut is not None and len(fut):
        nk_close = None
        if nk is not None and len(nk):
            nk_base = nk[[d.date() <= ctx.base for d in nk.index]]
            if len(nk_base) and nk_base.index[-1].date() == ctx.base:
                nk_close = float(nk_base.iloc[-1])
        if nk_close is None:  # まとめて取得した表に前日分が無い朝（11/5・11/6・11/10に発生）は、日経平均だけで取り直す
            nk_close = _nikkei_close_on(ctx.base)
        if nk_close:
            futures_gap = float((fut.iloc[-1] / nk_close - 1) * 100)
        elif len(fut) >= 2:
            futures_gap = float((fut.iloc[-1] / fut.iloc[-2] - 1) * 100)
            errors.append("日経平均の基準日の終値がYahooに無いため、先物の前日比で代用")
    if futures_gap is None:
        direction = "方向は判断できません（先物データなし）"
    elif futures_gap >= 0.3:
        direction = f"日経平均先物は前日の日経平均より{futures_gap:+.1f}% → 上昇して始まりそう"
    elif futures_gap <= -0.3:
        direction = f"日経平均先物は前日の日経平均より{futures_gap:+.1f}% → 下落して始まりそう"
    else:
        direction = f"日経平均先物は前日の日経平均より{futures_gap:+.1f}% → ほぼ横ばい"

    fx = _series(df, "JPY=X")
    adr_rows = []
    for adr, code in ADRS.items():
        ls = ctx.universe.get(code)
        if ls is None:
            continue  # 名証に上場していない
        a, jp = _series(df, adr), _series(df, f"{code}.T")
        q = ctx.quotes.get(code)
        jp_price = q.contest_price if q else (float(jp.iloc[-1]) if jp is not None and len(jp) else None)
        row = AdrRow(adr, code, ls.name, jp_price, None, None, None, False, ls.single)
        if a is None or len(a) < 5 or fx is None or jp is None or len(jp) < 5:
            adr_rows.append(row)
            continue
        row.traded = us_date is not None and a.index[-1].date() == us_date and float(a.iloc[-1]) != float(a.iloc[-2])
        row.adr_change_pct = float((a.iloc[-1] / a.iloc[-2] - 1) * 100)
        ratios = []
        for d in a.index[-25:-1]:
            dj = [x for x in jp.index if x.date() == d.date()]
            df_ = [x for x in fx.index if x.date() == d.date()]
            if dj and df_:
                ratios.append(float(a[d]) * float(fx[df_[0]]) / float(jp[dj[0]]))
        if len(ratios) >= 5 and jp_price:
            ratio = statistics.median(ratios)
            row.implied = float(a.iloc[-1]) * float(fx.iloc[-1]) / ratio
            row.gap_pct = (row.implied / jp_price - 1) * 100
        adr_rows.append(row)
    adr_rows.sort(key=lambda r: (r.gap_pct is None or not r.traded, -(r.gap_pct or 0)))
    m = Morning(now.isoformat(), f"{us_date}" if us_date else "", idx_rows, adr_rows, futures_gap, direction, errors)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(asdict(m), ensure_ascii=False, indent=1), encoding="utf-8")
    return m


def _from_dict(d: dict) -> Morning:
    d = dict(d)
    d["indices"] = [IndexRow(**x) for x in d.get("indices", [])]
    d["adrs"] = [AdrRow(**x) for x in d.get("adrs", [])]
    return Morning(**d)


def line_text(m: Morning, deadline) -> str:
    from .calendar_jp import fmt_date

    out = [f"【名証 朝レポート】締切 {fmt_date(deadline.date())} 7:59", m.direction]
    for r in m.indices:
        if r.change_pct is not None and r.symbol != "^N225":
            out.append(f"{r.label} {r.change_pct:+.1f}%")
    top = [a for a in m.adrs if a.traded and a.gap_pct is not None][:3]
    if top:
        out.append("ADRから見た先回り候補（前日終値で買える）:")
        out += [f"・{a.name}({a.code}) {a.gap_pct:+.1f}%" for a in top]
    from .notify import page_url

    out.append(page_url())
    return "\n".join(out)


def is_morning(now) -> bool:
    """朝レポートを作る時間（6:00〜7:59。実行予定は6:20と7:05）。

    米国株の取引は日本時間6:00（夏は5:00）に終わるので、それより前（遅れて動いた夜中の起動など）は作らない。
    """
    return 6 <= now.hour < 8

