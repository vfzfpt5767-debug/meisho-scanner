"""開示タイトルから材料を分類して点数をつける。

TDnetのPDF/XBRLは自動取得しない方針（robots.txtの拒否のため）なので、
判定はタイトルの文言だけで行う。タイトルで判断できないものは「要確認」にする。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 区分（表示順）
S = "S"  # TOB・MBOの対象
A = "A"  # 好材料
CHECK = "CHECK"  # 要確認（タイトルだけでは良し悪しが分からない）
INFO = "INFO"  # 参考
BAD = "BAD"  # 悪材料
SELF_TOB = "SELF_TOB"  # 自社株TOB（値上がりしにくい）
TOB_REL = "TOB_REL"  # TOB関連の続報・買い手側など

LABELS = {
    S: "S：TOB・MBOの対象",
    A: "A：好材料",
    CHECK: "要確認",
    INFO: "参考",
    BAD: "悪材料",
    SELF_TOB: "自社株TOB（別扱い）",
    TOB_REL: "TOB関連の続報",
}


@dataclass
class Tag:
    cat: str
    label: str
    score: int
    reason: str


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", "", s)


def _has(t: str, *words: str) -> bool:
    return any(w in t for w in words)


CODE_IN_TITLE = re.compile(r"証券コード[:：]?\s*([0-9][0-9A-Z]{3})")


def target_codes(title: str) -> list[str]:
    """タイトル中の「（証券コード：1234）」を取り出す。"""
    return CODE_IN_TITLE.findall(norm(title))


def _load_weights() -> dict[str, int]:
    """検証結果（scanner/weights.json）で決めた点数。件数30以上のラベルだけ入っている。"""
    import json
    from pathlib import Path

    p = Path(__file__).with_name("weights.json")
    try:
        return {k: int(v) for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    except Exception:  # noqa: BLE001
        return {}


WEIGHTS = _load_weights()


def classify(title: str, own_code: str = "") -> list[Tag]:
    """1件の開示タイトルを分類する。複数の材料を含むことがあるのでリストで返す。"""
    tags = _classify(title, own_code)
    for t in tags:
        if t.label in WEIGHTS and t.cat in (A, CHECK):
            t.score = WEIGHTS[t.label]
    return tags


def _classify(title: str, own_code: str = "") -> list[Tag]:
    t = norm(title)
    tags: list[Tag] = []
    is_followup = (
        t.startswith(("(訂正", "(変更)", "(経過開示)", "(開示事項の経過)", "(開示事項の変更)", "(追加)"))
        or _has(t, "の一部訂正", "の訂正について", "の訂正に関する")
        or re.search(r"」(の|に係る|に関する)?(一部)?(訂正|変更)", t) is not None
        or _has(t, "資金使途")
    )

    # ---- TOB・MBO ----
    if _has(t, "公開買付", "MBO", "マネジメント・バイアウト"):
        codes = target_codes(t)
        own_target = bool(own_code) and own_code in codes
        self_side = (
            _has(t, "当社株券", "当社株式", "当社の株券", "当社の株式", "当社普通株式", "当社の普通株式")
            or own_target
            or (_has(t, "MBO", "マネジメント・バイアウト", "応募推奨", "応募の推奨", "賛同の意見") and not codes)
        )
        if _has(t, "自己株式の公開買付けへの応募", "自己株式の公開買付への応募"):
            tags.append(Tag(TOB_REL, "他社の自社株TOBへの応募", 0, "保有している他社株を、その会社の自社株TOBに売る開示"))
        elif _has(t, "自己株式の公開買付", "自己株式の取得及び自己株式の公開買付"):
            tags.append(Tag(SELF_TOB, "自社株TOB", 0, "会社が自分の株を買い戻すTOB。値上がり材料になりにくい"))
        elif _has(t, "準ずる行為"):
            tags.append(Tag(INFO, "買集め", 10, "大株主による株の買い集め（TOBそのものではない）"))
        elif _has(t, "不成立", "撤回"):
            tags.append(Tag(BAD, "TOB不成立・撤回", -80, "TOBが成立しなかった/撤回された。株価が下がりやすい"))
        elif _has(t, "結果"):
            tags.append(Tag(TOB_REL, "TOB結果", 0, "TOBの結果発表（これ以上は上がりにくい）"))
        elif _has(t, "買付価格の引上げ", "買付け価格の引上げ", "価格の引上げ", "買付条件等の変更") and self_side:
            tags.append(Tag(S, "TOB条件変更", 95, "TOB条件の変更（価格引上げならさらに上昇余地。PDFで確認）"))
        elif is_followup:
            tags.append(Tag(TOB_REL, "TOB関連の訂正・変更", 0, "以前のTOB開示の訂正・変更"))
        elif self_side and _has(t, "応募"):
            # 「当社株式に対する公開買付けへの応募」は保有株をTOBに売るほう
            if _has(t, "意見表明", "賛同", "応募推奨", "応募を推奨", "応募の推奨", "MBO", "マネジメント・バイアウト"):
                tags.append(Tag(S, "TOB対象（意見表明）", 100, _tob_reason(t)))
            else:
                tags.append(Tag(TOB_REL, "TOBへの応募", 0, "保有株をTOBに応募する開示"))
        elif self_side:
            tags.append(Tag(S, "TOB対象", 100, _tob_reason(t)))
        elif codes:
            tags.append(Tag(TOB_REL, "他社へのTOB（買い手側）", 0, f"証券コード{','.join(codes)}の会社へのTOB（買う側の開示）"))
        else:
            tags.append(Tag(TOB_REL, "TOB関連", 0, "TOBに関する開示（対象は自社ではない可能性）"))
        return tags

    if _has(t, "株式併合", "株式等売渡請求", "株式売渡請求", "スクイーズアウト"):
        tags.append(Tag(TOB_REL, "非公開化の手続", 0, "TOB後の非公開化手続など"))
        return tags

    # ---- 業績予想の修正 ----
    if _has(t, "上方修正"):
        tags.append(Tag(A, "上方修正", 60, "業績予想の上方修正（修正率はPDFで確認）"))
    elif _has(t, "下方修正"):
        tags.append(Tag(BAD, "下方修正", -60, "業績予想の下方修正"))
    elif _has(t, "業績予想の修正", "業績予想値の修正", "業績見通しの修正", "業績予想修正", "業績予想を修正"):
        tags.append(Tag(CHECK, "業績予想の修正", 40, "上方か下方かタイトルでは不明。PDFで確認"))
    elif _has(t, "差異"):
        tags.append(Tag(CHECK, "予想と実績の差異", 25, "予想と実績の差。良し悪しはPDFで確認"))

    # ---- 配当 ----
    if _has(t, "減配", "無配", "配当の見送り", "配当を見送"):
        tags.append(Tag(BAD, "減配・無配", -50, "配当の減額・見送り"))
    elif _has(t, "増配", "復配", "記念配当", "特別配当"):
        tags.append(Tag(A, "増配", 50, "配当の増額・復活・記念配当"))
    elif _has(t, "配当予想の修正", "配当予想修正"):
        tags.append(Tag(CHECK, "配当予想の修正", 35, "増配か減配かタイトルでは不明。PDFで確認"))
    if _has(t, "株主還元方針", "配当方針"):
        if _has(t, "引き上げ", "引上げ", "強化", "拡充", "累進配当", "DOE"):
            tags.append(Tag(A, "株主還元の強化", 40, "配当方針・株主還元の強化"))
        elif _has(t, "変更", "策定"):
            tags.append(Tag(CHECK, "株主還元方針の変更", 30, "還元強化なら好材料。PDFで確認"))

    # ---- 自己株式 ----
    if _has(t, "自己株式") and _has(t, "取得"):
        if _has(t, "取得状況", "取得結果", "取得終了", "取得の終了", "買付け価格確定", "買付価格確定", "買付けの結果", "買付結果"):
            tags.append(Tag(INFO, "自社株買いの経過", 5, "自社株買いの途中経過・結果"))
        elif _has(t, "取得に係る事項の決定", "取得に関する", "取得及び", "の取得のお知らせ", "取得を決定", "立会外買付"):
            tags.append(Tag(A, "自社株買い", 45, "自己株式取得の決定（規模＝発行済株式に対する%はPDFで確認）"))
    elif _has(t, "自己株式の消却", "自己株式消却"):
        tags.append(Tag(INFO, "自己株式の消却", 10, "自社株の消却"))

    # ---- 株主優待 ----
    if _has(t, "株主優待"):
        if _has(t, "廃止"):
            tags.append(Tag(BAD, "優待廃止", -40, "株主優待の廃止"))
        elif _has(t, "発送", "基準日", "お届け"):
            pass
        elif _has(t, "新設", "導入", "拡充", "拡大", "再開", "優待制度の開始"):
            tags.append(Tag(A, "優待新設・拡充", 45, "株主優待の新設・拡充"))
        else:
            tags.append(Tag(CHECK, "優待の変更", 20, "株主優待の変更。拡充か縮小かPDFで確認"))

    # ---- 株式分割 ----
    if _has(t, "株式分割", "株式の分割") and not _has(t, "会社分割"):
        tags.append(Tag(A, "株式分割", 40, "株式分割（投資しやすくなり買われやすい）"))

    # ---- 提携 ----
    if _has(t, "業務提携", "資本提携", "資本業務提携"):
        if _has(t, "解消", "終了"):
            tags.append(Tag(BAD, "提携解消", -20, "提携の解消"))
        else:
            tags.append(Tag(A, "業務提携", 30, "業務・資本提携（規模はPDFで確認）"))

    # ---- その他の好材料（弱め） ----
    if _has(t, "特別利益"):
        tags.append(Tag(A, "特別利益", 20, "特別利益の計上（一時的な利益）"))

    # ---- 悪材料 ----
    if _has(t, "第三者割当") and _has(t, "自己株式の処分", "自己株式処分"):
        pass  # 従業員向けなどの小さな処分は無視
    elif _has(t, "譲渡制限付", "株式報酬", "ストック・オプション", "ストックオプション"):
        pass  # 役員・従業員向けの報酬は無視
    elif _has(t, "MSワラント", "行使価額修正条項", "転換価額修正条項") and _has(t, "発行") and not _has(t, "発行された"):
        tags.append(Tag(BAD, "MSワラント", -60, "行使価額修正条項付きの新株予約権（株価の重しになりやすい）"))
    elif _has(t, "新株予約権") and _has(t, "発行") and not _has(t, "発行された", "発行済"):
        tags.append(Tag(BAD, "新株予約権の発行", -40, "新株予約権の発行（将来の株数増加）"))
    elif _has(t, "新株予約権"):
        if _has(t, "大量行使", "大量転換", "行使状況"):
            tags.append(Tag(BAD, "新株予約権の行使", -15, "新株予約権の行使・転換が進行中（株数が増える）"))
    elif _has(t, "公募", "新株式発行", "新株式の発行", "募集株式", "第三者割当", "株式の売出し", "株式売出し"):
        tags.append(Tag(BAD, "増資・売出し", -50, "公募増資・第三者割当・売出し（株数が増えて下がりやすい）"))
    if _has(t, "特別損失", "減損損失"):
        tags.append(Tag(BAD, "特別損失", -30, "特別損失・減損の計上"))
    if _has(t, "東京証券取引所における当社株式の上場廃止", "東京証券取引所への上場廃止申請", "東京証券取引所の上場廃止"):
        tags.append(Tag(CHECK, "東証の上場廃止", 10, "東証から上場廃止（名証には残る可能性。名証単独銘柄になるかも）"))
    elif _has(t, "不適切な会計", "不適切会計", "第三者委員会", "特別調査委員会", "継続企業の前提", "債務超過", "監理銘柄", "整理銘柄"):
        tags.append(Tag(BAD, "要注意事項", -70, "会計問題・上場廃止リスクなど"))
    if _has(t, "立会外分売"):
        tags.append(Tag(BAD, "立会外分売", -15, "大株主の売り（一時的な需給悪化）"))

    # ---- 決算短信 ----
    if _has(t, "決算短信") and not is_followup:
        tags.append(Tag(INFO, "決算短信", 15, "決算発表（数値はPDFで確認。自動計算はしていません）"))

    if is_followup and tags:
        # 訂正・変更の開示は参考扱いに落とす
        tags = [Tag(INFO, "訂正・変更: " + x.label, 3, "以前の開示の訂正・変更") for x in tags]
    if not tags:
        tags.append(Tag(INFO, "その他", 0, ""))
    return tags


def _tob_reason(t: str) -> str:
    r = "他社によるTOB（公開買付け）の対象。TOB価格を tob_prices.csv に入力すると差額を計算します"
    if _has(t, "MBO", "マネジメント・バイアウト"):
        r = "MBO（経営陣による買収）の対象。TOB価格を tob_prices.csv に入力すると差額を計算します"
    if _has(t, "反対"):
        r += "（会社は反対＝敵対的TOB）"
    elif _has(t, "中立", "留保"):
        r += "（会社は中立・意見留保）"
    if _has(t, "開始予定"):
        r += "。TOBはまだ開始前（予定）"
    return r


def card_category(tags: list[Tag]) -> str:
    """1社に複数の開示があるとき、どの枠に入れるか決める。"""
    cats = {t.cat for t in tags}
    if S in cats:
        return S
    has_a, has_bad, has_check = A in cats, BAD in cats, CHECK in cats
    if has_a and not has_bad:
        return A
    if has_check or has_a:
        return CHECK
    if has_bad:
        return BAD
    if SELF_TOB in cats:
        return SELF_TOB
    if TOB_REL in cats:
        return TOB_REL
    return INFO


def card_score(tags: list[Tag]) -> int:
    pos = sorted((t.score for t in tags if t.score > 0), reverse=True)
    neg = sum(t.score for t in tags if t.score < 0)
    if not pos:
        return neg
    return pos[0] + 5 * (len(pos) - 1) + (neg if card_category(tags) != BAD else 0)
