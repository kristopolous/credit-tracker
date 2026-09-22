"""Follow links found in scraped text to where sponsors actually post codes.

Event page descriptions rarely contain the literal redemption code - they
link out to a "hackathon resources" doc, a Discord channel, or a pinned
post where the real code lives. This module resolves shortlinks (bit.ly,
etc.) and, when a link resolves to a Google Doc, pulls its plain text via
Google's export endpoint (no auth needed for anyone-with-link docs).
"""
from __future__ import annotations

import asyncio
import re

import httpx

from sponsor_credit_feed.sources.base import throttle

URL_RE = re.compile(r"https?://[^\s)\]}\"']+", re.I)
GDOC_ID_RE = re.compile(r"docs\.google\.com/document/d/([a-zA-Z0-9_-]+)")

# Skip well-known hosts that are never going to resolve to a resources doc -
# avoids burning a request-per-link on every nav/social/footer link on a
# full landing page.
SKIP_HOST_RE = re.compile(
    r"(twitter\.com|x\.com|linkedin\.com|facebook\.com|instagram\.com|"
    r"youtube\.com|youtu\.be|github\.com|tiktok\.com|luma\.com|lu\.ma|"
    r"apple\.com|play\.google\.com|discord\.gg|discord\.com)",
    re.I,
)

MAX_LINKS_PER_POLL = 20
REQUEST_TIMEOUT = 6
MAX_CONCURRENT_RESOLVES = 4


async def _resolve_one(client: httpx.AsyncClient, url: str) -> dict | None:
    await throttle(url)
    try:
        resp = await client.get(url, timeout=REQUEST_TIMEOUT, follow_redirects=True)
        final_url = str(resp.url)
    except Exception:
        return None

    m = GDOC_ID_RE.search(final_url)
    if not m:
        return None
    doc_id = m.group(1)

    doc_url = f"https://docs.google.com/document/d/{doc_id}/"
    try:
        await throttle(doc_url)
        doc_resp = await client.get(
            f"{doc_url}export?format=txt", timeout=REQUEST_TIMEOUT, follow_redirects=True
        )
        doc_resp.raise_for_status()
        return {"source_url": doc_url, "text": doc_resp.text, "_doc_id": doc_id}
    except Exception:
        return None


async def follow_resource_links(client: httpx.AsyncClient, text: str, event_url: str | None = None) -> list[dict]:
    """Resolve links in `text`; return [{source_url, text, event_url}] for
    any that turn out to be a Google Doc, fetched as plain text. Resolves
    links concurrently (bounded) so one slow/unresponsive link doesn't
    stall the whole poll.

    `event_url` tags the returned blob(s) as belonging to the same event
    that linked to them - the doc itself isn't a separate event, it's
    tonight's resources doc, so every deal found in it should still group
    under tonight's event card rather than getting its own."""
    candidates = [u for u in URL_RE.findall(text) if not SKIP_HOST_RE.search(u)]
    candidates = list(dict.fromkeys(candidates))[:MAX_LINKS_PER_POLL]  # dedupe, keep order

    sem = asyncio.Semaphore(MAX_CONCURRENT_RESOLVES)

    async def bounded(url: str) -> dict | None:
        async with sem:
            return await _resolve_one(client, url)

    results = await asyncio.gather(*(bounded(u) for u in candidates))

    out: list[dict] = []
    seen_ids: set[str] = set()
    for r in results:
        if r is None or r["_doc_id"] in seen_ids:
            continue
        seen_ids.add(r["_doc_id"])
        blob = {"source_url": r["source_url"], "text": r["text"]}
        if event_url:
            blob["event_url"] = event_url
        out.append(blob)
    return out
