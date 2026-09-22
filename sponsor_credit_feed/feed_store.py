from __future__ import annotations

import asyncio
import difflib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, asdict

from sponsor_credit_feed import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_items (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    codes TEXT NOT NULL,
    amounts TEXT NOT NULL,
    sponsors TEXT NOT NULL,
    score REAL NOT NULL,
    discovered_at REAL NOT NULL,
    redeem_url TEXT,
    service TEXT,
    event_url TEXT
);
CREATE TABLE IF NOT EXISTS events (
    event_url TEXT PRIMARY KEY,
    title TEXT NOT NULL
);
"""


@dataclass
class FeedItem:
    id: str
    source: str
    source_url: str
    kind: str
    text: str
    codes: list[str]
    amounts: list[str]
    sponsors: list[str]
    score: float
    discovered_at: float
    redeem_url: str | None = None
    service: str | None = None
    event_url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class FeedStore:
    def __init__(self, path: str = config.DB_PATH):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        for ddl in (
            "ALTER TABLE feed_items ADD COLUMN redeem_url TEXT",
            "ALTER TABLE feed_items ADD COLUMN service TEXT",
            "ALTER TABLE feed_items ADD COLUMN event_url TEXT",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # already migrated
        self._conn.commit()
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _publish(self, item: FeedItem) -> None:
        for q in list(self._subscribers):
            q.put_nowait(item)

    def set_event_title(self, event_url: str, title: str) -> None:
        """Record the real name of an event, independent of any individual
        deal row - a resource doc or a redemption-service link that later
        upgrades a deal in place never has a title of its own to offer, and
        must not be able to clobber a real title captured elsewhere."""
        self._conn.execute(
            "INSERT INTO events (event_url, title) VALUES (?, ?) "
            "ON CONFLICT(event_url) DO UPDATE SET title = excluded.title",
            (event_url, title),
        )
        self._conn.commit()

    def recent(self, limit: int = 200) -> list[dict]:
        cur = self._conn.execute(
            "SELECT f.id, f.source, f.source_url, f.kind, f.text, f.codes, f.amounts, "
            "f.sponsors, f.score, f.discovered_at, f.redeem_url, f.service, f.event_url, "
            "e.title FROM feed_items f LEFT JOIN events e ON e.event_url = f.event_url "
            "ORDER BY f.discovered_at DESC LIMIT ?",
            (limit,),
        )
        out = []
        for row in cur.fetchall():
            out.append(
                {
                    "id": row[0],
                    "source": row[1],
                    "source_url": row[2],
                    "kind": row[3],
                    "text": row[4],
                    "codes": json.loads(row[5]),
                    "amounts": json.loads(row[6]),
                    "sponsors": json.loads(row[7]),
                    "score": row[8],
                    "discovered_at": row[9],
                    "redeem_url": row[10],
                    "service": row[11],
                    "event_url": row[12] or row[2],
                    "event_title": row[13],
                }
            )
        return out

    def _is_near_duplicate(self, text: str, codes: list[str]) -> bool:
        """Dedup globally, not per-source: the same code/credit discovered
        via both a direct event page and a hub crawl of another platform is
        still the same underlying credit, not two distinct finds."""
        codes_lower = {c.lower() for c in codes}
        cur = self._conn.execute(
            "SELECT text, codes FROM feed_items ORDER BY discovered_at DESC LIMIT 300"
        )
        for existing_text, existing_codes_json in cur.fetchall():
            if codes_lower:
                existing_codes = {c.lower() for c in json.loads(existing_codes_json)}
                if codes_lower & existing_codes:
                    return True
            if difflib.SequenceMatcher(None, text.lower(), existing_text.lower()).ratio() > 0.9:
                return True
        return False

    def _find_same_deal(
        self, event_url: str, service: str | None, amounts: list[str]
    ) -> tuple[str, bool] | None:
        """The same $50 Cognee credit often gets described 3 different ways
        across one event's own copy ("$50 of Cognee Cloud Credits", "$100
        free credits ($50 from Cognee...)", "$125 in Credits for everyone
        (Cognee + ...)") - none of those share enough text to trip
        _is_near_duplicate, but they're the same underlying deal, not 3
        separate ones. Matches on (event, service, amount) instead. Returns
        (existing_id, existing_has_code) so the caller can decide whether
        to keep the existing row or upgrade it in place."""
        if not service or not amounts:
            return None
        amounts_key = frozenset(a.lower() for a in amounts)
        cur = self._conn.execute(
            "SELECT id, amounts, codes FROM feed_items WHERE event_url = ? AND service = ?",
            (event_url, service),
        )
        for existing_id, existing_amounts_json, existing_codes_json in cur.fetchall():
            existing_key = frozenset(a.lower() for a in json.loads(existing_amounts_json))
            if existing_key == amounts_key:
                return existing_id, bool(json.loads(existing_codes_json))
        return None

    def add(
        self,
        *,
        source: str,
        source_url: str,
        kind: str,
        text: str,
        codes: list[str],
        amounts: list[str],
        sponsors: list[str],
        score: float,
        redeem_url: str | None = None,
        service: str | None = None,
        event_url: str | None = None,
    ) -> FeedItem | None:
        if self._is_near_duplicate(text, codes):
            return None

        resolved_event_url = event_url or source_url
        same_deal = self._find_same_deal(resolved_event_url, service, amounts)
        if same_deal:
            existing_id, existing_has_code = same_deal
            if existing_has_code or not codes:
                # Existing description is as good or better - this one adds
                # nothing new, skip it.
                return None
            # This mention has the literal code the existing one lacked -
            # upgrade that row in place (same id) rather than adding a
            # second, better entry for the same deal.
            item_id = existing_id
        else:
            item_id = str(uuid.uuid4())

        item = FeedItem(
            id=item_id,
            source=source,
            source_url=source_url,
            kind=kind,
            text=text,
            codes=codes,
            amounts=amounts,
            sponsors=sponsors,
            score=score,
            discovered_at=time.time(),
            redeem_url=redeem_url,
            service=service,
            event_url=resolved_event_url,
        )
        if same_deal:
            self._conn.execute(
                "UPDATE feed_items SET source=?, source_url=?, kind=?, text=?, codes=?, "
                "amounts=?, sponsors=?, score=?, discovered_at=?, redeem_url=?, service=?, "
                "event_url=? WHERE id=?",
                (
                    item.source,
                    item.source_url,
                    item.kind,
                    item.text,
                    json.dumps(item.codes),
                    json.dumps(item.amounts),
                    json.dumps(item.sponsors),
                    item.score,
                    item.discovered_at,
                    item.redeem_url,
                    item.service,
                    item.event_url,
                    item.id,
                ),
            )
        else:
            self._conn.execute(
                "INSERT INTO feed_items (id, source, source_url, kind, text, codes, amounts, "
                "sponsors, score, discovered_at, redeem_url, service, event_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.id,
                    item.source,
                    item.source_url,
                    item.kind,
                    item.text,
                    json.dumps(item.codes),
                    json.dumps(item.amounts),
                    json.dumps(item.sponsors),
                    item.score,
                    item.discovered_at,
                    item.redeem_url,
                    item.service,
                    item.event_url,
                ),
            )
        self._conn.commit()
        self._publish(item)
        return item


store = FeedStore()
