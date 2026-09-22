"""Source for plain-text event inventories, e.g. Cerebral Valley's
llms-full.txt (https://cerebralvalley.ai/llms-full.txt) - a continuously
updated, curated text dump of their event listings, built for LLM
consumption. Unlike the HTML listing pages, this one file alone covers
weeks of events (including ones already concluded) in one lightweight
fetch, no crawling or per-event requests needed.
"""
from __future__ import annotations

import httpx

from sponsor_credit_feed.sources.base import Source, fetch


class PlainTextSource(Source):
    def __init__(self, name: str, urls: list[str]):
        self.name = name
        self.urls = urls

    async def poll(self, client: httpx.AsyncClient) -> list[dict]:
        out = []
        for url in self.urls:
            page = await fetch(client, url)
            if page and page.html.strip():
                out.append({"source_url": url, "text": page.html})
        return out
