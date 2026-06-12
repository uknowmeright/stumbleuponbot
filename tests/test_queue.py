"""Tests for queue operations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stumbleupon.db import init_db
from stumbleupon.models import Clip, Site
from stumbleupon import queue


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "stumbleupon.db"
    init_db(p)
    return p


def _insert_site(db_path: Path, url: str = "https://example.com") -> int:
    with sqlite3_connect(db_path) as conn:
        cur = conn.execute("INSERT INTO sites (url) VALUES (?)", (url,))
        conn.commit()
        return cur.lastrowid or 0


def _insert_clip(
    db_path: Path,
    site_id: int,
    status: str = "pending",
    r2_url: str | None = None,
    caption: str | None = None,
    edited_caption: str | None = None,
    scheduled_for: datetime | None = None,
) -> int:
    with sqlite3_connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO clips (site_id, status, r2_public_url, caption, edited_caption, scheduled_for) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (site_id, status, r2_url, caption, edited_caption, scheduled_for.isoformat() if scheduled_for else None),
        )
        conn.commit()
        return cur.lastrowid or 0


# We need sqlite3_connect as a context manager helper for the test fixtures.
import sqlite3
from contextlib import contextmanager

@contextmanager
def sqlite3_connect(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def test_get_pending_clips_returns_only_pending(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    pending_id = _insert_clip(db_path, site_id, status="pending", r2_url="https://r2/x.mp4", caption="c1")
    _insert_clip(db_path, site_id, status="approved", r2_url="https://r2/y.mp4", caption="c2")
    _insert_clip(db_path, site_id, status="needs_attention")

    pending = queue.get_pending_clips(db_path)
    assert [c.id for c in pending] == [pending_id]
    assert isinstance(pending[0], Clip)
    assert pending[0].status == "pending"


def test_get_pending_clips_excludes_clips_without_r2_url(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    _insert_clip(db_path, site_id, status="pending")  # no r2_url
    pending = queue.get_pending_clips(db_path)
    assert pending == []


def test_approve_clip_moves_to_approved(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    clip_id = _insert_clip(db_path, site_id, status="pending", r2_url="https://r2/x.mp4")

    queue.approve_clip(db_path, clip_id, reviewer="paul")

    with sqlite3_connect(db_path) as conn:
        row = conn.execute("SELECT status, reviewed_by FROM clips WHERE id=?", (clip_id,)).fetchone()
    assert row["status"] == "approved"
    assert row["reviewed_by"] == "paul"


def test_reject_clip_moves_to_rejected(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    clip_id = _insert_clip(db_path, site_id, status="pending", r2_url="https://r2/x.mp4")
    queue.reject_clip(db_path, clip_id, reviewer="paul", notes="caption is off")
    with sqlite3_connect(db_path) as conn:
        row = conn.execute("SELECT status, review_notes FROM clips WHERE id=?", (clip_id,)).fetchone()
    assert row["status"] == "rejected"
    assert row["review_notes"] == "caption is off"


def test_edit_caption_persists_edit(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    clip_id = _insert_clip(db_path, site_id, caption="original")
    queue.edit_caption(db_path, clip_id, "human-tweaked")
    with sqlite3_connect(db_path) as conn:
        row = conn.execute("SELECT caption, edited_caption FROM clips WHERE id=?", (clip_id,)).fetchone()
    assert row["caption"] == "original"
    assert row["edited_caption"] == "human-tweaked"


def test_mark_posted_sets_status_and_url(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    clip_id = _insert_clip(db_path, site_id, status="approved", r2_url="https://r2/x.mp4")
    queue.mark_posted(db_path, clip_id, external_url="https://tiktok.com/v/abc")
    with sqlite3_connect(db_path) as conn:
        row = conn.execute("SELECT status FROM clips WHERE id=?", (clip_id,)).fetchone()
        posting = conn.execute(
            "SELECT status, external_url FROM postings WHERE clip_id=?", (clip_id,)
        ).fetchone()
    assert row["status"] == "posted"
    assert posting["status"] == "posted"
    assert posting["external_url"] == "https://tiktok.com/v/abc"


def test_mark_posting_failed_keeps_clip_approved(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    clip_id = _insert_clip(db_path, site_id, status="approved", r2_url="https://r2/x.mp4")
    queue.mark_posting_failed(db_path, clip_id, error="buffer 500")
    with sqlite3_connect(db_path) as conn:
        clip = conn.execute("SELECT status FROM clips WHERE id=?", (clip_id,)).fetchone()
        posting = conn.execute(
            "SELECT status, error FROM postings WHERE clip_id=?", (clip_id,)
        ).fetchone()
    assert clip["status"] == "approved"  # unchanged, ready to retry
    assert posting["status"] == "failed"
    assert posting["error"] == "buffer 500"


def test_get_approved_ready_to_post_respects_schedule(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    now = datetime.now(timezone.utc)
    past = now - timedelta(hours=1)
    future = now + timedelta(hours=1)

    ready_id = _insert_clip(db_path, site_id, status="approved", r2_url="https://r2/a.mp4", scheduled_for=past)
    _insert_clip(db_path, site_id, status="approved", r2_url="https://r2/b.mp4", scheduled_for=future)
    none_id = _insert_clip(db_path, site_id, status="approved", r2_url="https://r2/c.mp4", scheduled_for=None)

    ready = queue.get_approved_ready_to_post(db_path, now=now)
    # Per the docstring, clips with scheduled_for in the past (or unset) are returned.
    # Past comes first (COALESCE(scheduled_for, created_at) ordering); None falls back to created_at.
    assert [c.id for c in ready] == [ready_id, none_id]


def test_mark_site_recorded_updates_status(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    queue.mark_site_recorded(db_path, site_id, recording_path="data/recordings/1.webm")

    with sqlite3_connect(db_path) as conn:
        row = conn.execute("SELECT status FROM sites WHERE id=?", (site_id,)).fetchone()
    assert row["status"] == "recorded"


def test_mark_site_recorded_stamps_last_attempted(db_path: Path) -> None:
    """Used to compute backoff / metrics later."""
    site_id = _insert_site(db_path)
    queue.mark_site_recorded(db_path, site_id, recording_path="x.webm")

    with sqlite3_connect(db_path) as conn:
        row = conn.execute("SELECT last_attempted FROM sites WHERE id=?", (site_id,)).fetchone()
    assert row["last_attempted"] is not None


def test_mark_site_failed_updates_status_and_skip_reason(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    queue.mark_site_failed(db_path, site_id, error="browser crashed")

    with sqlite3_connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, skip_reason FROM sites WHERE id=?",
            (site_id,),
        ).fetchone()
    assert row["status"] == "failed"
    assert row["skip_reason"] == "browser crashed"


def test_mark_site_failed_handles_long_error_messages(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    long_error = "x" * 1000
    queue.mark_site_failed(db_path, site_id, error=long_error)
    with sqlite3_connect(db_path) as conn:
        row = conn.execute("SELECT skip_reason FROM sites WHERE id=?", (site_id,)).fetchone()
    assert row["skip_reason"] == long_error
