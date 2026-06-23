"""Tests for the recorder module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

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


# ---------------------------------------------------------------------------
# record_site: mute script is wired up via add_init_script (regression)
# ---------------------------------------------------------------------------


def test_mute_page_script_mutes_audio_and_video() -> None:
    """The init script must mute both <audio> and <video> elements, and
    must cover both initial-load and dynamically-injected media."""
    script = recorder._MUTE_PAGE_SCRIPT
    assert "querySelectorAll" in script
    # The selector is a CSS selector list like 'audio,video' (comma, no
    # space) — a single string passed to querySelectorAll. Both element
    # types must be referenced.
    assert "audio" in script
    assert "video" in script
    assert "muted = true" in script
    # DOMContentLoaded catches initial-load media; MutationObserver catches
    # media injected later by the page's own JS (autoplay popups, etc.).
    assert "DOMContentLoaded" in script
    assert "MutationObserver" in script


def test_record_site_installs_mute_script_via_add_init_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """record_site must register the mute script on the browser context
    BEFORE navigating to the target URL.

    Regression: v1 called `page.evaluate(...)` BEFORE `page.goto(...)`.
    That runs on about:blank (no audio/video elements exist), so the
    eval was a no-op. The site's actual autoplay audio leaked into the
    recording. The fix is `context.add_init_script(...)`, which runs
    at document_start on every navigation.
    """
    output_path = tmp_path / "out.webm"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Pre-create the output so the post-recording rename succeeds.
    output_path.write_bytes(b"fake webm")

    # Build the Playwright object graph as a chain of MagicMocks.
    page = MagicMock()
    context = MagicMock()
    context.new_page.return_value = page
    browser = MagicMock()
    browser.new_context.return_value = context
    chromium = MagicMock()
    chromium.launch.return_value = browser
    p = MagicMock()
    p.chromium = chromium

    # sync_playwright() is used as a context manager.
    pw_cm = MagicMock()
    pw_cm.__enter__ = MagicMock(return_value=p)
    pw_cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(recorder, "sync_playwright", lambda: pw_cm)

    # page.evaluate("() => performance.now()") is used to read the page's
    # clock for the mouse/scroll timeline. Return 0 so the timeline math
    # is well-defined.
    page.evaluate.return_value = 0

    # Empty mouse + scroll paths so the recording loop is a no-op;
    # the test only cares about the setup steps.
    monkeypatch.setattr(recorder, "generate_mouse_path", lambda *a, **kw: [])
    monkeypatch.setattr(recorder, "generate_scroll_events", lambda *a, **kw: [])

    # Make Path.glob(<output_path>.parent) find our pre-made fake video.
    monkeypatch.setattr(
        type(output_path.parent), "glob",
        lambda self, pattern: [output_path],
    )

    recorder.record_site(
        site_url="https://example.com",
        output_path=output_path,
        duration_sec=0.5,
    )

    # 1. The mute script was installed on the context.
    assert context.add_init_script.called, (
        "context.add_init_script must be called to mute page audio"
    )
    init_script = context.add_init_script.call_args.args[0]
    assert init_script == recorder._MUTE_PAGE_SCRIPT
    assert "muted = true" in init_script

    # 2. The old buggy path is NOT used. page.evaluate(...) was never
    #    called with a mute script before navigation.
    for call in page.evaluate.call_args_list:
        eval_arg = call.args[0] if call.args else ""
        assert "muted" not in str(eval_arg), (
            "page.evaluate with mute was the v1 bug; "
            "use context.add_init_script instead"
        )

    # 3. add_init_script was called BEFORE goto (order matters — the
    #    init script only runs on subsequent navigations).
    add_init_idx = next(
        i for i, c in enumerate(context.method_calls)
        if c[0] == "add_init_script"
    )
    new_page_idx = next(
        i for i, c in enumerate(context.method_calls)
        if c[0] == "new_page"
    )
    assert add_init_idx < new_page_idx, (
        "add_init_script must be called before new_page()"
    )
