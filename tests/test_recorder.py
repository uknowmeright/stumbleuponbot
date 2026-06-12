"""Tests for the recorder module."""

from __future__ import annotations

import pytest

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
