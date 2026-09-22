"""Tracks what the crawler actually did on its most recent (and in-progress)
poll cycle, so the frontend can show real numbers - "checked 80 pages across
3 platforms 4m ago" - instead of just a "live" dot that proves nothing was
ever actually looked at.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class SourceRunStats:
    label: str
    blobs_checked: int = 0
    candidates_found: int = 0
    error: str | None = None
    finished_at: float | None = None


class CrawlStatus:
    def __init__(self) -> None:
        self.in_progress = False
        self.cycle_started_at: float | None = None
        self.last_completed_at: float | None = None
        # Sources fill in as they finish during the in-progress cycle -
        # shown live so "currently checking" is visibly true, not just
        # claimed. Kept separate from last_sources so the display doesn't
        # flash to empty the instant a new cycle starts.
        self.current_sources: dict[str, SourceRunStats] = {}
        self.last_sources: dict[str, SourceRunStats] = {}

    def start_cycle(self) -> None:
        self.in_progress = True
        self.cycle_started_at = time.time()
        self.current_sources = {}

    def record_source(
        self,
        label: str,
        blobs_checked: int,
        candidates_found: int,
        error: str | None = None,
    ) -> None:
        self.current_sources[label] = SourceRunStats(
            label=label,
            blobs_checked=blobs_checked,
            candidates_found=candidates_found,
            error=error,
            finished_at=time.time(),
        )

    def finish_cycle(self) -> None:
        self.in_progress = False
        self.last_completed_at = time.time()
        self.last_sources = dict(self.current_sources)

    def to_dict(self) -> dict:
        # While a cycle is running, show it filling in live; in the gap
        # right after a new cycle starts (before any source has reported
        # back yet), fall back to the previous cycle's numbers rather than
        # blanking out.
        sources = self.current_sources or self.last_sources
        ordered = sorted(sources.values(), key=lambda s: s.label)
        return {
            "in_progress": self.in_progress,
            "cycle_started_at": self.cycle_started_at,
            "last_completed_at": self.last_completed_at,
            "total_checked": sum(s.blobs_checked for s in ordered),
            "total_candidates": sum(s.candidates_found for s in ordered),
            "sources_done": len(self.current_sources) if self.in_progress else len(ordered),
            "sources_total": None,  # filled in by caller, which knows the source list
            "sources": [
                {
                    "label": s.label,
                    "blobs_checked": s.blobs_checked,
                    "candidates_found": s.candidates_found,
                    "error": s.error,
                }
                for s in ordered
            ],
        }


status = CrawlStatus()
