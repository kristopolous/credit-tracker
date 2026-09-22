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

CODE_KEYWORDS = re.compile(
    r"\b(promo\s?code|coupon(?:\s?code)?|redeem(?:\s?code)?|redemption\s?code|"
    r"discount\s?code|voucher|unlock\s?code|invite\s?code|referral\s?code|"
    r"credit\s?code|access\s?code)\b",
    re.I,
)

QR_KEYWORDS = re.compile(r"\bQR\s?code\b|\bscan(?:\s+the)?\s+qr\b", re.I)

# A literal code token: e.g. "use code: COGNEE50", "Promo code: cognee50",
# "code COGNEE-AWS25". Real codes aren't always all-caps (e.g. "cognee50"),
# so this is case-insensitive as long as it looks token-shaped (letters +
# digits, no spaces). Requires at least one digit via lookahead, otherwise
# "Code for the event" / "Sample Code: https://..." mis-capture the next
# plain English word ("for", "https") as if it were the code itself.
CODE_TOKEN_RE = re.compile(
    r"\bcode[:\s]+((?=[A-Za-z0-9\-_]*\d)[A-Za-z0-9][A-Za-z0-9\-_]{2,19})\b",
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
# or "Vercel promo code: VERCEL25". The capital-letter requirement (not
# re.I on this group - Python 3.11+ scoped inline flags let the keyword
# alternation stay case-insensitive on its own) is the generic substitute
# for a fixed sponsor list: any Title-Case word/phrase right before the
# keyword counts, whoever the sponsor turns out to be.
COMPANY_BEFORE_CODE_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&.]{1,24}(?:\s[A-Z][A-Za-z0-9&.]{1,24}){0,2})\s+"
    r"(?:(?i:promo\s?code|coupon(?:\s?code)?|redeem(?:\s?code)?|redemption\s?code|"
    r"discount\s?code|voucher|unlock\s?code|invite\s?code|referral\s?code|"
    r"credit\s?code|access\s?code))\b"
)

# Generic capitalized sentence-starters that sit before "code:" without
# being a company name ("Use code: X", "Enter code: X") - filtered out of
# COMPANY_BEFORE_CODE_RE matches. This is a stoplist of ordinary English
# words, not a sponsor list - it never has to be updated for a new sponsor.
_GENERIC_LEADING_WORDS = {
    "use", "enter", "apply", "get", "your", "the", "our", "this", "see",
    "for", "with", "new", "special", "limited", "each", "please", "grab",
    "redeem", "claim", "add", "type", "copy", "paste", "click", "simply",
    "just", "here", "check", "note", "important", "reminder",
}


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
    # enough to be useful in a feed card.
    parts = re.split(r"\n{1,}|(?<=[.!?])\s{2,}", text)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Further split overly long paragraphs into sentences.
        if len(p) > 280:
            out.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", p) if s.strip())
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
    """Find a capitalized company/product name sitting right before a code
    keyword, e.g. "Nebius redemption code: NEBIUSHACK50" -> "Nebius". No
    fixed sponsor list - just a stoplist of ordinary English words that
    could otherwise be mistaken for a name ("Use code:", "Enter code:")."""
    for m in COMPANY_BEFORE_CODE_RE.finditer(snippet):
        candidate = m.group(1).strip()
        first_word = candidate.split()[0].lower()
        if first_word in _GENERIC_LEADING_WORDS:
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
