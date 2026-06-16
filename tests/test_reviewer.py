"""Tests for the reviewer module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stumbleupon import reviewer
from stumbleupon.models import Clip


# ---------------------------------------------------------------------------
# parse_user_choice
# ---------------------------------------------------------------------------


def test_parse_user_choice_recognizes_approve() -> None:
    assert reviewer.parse_user_choice("a") == "approve"
    assert reviewer.parse_user_choice("A") == "approve"  # case-insensitive
    assert reviewer.parse_user_choice("approve") == "approve"
    assert reviewer.parse_user_choice("approve\n") == "approve"


def test_parse_user_choice_recognizes_reject() -> None:
    assert reviewer.parse_user_choice("r") == "reject"
    assert reviewer.parse_user_choice("R") == "reject"
    assert reviewer.parse_user_choice("reject") == "reject"


def test_parse_user_choice_recognizes_edit() -> None:
    assert reviewer.parse_user_choice("e") == "edit"
    assert reviewer.parse_user_choice("E") == "edit"
    assert reviewer.parse_user_choice("edit") == "edit"


def test_parse_user_choice_recognizes_skip() -> None:
    assert reviewer.parse_user_choice("s") == "skip"
    assert reviewer.parse_user_choice("S") == "skip"
    assert reviewer.parse_user_choice("skip") == "skip"


def test_parse_user_choice_recognizes_quit() -> None:
    assert reviewer.parse_user_choice("q") == "quit"
    assert reviewer.parse_user_choice("Q") == "quit"
    assert reviewer.parse_user_choice("quit") == "quit"


def test_parse_user_choice_returns_none_for_unrecognized() -> None:
    """Returns None for empty input or unrecognized characters."""
    assert reviewer.parse_user_choice("") is None
    assert reviewer.parse_user_choice("  ") is None
    assert reviewer.parse_user_choice("xyz") is None
    assert reviewer.parse_user_choice("hello") is None


# ---------------------------------------------------------------------------
# format_clip_summary
# ---------------------------------------------------------------------------


def _make_clip(**overrides) -> Clip:
    """Helper to construct a Clip with reasonable defaults."""
    defaults = dict(
        id=42,
        site_id=1,
        caption="A genuinely weird site from 2003 that still works",
        hashtags="weirdweb,oldsite,flash",
        final_path="data/final/42.mp4",
    )
    defaults.update(overrides)
    return Clip(**defaults)


def test_format_clip_summary_includes_clip_id() -> None:
    summary = reviewer.format_clip_summary(_make_clip(id=42), sound_used=None)
    assert "42" in summary


def test_format_clip_summary_includes_caption() -> None:
    summary = reviewer.format_clip_summary(_make_clip(caption="hello world"), sound_used=None)
    assert "hello world" in summary


def test_format_clip_summary_includes_hashtags() -> None:
    summary = reviewer.format_clip_summary(_make_clip(hashtags="a,b,c"), sound_used=None)
    assert "a" in summary and "b" in summary and "c" in summary


def test_format_clip_summary_includes_final_path() -> None:
    summary = reviewer.format_clip_summary(
        _make_clip(final_path="data/final/42.mp4"), sound_used=None
    )
    assert "data/final/42.mp4" in summary


def test_format_clip_summary_includes_sound_used_when_provided() -> None:
    summary = reviewer.format_clip_summary(
        _make_clip(),
        sound_used="data/sounds/viral.mp3",
    )
    assert "viral" in summary or "data/sounds" in summary


def test_format_clip_summary_handles_missing_sound() -> None:
    summary = reviewer.format_clip_summary(_make_clip(), sound_used=None)
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_format_clip_summary_ends_with_action_prompt() -> None:
    summary = reviewer.format_clip_summary(_make_clip(), sound_used=None)
    text_lower = summary.lower()
    assert "a" in text_lower and "r" in text_lower and "q" in text_lower


# ---------------------------------------------------------------------------
# extract_thumbnail
# ---------------------------------------------------------------------------


def test_extract_thumbnail_runs_ffmpeg(tmp_path: Path) -> None:
    """The function should call ffmpeg via subprocess with the right args."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = b""
    mock_result.stderr = b""

    with patch("stumbleupon.reviewer.subprocess.run", return_value=mock_result) as mock_run:
        reviewer.extract_thumbnail(
            mp4_path=tmp_path / "in.mp4",
            output_path=tmp_path / "thumb.jpg",
            at_sec=1.0,
        )

    mock_run.assert_called_once()
    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "ffmpeg"
    assert str(tmp_path / "in.mp4") in cmd
    assert str(tmp_path / "thumb.jpg") in cmd
    assert "-ss" in cmd
    assert "1.0" in cmd
    assert "-vframes" in cmd
    assert "1" in cmd


def test_extract_thumbnail_creates_output_dir_if_missing(tmp_path: Path) -> None:
    """The function should mkdir -p the output dir before running ffmpeg."""
    mock_result = MagicMock()
    out = tmp_path / "subdir" / "deep" / "thumb.jpg"

    with patch("stumbleupon.reviewer.subprocess.run", return_value=mock_result):
        reviewer.extract_thumbnail(
            mp4_path=tmp_path / "in.mp4",
            output_path=out,
        )

    assert out.parent.exists()


# ---------------------------------------------------------------------------
# open_clip_in_player
# ---------------------------------------------------------------------------


def test_open_clip_in_player_runs_open(tmp_path: Path) -> None:
    """The function should call the macOS `open` command with the mp4 path."""
    mock_result = MagicMock()
    mp4 = tmp_path / "clip.mp4"

    with patch("stumbleupon.reviewer.subprocess.run", return_value=mock_result) as mock_run:
        reviewer.open_clip_in_player(mp4)

    mock_run.assert_called_once()
    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "open"
    assert str(mp4) in cmd


# ---------------------------------------------------------------------------
# review_pending_clips (orchestrator)
# ---------------------------------------------------------------------------


def test_review_pending_clips_processes_actions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """End-to-end with mocked I/O. Verifies actions are applied to the DB."""
    import sqlite3
    from stumbleupon.db import init_db
    from stumbleupon import queue, reviewer

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        a = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path, caption, hashtags) "
            "VALUES (?, 'pending', 'data/recordings/1.webm', 'data/final/1.mp4', "
            "'caption A', 'a,b,c')",
            (site_id,),
        ).lastrowid
        b = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path, caption, hashtags) "
            "VALUES (?, 'pending', 'data/recordings/2.webm', 'data/final/2.mp4', "
            "'caption B', 'd,e,f')",
            (site_id,),
        ).lastrowid
        conn.commit()

    # Mock the I/O: open_clip_in_player is a no-op, prompt returns the next action
    monkeypatch.setattr(reviewer, "open_clip_in_player", lambda mp4: None)

    actions = iter(["a", "r", "q"])  # approve a, reject b, quit
    monkeypatch.setattr("builtins.input", lambda prompt="": next(actions))

    results = reviewer.review_pending_clips(db_path=db_path, limit=10)

    assert results == {a: "approved", b: "rejected"}

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row_a = conn.execute("SELECT status FROM clips WHERE id=?", (a,)).fetchone()
        row_b = conn.execute("SELECT status FROM clips WHERE id=?", (b,)).fetchone()
    assert row_a["status"] == "approved"
    assert row_b["status"] == "rejected"


def test_review_pending_clips_handles_quit_early(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Quitting on the first clip leaves the rest untouched."""
    import sqlite3
    from stumbleupon.db import init_db
    from stumbleupon import queue, reviewer

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        a = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path) "
            "VALUES (?, 'pending', 'r.webm', 'f.mp4')",
            (site_id,),
        ).lastrowid
        b = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path) "
            "VALUES (?, 'pending', 'r.webm', 'f.mp4')",
            (site_id,),
        ).lastrowid
        conn.commit()

    monkeypatch.setattr(reviewer, "open_clip_in_player", lambda mp4: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "q")  # quit on first clip

    results = reviewer.review_pending_clips(db_path=db_path, limit=10)

    assert results == {}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row_b = conn.execute("SELECT status FROM clips WHERE id=?", (b,)).fetchone()
    assert row_b["status"] == "pending"
