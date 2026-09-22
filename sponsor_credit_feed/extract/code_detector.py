"""Heuristic extraction of sponsor credit/redemption signals from free text.

Intentionally simple regex + keyword scoring rather than an LLM call, so
it's instant and free to run on every poll.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

# Only counts as a *credit* signal when the word "credit(s)" actually
# appears near the dollar amount - a bare "$10,000 prize pool" shouldn't
# match, since that's not a redeemable sponsor credit. Allows a "k" suffix
# ("$5k") and a handful of words in between ("$5k in AAI credits") without
# crossing into a second dollar amount. This only feeds the display
# "amount" field and score - it is never enough on its own to surface a
# candidate (see find_candidates: a literal code or QR is always required).
CREDIT_AMOUNT_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})*|\d+)(k)?\b(?:(?!\$)[^.\n]){0,30}?\bcredits?\b", re.I
)

_EN_CODE_KEYWORD_ALTS = (
    r"promo\s?code|coupon(?:\s?code)?|redeem(?:\s?code)?|redemption\s?code|"
    r"discount\s?code|voucher|unlock\s?code|invite\s?code|referral\s?code|"
    r"credit\s?code|access\s?code"
)

# Spanish equivalents. Unlike English (where the trigger word "code" sits
# at the END of the phrase - "promo code", "discount code"), Spanish and
# Portuguese put the head noun ("código") FIRST - "código promocional",
# "código de descuento". Listed here as literal whole-phrase alternatives
# (not an open-ended "código + any qualifier" pattern) for the same reason
# the Chinese list below is literal phrases rather than a generative
# pattern: it avoids false positives like "código postal" (zip code).
_ES_CODE_KEYWORD_ALTS = (
    r"c[oó]digo\spromocional|c[oó]digo\spromo|c[oó]digo\sde\sdescuento|"
    r"c[oó]digo\sdescuento|c[oó]digo\sde\scup[oó]n|cup[oó]n\sde\sdescuento|"
    r"cup[oó]n|c[oó]digo\sde\scanje|c[oó]digo\scanje|c[oó]digo\sde\sreferido|"
    r"c[oó]digo\sreferido|c[oó]digo\sde\sacceso|c[oó]digo\sde\sinvitaci[oó]n|"
    r"c[oó]digo\sde\sregalo"
)

# Portuguese equivalents - same "head noun first" shape as Spanish.
_PT_CODE_KEYWORD_ALTS = (
    r"c[oó]digo\spromocional|c[oó]digo\spromo|c[oó]digo\sde\sdesconto|"
    r"c[oó]digo\sdesconto|c[oó]digo\sde\scupom|cupom\sde\sdesconto|"
    r"cupom|c[oó]digo\sde\sresgate|c[oó]digo\sresgate|c[oó]digo\sde\sindica[cç][aã]o|"
    r"c[oó]digo\sindica[cç][aã]o|c[oó]digo\sde\sacesso|c[oó]digo\sde\sconvite|"
    r"c[oó]digo\sde\spresente"
)

# Chinese equivalents of the same redemption-code phrasing (优惠码/promo
# code, 兑换码/redeem code, 折扣码/discount code, 邀请码/invite code, 促销码/
# promo code, 推荐码/referral code, 访问码/access code, 解锁码/unlock code,
# 礼品码/gift code, 激活码/activation code). CJK script has no spaces
# between words, so these alternatives are matched as raw substrings - \b
# word-boundary anchoring (which relies on a \w/non-\w transition) isn't
# meaningful or needed here the way it is for the English alternatives.
_ZH_CODE_KEYWORD_ALTS = (
    r"优惠码|优惠券码|兑换码|兑换代码|折扣码|邀请码|促销码|推荐码|访问码|解锁码|礼品码|激活码"
)

# Japanese equivalents, mostly katakana loanwords ending in コード ("code")
# or クーポン ("coupon") bare - プロモコード (promo code), クーポンコード
# (coupon code), 割引コード (discount code), 招待コード (invite code), 紹介
# コード (referral code), 交換コード (redeem code), 引き換えコード
# (redemption code), アクセスコード (access code), ギフトコード (gift code),
# 特典コード/ボーナスコード (bonus code). Same "trailing code word" shape as
# English/Chinese, and same reasoning as Chinese for skipping \b anchors -
# Japanese kana/kanji are Unicode word characters too, so \b never fires
# between two of them.
_JA_CODE_KEYWORD_ALTS = (
    r"プロモコード|クーポンコード|クーポン|割引コード|招待コード|紹介コード|"
    r"交換コード|引き換えコード|アクセスコード|ギフトコード|特典コード|ボーナスコード"
)

# Chinese + Japanese combined, for the places (CODE_TOKEN_RE, the
# CJK-script company fallback) that treat any CJK-script code keyword the
# same way regardless of which of the two languages it's from.
_CJK_CODE_KEYWORD_ALTS = rf"{_ZH_CODE_KEYWORD_ALTS}|{_JA_CODE_KEYWORD_ALTS}"

CODE_KEYWORDS = re.compile(
    rf"\b(?:{_EN_CODE_KEYWORD_ALTS}|{_ES_CODE_KEYWORD_ALTS}|{_PT_CODE_KEYWORD_ALTS})\b"
    rf"|(?:{_CJK_CODE_KEYWORD_ALTS})",
    re.I,
)

QR_KEYWORDS = re.compile(
    r"\bQR\s?code\b|\bscan(?:\s+the)?\s+qr\b|"
    r"\bc[oó]digo\s?QR\b|\bescane[ae](?:\s+el|\s+o)?\s+(?:c[oó]digo\s+)?qr\b|"
    r"二维码|扫码|QR\s?コード|QRコードをスキャン|QRコードを読み取",
    re.I,
)

# A literal code token: e.g. "use code: COGNEE50", "Promo code: cognee50",
# "code COGNEE-AWS25". Real codes aren't always all-caps (e.g. "cognee50"),
# so this is case-insensitive as long as it looks token-shaped (letters +
# digits, no spaces). Requires at least one digit via lookahead, otherwise
# "Code for the event" / "Sample Code: https://..." mis-capture the next
# plain English word ("for", "https") as if it were the code itself.
#
# The actual code token itself is always written in Latin letters/digits
# regardless of the surrounding sentence's language (e.g. "兑换码：
# COGNEE50", "código promocional: COGNEE50"), so every alternative here is
# just a different language's trigger phrase immediately before the same
# token shape and digit requirement:
#  - English: bare "code" is enough, since it's the tail word of every EN
#    phrase ("promo code", "discount code", ...) - matching just the tail
#    covers all of them in one alternative.
#  - Spanish/Portuguese: the trigger word ("código"/"cupón"/"cupom") comes
#    FIRST in the phrase, with a qualifier after it ("código promocional"),
#    so the full phrase list is needed here, not just the head noun - a
#    bare "código" trigger would stop before "promocional" and never reach
#    the colon/token that follows it.
#  - Chinese/Japanese: same full-phrase requirement, and no \b before
#    those alternatives - \b only fires on a word-char/non-word-char
#    transition, and CJK/kana characters don't participate in that the way
#    ASCII letters do, so requiring it would silently fail to match.
CODE_TOKEN_RE = re.compile(
    rf"(?:\bcode|(?:{_ES_CODE_KEYWORD_ALTS})|(?:{_PT_CODE_KEYWORD_ALTS})|"
    rf"(?:{_CJK_CODE_KEYWORD_ALTS}))[:：\s]+"
    r"((?=[A-Za-z0-9\-_]*\d)[A-Za-z0-9][A-Za-z0-9\-_]{2,19})\b",
    re.I,
)

# Common English words that could otherwise slip past the digit check by
# coincidence (e.g. a token like "2FA" or a year in a sentence) - filtered
# out of any code candidate as a final guard.
CODE_STOPWORDS = {"http", "https", "www"}

# A redemption URL sitting right next to a credit/code mention - e.g.
# "https://platform.cognee.ai/billing" or "https://brightdata.com?promo=…".
URL_RE = re.compile(r"https?://[^\s)]+", re.I)

# Multi-label public suffixes this heuristic knows about, so
# "example.co.uk" resolves to "example" rather than "co". Not exhaustive -
# just enough not to misfire on the common ones.
_MULTI_LABEL_TLDS = {"co.uk", "com.au", "co.jp", "com.br", "co.in"}


def _company_from_host(host: str) -> str | None:
    """Derive a company name from a redeem URL's domain - generically, not
    from a fixed sponsor list. New hackathons bring new sponsors every
    week; a hardcoded list would silently drop all of them. This is why
    the redeem link's *domain* is authoritative rather than name-matching
    the code text itself: a code like "cognee50" can be issued *by* Bright
    Data as part of a cross-promo, and the domain gets that right where a
    text match on "cognee" wouldn't."""
    host = host.lower().split(":")[0]
    labels = [l for l in host.split(".") if l]
    if len(labels) < 2:
        return None
    suffix2 = ".".join(labels[-2:])
    if suffix2 in _MULTI_LABEL_TLDS and len(labels) >= 3:
        registrable = labels[-3]
    else:
        registrable = labels[-2]
    words = [w for w in re.split(r"[-_]+", registrable) if w]
    if not words:
        return None
    return " ".join(w.capitalize() for w in words)


# Fallback company signal for mentions with no redeem link at all: a
# capitalized company/product name sitting directly next to the code
# keyword in the same snippet, e.g. "Nebius redemption code: NEBIUSHACK50"
# or "Vercel promo code: VERCEL25" - or, since the code keyword itself may
# be in any of the other supported languages while the brand name is still
# written in Latin script (common - a global sponsor's name usually isn't
# translated even in otherwise-local-language copy, e.g. "Nebius código
# promocional: NEBIUSHACK50" or "Nebius 兑换码：NEBIUSHACK50"), any
# language's code keyword works as the anchor. The capital-letter
# requirement (not re.I on this group - Python 3.11+ scoped inline flags
# let the keyword alternation stay case-insensitive on its own) is the
# generic substitute for a fixed sponsor list: any Title-Case word/phrase
# right before the keyword counts, whoever the sponsor turns out to be.
COMPANY_BEFORE_CODE_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&.]{1,24}(?:\s[A-Z][A-Za-z0-9&.]{1,24}){0,2})\s*"
    rf"(?:(?i:{_EN_CODE_KEYWORD_ALTS}|{_ES_CODE_KEYWORD_ALTS}|{_PT_CODE_KEYWORD_ALTS})|"
    rf"(?:{_CJK_CODE_KEYWORD_ALTS}))\b"
)

# Same idea, for a company/product name written in CJK script itself -
# Chinese/Japanese have no capitalization to key off, so instead this
# matches any contiguous run of 2-8 CJK-range characters (kanji, hiragana,
# katakana and Chinese ideographs all fall in this combined range) sitting
# directly next to a CJK code keyword (no space needed; neither language is
# space-separated), e.g. "英伟达兑换码：GPU50HACK" -> "英伟达" or
# "任天堂クーポンコード：SWITCH20" -> "任天堂". Still no fixed sponsor list -
# the only filter is the generic-leading-word stoplist below, same as the
# Latin-script path.
CJK_COMPANY_BEFORE_CODE_RE = re.compile(
    rf"([぀-ヿ一-鿿]{{2,8}})(?:{_CJK_CODE_KEYWORD_ALTS})"
)
# Old name kept as an alias - some callers/tests may still refer to it.
ZH_COMPANY_BEFORE_CODE_RE = CJK_COMPANY_BEFORE_CODE_RE

# Generic capitalized sentence-starters that sit before "code:" without
# being a company name ("Use code: X", "Enter code: X") - filtered out of
# COMPANY_BEFORE_CODE_RE matches. This is a stoplist of ordinary words, not
# a sponsor list - it never has to be updated for a new sponsor. Includes
# Spanish/Portuguese equivalents too, since COMPANY_BEFORE_CODE_RE's
# capital-letter-run capture is shared across all three Latin-script
# languages and this stoplist only ever checks the first captured word.
_GENERIC_LEADING_WORDS = {
    # English
    "use", "enter", "apply", "get", "your", "the", "our", "this", "see",
    "for", "with", "new", "special", "limited", "each", "please", "grab",
    "redeem", "claim", "add", "type", "copy", "paste", "click", "simply",
    "just", "here", "check", "note", "important", "reminder",
    # Spanish
    "usa", "use", "ingresa", "introduce", "aplica", "obtén", "tu",
    "nuestro", "nuevo", "especial", "canjea", "consigue", "aquí",
    # Portuguese
    "use", "usar", "insira", "digite", "aplique", "obtenha", "seu", "nosso",
    "novo", "especial", "resgate", "aqui",
}

# Same idea in Chinese/Japanese: ordinary words/phrases that can sit right
# before a code keyword without being a sponsor name ("使用兑换码" = "use
# redeem code", "输入优惠码" = "enter promo code", "クーポンコードを入力"
# = "enter coupon code", etc.) - a stoplist of common words, not a sponsor
# list. Checked as a *substring* of the captured run rather than an exact
# match: unlike the English/Spanish/Portuguese stoplist (which only needs
# to check the first space-separated word), CJK scripts aren't reliably
# space-separated, so a generic instruction phrase like "扫码领取" ("scan
# the code to claim") ends up concatenated with whatever comes before the
# code keyword, and "领取" alone wouldn't equal the full captured
# "扫码领取" run.
_CJK_GENERIC_LEADING_WORDS = (
    # Chinese
    "使用", "输入", "填写", "请输入", "获取", "领取", "新", "限时", "专属",
    "我们的", "您的", "你的", "此", "查看", "注意", "提示", "复制", "粘贴",
    "点击", "立即", "每", "请", "这个", "该", "扫码", "扫一扫",
    # Japanese
    "使う", "入力", "こちら", "こちらの", "新しい", "限定", "今すぐ", "ご利用",
    "あなたの", "私たちの", "こちらから", "コピー", "貼り付け", "クリック",
    "注意", "重要", "確認",
)
# Old name kept as an alias - some callers/tests may still refer to it.
_ZH_GENERIC_LEADING_WORDS = _CJK_GENERIC_LEADING_WORDS


@dataclass
class Candidate:
    text: str
    score: float
    has_credit_amount: bool = False
    has_code_keyword: bool = False
    has_qr_keyword: bool = False
    codes: list[str] = field(default_factory=list)
    amounts: list[str] = field(default_factory=list)
    sponsors: list[str] = field(default_factory=list)
    redeem_url: str | None = None
    company: str | None = None

    @property
    def kind(self) -> str:
        if self.has_qr_keyword and not self.codes:
            return "qr"
        if self.codes:
            return "code"
        return "credit"

    @property
    def service(self) -> str | None:
        return self.company


# A bullet line that's *just* a URL (e.g. the redemption link sitting on
# its own line right under the code) - merged onto the previous line so the
# code and its redeem link end up in the same snippet.
_BARE_URL_LINE_RE = re.compile(r"\n[ \t]*[*\-•][ \t]*(https?://\S+)[ \t]*(?=\n|$)")


_LEADING_BULLET_RE = re.compile(r"^[\s]*[*\-•][ \t]*")


def _clean_display_text(snippet: str, urls: list[str]) -> str:
    """Strip bullet markers and embedded URLs from a snippet - the URLs are
    surfaced separately as a clickable redeem link, so leaving them inline
    just duplicates a long raw link in the middle of the sentence."""
    cleaned = _LEADING_BULLET_RE.sub("", snippet)
    for url in urls:
        cleaned = cleaned.replace(url, "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    return cleaned.strip(" \t,;:-")


def _split_snippets(text: str) -> list[str]:
    text = _BARE_URL_LINE_RE.sub(r" \1", text)
    # Break on blank lines / sentence-ish boundaries, keep snippets short
    # enough to be useful in a feed card. Chinese sentences typically end
    # with full-width punctuation (。！？) rather than ASCII ".!?", and
    # (unlike English) usually aren't followed by a space at all - so that
    # alternative has no `\s{2,}` requirement.
    parts = re.split(r"\n{1,}|(?<=[.!?])\s{2,}|(?<=[。！？])", text)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Further split overly long paragraphs into sentences.
        if len(p) > 280:
            out.extend(
                s.strip()
                for s in re.split(r"(?<=[.!?])\s+|(?<=[。！？])", p)
                if s.strip()
            )
        else:
            out.append(p)
    return _drop_prefix_duplicates(out)


def _drop_prefix_duplicates(snippets: list[str]) -> list[str]:
    """The same sentence sometimes appears twice in a source document - once
    in full, once truncated (e.g. a summary blurb elsewhere cuts off right
    before the "Compete to win $100k..." follow-up that would otherwise
    reveal this is prize money, not a giveaway). Drop any snippet that's a
    strict prefix of a longer one, keeping only the fuller version."""
    by_length = sorted(snippets, key=len, reverse=True)
    kept: list[str] = []
    for s in by_length:
        if not any(longer.startswith(s) for longer in kept):
            kept.append(s)
    kept_set = set(kept)
    return [s for s in snippets if s in kept_set]


def _company_before_code(snippet: str) -> str | None:
    """Find a company/product name sitting right before a code keyword,
    e.g. "Nebius redemption code: NEBIUSHACK50" -> "Nebius",
    "código promocional NEBIUSHACK50" style Spanish/Portuguese copy, or
    "英伟达兑换码：GPU50HACK" -> "英伟达" / "任天堂クーポンコード" ->
    "任天堂" in Chinese/Japanese. No fixed sponsor list - just a stoplist of
    ordinary words per language/script that could otherwise be mistaken for
    a name ("Use code:", "使用兑换码", "クーポンコードを入力")."""
    for m in COMPANY_BEFORE_CODE_RE.finditer(snippet):
        candidate = m.group(1).strip()
        first_word = candidate.split()[0].lower()
        if first_word in _GENERIC_LEADING_WORDS:
            continue
        return candidate
    for m in CJK_COMPANY_BEFORE_CODE_RE.finditer(snippet):
        candidate = m.group(1).strip()
        if any(w in candidate for w in _CJK_GENERIC_LEADING_WORDS):
            continue
        return candidate
    return None


def find_candidates(text: str, min_score: float = 1.0) -> list[Candidate]:
    """Scan raw text and return scored candidate snippets worth surfacing.

    A candidate must have BOTH a literal redemption code (or a QR code) AND
    an identifiable company it belongs to - no exceptions, and no fixed
    whitelist of "known" sponsors. Company resolution is generic: the
    registrable domain of the redeem link if there is one, otherwise a
    capitalized name sitting right next to the code keyword in the text.
    A bare dollar amount with no code, or a code with no identifiable
    company, is not surfaced - "some code on this page, maybe" isn't a
    result.
    """
    candidates: list[Candidate] = []
    for snippet in _split_snippets(text):
        amounts = CREDIT_AMOUNT_RE.findall(snippet)
        has_credit = bool(amounts)
        has_code_kw = bool(CODE_KEYWORDS.search(snippet))

        codes = [c for c in CODE_TOKEN_RE.findall(snippet) if c.lower() not in CODE_STOPWORDS]

        # A bare "QR code" mention (e.g. a doc's own share-QR) isn't a
        # sponsor redemption on its own - only count it once it's paired
        # with a credit amount or a code keyword.
        has_qr = bool(QR_KEYWORDS.search(snippet)) and (has_credit or has_code_kw)

        # A redeemable credit needs a literal code or QR - a bare dollar
        # amount, or a "promo code" mention with no actual token found, is
        # exactly the vague "there's a code here somewhere, I think" result
        # this project must never show.
        if not (codes or has_qr):
            continue

        score = 0.0
        if has_credit:
            score += 1.2
        if has_code_kw:
            score += 1.5
        if codes:
            score += 1.5
        if has_qr:
            score += 1.3
        if score < min_score:
            continue

        urls = URL_RE.findall(snippet)

        # Company resolution: the redeem link's own domain is authoritative
        # when present (handles cross-promos, e.g. a "cognee50" code that's
        # actually redeemed at brightdata.com). Otherwise fall back to a
        # capitalized name sitting right next to the code keyword.
        company = None
        if urls:
            company = _company_from_host(urlparse(urls[0]).netloc)
        if not company:
            company = _company_before_code(snippet)

        # This is a *sponsor* credit feed - if we can't tell which company
        # a code belongs to, it isn't a usable result. Drop it rather than
        # show a code with a blank/unknown company.
        if not company:
            continue

        candidates.append(
            Candidate(
                text=_clean_display_text(snippet, urls),
                score=round(score, 2),
                has_credit_amount=has_credit,
                has_code_keyword=has_code_kw,
                has_qr_keyword=has_qr,
                codes=codes,
                amounts=[f"${digits}{k or ''}" for digits, k in amounts],
                sponsors=[company],
                redeem_url=urls[0] if urls else None,
                company=company,
            )
        )
    return candidates
