"""Console entrypoint: `sponsor-credit-feed` (after `pip install`)."""
from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sponsor-credit-feed",
        description="Live feed of hackathon sponsor credit codes across Luma, Cerebral Valley, and lablab.ai.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev only)")
    args = parser.parse_args()

    uvicorn.run(
        "sponsor_credit_feed.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
