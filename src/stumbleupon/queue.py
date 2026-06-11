"""High-level DB operations on clips and postings.

This is the only module that mutates `clips.status`. Everything else
calls into here. Returns dataclasses, not dicts.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .db import get_connection
from .models import Clip, Posting


def _row_to_clip(row: sqlite3.Row) -> Clip:
    return Clip(
        id=row["id"],
        site_id=row["site_id"],
        recording_path=row["recording_path"],
        final_path=row["final_path"],
        r2_public_url=row["r2_public_url"],
        caption=row["caption"],
        hashtags=row["hashtags"],
        sound_id=row["sound_id"],
        duration_sec=row["duration_sec"],
        created_at=row["created_at"],
        status=row["status"],
        review_notes=row["review_notes"],
        reviewed_at=row["reviewed_at"],
        reviewed_by=row["reviewed_by"],
        edited_caption=row["edited_caption"],
        scheduled_for=row["scheduled_for"],
    )


def _row_to_posting(row: sqlite3.Row) -> Posting:
    return Posting(
        id=row["id"],
        clip_id=row["clip_id"],
        platform=row["platform"],
        external_id=row["external_id"],
        external_url=row["external_url"],
        status=row["status"],
        error=row["error"],
        posted_at=row["posted_at"],
        scheduled_for=row["scheduled_for"],
    )


# ---------------------------------------------------------------------------
# Reviewer-driven transitions
# ---------------------------------------------------------------------------


def get_pending_clips(db_path: Path) -> list[Clip]:
    """Clips awaiting human review (status=pending, has r2_public_url)."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM clips "
            "WHERE status='pending' AND r2_public_url IS NOT NULL "
            "ORDER BY created_at ASC"
        ).fetchall()
    return [_row_to_clip(r) for r in rows]


def approve_clip(db_path: Path, clip_id: int, reviewer: str) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET status='approved', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP "
            "WHERE id=?",
            (reviewer, clip_id),
        )


def reject_clip(db_path: Path, clip_id: int, reviewer: str, notes: str = "") -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET status='rejected', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP, "
            "review_notes=? WHERE id=?",
            (reviewer, notes, clip_id),
        )


def edit_caption(db_path: Path, clip_id: int, new_caption: str) -> None:
    """Save a human-edited caption. The original `caption` is preserved for comparison."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET edited_caption=? WHERE id=?",
            (new_caption, clip_id),
        )


# ---------------------------------------------------------------------------
# Poster-driven transitions
# ---------------------------------------------------------------------------


def get_approved_ready_to_post(db_path: Path, now: datetime | None = None) -> list[Clip]:
    """Approved clips whose scheduled_for is in the past (or unset)."""
    if now is None:
        now = datetime.now(timezone.utc)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM clips "
            "WHERE status='approved' AND r2_public_url IS NOT NULL "
            "AND (scheduled_for IS NULL OR scheduled_for <= ?) "
            "ORDER BY (scheduled_for IS NULL) ASC, scheduled_for ASC, created_at ASC",
            (now.isoformat(),),
        ).fetchall()
    return [_row_to_clip(r) for r in rows]


def mark_posted(db_path: Path, clip_id: int, external_url: str) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET status='posted' WHERE id=?",
            (clip_id,),
        )
        conn.execute(
            "INSERT INTO postings (clip_id, platform, status, external_url, posted_at) "
            "VALUES (?, 'tiktok', 'posted', ?, CURRENT_TIMESTAMP)",
            (clip_id, external_url),
        )


def mark_posting_failed(db_path: Path, clip_id: int, error: str) -> None:
    """Record a failed Buffer/R2 attempt. Keep the clip approved for retry."""
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO postings (clip_id, platform, status, error) "
            "VALUES (?, 'tiktok', 'failed', ?)",
            (clip_id, error),
        )


def record_posting_queued(db_path: Path, clip_id: int, scheduled_for: datetime) -> None:
    """Mark a posting as scheduled (Buffer accepted it, awaiting publish time)."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET scheduled_for=? WHERE id=?",
            (scheduled_for.isoformat(), clip_id),
        )
        conn.execute(
            "INSERT INTO postings (clip_id, platform, status, scheduled_for) "
            "VALUES (?, 'tiktok', 'queued', ?)",
            (clip_id, scheduled_for.isoformat()),
        )
