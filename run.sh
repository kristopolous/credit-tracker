#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -e .

if [ ! -f feed.db ]; then
  python3 -m scripts.seed_demo
fi

uvicorn sponsor_credit_feed.main:app --host 0.0.0.0 --port 8000 --reload
