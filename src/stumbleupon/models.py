"""Dataclass models for sites, clips, sounds, and postings.

These mirror the SQLite schema in `db.py`. The DB returns dicts; the queue
layer converts dicts into these dataclasses. Status fields are validated
against known sets at construction time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


SITE_STATUSES = frozenset({"fresh", "recorded", "failed", "skipped"})
CLIP_STATUSES = frozenset(
    {"pending", "needs_attention", "approved", "rejected", "posted", "failed"}
)
POSTING_STATUSES = frozenset({"queued", "posted", "failed"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Site:
    id: int | None
    url: str
    title: str | None = None
    description: str | None = None
    source: str = "stumbleupon.cc"
    discovered_at: datetime = field(default_factory=_utcnow)
    last_attempted: datetime | None = None
    status: str = "fresh"
    skip_reason: str | None = None
    tags: str | None = None

    def __post_init__(self) -> None:
        if self.status not in SITE_STATUSES:
            raise ValueError(f"unknown site status: {self.status!r}")


@dataclass
class Clip:
    id: int | None
    site_id: int
    recording_path: str | None = None
    final_path: str | None = None
    r2_public_url: str | None = None
    caption: str | None = None
    hashtags: str | None = None
    sound_id: int | None = None
    duration_sec: float | None = None
    created_at: datetime = field(default_factory=_utcnow)
    last_attempted: datetime | None = None
    status: str = "pending"
    review_notes: str | None = None
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    edited_caption: str | None = None
    scheduled_for: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in CLIP_STATUSES:
            raise ValueError(f"unknown clip status: {self.status!r}")

    @property
    def effective_caption(self) -> str | None:
        """Prefer human-edited caption; fall back to the LLM-generated one."""
        return self.edited_caption or self.caption


@dataclass
class Sound:
    id: int | None
    tiktok_sound_id: str
    title: str | None = None
    artist: str | None = None
    audio_path: str | None = None
    trending_score: float = 0.0
    fetched_at: datetime = field(default_factory=_utcnow)
    last_used_at: datetime | None = None


@dataclass
class Posting:
    id: int | None
    clip_id: int
    platform: str
    external_id: str | None = None
    external_url: str | None = None
    status: str = "queued"
    error: str | None = None
    posted_at: datetime | None = None
    scheduled_for: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in POSTING_STATUSES:
            raise ValueError(f"unknown posting status: {self.status!r}")
