"""Optional AWS Strands agent that adjudicates ambiguous candidates.

The regex heuristics in code_detector.py already do most of the work for
free. This agent is a second-pass filter for candidates that scored
positively but are ambiguous, asking a model to say yes/no and pull out a
clean structured record. If STRANDS_ENABLED is false (no AWS creds), this
module is never imported by the poller and heuristics decide alone.
"""
from __future__ import annotations

from sponsor_credit_feed import config

_agent = None


def _get_agent():
    global _agent
    if _agent is not None:
        return _agent
    from strands import Agent  # imported lazily; optional dependency

    _agent = Agent(
        model=f"bedrock/anthropic.claude-3-5-haiku-20241022-v1:0",
        system_prompt=(
            "You review short text snippets scraped from hackathon event "
            "pages. Decide if the snippet announces a real, redeemable "
            "sponsor credit code or QR code (not just a mention of a "
            "sponsor, or a generic prize amount with no code). "
            "Reply with strict JSON: "
            '{"is_redeemable": bool, "sponsor": str|null, "code": str|null, '
            '"amount": str|null, "confidence": float}'
        ),
    )
    return _agent


def classify(snippet: str) -> dict | None:
    """Return a structured verdict, or None if the agent can't run."""
    if not config.STRANDS_ENABLED:
        return None
    try:
        agent = _get_agent()
        result = agent(snippet)
        import json

        text = str(result)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return None
        return json.loads(text[start : end + 1])
    except Exception:
        return None
