"""Optional Cognee-backed memory layer.

Cognee builds a knowledge graph over everything we ingest, which we use for
two things the SQLite feed store can't do well on its own:
  1. Semantic dedup - the same $50 Cognee credit code gets reposted across
     Luma, a Discord mirror on the CV page, etc. String matching misses
     paraphrases; Cognee's graph search catches them.
  2. Ask-a-question over the feed - "what sponsor credits are still
     available right now" answered by cognee.search instead of us writing
     bespoke SQL.

Supports both of Cognee's auth modes (see app/config.py): Cloud mode
(COGNEE_API_KEY + COGNEE_SERVICE_URL) uses cognee.serve()/remember()/recall();
local mode (LLM_API_KEY only) uses cognee.add()/cognify()/search() against
your own LLM key. If COGNEE_ENABLED is false, every function here is a no-op
and the feed just runs on plain SQLite dedup.
"""
from __future__ import annotations

from sponsor_credit_feed import config

_ready = False


async def _ensure_ready():
    global _ready
    if _ready or not config.COGNEE_ENABLED:
        return
    import cognee

    if config.COGNEE_CLOUD:
        await cognee.serve(url=config.COGNEE_SERVICE_URL, api_key=config.COGNEE_API_KEY)
    else:
        await cognee.prune.prune_data()
    _ready = True


async def remember(text: str) -> None:
    if not config.COGNEE_ENABLED:
        return
    try:
        import cognee

        await _ensure_ready()
        if config.COGNEE_CLOUD:
            await cognee.remember(text, dataset_name="sponsor_codes")
        else:
            await cognee.add(text, dataset_name="sponsor_codes")
            await cognee.cognify(["sponsor_codes"])
    except Exception:
        # Memory enrichment is best-effort; never block the live feed on it.
        pass


async def is_duplicate(text: str) -> bool:
    if not config.COGNEE_ENABLED:
        return False
    try:
        import cognee

        await _ensure_ready()
        if config.COGNEE_CLOUD:
            results = await cognee.recall(query_text=text, dataset_name="sponsor_codes")
            texts = [str(r) for r in (results or [])]
        else:
            from cognee import SearchType

            results = await cognee.search(
                query_text=text, query_type=SearchType.CHUNKS, datasets=["sponsor_codes"]
            )
            texts = [r.get("text", "") for r in results if isinstance(r, dict)]
        return any(_similar(text, t) for t in texts)
    except Exception:
        return False


def _similar(a: str, b: str, threshold: float = 0.85) -> bool:
    import difflib

    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= threshold


async def ask(question: str) -> str | None:
    if not config.COGNEE_ENABLED:
        return None
    try:
        import cognee

        await _ensure_ready()
        if config.COGNEE_CLOUD:
            results = await cognee.recall(query_text=question, dataset_name="sponsor_codes")
            return str(results[0]) if results else None

        from cognee import SearchType

        results = await cognee.search(
            query_text=question, query_type=SearchType.GRAPH_COMPLETION, datasets=["sponsor_codes"]
        )
        return str(results[0]) if results else None
    except Exception:
        return None
