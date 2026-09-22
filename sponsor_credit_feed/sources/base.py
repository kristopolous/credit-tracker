from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from sponsor_credit_feed import config

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Global, per-host request throttle. This app crawls a lot of pages on the
# same few hosts (recursively following Luma calendars in particular) -
# without this, concurrent fetches can burst dozens of requests to the same
# host in a couple seconds and trip their rate limiter, which affects
# *everyone* on the network hitting that host, not just this app. Every
# outbound request in this codebase should go through `throttle()` first.
MIN_INTERVAL_BY_HOST = {
    "luma.com": 1.5,
    "lu.ma": 1.5,
}
DEFAULT_MIN_INTERVAL = 0.5

_last_request_at: dict[str, float] = {}
_host_locks: dict[str, asyncio.Lock] = {}


async def throttle(url: str) -> None:
    host = urlparse(url).netloc.lower()
    lock = _host_locks.setdefault(host, asyncio.Lock())
    async with lock:
        min_interval = MIN_INTERVAL_BY_HOST.get(host, DEFAULT_MIN_INTERVAL)
        wait = min_interval - (time.monotonic() - _last_request_at.get(host, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at[host] = time.monotonic()


@dataclass
class Page:
    url: str
    html: str


# Luma always goes direct, never through Bright Data. Direct requests work
# fine against Luma (plain HTML, no bot-wall), and Bright Data's Web
# Unlocker zones use a shared IP pool - if any other customer's traffic (or
# an earlier burst of ours) trips Cloudflare's bot challenge for that zone
# on luma.com, every subsequent request through it silently gets a JS
# challenge page back instead of real content until that cools down.
_DIRECT_ONLY_HOSTS = ("luma.com", "lu.ma")


async def fetch(client: httpx.AsyncClient, url: str) -> Page | None:
    """Fetch a URL, routing through Bright Data's Web Unlocker if configured."""
    await throttle(url)
    host = urlparse(url).netloc.lower()
    is_direct_only_host = any(host == h or host.endswith("." + h) for h in _DIRECT_ONLY_HOSTS)
    # Plain-text endpoints (llms.txt/llms-full.txt - a growing convention
    # for LLM-friendly content dumps) don't need bot-bypass: no anti-bot
    # HTML challenge is relevant for a static text file, and routing large
    # ones through Web Unlocker measured 4-18s+ per fetch here vs. a
    # near-instant direct GET, occasionally exceeding the request timeout
    # entirely and silently dropping the whole source for that poll.
    is_plain_text = urlparse(url).path.endswith(".txt")
    use_unlocker = bool(config.BRIGHTDATA_API_KEY) and not is_direct_only_host and not is_plain_text
    try:
        if use_unlocker:
            resp = await client.post(
                "https://api.brightdata.com/request",
                headers={"Authorization": f"Bearer {config.BRIGHTDATA_API_KEY}"},
                json={"zone": config.BRIGHTDATA_ZONE, "url": url, "format": "raw"},
                timeout=30,
            )
        else:
            resp = await client.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=15, follow_redirects=True
            )
        resp.raise_for_status()
        return Page(url=url, html=resp.text)
    except Exception:
        return None


# Some listing/hub pages (cerebralvalley.ai/events, lablab.ai/event) render
# their actual event cards client-side after load - a plain GET only sees
# the pre-hydration shell (a handful of links, if any). Lightpanda (a real
# JS-executing browser engine, github.com/lightpanda-io/browser) runs the
# page and dumps the DOM *after* hydration, so the full list of event links
# these pages exist to publish is actually visible. Individual event pages
# on these same platforms are already server-rendered (the data is right
# there in a __NEXT_DATA__ blob) and don't need this - only the listing
# pages themselves do, so this is used narrowly, not as a blanket replacement
# for the plain-httpx `fetch()` above.
_LIGHTPANDA_BIN = shutil.which("lightpanda")
# 8000ms was enough for the plain /events page but too short for
# /events?startDate=... (that variant's event list hadn't finished
# hydrating yet at 8s in testing, but reliably had by 15s) - use one
# conservative wait for both rather than special-casing by URL shape.
LIGHTPANDA_WAIT_MS = 15000
LIGHTPANDA_TIMEOUT = 40


async def fetch_rendered(url: str) -> Page | None:
    """Fetch a URL through lightpanda (headless, JS-executing) and return the
    HTML after client-side hydration. Falls back to None if lightpanda isn't
    installed or the fetch fails - callers should have a plain-fetch
    fallback path for that case."""
    if not _LIGHTPANDA_BIN:
        return None
    await throttle(url)
    try:
        proc = await asyncio.create_subprocess_exec(
            _LIGHTPANDA_BIN,
            "fetch",
            url,
            "--dump",
            "html",
            "--wait-until",
            "networkidle",
            "--wait-ms",
            str(LIGHTPANDA_WAIT_MS),
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=LIGHTPANDA_TIMEOUT)
        data = json.loads(stdout)
        if data.get("error") or not data.get("content"):
            return None
        return Page(url=url, html=data["content"])
    except Exception:
        return None


class Source:
    name: str = "base"

    async def poll(self, client: httpx.AsyncClient) -> list[dict]:
        """Return a list of {source_url, text} blobs to run detection over."""
        raise NotImplementedError


LUMA_HOST_RE = re.compile(r"(^|\.)(luma\.com|lu\.ma)$", re.I)


def platform_of(url: str) -> str | None:
    """Tag a URL by the actual event platform it belongs to, or None if it
    isn't one of the three tracked platforms (e.g. a *redemption* link like
    brightdata.com or platform.cognee.ai - useful to know, but not itself
    an event platform to file something under). Used to tag each finding
    by where it really lives - e.g. a Luma event mentioned inside Cerebral
    Valley's curated llms-full.txt, or discovered via Cerebral Valley's
    events hub, still files under "luma"."""
    host = urlparse(url).netloc.lower()
    if LUMA_HOST_RE.search(host):
        return "luma"
    if "cerebralvalley.ai" in host:
        return "cerebral_valley"
    if "lablab.ai" in host:
        return "lablab"
    return None
