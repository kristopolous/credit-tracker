# Sponsor Credit Feed

Live aggregator of sponsor credit-redemption codes and QR codes posted across
hackathon platforms — built for **Battle of the Personal Brains**
(Cognee × AWS Strands × Bright Data, Sep 21 2026).

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
export endpoint. That's how tonight's actual codes get found: the Luma page
links to a bit.ly which resolves to a Google Doc containing the real Cognee
(`PERSONALBRAIN0926`) and Bright Data (`cognee50`) promo codes.

## Install & run

```bash
pip install -e .
sponsor-credit-feed              # http://127.0.0.1:8000
sponsor-credit-feed --port 9000  # custom port
```

Or for local dev with auto-reload: `./run.sh`.

First run seeds a couple of demo cards so the UI isn't empty while waiting
for the first crawl (every 10 minutes by default — see [Crawling](#crawling)
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

## Ranking

Not every event publishes a literal redeemable code - some just mention "$X
credits" in passing. Those aren't useless (real signal), but they're not
actionable either, so the feed ranks anything with a literal code or QR
above everything else, and sorts each tier by recency. Nothing is dropped;
a no-code mention just sinks to the bottom instead of competing with real
codes for the top slot.

## Sponsor integration

This maps onto all three of tonight's sponsors:

- **Bright Data** — `sources/base.py` routes fetches through Bright Data's
  Web Unlocker API when `BRIGHTDATA_API_KEY` is set (falls back to a plain
  `httpx` GET otherwise; Luma always goes direct - see
  [Crawling](#crawling)). The zone name is account-specific - find yours
  with `curl https://api.brightdata.com/zone/get_active_zones -H "Authorization: Bearer $BRIGHTDATA_API_KEY"`
  rather than assuming a default.
- **Cognee** — `memory/cognee_memory.py` is an optional enrichment layer:
  every accepted item is remembered in a Cognee-built knowledge graph, and
  new candidates are checked against it for semantic duplicates (catches
  reposts/paraphrases that plain string matching misses). Supports both
  Cognee Cloud (`COGNEE_API_KEY` + `COGNEE_SERVICE_URL`, the latter from the
  API Keys page in the Cognee Cloud console) and local mode (`LLM_API_KEY`
  only).
- **AWS Strands Agents** — `extract/agent.py` is an optional second-pass
  classifier: ambiguous snippets that pass the regex heuristics get a
  yes/no + structured extraction from a Strands agent running on Bedrock.
  Enable with `STRANDS_ENABLED=true` + AWS credentials.

Every one of these is **optional and gracefully no-ops without credentials**
— the aggregator runs fully on regex heuristics + SQLite out of the box, so
it's always demoable regardless of what's been redeemed. Install them with
`pip install -e ".[cognee,strands]"` (or `".[all]"` for both).

## How detection works

`extract/code_detector.py` scores each text snippet on:

- a `$NN in credits` phrase (not just any dollar amount — avoids flagging
  generic prize pools), including shorthand like `$5k`
- code keywords (`promo code`, `redeem code`, `coupon`, `voucher`, …) plus an
  attempt to pull the literal code token out (`code: cognee50`)
- QR-code language (`scan the QR code`, …)

...and excludes anything that looks like a prize/reward rather than an
upfront giveaway: explicit language (`prize pool`, `compete for`, `for the
winners`, `1st place`, …), and independently, a sanity-check on amount size -
nobody hands out $500+ in free credits just for showing up, so a bare dollar
amount above that without a literal code to prove otherwise is assumed to be
a prize, not a giveaway.

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
  extract/       - regex heuristics + optional Strands classifier + QR decode
  memory/        - optional Cognee knowledge-graph enrichment
  feed_store.py  - SQLite store + global near-dup filtering + SSE pub/sub
  poller.py      - background loop tying it all together
  main.py        - FastAPI app: /api/feed, /api/stream (SSE), static UI
  cli.py         - `sponsor-credit-feed` console entrypoint
  static/        - live feed frontend (vanilla JS, EventSource)
```

## Known limitations

- Cerebral Valley and lablab.ai are scraped as server-rendered HTML text
  only — content injected client-side after hydration won't be seen (no
  headless browser in the loop). Add one (e.g. Bright Data's Scraping
  Browser) if that turns out to matter.
- No platform here exposes a public "past events" archive API, so historical
  coverage is opportunistic (whatever a listing/calendar page currently
  links to) rather than a true date-range query - an event that's fully
  scrolled off every listing page won't be found.
- Source URL lists are hardcoded in `main.py` — add more event/listing URLs
  there as you find them.
