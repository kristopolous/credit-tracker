"""Luma event source.

Luma event pages (Next.js) embed the full event record as JSON in a
<script id="__NEXT_DATA__"> tag - including the rich-text description
(ProseMirror doc), FAQs, and a `coupon` field. Parsing that JSON is far more
reliable than scraping rendered DOM text.
"""
from __future__ import annotations

import json
import re

import httpx
from bs4 import BeautifulSoup

from sponsor_credit_feed.sources.base import Source, fetch
from sponsor_credit_feed.sources.link_discovery import follow_resource_links

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


def _walk_doc_text(node) -> list[str]:
    """Flatten a ProseMirror-style rich text doc into paragraph strings.

    Also pulls out link-mark hrefs (e.g. a "Hackathon Resources" link whose
    visible text doesn't include the URL itself), so link discovery can
    follow them even when the URL isn't in the visible text.
    """
    texts: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "text" and "text" in node:
            texts.append(node["text"])
            for mark in node.get("marks", []) or []:
                if mark.get("type") == "link":
                    href = (mark.get("attrs") or {}).get("href")
                    if href:
                        texts.append(href)
        for child in node.get("content", []) or []:
            texts.extend(_walk_doc_text(child))
        if node.get("type") in ("paragraph", "heading", "list_item"):
            texts.append("\n")
    elif isinstance(node, list):
        for child in node:
            texts.extend(_walk_doc_text(child))
    return texts


def extract_luma_title(html: str) -> str | None:
    """Pull the event's real name out of a Luma event page's __NEXT_DATA__
    JSON - e.g. "Battle of the Personal Brains Hackathon", not a guess
    derived from the URL slug ("sep-21-cognee-aws")."""
    m = NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        event = data["props"]["pageProps"]["initialData"]["data"]
        name = (event.get("event") or {}).get("name")
        return str(name) if name else None
    except Exception:
        return None


def extract_luma_text(html: str) -> str:
    """Pull the event title/description/coupon/FAQs out of a Luma event
    page's __NEXT_DATA__ JSON blob. Shared by LumaSource (explicit seed
    URLs) and HubSource (Luma links discovered on other listing pages)."""
    m = NEXT_DATA_RE.search(html)
    if not m:
        # Fall back to visible page text if Luma changes their build.
        return BeautifulSoup(html, "lxml").get_text("\n")

    try:
        data = json.loads(m.group(1))
        event = data["props"]["pageProps"]["initialData"]["data"]
    except Exception:
        return BeautifulSoup(html, "lxml").get_text("\n")

    chunks: list[str] = []

    title = (event.get("event") or {}).get("name")
    if title:
        chunks.append(str(title))

    coupon = event.get("coupon")
    if coupon:
        chunks.append(f"Coupon: {json.dumps(coupon)}")

    desc = event.get("description_mirror")
    if desc:
        chunks.extend(_walk_doc_text(desc))

    faqs = event.get("faqs")
    if faqs:
        chunks.append(json.dumps(faqs))

    for info in event.get("featured_infos") or []:
        if isinstance(info, dict) and info.get("text"):
            chunks.append(str(info["text"]))

    return "\n".join(c for c in chunks if c)


def extract_luma_calendar_slug(html: str) -> str | None:
    """From a Luma *event* page's JSON, pull the host organizer's calendar
    slug (e.g. "BrightData"). Used to recursively discover more events: an
    organizer's calendar page (luma.com/<slug>) usually lists many more of
    their events beyond the one we started from."""
    m = NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        event = data["props"]["pageProps"]["initialData"]["data"]
        return event["calendar"]["slug"]
    except Exception:
        return None


def extract_event_urls_from_calendar(html: str) -> list[str]:
    """From a Luma *calendar* page's JSON, pull every listed event's URL."""
    m = NEXT_DATA_RE.search(html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
        cal_data = data["props"]["pageProps"]["initialData"]["data"]
    except Exception:
        return []

    urls: set[str] = set()
    for key in ("upcoming", "past"):
        section = cal_data.get(key) or {}
        for entry in section.get("entries", []) or []:
            slug = (entry.get("event") or {}).get("url")
            if slug:
                urls.add(f"https://luma.com/{slug}")
    for entry in cal_data.get("featured_items", []) or []:
        slug = (entry.get("event") or {}).get("url")
        if slug:
            urls.add(f"https://luma.com/{slug}")
    return list(urls)


class LumaSource(Source):
    name = "luma"

    def __init__(self, event_urls: list[str]):
        self.event_urls = event_urls

    async def poll(self, client: httpx.AsyncClient) -> list[dict]:
        out = []
        for url in self.event_urls:
            page = await fetch(client, url)
            if not page:
                continue
            text = extract_luma_text(page.html)
            if not text:
                continue
            title = extract_luma_title(page.html)
            blob = {"source_url": url, "text": text}
            if title:
                blob["title"] = title
            out.append(blob)
            out.extend(await follow_resource_links(client, text, event_url=url))
        return out
