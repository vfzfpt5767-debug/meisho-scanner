"""会社のIRページを直接見て、同じ開示のPDFや本文を確かめる（TDnet は開かない）。

- 一覧：data/ir_pages.json（名証上場の会社ごとに、トップページ・IRニュースのページ・robots.txt の可否）
    python -m scanner.ir build [--limit N] [--codes 1869,6999]   # 一覧を作る／更新する（各社1〜4ページだけ、間隔をあけて見る）
  会社のトップページは ①data/ir_sources.csv（手で書いた分。優先）②名証の上場会社検索の「会社ホームページ」から。
  各社の robots.txt で許可されているページだけを開く（許可されていなければ「robots.txt で不可」と記録して開かない）。
- 確認：スキャンのたびに、S・A・要確認の名証上場銘柄（最大8件）について、IRニュースのページから
  同じ開示（タイトルが似ているもの）を探し、PDFか本文の先頭を data/analyst_input.json の candidates[].ir_check に入れる。
    python -m scanner.ir lookup 1869 "自己株式取得に係る事項の決定に関するお知らせ"   # 手で試す
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
import unicodedata
from datetime import datetime
from html import unescape
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from . import http
from .config import CACHE_DIR, DATA_DIR, ROOT, USER_AGENT

PAGES = DATA_DIR / "ir_pages.json"
SOURCES = ROOT / "data" / "ir_sources.csv"
CACHE = CACHE_DIR / "ir"
MAX_LOOKUPS = 8
RETRY_MIN = 40  # 見つからなかったとき、次に見に行くまでの分
UA_NAME = "meisho-scanner"

IR_WORDS = ("IR", "ＩＲ", "投資家", "株主", "Investor", "investor")
NEWS_WORDS = ("IRニュース", "ＩＲニュース", "IR情報", "IRライブラリ", "適時開示", "IRニュースリリース", "ニュースリリース",
              "IRお知らせ", "お知らせ", "ニュース", "News", "news", "決算短信", "IR資料", "プレスリリース")
DATE_RE = re.compile(r"(20\d{2})\s*[年/.\-]\s*(\d{1,2})\s*[月/.\-]\s*(\d{1,2})")

_robots: dict[str, RobotFileParser | None] = {}


# ---------------- robots.txt ----------------

class Unreachable(Exception):
    """サイトにつながらない・robots.txt が一時的に読めない（あとでやり直す）。"""


class Disallowed(Exception):
    """robots.txt で自動取得が許可されていない（開かない）。"""


def robots_state(url: str) -> str:
    """allow / disallow / unreachable。robots.txt がない（4xx）は許可、5xx・接続できないは unreachable（開かない）。"""
    p = urlparse(url)
    host = f"{p.scheme}://{p.netloc}"
    if host not in _robots:
        rp: RobotFileParser | None = RobotFileParser()
        try:
            r = http._session.get(host + "/robots.txt", timeout=20, allow_redirects=True)
            if r.status_code >= 500 or r.status_code == 429:
                rp = None
            elif r.status_code >= 400:
                rp.parse([])  # robots.txt がない＝制限なし
            else:
                rp.parse(_decode(r).splitlines())
        except Exception:  # noqa: BLE001
            rp = None
        _robots[host] = rp
    rp = _robots[host]
    if rp is None:
        return "unreachable"
    return "allow" if rp.can_fetch(UA_NAME, url) and rp.can_fetch(USER_AGENT, url) else "disallow"


def robots_ok(url: str) -> bool:
    return robots_state(url) == "allow"


def fetch(url: str):
    """robots.txt で許可されていれば取得する（同じサイトには間隔をあける）。"""
    st = robots_state(url)
    if st == "disallow":
        raise Disallowed(url)
    if st == "unreachable":
        raise Unreachable(url)
    try:
        return http.get(url, timeout=30, retries=1)
    except Exception as e:  # noqa: BLE001
        raise Unreachable(f"{url} {type(e).__name__}") from e


# ---------------- ページの読み取り ----------------

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[\s　「」『』（）()【】\[\]・、。,:：]", "", s)


def links(html: str, base: str) -> list[dict]:
    """ページのリンク（URL・文字・近くの日付）。"""
    out = []
    for m in re.finditer(r"<a\b[^>]*href\s*=\s*[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", html, flags=re.S | re.I):
        href, inner = m[1].strip(), m[2]
        text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", inner))).strip()
        if href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        before = re.sub(r"<[^>]+>", " ", html[max(0, m.start() - 300):m.start()])
        dates = DATE_RE.findall(before + " " + text)
        d = ""
        if dates:
            y, mo, da = dates[-1]
            d = f"{int(y):04d}-{int(mo):02d}-{int(da):02d}"
        out.append({"url": urljoin(base, href), "text": text, "date": d})
    return out


def _decode(r) -> str:
    raw = r.content
    for enc in (r.encoding if r.encoding and r.encoding.lower() not in ("iso-8859-1",) else None, "utf-8", "cp932", "euc-jp"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _same_site(a: str, b: str) -> bool:
    ha, hb = urlparse(a).netloc.split(":")[0], urlparse(b).netloc.split(":")[0]
    root = lambda h: ".".join(h.split(".")[-3:]) if h.endswith((".co.jp", ".or.jp", ".ne.jp")) else ".".join(h.split(".")[-2:])  # noqa: E731
    return root(ha) == root(hb)


def find_ir_page(home: str) -> dict:
    """トップページ → IRのページ → IRニュースのページ を探す。"""
    try:
        r = fetch(home)
    except Disallowed:
        return {"status": "robots_disallow", "home": home}
    except Unreachable:
        return {"status": "unreachable", "home": home}
    html = _decode(r)
    cands = []
    for ln in links(html, r.url):
        t, u = ln["text"], ln["url"]
        score = sum(2 for w in IR_WORDS if w in t) + (2 if re.search(r"/ir(/|$|\.|_)", u.lower()) else 0)
        if score and _same_site(u, r.url) or score and "ir" in urlparse(u).netloc.split(".")[0]:
            cands.append((score, u))
    if not cands:
        return {"status": "no_ir_link", "home": home}
    ir_top = sorted(cands, key=lambda x: -x[0])[0][1]
    try:
        r2 = fetch(ir_top)
    except Disallowed:
        return {"status": "robots_disallow", "home": home, "ir_top": ir_top}
    except Unreachable:
        return {"status": "unreachable", "home": home, "ir_top": ir_top}
    html2 = _decode(r2)
    best = (_page_score(html2), r2.url)
    news = []
    for ln in links(html2, r2.url):
        if any(w in ln["text"] for w in NEWS_WORDS) or re.search(r"(news|release|library|disclosure|tdnet)", ln["url"].lower()):
            news.append(ln["url"])
    for u in list(dict.fromkeys(news))[:2]:
        try:
            r3 = fetch(u)
        except (Disallowed, Unreachable):
            continue
        sc = _page_score(_decode(r3))
        if sc > best[0]:
            best = (sc, r3.url)
    return {"status": "ok", "home": home, "ir_top": r2.url, "ir_news": best[1], "score": best[0]}


def _page_score(html: str) -> int:
    """IRニュースのページらしさ（日付つきのPDF・開示らしいリンクの数）。"""
    n = 0
    for ln in links(html, "https://x/"):
        if ln["date"] and (ln["url"].lower().endswith(".pdf") or any(w in ln["text"] for w in ("お知らせ", "決算", "開示"))):
            n += 1
    return n


# ---------------- 一覧を作る ----------------

def _nse_home(code: str) -> str:
    """名証の上場会社検索（基本情報）の「会社ホームページ」（stockUrl_j）。"""
    c5 = code + "0" if len(code) == 4 else code
    j = http.get("https://www.nse.or.jp/api/stock/view.json", params={"stockCode": c5}).json()
    st = (j.get("stock") or [{}])[0]
    return (st.get("stockUrl_j") or "").strip()


def _manual_sources() -> dict[str, dict]:
    out = {}
    if SOURCES.exists():
        for row in csv.DictReader(io.StringIO(SOURCES.read_text(encoding="utf-8-sig"))):
            c = (row.get("code") or "").strip().upper()
            if c and not c.startswith("#"):
                out[c] = {"home": (row.get("home") or "").strip(), "ir_news": (row.get("ir_news") or "").strip()}
    return out


def build(limit: int | None = None, only: list[str] | None = None) -> dict:
    from .sources import nse_list

    universe, _ = nse_list.load(refresh=False)
    codes = sorted(c for c, ls in universe.items() if ls.division != "その他(ETF等)")
    if only:
        codes = [c for c in codes if c in only]
    pages = json.loads(PAGES.read_text(encoding="utf-8")) if PAGES.exists() else {}
    manual = _manual_sources()
    todo = []
    for c in codes:
        if limit is not None and len(todo) >= limit:
            break
        m = manual.get(c, {})
        old = pages.get(c, {})
        if old.get("status") in ("ok", "robots_disallow", "no_ir_link") and not m.get("ir_news") and not m.get("home"):
            continue  # unreachable・error・no_home は毎回やり直す
        todo.append(c)
    # 1) トップページ（名証の基本情報。名証のサイトには3秒おき）
    homes = {}
    for i, c in enumerate(todo, 1):
        m = manual.get(c, {})
        homes[c] = m.get("home") or pages.get(c, {}).get("home") or ""
        if not homes[c]:
            try:
                homes[c] = _nse_home(c)
            except Exception as e:  # noqa: BLE001
                print(c, "名証の基本情報を読めませんでした:", type(e).__name__)
        if i % 25 == 0:
            print("トップページ", i, "/", len(todo), flush=True)

    # 2) 各社のIRページ（会社ごとに別のサイトなので6社ずつ並べて見る。同じサイトへは3秒おき）
    from concurrent.futures import ThreadPoolExecutor

    def one(c: str) -> tuple[str, dict]:
        m = manual.get(c, {})
        home = homes.get(c, "")
        ent = {"name": universe[c].name, "single": universe[c].single,
               "checked": datetime.now().isoformat(timespec="minutes")}
        if m.get("ir_news"):
            ent.update(status={"allow": "ok", "disallow": "robots_disallow"}.get(robots_state(m["ir_news"]), "unreachable"),
                       home=home, ir_news=m["ir_news"], method="手入力")
        elif not home:
            ent.update(status="no_home", method="")
        else:
            try:
                ent.update(find_ir_page(home), method="名証の会社ホームページ→IRページ")
            except Exception as e:  # noqa: BLE001
                ent.update(status="error", home=home, error=f"{type(e).__name__}")
        return c, ent

    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, (c, ent) in enumerate(ex.map(one, todo), 1):
            pages[c] = ent
            if i % 25 == 0:
                PAGES.write_text(json.dumps(pages, ensure_ascii=False, indent=1), encoding="utf-8")
                print("IRページ", i, "/", len(todo), flush=True)
    PAGES.write_text(json.dumps(pages, ensure_ascii=False, indent=1), encoding="utf-8")
    return pages


# ---------------- 同じ開示を探す ----------------

def _bigrams(s: str) -> set[str]:
    s = _norm(s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def similarity(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def _pdf_text(data: bytes, max_chars: int = 3500) -> str:
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages[:3])
        return re.sub(r"[ \t]+", " ", text).strip()[:max_chars]
    except Exception as e:  # noqa: BLE001
        return f"（PDFの文字を取り出せませんでした：{type(e).__name__}）"


def lookup(code: str, title: str, date_hint: str = "") -> dict:
    """IRニュースのページから同じ開示を探す。"""
    pages = json.loads(PAGES.read_text(encoding="utf-8")) if PAGES.exists() else {}
    ent = pages.get(code)
    if not ent or ent.get("status") != "ok" or not ent.get("ir_news"):
        return {"status": "no_ir_page", "note": (ent or {}).get("status", "一覧にない")}
    page = ent["ir_news"]
    try:
        r = fetch(page)
    except Disallowed:
        return {"status": "robots_disallow", "ir_page": page}
    except Unreachable:
        return {"status": "unreachable", "ir_page": page}
    best, best_sc = None, 0.0
    for ln in links(_decode(r), r.url):
        sc = similarity(title, ln["text"])
        if date_hint and ln["date"] == date_hint:
            sc += 0.15
        if sc > best_sc:
            best, best_sc = ln, sc
    recent = [{"text": ln["text"][:60], "url": ln["url"], "date": ln["date"]}
              for ln in links(_decode(r), r.url) if ln["date"]][:5]
    if not best or best_sc < 0.45:
        return {"status": "not_found", "ir_page": page, "recent": recent}
    out = {"status": "found", "ir_page": page, "url": best["url"], "matched_title": best["text"][:100],
           "similarity": round(best_sc, 2), "date": best["date"]}
    try:
        r2 = fetch(best["url"])
    except (Disallowed, Unreachable) as e:
        out["excerpt"] = ("（このリンク先は robots.txt で自動取得できません。URLを開いて確認してください）"
                          if isinstance(e, Disallowed) else "（リンク先につながりませんでした）")
        return out
    if r2.content[:4] == b"%PDF" or best["url"].lower().endswith(".pdf"):
        out["excerpt"] = _pdf_text(r2.content)
        out["kind"] = "pdf"
    else:
        html = _decode(r2)
        pdfs = [ln["url"] for ln in links(html, r2.url) if ln["url"].lower().endswith(".pdf")]
        text = re.sub(r"\s+", " ", unescape(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.S)))
        out["excerpt"] = text.strip()[:3500]
        out["kind"] = "html"
        if pdfs:
            try:
                r3 = fetch(pdfs[0])
            except (Disallowed, Unreachable):
                r3 = None
            if r3 is not None and r3.content[:4] == b"%PDF":
                out["excerpt"] = _pdf_text(r3.content)
                out["url"], out["kind"] = pdfs[0], "pdf"
    return out


def attach_ir_checks(data: dict) -> None:
    """入力の候補（名証上場・S/A/要確認）に ir_check を付ける。結果は日ごとにキャッシュして、何度も見に行かない。"""
    if not PAGES.exists():
        return
    stuck = {h["code"] for h in data.get("stop_limits", []) if h.get("kind") == "stop_high"}
    cands = sorted(data.get("candidates", []), key=lambda c: (c.get("category") != "S", c.get("code") not in stuck,
                                                              not c.get("single"), -(c.get("score") or 0)))
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{data.get('base_day', 'x')}.json"
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    now = datetime.now()
    for c in cands[:MAX_LOOKUPS]:
        items = c.get("items", [])
        it = next((i for i in items if any(lb.get("cat") in ("S", "A", "CHECK") for lb in i.get("labels", []))),
                  items[0] if items else None)
        if not it:
            continue
        key = f"{c['code']}|{it['title']}"
        hit = cache.get(key)
        if hit and (hit.get("status") == "found" or hit.get("status") in ("no_ir_page", "robots_disallow") or
                    (now - datetime.fromisoformat(hit["checked"])).total_seconds() < RETRY_MIN * 60):
            c["ir_check"] = hit
            continue
        md = re.match(r"(\d{2})/(\d{2})", it.get("time", ""))
        date_hint = f"{data.get('base_day', '')[:4]}-{md[1]}-{md[2]}" if md else ""
        try:
            res = lookup(c["code"], it["title"], date_hint)
        except Exception as e:  # noqa: BLE001
            res = {"status": "error", "note": type(e).__name__}
        res["checked"] = now.isoformat(timespec="minutes")
        res["for_title"] = it["title"]
        cache[key] = res
        c["ir_check"] = res
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build":
        args = sys.argv[2:]
        lim = int(args[args.index("--limit") + 1]) if "--limit" in args else None
        only = args[args.index("--codes") + 1].split(",") if "--codes" in args else None
        pages = build(lim, only)
        from collections import Counter

        print(Counter(v.get("status") for v in pages.values()))
        return 0
    if cmd == "lookup" and len(sys.argv) >= 4:
        print(json.dumps(lookup(sys.argv[2], sys.argv[3]), ensure_ascii=False, indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    t0 = time.time()
    raise SystemExit(main())
