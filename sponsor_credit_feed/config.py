import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


BRIGHTDATA_API_KEY = os.getenv("BRIGHTDATA_API_KEY", "").strip()
BRIGHTDATA_ZONE = os.getenv("BRIGHTDATA_ZONE", "web_unlocker1").strip()

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "5400"))

DB_PATH = os.getenv("FEED_DB_PATH", "feed.db")
