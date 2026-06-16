"""Tests for the cmd_run pipeline orchestration in main.py."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from stumbleupon.db import init_db
from stumbleupon.main import attach_sounds_to_pending_clips
from stumbleupon.models import Clip, Sound


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "stumbleupon.db"
    init_db(p)
    return p


@contextmanager
def sqlite3_connect(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _make_clips(db_path: Path, n: int) -> list[Clip]:
    """Insert N pending clips with caption+recording but no sound; return as Clip list."""
    clips: list[Clip] = []
    with sqlite3_connect(db_path) as conn:
        site_id = conn.execute(
            "INSERT INTO sites (url) VALUES ('https://x.com')"
        ).lastrowid
        for i in range(n):
            clip_id = conn.execute(
                "INSERT INTO clips (site_id, status, recording_path, caption) "
                "VALUES (?, 'pending', ?, ?)",
                (site_id, f"data/recordings/{i}.webm", f"caption {i}"),
            ).lastrowid
            clips.append(Clip(id=clip_id, site_id=site_id,
                              recording_path=f"data/recordings/{i}.webm",
                              caption=f"caption {i}"))
    return clips


def _make_sounds(db_path: Path, n: int) -> list[Sound]:
    """Insert N sounds with audio_path set; return as Sound list."""
    sounds: list[Sound] = []
    with sqlite3_connect(db_path) as conn:
        for i in range(n):
            sound_id = conn.execute(
                "INSERT INTO sounds (tiktok_sound_id, audio_path) VALUES (?, ?)",
                (f"s{i}", f"/tmp/s{i}.mp3"),
            ).lastrowid
            sounds.append(Sound(id=sound_id, tiktok_sound_id=f"s{i}",
                                audio_path=f"/tmp/s{i}.mp3"))
    return sounds


# ---------------------------------------------------------------------------
# attach_sounds_to_pending_clips
# ---------------------------------------------------------------------------


def test_attach_sounds_to_pending_clips_marks_excess_clips_needs_attention(
    db_path: Path,
) -> None:
    """When there are fewer sounds than clips, the surplus clips must be
    marked `needs_attention` — not silently dropped.

    Regression: the previous zip-based loop in cmd_run would skip surplus
    clips entirely; the pre-zip per-clip loop marked them. We want the
    per-clip behavior back.
    """
    clips = _make_clips(db_path, 5)
    sounds = _make_sounds(db_path, 2)

    attached = attach_sounds_to_pending_clips(db_path, clips, sounds)

    assert attached == 2

    with sqlite3_connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, status, sound_id FROM clips ORDER BY id"
        ).fetchall()
    by_id = {r["id"]: r for r in rows}

    # First 2 clips (in pending_for_sound order) get the 2 sounds.
    assert by_id[clips[0].id]["sound_id"] == sounds[0].id
    assert by_id[clips[0].id]["status"] == "pending"
    assert by_id[clips[1].id]["sound_id"] == sounds[1].id
    assert by_id[clips[1].id]["status"] == "pending"

    # Remaining 3 clips must be marked needs_attention.
    for c in clips[2:]:
        assert by_id[c.id]["sound_id"] is None
        assert by_id[c.id]["status"] == "needs_attention"


def test_attach_sounds_to_pending_clips_one_to_one(db_path: Path) -> None:
    """When clip count equals sound count, every clip gets a sound."""
    clips = _make_clips(db_path, 3)
    sounds = _make_sounds(db_path, 3)

    attached = attach_sounds_to_pending_clips(db_path, clips, sounds)

    assert attached == 3
    with sqlite3_connect(db_path) as conn:
        for c, s in zip(clips, sounds):
            row = conn.execute(
                "SELECT status, sound_id FROM clips WHERE id=?", (c.id,)
            ).fetchone()
            assert row["sound_id"] == s.id
            assert row["status"] == "pending"


def test_attach_sounds_to_pending_clips_no_sounds_marks_all(
    db_path: Path,
) -> None:
    """If the sounds batch is empty, every clip is marked needs_attention."""
    clips = _make_clips(db_path, 3)
    sounds: list[Sound] = []

    attached = attach_sounds_to_pending_clips(db_path, clips, sounds)

    assert attached == 0
    with sqlite3_connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, status, sound_id FROM clips ORDER BY id"
        ).fetchall()
    for r in rows:
        assert r["sound_id"] is None
        assert r["status"] == "needs_attention"


def test_attach_sounds_to_pending_clips_no_clips_is_noop(db_path: Path) -> None:
    """Empty clip list: nothing to do, return 0."""
    sounds = _make_sounds(db_path, 2)
    attached = attach_sounds_to_pending_clips(db_path, [], sounds)
    assert attached == 0


def test_attach_sounds_to_pending_clips_isolates_per_clip_failures(
    db_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure on one clip doesn't sink the batch — the others still attach.

    monkeypatches queue.attach_sound_to_clip so the 2nd clip raises;
    verifies clips 1 and 3 are processed normally.
    """
    from stumbleupon import queue as q

    clips = _make_clips(db_path, 3)
    sounds = _make_sounds(db_path, 3)
    failing_clip_id = clips[1].id

    real_attach = q.attach_sound_to_clip
    call_count = {"n": 0}

    def maybe_failing(db_path, clip_id, sound_id):
        call_count["n"] += 1
        if clip_id == failing_clip_id:
            raise RuntimeError("simulated DB error")
        return real_attach(db_path, clip_id, sound_id)

    monkeypatch.setattr(q, "attach_sound_to_clip", maybe_failing)

    attached = attach_sounds_to_pending_clips(db_path, clips, sounds)

    # All 3 attempts happened; only 2 succeeded (the failing one didn't
    # attach, so the count of "sounds attached" is 2).
    assert call_count["n"] == 3
    assert attached == 2

    with sqlite3_connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, status, sound_id FROM clips ORDER BY id"
        ).fetchall()
    by_id = {r["id"]: r for r in rows}

    # Clip 1: attached normally.
    assert by_id[clips[0].id]["sound_id"] == sounds[0].id
    assert by_id[clips[0].id]["status"] == "pending"
    # Clip 2: failed, status untouched (still pending), no sound attached.
    assert by_id[clips[1].id]["sound_id"] is None
    assert by_id[clips[1].id]["status"] == "pending"
    # Clip 3: attached normally — the batch continued past the failure.
    assert by_id[clips[2].id]["sound_id"] == sounds[2].id
    assert by_id[clips[2].id]["status"] == "pending"
