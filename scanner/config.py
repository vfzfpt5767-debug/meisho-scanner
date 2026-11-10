"""設定値（コンテストのルールなど）をまとめた場所。"""
from __future__ import annotations

import os
from datetime import date, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
DOCS_DIR = ROOT / "docs"
TOB_PRICES_CSV = ROOT / "tob_prices.csv"

# コンテストのルール
CONTEST_START = date(2026, 10, 22)
CONTEST_END = date(2026, 11, 30)
BUDGET_PER_TERM = 3_000_000  # 1タームに買える上限（円）
MARKET_CLOSE = time(15, 30)  # 大引け
ORDER_DEADLINE = time(7, 59)  # 翌営業日の朝の注文締切

# 夜に注文して寝る運用（2026-11-03 ユーザー指定）
AI_NIGHT_RUN = time(21, 15)  # AIアナリストの定時起動（毎日・祝日も）
RECORD_FROM = time(16, 30)  # この時刻〜RECORD_UNTIL に届いたAIの注文だけ保有に自動記録する
RECORD_UNTIL = time(23, 30)  # これより後の指示は「参考（起きていれば）」として送るだけ
AI_WATCH_AT = time(22, 30)  # この時刻までに夜の判断が届いていなければLINEで知らせる

# LINEは「売る・買うの注文があるときだけ、1通にまとめて」送る（2026-11-10 ユーザー指定：
# 起動と通知が多い。作業が必要な売り買いの判断だけ。「〜が上がった」などの報告はいらない）
LINE_REPORTS = False  # 夜の候補の通知・追加の通知・朝レポートをLINEで送るか（ページには今までどおり出る）
LINE_ASK_HUMAN = False  # 「確認のお願い」をLINEで送るか
LINE_NO_ORDER = False  # 注文のない（見送りの）AIの結論もLINEで送るか
# AIの臨時起動：21:15の定時の判断が届いたあと、TOB（S）と保有銘柄の警告（W）が新しく出たときだけ、1晩1回まで
FIRE_AFTER_MAIN_RUN = True
LATE_FIRE_KINDS = ("S", "W")

# 要確認の中で一番上に出すラベル（検証で上がった割合が高いもの。2026-11-03 ユーザー指定）
PRIORITY_CHECK_LABELS = ("配当予想の修正", "株主還元方針の変更")

# TOB価格との差がこの%以下なら「売り時」と表示する
TOB_SELL_GAP_PCT = 1.0

# GitHubのリポジトリ名（tob_prices.csv の編集リンクに使う）
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "vfzfpt5767-debug/meisho-scanner")
GITHUB_BRANCH = os.environ.get("SCANNER_BRANCH", "main")

USER_AGENT = (
    "meisho-scanner/1.0 (personal tool for a student stock contest; "
    "low-frequency; https://github.com/" + GITHUB_REPOSITORY + ")"
)
