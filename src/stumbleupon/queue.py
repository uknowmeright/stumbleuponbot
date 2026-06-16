"""High-level DB operations on clips and postings.

This is the only module that mutates `clips.status`. Everything else
calls into here. Returns dataclasses, not dicts.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .db import get_connection
from .models import Clip, Posting, Sound


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


def _row_to_sound(row: sqlite3.Row) -> Sound:
    return Sound(
        id=row["id"],
        tiktok_sound_id=row["tiktok_sound_id"],
        title=row["title"],
        artist=row["artist"],
        audio_path=row["audio_path"],
        trending_score=row["trending_score"],
        fetched_at=row["fetched_at"],
        last_used_at=row["last_used_at"],
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

    LEFT JOINs `sounds` to expose `sounds.audio_path` (aliased as
    `sound_audio_path`) so the composer can mix in audio. The column
    is NULL when the clip has no `sound_id` attached yet.
    """
    sql = (
        "SELECT clips.*, sounds.audio_path AS sound_audio_path "
        "FROM clips "
        "LEFT JOIN sounds ON sounds.id = clips.sound_id "
        "WHERE clips.status='pending' "
        "AND clips.recording_path IS NOT NULL "
        "AND clips.final_path IS NULL "
        "ORDER BY clips.created_at ASC"
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


# ---------------------------------------------------------------------------
# Reviewer-driven queries
# ---------------------------------------------------------------------------


def get_clips_to_review(db_path: Path, limit: int | None = None) -> list[Clip]:
    """Return pending clips that have a final_path but no r2_public_url.

    These are the clips the reviewer should process. Already-posted
    clips (r2_public_url set) and clips without a composer output
    (final_path NULL) are excluded.
    """
    sql = (
        "SELECT * FROM clips "
        "WHERE status='pending' "
        "AND final_path IS NOT NULL "
        "AND r2_public_url IS NULL "
        "ORDER BY created_at ASC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    else:
        params = ()
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_clip(r) for r in rows]


# ---------------------------------------------------------------------------
# Poster-driven queries + transitions
# ---------------------------------------------------------------------------


def get_approved_clips(db_path: Path, limit: int | None = None) -> list[Clip]:
    """Return approved clips, oldest first.

    The poster handles R2 upload itself, so this query does NOT filter
    by r2_public_url. The poster may pick up clips that already have an
    R2 URL (e.g., after a partial failure where R2 succeeded but the
    Buffer post failed) and retry the Buffer call.
    """
    sql = "SELECT * FROM clips WHERE status='approved' ORDER BY created_at ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    else:
        params = ()
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_clip(r) for r in rows]


def set_clip_r2_url(db_path: Path, clip_id: int, r2_url: str) -> None:
    """Set the r2_public_url column for a clip after R2 upload succeeds."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET r2_public_url=?, last_attempted=CURRENT_TIMESTAMP WHERE id=?",
            (r2_url, clip_id),
        )


# ---------------------------------------------------------------------------
# Sounds-driven queries + transitions
# ---------------------------------------------------------------------------


def upsert_sound(
    db_path: Path,
    tiktok_sound_id: str,
    title: str,
    artist: str,
    views: int,
    audio_path: str | None = None,
) -> int:
    """Insert a sound row, or update it if tiktok_sound_id already exists.

    On conflict: refreshes title, artist, trending_score (from views),
    fetched_at; COALESCE preserves an existing audio_path if the new
    one is None. Returns the row id.
    """
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, title, artist, trending_score, audio_path) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(tiktok_sound_id) DO UPDATE SET "
            "  title=excluded.title, "
            "  artist=excluded.artist, "
            "  trending_score=excluded.trending_score, "
            "  audio_path=COALESCE(excluded.audio_path, sounds.audio_path), "
            "  fetched_at=CURRENT_TIMESTAMP "
            "RETURNING id",
            (tiktok_sound_id, title, artist, float(views), audio_path),
        )
        row = cur.fetchone()
        return row[0] if row else 0


def count_sounds(db_path: Path) -> int:
    """Total sounds in the catalog."""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM sounds").fetchone()
    return int(row["n"])


def get_next_sounds(
    db_path: Path,
    limit: int,
    *,
    exclude_used_within_days: int = 3,
) -> list[Sound]:
    """Return up to `limit` highest-trending sounds not used in the last N days.

    Skips sounds with NULL audio_path (not yet downloaded). Ordered by
    trending_score DESC, then fetched_at DESC, then id DESC (final
    tie-breaker for determinism). Returns an empty list when the
    catalog has no eligible sounds (or fewer than `limit`).
    """
    sql = (
        "SELECT * FROM sounds "
        "WHERE audio_path IS NOT NULL "
        "AND (last_used_at IS NULL "
        "     OR last_used_at < datetime('now', ?)) "
        "ORDER BY trending_score DESC, fetched_at DESC, id DESC "
        "LIMIT ?"
    )
    days_modifier = f"-{exclude_used_within_days} days"
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, (days_modifier, limit)).fetchall()
    return [_row_to_sound(row) for row in rows]


def get_next_sound(
    db_path: Path, *, exclude_used_within_days: int = 3
) -> Sound | None:
    """Return the highest-trending sound not used in the last N days.

    Convenience wrapper around `get_next_sounds(limit=1)`. Returns
    None when no candidate exists. Callers that need a batch of
    sounds (e.g., one per pending clip in `cmd_run`) should call
    `get_next_sounds` directly to avoid the racy "stamp last_used_at
    after each pick" behavior.
    """
    sounds = get_next_sounds(db_path, limit=1, exclude_used_within_days=exclude_used_within_days)
    return sounds[0] if sounds else None


def attach_sound_to_clip(db_path: Path, clip_id: int, sound_id: int) -> None:
    """Link a sound to a clip and stamp the sound's last_used_at."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET sound_id=? WHERE id=?",
            (sound_id, clip_id),
        )
        conn.execute(
            "UPDATE sounds SET last_used_at=CURRENT_TIMESTAMP WHERE id=?",
            (sound_id,),
        )


def mark_clip_needs_attention(db_path: Path, clip_id: int) -> None:
    """Mark an existing clip's status as 'needs_attention' (no sound available)."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET status='needs_attention', last_attempted=CURRENT_TIMESTAMP "
            "WHERE id=?",
            (clip_id,),
        )


def get_clips_needing_sound(db_path: Path, limit: int | None = None) -> list[Clip]:
    """Pending clips with caption + recording, no sound yet — the pick step.

    These are clips the captioner has finished; the composer hasn't
    picked them up yet; the sound hasn't been attached.
    """
    sql = (
        "SELECT * FROM clips "
        "WHERE status='pending' "
        "AND sound_id IS NULL "
        "AND recording_path IS NOT NULL "
        "AND caption IS NOT NULL "
        "ORDER BY created_at ASC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    else:
        params = ()
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_clip(r) for r in rows]
