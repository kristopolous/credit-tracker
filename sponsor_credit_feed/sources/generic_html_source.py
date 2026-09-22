"""Generic fallback source: fetch a list of pages, strip to visible text.

Used for sites without a convenient structured JSON endpoint (Cerebral
Valley, lablab.ai). Tries lightpanda (real JS execution) first so text
injected client-side after hydration is visible too, falling back to a
plain fetch (server-rendered content only) if lightpanda isn't available.
"""
from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from sponsor_credit_feed.sources.base import Source, fetch, fetch_rendered
from sponsor_credit_feed.sources.link_discovery import follow_resource_links


class GenericHtmlSource(Source):
    def __init__(self, name: str, urls: list[str]):
        self.name = name
        self.urls = urls

    async def poll(self, client: httpx.AsyncClient) -> list[dict]:
        out = []
        for url in self.urls:
            page = await fetch_rendered(url) or await fetch(client, url)
            if not page:
                continue
            soup = BeautifulSoup(page.html, "lxml")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text("\n")
            if not text.strip():
                continue
            out.append({"source_url": url, "text": text})
            out.extend(await follow_resource_links(client, text))
        return out
