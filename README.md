# Sponsor Credit Feed

Live aggregator of sponsor credit-redemption codes and QR codes posted across
hackathon platforms.

It crawls Luma, Cerebral Valley, and lablab.ai (not just one event each — see
[Crawling](#crawling)), scores snippets of text for "this looks like a real
redeemable sponsor credit / promo code / QR code," and streams matches into a
live-updating feed over Server-Sent Events. It deliberately filters out prize
pools and contest rewards: **only credits you can actually redeem today**
count, not cash/credits you'd have to win.

Event descriptions themselves rarely contain the literal code - hosts post it
in a linked "resources" doc instead. `sources/link_discovery.py` follows
links found in scraped text (resolving shortlinks like bit.ly along the way)
and, when one resolves to a Google Doc, pulls its plain text via Google's
export endpoint. That's how real codes get found in practice: a Luma page
links to a bit.ly which resolves to a Google Doc containing the actual
promo codes (e.g. Cognee's `PERSONALBRAIN0926`, Bright Data's `cognee50`).

## Install & run

```bash
pip install -e .
sponsor-credit-feed              # http://127.0.0.1:8000
sponsor-credit-feed --port 9000  # custom port
```

Or for local dev with auto-reload: `./run.sh`.

First run seeds a couple of demo cards so the UI isn't empty while waiting
for the first crawl (every 90 minutes by default — see [Crawling](#crawling)
for why it's not faster). To wire up sponsor credits, copy `.env.example` to
`.env` and fill in whatever keys you've redeemed.

## Crawling

Rather than poll one hardcoded event, `sources/hub_source.py` crawls listing
pages that link out to dozens of individual hackathons
(`cerebralvalley.ai/events`, `lablab.ai/event`) and, for extra breadth, does
one level of recursive discovery on Luma: every Luma event names its host's
organizer calendar (e.g. `luma.com/BrightData`), and that calendar page lists
more of that organizer's events. Every discovered event is tagged by its
*actual* platform regardless of which hub page found it.

**Rate limiting matters here.** Recursively crawling Luma calendars can burst
a lot of requests at the same host fast enough to trip their rate limiter -
which affects everyone on the network hitting Luma, not just this app. Every
outbound request goes through `sources/base.py`'s `throttle()`: a global,
per-host minimum interval (1.5s for Luma, 0.5s elsewhere) enforced
regardless of how much concurrency the crawler code asks for. Don't lower
this without a good reason. Luma requests also always go direct rather than
through Bright Data's Web Unlocker - Unlocker zones use a shared IP pool, and
if any customer's traffic (including an earlier burst of ours) trips
Cloudflare's bot challenge for that zone on luma.com, every subsequent
request through it silently gets a JS challenge page back instead of real
content.

## Crawl transparency

A bare "live" dot doesn't prove anything actually got checked. The backend
tracks real stats for the current (or most recently completed) poll cycle in
`status.py` and exposes them at `GET /api/status`:

```json
{
  "in_progress": false,
  "last_completed_at": 1758,
  "total_checked": 80,
  "total_candidates": 2,
  "sources_done": 6,
  "sources_total": 6,
  "sources": [
    {"label": "luma/LumaSource", "blobs_checked": 2, "candidates_found": 2, "error": null},
    {"label": "cerebral_valley/HubSource", "blobs_checked": 60, "candidates_found": 0, "error": null},
    ...
  ],
  "poll_interval_seconds": 5400
}
```

`poller.py` records per-source counts (pages/events fetched, candidates
that survived detection, and any error) as each source finishes, both mid-cycle
and at completion. The frontend polls this endpoint and renders it in the
header as "checked N pages across M sources Xm ago · found Y real codes this
pass", clickable to expand a per-source breakdown - so a low result count is
visibly the result of having actually looked, not a mystery.

## Ranking

Every card in the feed already has both a literal code (or QR) and an
identifiable company — see [How detection works](#how-detection-works) — so
there's no separate "maybe" tier to rank down. Cards are sorted by recency.

## Fetching

- **Bright Data** — `sources/base.py` routes fetches through Bright Data's
  Web Unlocker API when `BRIGHTDATA_API_KEY` is set (falls back to a plain
  `httpx` GET otherwise; Luma always goes direct - see
  [Crawling](#crawling)). The zone name is account-specific - find yours
  with `curl https://api.brightdata.com/zone/get_active_zones -H "Authorization: Bearer $BRIGHTDATA_API_KEY"`
  rather than assuming a default.
- **Lightpanda** — `sources/base.py`'s `fetch_rendered()` shells out to the
  [lightpanda](https://github.com/lightpanda-io/browser) headless,
  JS-executing browser CLI for listing/hub pages
  (`cerebralvalley.ai/events`, `lablab.ai/event`) that populate their event
  cards client-side after load, where a plain GET only sees the
  pre-hydration shell. Falls back to the plain fetch path if lightpanda
  isn't installed. Individual event pages don't need this — they're already
  server-rendered.

Both are optional and gracefully no-op without the binary/credentials
present — the aggregator runs fully on regex heuristics + SQLite out of the
box, so it's always demoable.

## How detection works

`extract/code_detector.py` requires **both** of these before it will ever
surface a card - there is no lower tier:

- a literal redemption code (`code: cognee50`, `promo code: VERCEL25`, …) or
  an actual QR code image
- an identifiable company the code belongs to

A dollar amount alone (`$1000 in AWS credits`) is not a result on its own -
it's just a fact folded into a card that already has a real code. This is
deliberate: showing "there might be a code on this page, for $5000, maybe"
is worse than useless, so nothing gets a card without both a code and a
company attached.

Company resolution is **not** a hardcoded sponsor list — a fixed whitelist
would silently drop every sponsor it doesn't already know about, which is
most of them at any given hackathon. Instead:

1. If the snippet has a redeem link, the company is derived from that
   link's own domain (e.g. `platform.cognee.ai` → `Cognee`,
   `brightdata.com` → `Brightdata`). This also correctly handles
   cross-promos — a code like `cognee50` that's actually redeemed *at*
   Bright Data resolves to Bright Data, not Cognee, because the domain is
   authoritative over the code text.
2. Otherwise, a capitalized company/product name sitting directly next to
   the code keyword (`Nebius redemption code: NEBIUSHACK50` → `Nebius`),
   filtered against a small stoplist of ordinary English sentence-starters
   (`Use code:`, `Enter code:`) that could otherwise look like a name.

If neither resolves a company, the candidate is dropped — a code with no
identifiable owner isn't a usable result either.

**Sponsor copy is detected in Chinese, Spanish, Portuguese and Japanese
too, not just English.** Same hard requirement in every language: a
literal code (or QR) plus an identifiable company, no exceptions, no
per-language sponsor whitelist.

- **Chinese**: keyword patterns match 优惠码/兑换码/折扣码/邀请码/促销码/
  推荐码/访问码/解锁码/礼品码/激活码 and 二维码/扫码 for QR.
- **Japanese**: matches katakana loanwords like クーポンコード/プロモコード/
  割引コード/招待コード/紹介コード and QRコードをスキャン for QR.
- **Spanish**: matches código promocional/código de descuento/cupón de
  descuento/código de canje/código de invitación, etc. Unlike English
  ("promo code" — trigger word last), the Spanish/Portuguese trigger word
  ("código"/"cupón") comes *first*, so these are matched as full phrases
  rather than a single head-noun pattern, which also avoids false
  positives like "código postal" (zip code).
- **Portuguese**: matches código promocional/código de desconto/cupom de
  desconto/código de resgate/código de convite, etc. — same phrase-first
  shape as Spanish.

Sentence splitting also handles full-width `。！？` punctuation (Chinese
and Japanese both use it, and unlike English don't put a space after it).
Company resolution works the same way regardless of language: the
redeem-link domain is authoritative when present (language-independent,
since it only looks at the URL); otherwise a name sitting right next to
the code keyword — which works whether that name is in the local script
(`英伟达兑换码：GPU50HACK` → `英伟达`, `任天堂クーポンコード：SWITCH20` →
`任天堂`) or a Latin-script brand name inside otherwise-local-language
copy (`Nebius código promocional: NEBIUSHACK50` → `Nebius`, `Nebius
クーポンコード：NEBIUSHACK50` → `Nebius`) — a common case, since global
sponsors' names usually aren't translated even in local-language event
copy. Same rule as English throughout: a bare prize-pool mention with no
literal code is still dropped in every language, and each language has its
own stoplist of generic instruction phrases (`Usa el código…`, `使用优惠
码…`, `クーポンコードをご利用ください…`) that get filtered rather than
mistaken for a company name.

`extract/qr_detector.py` can additionally decode an actual QR code image via
OpenCV if a page links one (no `libzbar` system dependency needed).

## Architecture

```
sponsor_credit_feed/
  sources/       - per-platform fetchers: luma_source.py (parses the
                    __NEXT_DATA__ JSON Next.js embeds), generic_html_source.py
                    (fallback HTML text), hub_source.py (crawls listing pages
                    + recursive Luma calendar discovery), link_discovery.py
                    (follows links out to resource docs), base.py (shared
                    fetch() + per-host throttle())
  extract/       - regex heuristics + QR decode
  feed_store.py  - SQLite store + global near-dup filtering + SSE pub/sub
  poller.py      - background loop tying it all together
  main.py        - FastAPI app: /api/feed, /api/stream (SSE), static UI
  cli.py         - `sponsor-credit-feed` console entrypoint
  static/        - live feed frontend (vanilla JS, EventSource)
```

## Known limitations

- lablab.ai has no equivalent of Cerebral Valley's `?startDate=` param that
  we've found yet, so its historical coverage is still just "whatever the
  current listing page links to."
- Source URL lists are hardcoded in `main.py` — add more event/listing URLs
  there as you find them. Cerebral Valley's `/events?startDate=YYYY-MM-DD`
  re-anchors its listing to an earlier date (confirmed via lightpanda that
  this actually changes the hydrated results, not a no-op) — `main.py`
  currently seeds a couple of monthly anchors; add more/earlier ones for
  deeper history.
