"""Recorder: 30s vertical video clips of using each fresh site.

Pure logic (mouse-path and scroll-event generation) is unit-tested. The
Playwright wrapper that actually launches a browser is exercised via a
manual smoke command — Playwright is I/O-heavy and slow to spin up.
"""

from __future__ import annotations

import random
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import queue
from .db import get_connection


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


# Init script injected into every page on the recording context. It mutes
# every <audio>/<video> as soon as the DOM is ready AND any time a new
# media element is added (MutationObserver). This catches:
#   - media in the initial HTML (DOMContentLoaded path)
#   - media injected later by JS (autoplay popups, ad iframes, etc.)
#
# It must be installed via `context.add_init_script(...)` BEFORE the page
# is navigated, so the script runs at document_start. Running it via
# `page.evaluate(...)` after navigation is too late — between goto and
# eval, autoplay media has already produced sound.
_MUTE_PAGE_SCRIPT = """
() => {
  const mute = () => {
    document.querySelectorAll('audio,video').forEach(el => { el.muted = true; });
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mute);
  } else {
    mute();
  }
  new MutationObserver(mute).observe(document.documentElement, {
    childList: true, subtree: true,
  });
}
"""


def record_site(
    site_url: str,
    output_path: Path,
    duration_sec: float = 30.0,
    seed: int | None = None,
) -> None:
    """Open a browser, navigate to the site, simulate mouse + scroll activity,
    and write the recording to `output_path` as a webm file.

    Uses Playwright's sync API with `record_video_dir`. The browser tab
    audio is muted so the eventual TikTok sound is the only audio.

    This function will crash loudly if Playwright's browser binaries
    aren't installed (run `playwright install chromium` once).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Playwright writes the recording to this temp dir with a random name;
    # we move it to the final path on success.
    tmp_dir = output_path.parent / f".tmp_record_{output_path.stem}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    viewport = (1080, 1920)
    mouse_path = generate_mouse_path(duration_sec, viewport, seed=seed)
    scroll_events = generate_scroll_events(duration_sec, viewport[1], seed=seed)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                viewport={"width": viewport[0], "height": viewport[1]},
                record_video_dir=str(tmp_dir),
                record_video_size={"width": viewport[0], "height": viewport[1]},
            )
            # Mute all media on every page BEFORE any navigation. The
            # init script runs at document_start on each new document, so
            # auto-playing audio/video is muted before it produces sound.
            context.add_init_script(_MUTE_PAGE_SCRIPT)
            page = context.new_page()
            page.goto(site_url, wait_until="domcontentloaded", timeout=10000)

            # Replay the mouse path and scroll events on a timeline
            start_time = page.evaluate("() => performance.now()") / 1000.0
            for x, y, t in mouse_path:
                # Sleep until the scheduled time
                now = (page.evaluate("() => performance.now()") / 1000.0) - start_time
                if t > now:
                    page.wait_for_timeout(int((t - now) * 1000))
                page.mouse.move(x, y)

            for time_offset, delta_y in scroll_events:
                now = (page.evaluate("() => performance.now()") / 1000.0) - start_time
                if time_offset > now:
                    page.wait_for_timeout(int((time_offset - now) * 1000))
                page.mouse.wheel(0, delta_y)

            # Ensure the full duration has elapsed
            now = (page.evaluate("() => performance.now()") / 1000.0) - start_time
            if duration_sec - now > 0:
                page.wait_for_timeout(int((duration_sec - now) * 1000))

            page.close()
            context.close()
        finally:
            browser.close()

    # Playwright writes the video to tmp_dir with a UUID name. Find and move it.
    videos = list(tmp_dir.glob("*.webm"))
    if not videos:
        raise RuntimeError(f"Playwright did not produce a video in {tmp_dir}")
    if len(videos) > 1:
        # Unexpected but not fatal — pick the first
        pass
    videos[0].rename(output_path)
    # Clean up tmp dir if empty
    try:
        tmp_dir.rmdir()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Orchestrator: drive the recorder across fresh sites
# ---------------------------------------------------------------------------


def _fetch_fresh_sites(db_path: Path, limit: int) -> list[tuple[int, str]]:
    """Return [(site_id, url), ...] for fresh sites, oldest first."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, url FROM sites WHERE status='fresh' "
            "ORDER BY discovered_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [(row["id"], row["url"]) for row in rows]


def record_pending_sites(
    db_path: Path,
    recordings_dir: Path,
    duration_sec: float = 30.0,
    limit: int = 3,
) -> list[tuple[int, str, Path]]:
    """Record up to `limit` fresh sites. Returns [(site_id, url, recording_path), ...]
    for sites that were successfully recorded.

    Per-site failures are caught: the site is marked `failed` in the DB and
    the batch continues. The orchestrator never raises.
    """
    recordings_dir = Path(recordings_dir)
    recordings_dir.mkdir(parents=True, exist_ok=True)

    fresh = _fetch_fresh_sites(db_path, limit)
    out: list[tuple[int, str, Path]] = []
    for site_id, url in fresh:
        output_path = recordings_dir / f"{site_id}.webm"
        try:
            record_site(
                site_url=url,
                output_path=output_path,
                duration_sec=duration_sec,
            )
        except Exception as exc:
            queue.mark_site_failed(db_path, site_id, error=f"{type(exc).__name__}: {exc}")
            print(f"recorder: site {site_id} ({url}) failed: {exc!r}", flush=True)
            continue
        queue.mark_site_recorded(db_path, site_id, recording_path=str(output_path))
        out.append((site_id, url, output_path))
    return out
