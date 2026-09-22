from __future__ import annotations

import asyncio
import logging

import httpx

from sponsor_credit_feed import config
from sponsor_credit_feed.extract.code_detector import find_candidates
from sponsor_credit_feed.feed_store import store
from sponsor_credit_feed.sources.base import Source, platform_of

log = logging.getLogger("poller")


async def _handle_source(client: httpx.AsyncClient, source: Source) -> None:
    try:
        blobs = await source.poll(client)
    except Exception:
        log.exception("source %s failed to poll", source.name)
        return

    for blob in blobs:
        if blob.get("title"):
            store.set_event_title(blob.get("event_url", blob["source_url"]), blob["title"])

        candidates = find_candidates(blob["text"])
        for c in candidates:
            # find_candidates() already guarantees every candidate here has
            # a literal code (or QR) and a resolved company - nothing vaguer
            # than that ever reaches storage.

            # Prefer the candidate's own redeem_url for platform tagging,
            # but only when it resolves to one of the three tracked event
            # platforms - a redeem_url pointing at brightdata.com or
            # platform.cognee.ai is a *redemption* link, not the platform
            # this was found on. A blob can otherwise bundle events from
            # several platforms (e.g. Cerebral Valley's llms-full.txt
            # mentions Luma events), so the blob-level source is the
            # fallback for when there's no recognized per-candidate URL.
            item_source = (
                (c.redeem_url and platform_of(c.redeem_url)) or blob.get("source", source.name)
            )

            # Which event this deal actually belongs to, for grouping in
            # the UI - "here's an event, here are its deals" instead of one
            # card per mention. If the redeem_url itself points at a known
            # event platform, it *is* the event page (true for mentions
            # pulled out of a bundled multi-event blob like Cerebral
            # Valley's llms-full.txt). Otherwise a resource doc explicitly
            # carries the event_url of whatever page linked to it; failing
            # that, the blob's own page is the event.
            event_url = (
                (c.redeem_url if c.redeem_url and platform_of(c.redeem_url) else None)
                or blob.get("event_url")
                or blob["source_url"]
            )

            item = store.add(
                source=item_source,
                source_url=blob["source_url"],
                event_url=event_url,
                kind=c.kind,
                text=c.text,
                codes=c.codes,
                amounts=c.amounts,
                sponsors=c.sponsors,
                score=c.score,
                redeem_url=c.redeem_url,
                service=c.service,
            )


async def run_forever(sources: list[Source]) -> None:
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.gather(*(_handle_source(client, s) for s in sources))
            await asyncio.sleep(config.POLL_INTERVAL_SECONDS)
