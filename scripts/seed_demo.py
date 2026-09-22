"""Seed the feed with a few realistic demo items so the UI has content to
show immediately, independent of live polling. Safe to run repeatedly -
near-duplicate detection in FeedStore will skip re-inserting the same text.

Only redeemable sponsor *credits* are seeded here (not cash prizes) - this
tool tracks "can I actually apply this", not prize pool totals.

Usage: python3 -m scripts.seed_demo
"""
from sponsor_credit_feed.feed_store import store

DEMO_ITEMS = [
    dict(
        source="lablab",
        source_url="https://lablab.ai/event/assemblyai-voice-agent-hackathon",
        kind="code",
        text="AssemblyAI Voice Agent Hackathon: use promo code AAI-VOICE25 at "
        "checkout for $25 in AssemblyAI API credits.",
        codes=["AAI-VOICE25"],
        amounts=["$25"],
        sponsors=["assemblyai"],
        score=4.2,
        redeem_url="https://www.assemblyai.com/dashboard",
        service="AssemblyAI",
    ),
    dict(
        source="cerebral_valley",
        source_url="https://cerebralvalley.ai/events",
        kind="qr",
        text="Scan the QR code at the sponsor booth to redeem your $20 Bedrock credit voucher.",
        codes=[],
        amounts=["$20"],
        sponsors=["bedrock"],
        score=1.3,
        redeem_url=None,
        service="AWS Bedrock",
    ),
]

if __name__ == "__main__":
    added = 0
    for item in DEMO_ITEMS:
        if store.add(**item):
            added += 1
    print(f"seeded {added}/{len(DEMO_ITEMS)} demo items")
