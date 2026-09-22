from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from sponsor_credit_feed.feed_store import store
from sponsor_credit_feed.poller import run_forever
from sponsor_credit_feed.sources.luma_source import LumaSource
from sponsor_credit_feed.sources.generic_html_source import GenericHtmlSource
from sponsor_credit_feed.sources.hub_source import HubSource
from sponsor_credit_feed.sources.plain_text_source import PlainTextSource

LUMA_EVENT_URLS = [
    "https://luma.com/sep-21-cognee-aws?tk=2ES3hx",
]
CEREBRAL_VALLEY_URLS = [
    "https://cerebralvalley.ai/",
    "https://cerebralvalley.ai/events",
]
LABLAB_URLS = [
    "https://lablab.ai/event",
]
# Cerebral Valley's own curated, continuously-updated plain-text inventory
# of events (built for LLM consumption) - covers weeks of events, including
# already-concluded ones, in one lightweight fetch instead of crawling.
CEREBRAL_VALLEY_LLMS_URLS = [
    "https://cerebralvalley.ai/llms-full.txt",
]

SOURCES = [
    LumaSource(LUMA_EVENT_URLS),
    GenericHtmlSource("cerebral_valley", CEREBRAL_VALLEY_URLS),
    GenericHtmlSource("lablab", LABLAB_URLS),
    # cerebralvalley.ai/events and lablab.ai/event are hubs that link out to
    # dozens of individual hackathons (their own + Luma-hosted ones) - crawl
    # those links too instead of only the one explicitly seeded event each.
    HubSource("cerebral_valley", CEREBRAL_VALLEY_URLS),
    HubSource("lablab", LABLAB_URLS),
    PlainTextSource("cerebral_valley", CEREBRAL_VALLEY_LLMS_URLS),
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_forever(SOURCES))
    yield
    task.cancel()


app = FastAPI(title="Sponsor Credit Feed", lifespan=lifespan)


@app.get("/api/feed")
async def get_feed(limit: int = 200):
    return store.recent(limit=limit)


@app.get("/api/stream")
async def stream():
    async def event_gen():
        q = store.subscribe()
        try:
            for item in store.recent(limit=50)[::-1]:
                yield f"data: {json.dumps(item)}\n\n"
            while True:
                item = await q.get()
                yield f"data: {json.dumps(item.to_dict())}\n\n"
        finally:
            store.unsubscribe(q)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
