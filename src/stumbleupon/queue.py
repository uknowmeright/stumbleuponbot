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
        last_attempted=row["last_attempted"],
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


# ---------------------------------------------------------------------------
# Recorder-driven transitions
# ---------------------------------------------------------------------------


def mark_site_recorded(db_path: Path, site_id: int, recording_path: str = "") -> None:
    """Mark a site as successfully recorded.

    The `recording_path` arg is accepted for API stability; the sites
    schema doesn't have a recording_path column (the clip row will own
    that once the captioner plan lands). The orchestrator holds the
    recording path in memory and returns it from `record_pending_sites`.
    """
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE sites SET status='recorded', last_attempted=CURRENT_TIMESTAMP "
            "WHERE id=?",
            (site_id,),
        )


def mark_site_failed(db_path: Path, site_id: int, error: str) -> None:
    """Mark a site as failed (recording error). The error is stored in skip_reason."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE sites SET status='failed', skip_reason=?, last_attempted=CURRENT_TIMESTAMP "
            "WHERE id=?",
            (error, site_id),
        )


# ---------------------------------------------------------------------------
# Captioner-driven queries + transitions
# ---------------------------------------------------------------------------


def get_posted_caption_examples(db_path: Path, limit: int = 5) -> list[str]:
    """Return the captions of up to `limit` recently-posted clips, newest first.

    Used by the captioner to seed its prompt with examples of what
    on-brand copy looks like. The captions come from clips whose status
    reached 'posted' (i.e., real published content), not just approved.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT caption FROM clips WHERE status='posted' "
            "AND caption IS NOT NULL AND caption != '' "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [row["caption"] for row in rows]


def get_recorded_sites_without_clips(db_path: Path) -> list[tuple[int, str]]:
    """Return [(id, url), ...] for sites with status='recorded' that don't yet
    have a clip row. The recording_path is NOT included here — the orchestrator
    computes it by convention as `data/recordings/<site_id>.webm`.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT s.id, s.url FROM sites s "
            "WHERE s.status='recorded' "
            "AND NOT EXISTS (SELECT 1 FROM clips c WHERE c.site_id = s.id) "
            "ORDER BY s.discovered_at ASC"
        ).fetchall()
    return [(row["id"], row["url"]) for row in rows]


def create_clip(
    db_path: Path,
    site_id: int,
    recording_path: str,
    caption: str,
    hashtags: str,
) -> int:
    """Insert a new clip row with status='pending' (awaiting human review).

    `hashtags` is stored as a comma-separated string (the schema's
    convention). The clip_id is returned so the caller can map back.
    """
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO clips (site_id, recording_path, caption, hashtags, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (site_id, recording_path, caption, hashtags),
        )
        return cur.lastrowid or 0


# ---------------------------------------------------------------------------
# Composer-driven queries + transitions
# ---------------------------------------------------------------------------


def get_clips_to_compose(db_path: Path, limit: int | None = None) -> list[sqlite3.Row]:
    """Return pending clips that have a recording_path but no final_path.

    These are the clips the composer should process. Already-composed
    clips (final_path set) and clips without a recording (captioner
    hasn't run yet) are excluded.
    """
    sql = (
        "SELECT * FROM clips "
        "WHERE status='pending' "
        "AND recording_path IS NOT NULL "
        "AND final_path IS NULL "
        "ORDER BY created_at ASC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    else:
        params = ()
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [row for row in rows]


def mark_clip_composed(db_path: Path, clip_id: int, final_path: str) -> None:
    """Mark a clip's final_path after the composer has produced the mp4.

    The clip's status stays 'pending' — the human review gate hasn't run
    yet. last_attempted is stamped for metrics/backoff.
    """
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET final_path=?, last_attempted=CURRENT_TIMESTAMP "
            "WHERE id=?",
            (final_path, clip_id),
        )
