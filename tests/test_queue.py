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


# ---------------------------------------------------------------------------
# Poster-driven queries + transitions
# ---------------------------------------------------------------------------


def test_get_approved_clips_finds_approved_regardless_of_r2(db_path: Path) -> None:
    """Returns approved clips regardless of whether r2_public_url is set.

    The poster handles R2 upload itself, so it needs to see approved
    clips before the R2 URL is set.
    """
    with sqlite3_connect(db_path) as conn:
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        # 1: approved, no r2 (should be picked — poster will upload)
        a = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path, r2_public_url) "
            "VALUES (?, 'approved', 'r.webm', 'f.mp4', NULL)",
            (site_id,),
        ).lastrowid
        # 2: approved, has r2 (should also be picked — poster may retry)
        b = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path, r2_public_url) "
            "VALUES (?, 'approved', 'r.webm', 'f.mp4', 'https://r2/2.mp4')",
            (site_id,),
        ).lastrowid
        # 3: pending (skip — not approved)
        c = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path) "
            "VALUES (?, 'pending', 'r.webm', 'f.mp4')",
            (site_id,),
        ).lastrowid
        conn.commit()

    rows = queue.get_approved_clips(db_path)
    ids = [r.id for r in rows]
    assert ids == [a, b]


def test_set_clip_r2_url_sets_r2_public_url(db_path: Path) -> None:
    with sqlite3_connect(db_path) as conn:
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        clip_id = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path) "
            "VALUES (?, 'approved', 'r.webm', 'f.mp4')",
            (site_id,),
        ).lastrowid
        conn.commit()

    queue.set_clip_r2_url(db_path, clip_id, r2_url="https://media.example.com/42.mp4")
    with sqlite3_connect(db_path) as conn:
        row = conn.execute("SELECT r2_public_url FROM clips WHERE id=?", (clip_id,)).fetchone()
    assert row["r2_public_url"] == "https://media.example.com/42.mp4"


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


def test_get_posted_caption_examples_returns_published_captions(db_path: Path) -> None:
    """Returns the `caption` text of recently `posted` clips, newest first."""
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        for i, status in enumerate(["posted", "posted", "pending", "posted", "failed"]):
            conn.execute(
                "INSERT INTO clips (site_id, status, caption, hashtags) VALUES (?, ?, ?, ?)",
                (site_id, status, f"caption-{i}", "a,b"),
            )
        conn.commit()

    examples = queue.get_posted_caption_examples(db_path, limit=5)
    # Only the 3 'posted' captions, newest first
    assert examples == ["caption-3", "caption-1", "caption-0"]


def test_get_posted_caption_examples_respects_limit(db_path: Path) -> None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        for i in range(5):
            conn.execute(
                "INSERT INTO clips (site_id, status, caption) VALUES (?, 'posted', ?)",
                (site_id, f"cap-{i}"),
            )
        conn.commit()
    assert len(queue.get_posted_caption_examples(db_path, limit=2)) == 2


def test_get_recorded_sites_without_clips_picks_recorded_sites(db_path: Path) -> None:
    """Returns (id, url) for sites with status='recorded' that have no clip row."""
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO sites (url, status) VALUES ('https://a.com', 'recorded')")
        conn.execute("INSERT INTO sites (url, status) VALUES ('https://b.com', 'fresh')")
        conn.execute("INSERT INTO sites (url, status) VALUES ('https://c.com', 'failed')")
        site_with_clip = conn.execute(
            "INSERT INTO sites (url, status) VALUES ('https://d.com', 'recorded')"
        ).lastrowid
        conn.execute(
            "INSERT INTO clips (site_id, status) VALUES (?, 'pending')",
            (site_with_clip,),
        )
        conn.commit()

    rows = queue.get_recorded_sites_without_clips(db_path)
    urls = [url for _id, url in rows]
    assert urls == ["https://a.com"]


def test_create_clip_inserts_a_new_clip_row(db_path: Path) -> None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        conn.commit()

    clip_id = queue.create_clip(
        db_path,
        site_id=site_id,
        recording_path="data/recordings/1.webm",
        caption="A test caption",
        hashtags="weirdweb,oldsite,flash",
    )
    assert clip_id > 0

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT site_id, recording_path, caption, hashtags, status FROM clips WHERE id=?",
            (clip_id,),
        ).fetchone()
    assert row["site_id"] == site_id
    assert row["recording_path"] == "data/recordings/1.webm"
    assert row["caption"] == "A test caption"
    assert row["hashtags"] == "weirdweb,oldsite,flash"
    assert row["status"] == "pending"


def test_create_clip_returns_unique_ids(db_path: Path) -> None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        conn.commit()
    a = queue.create_clip(db_path, site_id, "a.webm", "cap a", "a")
    b = queue.create_clip(db_path, site_id, "b.webm", "cap b", "b")
    assert a != b


def test_get_clips_to_compose_finds_pending_with_recording_path(db_path: Path) -> None:
    """Returns clips that have recording_path but no final_path, status=pending."""
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        # 1: pending + recording (should be picked)
        a = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path) "
            "VALUES (?, 'pending', 'data/recordings/1.webm', NULL)",
            (site_id,),
        ).lastrowid
        # 2: pending + recording + final (already composed, skip)
        b = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path) "
            "VALUES (?, 'pending', 'data/recordings/2.webm', 'data/final/2.mp4')",
            (site_id,),
        ).lastrowid
        # 3: pending, no recording (captioner hasn't run yet, skip)
        c = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path) VALUES (?, 'pending', NULL)",
            (site_id,),
        ).lastrowid
        # 4: posted, has recording (skip — not pending)
        d = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path) VALUES (?, 'posted', 'data/recordings/4.webm')",
            (site_id,),
        ).lastrowid
        conn.commit()

    rows = queue.get_clips_to_compose(db_path)
    ids = [r["id"] for r in rows]
    assert ids == [a]


def test_get_clips_to_compose_respects_limit(db_path: Path) -> None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        for i in range(5):
            conn.execute(
                "INSERT INTO clips (site_id, status, recording_path) "
                "VALUES (?, 'pending', ?)",
                (site_id, f"data/recordings/{i}.webm"),
            )
        conn.commit()
    rows = queue.get_clips_to_compose(db_path, limit=2)
    assert len(rows) == 2


def test_mark_clip_composed_sets_final_path(db_path: Path) -> None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        clip_id = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path) "
            "VALUES (?, 'pending', 'data/recordings/1.webm')",
            (site_id,),
        ).lastrowid
        conn.commit()

    queue.mark_clip_composed(db_path, clip_id, final_path="data/final/1.mp4")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, final_path FROM clips WHERE id=?", (clip_id,)
        ).fetchone()
    assert row["status"] == "pending"  # still pending (awaits human review)
    assert row["final_path"] == "data/final/1.mp4"


def test_mark_clip_composed_stamps_last_attempted(db_path: Path) -> None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        clip_id = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path) "
            "VALUES (?, 'pending', 'data/recordings/1.webm')",
            (site_id,),
        ).lastrowid
        conn.commit()

    queue.mark_clip_composed(db_path, clip_id, final_path="data/final/1.mp4")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT last_attempted FROM clips WHERE id=?", (clip_id,)).fetchone()
    assert row["last_attempted"] is not None


def test_get_clips_to_review_finds_pending_with_final_path(db_path: Path) -> None:
    """Returns clips that have final_path but no r2_public_url, status=pending."""
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        # 1: pending + final (should be picked)
        a = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path, r2_public_url) "
            "VALUES (?, 'pending', 'data/recordings/1.webm', 'data/final/1.mp4', NULL)",
            (site_id,),
        ).lastrowid
        # 2: pending + final + r2 (already posted, skip)
        b = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path, r2_public_url) "
            "VALUES (?, 'pending', 'data/recordings/2.webm', 'data/final/2.mp4', 'https://r2/2.mp4')",
            (site_id,),
        ).lastrowid
        # 3: pending, no final (composer hasn't run, skip)
        c = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path) "
            "VALUES (?, 'pending', 'data/recordings/3.webm', NULL)",
            (site_id,),
        ).lastrowid
        # 4: approved (already reviewed, skip)
        d = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path) "
            "VALUES (?, 'approved', 'data/recordings/4.webm', 'data/final/4.mp4')",
            (site_id,),
        ).lastrowid
        conn.commit()

    rows = queue.get_clips_to_review(db_path)
    ids = [r.id for r in rows]
    assert ids == [a]


def test_upsert_sound_inserts_and_updates(db_path: Path) -> None:
    """Insert a sound, then upsert again with a new score — should update, not duplicate."""
    id1 = queue.upsert_sound(
        db_path, tiktok_sound_id="x1", title="Original", artist="A",
        views=100, audio_path="/tmp/x1.mp3",
    )
    id2 = queue.upsert_sound(
        db_path, tiktok_sound_id="x1", title="Updated", artist="A",
        views=200, audio_path="/tmp/x1.mp3",
    )
    assert id1 == id2  # same row, updated

    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT title, trending_score FROM sounds WHERE id=?", (id1,)).fetchone()
    assert row["title"] == "Updated"
    assert row["trending_score"] == 200.0


# ---------------------------------------------------------------------------
# Sounds-driven queries + transitions (selection-side)
# ---------------------------------------------------------------------------


def test_count_sounds_returns_total(db_path: Path) -> None:
    """count_sounds returns the row count."""
    assert queue.count_sounds(db_path) == 0
    with sqlite3_connect(db_path) as conn:
        conn.execute("INSERT INTO sounds (tiktok_sound_id) VALUES ('a')")
        conn.execute("INSERT INTO sounds (tiktok_sound_id) VALUES ('b')")
        conn.commit()
    assert queue.count_sounds(db_path) == 2


def test_get_next_sound_picks_highest_score(db_path: Path) -> None:
    """Selection should be ordered by trending_score DESC."""
    with sqlite3_connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, trending_score, audio_path) "
            "VALUES ('a', 100, '/tmp/a.mp3')"
        )
        conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, trending_score, audio_path) "
            "VALUES ('b', 500, '/tmp/b.mp3')"
        )
        conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, trending_score, audio_path) "
            "VALUES ('c', 250, '/tmp/c.mp3')"
        )
        conn.commit()

    sound = queue.get_next_sound(db_path)
    assert sound is not None
    assert sound.tiktok_sound_id == "b"


def test_get_next_sound_skips_sounds_without_audio(db_path: Path) -> None:
    """Sounds with NULL audio_path are unusable; skip them."""
    with sqlite3_connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, trending_score, audio_path) "
            "VALUES ('a', 999, NULL)"  # highest score but no audio
        )
        conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, trending_score, audio_path) "
            "VALUES ('b', 100, '/tmp/b.mp3')"
        )
        conn.commit()

    sound = queue.get_next_sound(db_path)
    assert sound is not None
    assert sound.tiktok_sound_id == "b"


def test_get_next_sound_excludes_recently_used(db_path: Path) -> None:
    """A sound used in the last 3 days should be excluded."""
    with sqlite3_connect(db_path) as conn:
        # 'a' was used today
        conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, trending_score, audio_path, last_used_at) "
            "VALUES ('a', 999, '/tmp/a.mp3', CURRENT_TIMESTAMP)"
        )
        # 'b' is fresh
        conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, trending_score, audio_path, last_used_at) "
            "VALUES ('b', 100, '/tmp/b.mp3', NULL)"
        )
        conn.commit()

    sound = queue.get_next_sound(db_path, exclude_used_within_days=3)
    assert sound is not None
    assert sound.tiktok_sound_id == "b"


def test_get_next_sound_returns_none_when_empty(db_path: Path) -> None:
    """Empty catalog → None (caller marks clip needs_attention)."""
    assert queue.get_next_sound(db_path) is None


def test_get_next_sound_returns_none_when_all_recently_used(db_path: Path) -> None:
    """All sounds used recently → None."""
    with sqlite3_connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, trending_score, audio_path, last_used_at) "
            "VALUES ('a', 999, '/tmp/a.mp3', CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, trending_score, audio_path, last_used_at) "
            "VALUES ('b', 100, '/tmp/b.mp3', CURRENT_TIMESTAMP)"
        )
        conn.commit()

    assert queue.get_next_sound(db_path, exclude_used_within_days=3) is None


def test_attach_sound_to_clip_sets_both_rows(db_path: Path) -> None:
    """attach_sound_to_clip sets clips.sound_id and stamps sounds.last_used_at."""
    with sqlite3_connect(db_path) as conn:
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        clip_id = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path) "
            "VALUES (?, 'pending', 'r.webm')",
            (site_id,),
        ).lastrowid
        sound_id = conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, audio_path) VALUES ('s1', '/tmp/s1.mp3')"
        ).lastrowid
        conn.commit()

    queue.attach_sound_to_clip(db_path, clip_id, sound_id)
    with sqlite3_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        clip = conn.execute("SELECT sound_id FROM clips WHERE id=?", (clip_id,)).fetchone()
        sound = conn.execute("SELECT last_used_at FROM sounds WHERE id=?", (sound_id,)).fetchone()
    assert clip["sound_id"] == sound_id
    assert sound["last_used_at"] is not None


def test_mark_clip_needs_attention_updates_status(db_path: Path) -> None:
    """The clip's status moves to 'needs_attention'."""
    with sqlite3_connect(db_path) as conn:
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        clip_id = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, caption) "
            "VALUES (?, 'pending', 'r.webm', 'cap')",
            (site_id,),
        ).lastrowid
        conn.commit()

    queue.mark_clip_needs_attention(db_path, clip_id)
    with sqlite3_connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status FROM clips WHERE id=?", (clip_id,)).fetchone()
    assert row["status"] == "needs_attention"


def test_get_clips_needing_sound_filters_correctly(db_path: Path) -> None:
    """Returns only pending clips with caption + recording, no sound yet."""
    with sqlite3_connect(db_path) as conn:
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        sound_id = conn.execute(
            "INSERT INTO sounds (tiktok_sound_id) VALUES ('s1')"
        ).lastrowid
        # 1: pending, no sound, has caption + recording → should be picked
        a = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, caption, sound_id) "
            "VALUES (?, 'pending', 'r.webm', 'cap', NULL)",
            (site_id,),
        ).lastrowid
        # 2: pending, has sound → skip
        b = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, caption, sound_id) "
            "VALUES (?, 'pending', 'r.webm', 'cap', ?)",
            (site_id, sound_id),
        ).lastrowid
        # 3: pending, no caption → skip (captioner hasn't run)
        c = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, sound_id) "
            "VALUES (?, 'pending', 'r.webm', NULL)",
            (site_id,),
        ).lastrowid
        # 4: needs_attention → skip
        d = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, caption, sound_id) "
            "VALUES (?, 'needs_attention', 'r.webm', 'cap', NULL)",
            (site_id,),
        ).lastrowid
        conn.commit()

    rows = queue.get_clips_needing_sound(db_path)
    ids = [r.id for r in rows]
    assert ids == [a]
