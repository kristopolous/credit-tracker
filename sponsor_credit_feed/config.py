import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


BRIGHTDATA_API_KEY = os.getenv("BRIGHTDATA_API_KEY", "").strip()
BRIGHTDATA_ZONE = os.getenv("BRIGHTDATA_ZONE", "web_unlocker1").strip()

LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
COGNEE_API_KEY = os.getenv("COGNEE_API_KEY", "").strip()
COGNEE_SERVICE_URL = os.getenv("COGNEE_SERVICE_URL", "").strip()
COGNEE_CLOUD = bool(COGNEE_API_KEY and COGNEE_SERVICE_URL)
COGNEE_ENABLED = _bool("COGNEE_ENABLED") and (COGNEE_CLOUD or bool(LLM_API_KEY))

AWS_REGION = os.getenv("AWS_REGION", "us-west-2").strip()
STRANDS_ENABLED = _bool("STRANDS_ENABLED") and bool(
    os.getenv("AWS_ACCESS_KEY_ID", "").strip()
)

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "5400"))

DB_PATH = os.getenv("FEED_DB_PATH", "feed.db")
