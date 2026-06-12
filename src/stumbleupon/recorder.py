"""Recorder: 30s vertical video clips of using each fresh site.

Pure logic (mouse-path and scroll-event generation) is unit-tested. The
Playwright wrapper that actually launches a browser is exercised via a
manual smoke command — Playwright is I/O-heavy and slow to spin up.
"""

from __future__ import annotations

import random


def generate_mouse_path(
    duration_sec: float,
    viewport: tuple[int, int],
    seed: int | None = None,
) -> list[tuple[int, int, float]]:
    """Generate a series of (x, y, time_offset) mouse positions over the session.

    The path stays inside the viewport and looks vaguely human — not a
    straight line, not pure random. The same seed produces the same path
    so tests are reproducible.
    """
    rng = random.Random(seed)
    width, height = viewport
    fps = 30
    n_points = max(2, int(duration_sec * fps))

    # Start somewhere in the middle, end somewhere else.
    x, y = rng.randint(width // 4, 3 * width // 4), rng.randint(height // 4, 3 * height // 4)
    target_x = rng.randint(0, width - 1)
    target_y = rng.randint(0, height - 1)

    out: list[tuple[int, int, float]] = []
    for i in range(n_points):
        # Lerp toward the target with some jitter, so the path is smooth-ish.
        t = i / (n_points - 1)
        # Ease in/out curve
        ease = t * t * (3 - 2 * t)
        nx = int(x + (target_x - x) * ease + rng.randint(-20, 20))
        ny = int(y + (target_y - y) * ease + rng.randint(-20, 20))
        # Clamp to viewport
        nx = max(0, min(width - 1, nx))
        ny = max(0, min(height - 1, ny))
        out.append((nx, ny, i / fps))
    return out


def generate_scroll_events(
    duration_sec: float,
    viewport_height: int,
    seed: int | None = None,
) -> list[tuple[float, int]]:
    """Generate (time_offset, scroll_delta_y) events for a recording session.

    Scrolls are spaced out — a real user doesn't scroll every frame. Each
    scroll is a moderate delta (a few hundred pixels). The same seed
    produces the same events so tests are reproducible.
    """
    rng = random.Random(seed)
    n_scrolls = max(1, int(duration_sec / rng.uniform(3.0, 8.0)))
    events: list[tuple[float, int]] = []
    for _ in range(n_scrolls):
        time_offset = rng.uniform(0.5, duration_sec - 0.5)
        # Scrolling down (positive delta_y) by 1/4 to 3/4 of the viewport
        delta = int(viewport_height * rng.uniform(0.25, 0.75))
        events.append((time_offset, delta))
    events.sort(key=lambda e: e[0])
    return events
