"""Crawl listing/hub pages that link out to many individual hackathon events.

Rather than reverse-engineer each platform's private "past events" archive
API (fragile, and most don't expose one publicly), this scrapes pages that
already aggregate a wide spread of current and recent AI hackathons -
cerebralvalley.ai/events links out to dozens of Luma events, its own hosted
events, and lablab.ai hackathons in one page; lablab.ai/event links to its
own catalog. Every discovered event link gets run through the same
extraction (+ resource-doc link-following) as an explicitly seeded event.

For more breadth, this also does one level of recursive discovery on Luma:
every Luma event page names its host's calendar (e.g. "BrightData"), and
that calendar's own page (luma.com/BrightData) lists more of that
organizer's events. So round 1 harvests event links from the listing pages,
and round 2 harvests more event links from the calendars those round-1
events belong to - all without guessing any URL, since every calendar slug
comes from a real API response.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from sponsor_credit_feed.sources.base import Source, fetch, fetch_rendered, platform_of, throttle
from sponsor_credit_feed.sources.link_discovery import follow_resource_links
from sponsor_credit_feed.sources.luma_source import (
    extract_event_urls_from_calendar,
    extract_luma_calendar_slug,
    extract_luma_text,
    extract_luma_title,
)

LUMA_HOST_RE = re.compile(r"(^|\.)(luma\.com|lu\.ma)$", re.I)

# Only follow links whose path looks like an individual event page (not
# nav/footer/social chrome) on hosts known to run hackathons.
EVENT_LINK_HOST_RE = re.compile(
    r"(luma\.com|lu\.ma|cerebralvalley\.ai|lablab\.ai)$", re.I
)
EVENT_LINK_PATH_RE = re.compile(r"^/(e|event|ai-hackathons)/", re.I)

# Kept well below what any of these sites would consider abusive: every
# request also goes through base.throttle(), which serializes requests to
# the same host with a minimum gap (1.5s for Luma) - these caps just bound
# the total work per poll on top of that.
MAX_EVENTS_PER_POLL = 60
MAX_CALENDAR_PAGES = 10
MAX_CONCURRENT_FETCHES = 4
REQUEST_TIMEOUT = 12


def _looks_like_event_link(url: str) -> bool:
    p = urlparse(url)
    if not EVENT_LINK_HOST_RE.search(p.netloc):
        return False
    # Luma event pages are just /<slug> at the root (no distinguishing
    # prefix) - accept any root-level path on a luma host as a candidate.
    if LUMA_HOST_RE.search(p.netloc):
        return bool(re.match(r"^/[A-Za-z0-9_-]{3,}$", p.path))
    return bool(EVENT_LINK_PATH_RE.match(p.path))


def _normalize(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme or "https", p.netloc, p.path.rstrip("/"), "", "", ""))


class HubSource(Source):
    def __init__(self, name: str, listing_urls: list[str]):
        self.name = name
        self.listing_urls = listing_urls

    async def poll(self, client: httpx.AsyncClient) -> list[dict]:
        sem = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
        visited: set[str] = set()
        blobs: list[dict] = []

        async def fetch_html(url: str) -> str | None:
            await throttle(url)
            try:
                resp = await client.get(url, timeout=REQUEST_TIMEOUT, follow_redirects=True)
                resp.raise_for_status()
                return resp.text
            except Exception:
                return None

        async def fetch_event(url: str) -> tuple[str, str | None]:
            async with sem:
                return url, await fetch_html(url)

        async def process(urls: list[str]) -> list[str]:
            """Fetch+extract each event URL, appending accepted blobs to
            `blobs`. Returns any newly discovered Luma calendar URLs."""
            new_urls = [u for u in urls if u not in visited]
            visited.update(new_urls)
            if not new_urls:
                return []

            results = await asyncio.gather(*(fetch_event(u) for u in new_urls))
            calendar_urls: list[str] = []
            for url, html in results:
                if not html:
                    continue
                is_luma = bool(LUMA_HOST_RE.search(httpx.URL(url).host))
                title = None
                if is_luma:
                    text = extract_luma_text(html)
                    title = extract_luma_title(html)
                    slug = extract_luma_calendar_slug(html)
                    if slug:
                        calendar_urls.append(f"https://luma.com/{slug}")
                else:
                    soup = BeautifulSoup(html, "lxml")
                    if soup.title and soup.title.string:
                        title = soup.title.string.strip()
                    for tag in soup(["script", "style", "noscript"]):
                        tag.decompose()
                    text = soup.get_text("\n")

                if text and text.strip():
                    # url here always passed _looks_like_event_link, so it's
                    # always one of the three known platforms.
                    blob = {"source_url": url, "text": text, "source": platform_of(url) or "unknown"}
                    if title:
                        blob["title"] = title
                    blobs.append(blob)
            return calendar_urls

        # Round 1: harvest event links from the seeded listing pages. These
        # listing pages (cerebralvalley.ai/events, lablab.ai/event) populate
        # their actual event cards client-side after load, so a plain GET
        # only sees a handful of links in the pre-hydration shell - try
        # lightpanda (real JS execution) first to see the fully hydrated
        # list, and fall back to the old plain-fetch path if lightpanda
        # isn't installed or the fetch fails for some reason.
        discovered: dict[str, None] = {}
        for listing_url in self.listing_urls:
            rendered = await fetch_rendered(listing_url)
            html = rendered.html if rendered else await fetch_html_via_unlocker(client, listing_url)
            if not html:
                continue
            soup = BeautifulSoup(html, "lxml")
            for a in soup.find_all("a", href=True):
                href = urljoin(listing_url, str(a["href"]))
                if href.startswith("http") and _looks_like_event_link(href):
                    discovered[_normalize(href)] = None

        round1_urls = list(discovered)[:MAX_EVENTS_PER_POLL]
        calendar_candidates = await process(round1_urls)

        # Round 2: for organizer calendars surfaced by round-1 Luma events,
        # harvest more of that organizer's events - real breadth, no
        # guessed URLs, everything sourced from an actual API response.
        remaining = MAX_EVENTS_PER_POLL - len(visited)
        if remaining > 0 and calendar_candidates:
            cal_urls = list(dict.fromkeys(calendar_candidates))[:MAX_CALENDAR_PAGES]
            cal_pages = await asyncio.gather(*(fetch_html(u) for u in cal_urls))
            more_events: dict[str, None] = {}
            for html in cal_pages:
                if not html:
                    continue
                for u in extract_event_urls_from_calendar(html):
                    more_events[_normalize(u)] = None
            round2_urls = [u for u in more_events if u not in visited][:remaining]
            await process(round2_urls)

        out: list[dict] = list(blobs)
        for r in blobs:
            out.extend(await follow_resource_links(client, r["text"], event_url=r["source_url"]))
        return out


async def fetch_html_via_unlocker(client: httpx.AsyncClient, url: str) -> str | None:
    page = await fetch(client, url)
    return page.html if page else None
