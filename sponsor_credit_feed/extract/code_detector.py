"""Heuristic extraction of sponsor credit/redemption signals from free text.

This is the fallback (and default) path used when Strands/Bedrock isn't
configured. It's intentionally simple regex + keyword scoring rather than an
LLM call, so it's instant and free to run on every poll.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

# Only counts as a *credit* signal when the word "credit(s)" actually
# appears near the dollar amount - a bare "$10,000 prize pool" shouldn't
# match, since that's not a redeemable sponsor credit. Allows a "k" suffix
# ("$5k") and a handful of words in between ("$5k in AAI credits") without
# crossing into a second dollar amount.
CREDIT_AMOUNT_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})*|\d+)(k)?\b(?:(?!\$)[^.\n]){0,30}?\bcredits?\b", re.I
)

# Nobody gives away $5k+ in free credits just for showing up - real upfront
# giveaways run tens-to-low-hundreds of dollars (tonight's actual Cognee/
# Bright Data/AWS credits are $25-$125). Used as a sanity check independent
# of phrasing to catch prize-pool amounts that don't use obvious "prize"
# language.
MAX_BELIEVABLE_GIVEAWAY = 500


def _amount_value(digits: str, k_suffix: str) -> float:
    value = float(digits.replace(",", ""))
    return value * 1000 if k_suffix else value

CODE_KEYWORDS = re.compile(
    r"\b(promo\s?code|coupon(?:\s?code)?|redeem(?:\s?code)?|redemption\s?code|"
    r"discount\s?code|voucher|unlock\s?code|invite\s?code|referral\s?code|"
    r"credit\s?code|access\s?code)\b",
    re.I,
)

QR_KEYWORDS = re.compile(r"\bQR\s?code\b|\bscan(?:\s+the)?\s+qr\b", re.I)

# Competitive/contingent reward language - "$10,000 across cash and
# credits" behind "compete for" is a *prize* you have to win, not a credit
# you can redeem today just by showing up. Excluded outright: this tool
# only tracks credits that are actually being given away, not prize pools.
PRIZE_KEYWORDS = re.compile(
    r"\b(compete (?:for|to win)|prize\s?pool|grand\s?prize|cash\s?prizes?|for the winners?|"
    r"winning\s?team|1st place|2nd place|3rd place|first place|second place|"
    r"third place|top\s?\d+\s?(?:teams?|finishers?|winners?)|"
    r"(?:cash|prizes?)\s?(?:and|&)\s?credits|credits\s?(?:and|&)\s?(?:cash|prizes?))\b",
    re.I,
)

# A "$AMOUNT ... -- Sponsor" bullet, as used in per-place prize breakdown
# lists (e.g. "$100 credits for 6 months -- Vercel" under a "First Place
# Prizes:" header several lines up). Genuine upfront giveaway mentions in
# the wild always read as full sentences ("$50 credits: Promo code X",
# "$125 in Credits for everyone") and never use this dash-separated
# per-sponsor-prize shape, so this is a reliable local signal - no need to
# look back at the (possibly distant) header line that introduced the list.
PRIZE_LIST_ITEM_RE = re.compile(r"\$[\d,]+\S*[^\n]*\s+--\s+\S")

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

# Generic standalone token that looks like a coupon code (all-caps/digits,
# 5-16 chars, at least one digit) - used as a weaker secondary signal near
# credit/coupon keyword hits.
LOOSE_TOKEN_RE = re.compile(r"\b((?=[A-Z0-9]*\d)[A-Z][A-Z0-9]{4,15})\b")

# Common English words that could otherwise slip past the digit check by
# coincidence (e.g. a token like "2FA" or a year in a sentence) - filtered
# out of any code candidate as a final guard.
CODE_STOPWORDS = {"http", "https", "www"}

# A redemption URL sitting right next to a credit/code mention - e.g.
# "https://platform.cognee.ai/billing" or "https://brightdata.com?promo=…".
URL_RE = re.compile(r"https?://[^\s)]+", re.I)

SPONSOR_HINTS = [
    "cognee",
    "aws",
    "amazon",
    "bright data",
    "brightdata",
    "bedrock",
    "strands",
    "openai",
    "anthropic",
    "assemblyai",
    "ibm",
    "aai",  # common shorthand for AssemblyAI in prize-pool copy ("$5k in AAI credits")
]

# Canonical display name for each sponsor hint - collapses "brightdata" /
# "bright data" into one label, "amazon" / "bedrock" / "strands" under AWS.
SPONSOR_DISPLAY = {
    "cognee": "Cognee",
    "aws": "AWS",
    "amazon": "AWS",
    "bright data": "Bright Data",
    "brightdata": "Bright Data",
    "bedrock": "AWS Bedrock",
    "strands": "AWS Strands",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "assemblyai": "AssemblyAI",
    "ibm": "IBM",
    "aai": "AssemblyAI",
}

# Redeem-URL domain -> service. Preferred over sponsor-hint text matching
# when available: a promo code like "cognee50" issued *by* Bright Data (as
# part of a Cognee x Bright Data cross-promo) contains the substring
# "cognee", which would otherwise misattribute it to Cognee instead of the
# service whose domain actually redeems it.
DOMAIN_SPONSOR = {
    "cognee.ai": "Cognee",
    "brightdata.com": "Bright Data",
    "amazon.com": "AWS",
    "amazon": "AWS",  # covers pulse.amazon, non-standard TLD promo links
    "aws.amazon.com": "AWS",
    "assemblyai.com": "AssemblyAI",
    "openai.com": "OpenAI",
    "anthropic.com": "Anthropic",
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

    @property
    def kind(self) -> str:
        if self.has_qr_keyword and not self.codes:
            return "qr"
        if self.codes:
            return "code"
        return "credit"

    @property
    def service(self) -> str | None:
        if self.redeem_url:
            host = urlparse(self.redeem_url).netloc.lower()
            for domain, name in DOMAIN_SPONSOR.items():
                if domain in host:
                    return name
        return SPONSOR_DISPLAY.get(self.sponsors[0]) if self.sponsors else None


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


def find_candidates(text: str, min_score: float = 1.0) -> list[Candidate]:
    """Scan raw text and return scored candidate snippets worth surfacing."""
    candidates: list[Candidate] = []
    for snippet in _split_snippets(text):
        amounts = CREDIT_AMOUNT_RE.findall(snippet)
        has_credit = bool(amounts)
        has_code_kw = bool(CODE_KEYWORDS.search(snippet))
        sponsors = [s for s in SPONSOR_HINTS if s in snippet.lower()]

        # A bare "QR code" mention (e.g. a doc's own share-QR) isn't a
        # sponsor redemption on its own - only count it once it's paired
        # with a credit amount, a code keyword, or a named sponsor.
        has_qr = bool(QR_KEYWORDS.search(snippet)) and (has_credit or has_code_kw or bool(sponsors))

        codes = [c for c in CODE_TOKEN_RE.findall(snippet) if c.lower() not in CODE_STOPWORDS]
        if has_code_kw and not codes:
            # weaker fallback: any shouty token near a code keyword
            codes = [
                t
                for t in LOOSE_TOKEN_RE.findall(snippet)
                if t not in ("QR", "AWS", "API", "USD")
            ][:1]

        score = 0.0
        if has_credit:
            score += 1.2
        if has_code_kw:
            score += 1.5
        if codes:
            score += 1.5
        if has_qr:
            score += 1.3

        # A redeemable credit needs either a real credit-dollar amount or a
        # literal code/QR - a snippet that just says "promo code" with no
        # amount or token isn't useful on its own.
        if not (has_credit or codes or has_qr):
            continue
        # "Compete for $10,000 across cash and credits" is a prize you have
        # to win, not a credit you can redeem today - skip it unless there's
        # an actual literal code, which is unambiguous evidence otherwise.
        if not codes and PRIZE_KEYWORDS.search(snippet):
            continue
        # "$100 credits for 6 months -- Vercel" is a per-sponsor line out of
        # a "First Place Prizes:" breakdown several lines up - the header
        # itself isn't in this snippet, but the list-item shape is a
        # reliable tell on its own.
        if not codes and PRIZE_LIST_ITEM_RE.search(snippet):
            continue
        # Sanity check independent of phrasing: nobody hands out $5k+ in
        # free credits just for showing up. Real upfront giveaways run in
        # the tens-to-low-hundreds (the actual Cognee/Bright Data/AWS
        # credits here are $25-$125); anything bigger without a literal
        # code to prove it's real is almost certainly a prize amount.
        if not codes and amounts:
            max_amount = max(_amount_value(digits, k) for digits, k in amounts)
            if max_amount > MAX_BELIEVABLE_GIVEAWAY:
                continue
        if score < min_score:
            continue

        urls = URL_RE.findall(snippet)

        # This is a *sponsor* credit feed - if we can't tell which sponsor
        # a code belongs to (no sponsor keyword in the text, and the
        # redeem link isn't one of the tracked sponsor domains), it isn't
        # one of the deals this project is for. Random unrelated discount
        # codes picked up while crawling an event's host calendar for
        # breadth (e.g. a membership-site code on an unrelated meetup) get
        # dropped here rather than shown with a blank service field.
        service_preview = None
        if urls:
            host = urlparse(urls[0]).netloc.lower()
            for domain, name in DOMAIN_SPONSOR.items():
                if domain in host:
                    service_preview = name
                    break
        if not service_preview and sponsors:
            service_preview = SPONSOR_DISPLAY.get(sponsors[0])
        if not service_preview:
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
                sponsors=sponsors,
                redeem_url=urls[0] if urls else None,
            )
        )
    return candidates
