"""Tests for the recorder module."""

from __future__ import annotations

from pathlib import Path

import pytest

from stumbleupon import recorder
from stumbleupon.db import init_db
from stumbleupon import queue
from stumbleupon.recorder import generate_mouse_path, generate_scroll_events


def test_generate_mouse_path_returns_correct_length() -> None:
    """Mouse path covers the full duration at ~30fps."""
    path = generate_mouse_path(
        duration_sec=3.0,
        viewport=(1080, 1920),
        seed=42,
    )
    # 30 events per second × 3 seconds = 90 points (give or take 1)
    assert 80 <= len(path) <= 100


def test_generate_mouse_path_points_are_within_viewport() -> None:
    path = generate_mouse_path(
        duration_sec=2.0,
        viewport=(1080, 1920),
        seed=1,
    )
    for x, y, _t in path:
        assert 0 <= x < 1080
        assert 0 <= y < 1920


def test_generate_mouse_path_is_deterministic_with_seed() -> None:
    """Same seed → same path. Important for test reproducibility."""
    a = generate_mouse_path(duration_sec=2.0, viewport=(1080, 1920), seed=99)
    b = generate_mouse_path(duration_sec=2.0, viewport=(1080, 1920), seed=99)
    assert a == b


def test_generate_mouse_path_changes_with_seed() -> None:
    a = generate_mouse_path(duration_sec=2.0, viewport=(1080, 1920), seed=1)
    b = generate_mouse_path(duration_sec=2.0, viewport=(1080, 1920), seed=2)
    assert a != b


def test_generate_mouse_path_timestamps_are_monotonic() -> None:
    """Timestamps are non-decreasing, end at or near duration_sec."""
    path = generate_mouse_path(
        duration_sec=2.0,
        viewport=(1080, 1920),
        seed=7,
    )
    times = [t for _x, _y, t in path]
    assert times == sorted(times)
    assert times[-1] >= 1.9  # within 0.1s of duration


def test_generate_scroll_events_returns_few_events() -> None:
    """A typical 30s session has a handful of scrolls, not one per frame."""
    events = generate_scroll_events(
        duration_sec=30.0,
        viewport_height=1920,
        seed=42,
    )
    # Expect roughly 4-10 scroll events for a 30s session
    assert 1 <= len(events) <= 20


def test_generate_scroll_events_timestamps_within_duration() -> None:
    events = generate_scroll_events(
        duration_sec=5.0,
        viewport_height=1920,
        seed=3,
    )
    for time_offset, _delta_y in events:
        assert 0.0 <= time_offset <= 5.0


def test_generate_scroll_events_is_deterministic_with_seed() -> None:
    a = generate_scroll_events(duration_sec=10.0, viewport_height=1920, seed=11)
    b = generate_scroll_events(duration_sec=10.0, viewport_height=1920, seed=11)
    assert a == b


def _seed_fresh_site(db_path: Path, url: str) -> int:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute("INSERT INTO sites (url, status) VALUES (?, 'fresh')", (url,))
        conn.commit()
        return cur.lastrowid or 0


def test_record_pending_sites_picks_fresh_sites_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The orchestrator should only attempt to record sites with status='fresh'.

    We monkeypatch record_site to a no-op (no Playwright) and check that
    the right sites are picked and their status is updated.
    """
    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)
    fresh_a = _seed_fresh_site(db_path, "https://a.com")
    fresh_b = _seed_fresh_site(db_path, "https://b.com")

    # Pre-insert a recorded site to ensure it's not picked up
    rec_id = _seed_fresh_site(db_path, "https://recorded.com")
    queue.mark_site_recorded(db_path, rec_id)

    seen_urls: list[str] = []

    def fake_record_site(site_url, output_path, duration_sec=30.0, seed=None):
        seen_urls.append(site_url)
        # Pretend a recording was made
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake webm")

    monkeypatch.setattr(recorder, "record_site", fake_record_site)

    results = recorder.record_pending_sites(
        db_path=db_path,
        recordings_dir=tmp_path / "recordings",
        duration_sec=30.0,
        limit=10,
    )

    assert sorted(seen_urls) == ["https://a.com", "https://b.com"]
    assert len(results) == 2
    assert {url for _id, url, path in results} == {"https://a.com", "https://b.com"}


def test_record_pending_sites_marks_failures_without_crashing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad site doesn't sink the batch."""
    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)
    a = _seed_fresh_site(db_path, "https://a.com")
    b = _seed_fresh_site(db_path, "https://b.com")

    def fake_record_site(site_url, output_path, duration_sec=30.0, seed=None):
        if "b.com" in site_url:
            raise RuntimeError("playwright crashed")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake webm")

    monkeypatch.setattr(recorder, "record_site", fake_record_site)

    results = recorder.record_pending_sites(
        db_path=db_path,
        recordings_dir=tmp_path / "recordings",
        duration_sec=30.0,
        limit=10,
    )

    # 'a' is recorded successfully; 'b' is marked failed but doesn't crash the run
    successful = {url for _id, url, path in results}
    assert successful == {"https://a.com"}

    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = {
            r["url"]: (r["status"], r["skip_reason"])
            for r in conn.execute("SELECT url, status, skip_reason FROM sites").fetchall()
        }
    assert rows["https://a.com"][0] == "recorded"
    assert rows["https://b.com"][0] == "failed"
    assert "playwright crashed" in rows["https://b.com"][1]
