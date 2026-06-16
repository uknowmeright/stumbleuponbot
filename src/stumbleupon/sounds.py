"""Sounds: refresh a local catalog of trending TikTok sounds.

The pure parts (HTML parse, yt-dlp argv builder, URL builder) are
unit-tested. The I/O parts (Playwright fetch, subprocess download)
are exercised via mocked tests + manual smoke command.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from . import queue
from playwright.sync_api import sync_playwright


# TikTok Creative Center URL for trending sounds
_CREATIVE_CENTER_URL = "https://www.tiktok.com/discover/music?lang=en"

# Recent Chrome desktop UA (locale en-US, macOS). Stable enough for v1.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def build_creative_center_url() -> str:
    """Return the Creative Center URL for trending sounds (English locale)."""
    return _CREATIVE_CENTER_URL


def build_ytdlp_argv(sound_url: str, out_path: Path) -> list[str]:
    """Build the yt-dlp argv list to extract audio to mp3.

    Extracts audio (-x), converts to mp3 at best quality, writes to
    out_path. No playlists, no warnings.
    """
    return [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "-o", str(out_path),
        "--no-playlist",
        "--no-warnings",
        sound_url,
    ]


def parse_trending_rows(html: str) -> list[dict]:
    """Extract trending sound rows from Creative Center HTML.

    Looks for the __UNIVERSAL_DATA_FOR_REHYDRATION__ JSON blob first
    (TikTok's hydration pattern). Returns a list of dicts shaped:
        {"tiktok_sound_id": str, "title": str, "artist": str, "views": int}
    Returns [] on any parse error or empty input — never raises.
    """
    if not html:
        return []
    try:
        # Find the hydration script
        m = re.search(
            r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if not m:
            return []
        blob = json.loads(m.group(1))
        # Navigate the blob — schema is brittle but consistent in 2024-26
        scope = blob.get("__DEFAULT_SCOPE__", {})
        sound_data = scope.get("webapp.sound-list", {})
        items = sound_data.get("items", [])
        rows: list[dict] = []
        for item in items:
            sid = item.get("id")
            if not sid:
                continue
            rows.append({
                "tiktok_sound_id": sid,
                "title": item.get("title", "") or "",
                "artist": item.get("authorName", "") or "",
                "views": int(item.get("playCount", 0) or 0),
            })
        return rows
    except Exception:
        return []


def fetch_html(url: str, *, retries: int = 3, backoff_sec: float = 2.0) -> str:
    """Fetch rendered HTML from a URL via Playwright with retry/backoff.

    Uses a fixed Chrome desktop UA, en-US locale, 1280x900 viewport.
    On exception, sleeps `backoff_sec * 2 ** attempt` and retries up
    to `retries` times. Raises the last exception if all retries fail.
    """
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(
                    user_agent=_USER_AGENT,
                    viewport={"width": 1280, "height": 900},
                    locale="en-US",
                    timezone_id="America/New_York",
                )
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # TikTok's trending content is client-rendered; wait briefly
                page.wait_for_timeout(2000)
                content = page.content()
                browser.close()
                return content
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                sleep_for = backoff_sec * (2 ** attempt)
                print(
                    f"sounds: fetch attempt {attempt + 1} failed: {exc!r}; "
                    f"sleeping {sleep_for}s before retry",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(sleep_for)
    assert last_exc is not None
    raise last_exc


def download_audio(sound_url: str, out_path: Path, *, timeout_sec: int = 60) -> None:
    """Download a TikTok sound's audio track to out_path via yt-dlp.

    Creates the parent directory if missing. Uses the argv from
    `build_ytdlp_argv`. Raises CalledProcessError or TimeoutExpired
    on failure (caller logs + skips).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    argv = build_ytdlp_argv(sound_url, out_path)
    subprocess.run(
        argv,
        check=True,
        capture_output=True,
        timeout=timeout_sec,
    )


def refresh_catalog(
    db_path: Path,
    *,
    audio_dir: Path = Path("data/sounds"),
    limit: int = 10,
    fetch_fn=None,
    download_fn=None,
) -> int:
    """Refresh the local sounds catalog from TikTok Creative Center.

    Pipeline: fetch HTML → parse rows → download audio (if missing) →
    upsert each row. Returns the count of upserted rows. Per-row
    download failures are logged and skipped (the row is still
    upserted with audio_path=NULL, so it can be retried on a later
    refresh).
    """
    fetch_fn = fetch_fn or fetch_html
    download_fn = download_fn or download_audio

    try:
        html = fetch_fn(build_creative_center_url())
    except Exception as exc:
        print(
            f"sounds: failed to fetch Creative Center: {exc!r}",
            file=sys.stderr,
            flush=True,
        )
        return 0

    rows = parse_trending_rows(html)[:limit]
    if not rows:
        print(
            "sounds: no trending rows parsed from HTML",
            file=sys.stderr,
            flush=True,
        )
        return 0

    audio_dir = Path(audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for row in rows:
        sid = row["tiktok_sound_id"]
        target = audio_dir / f"{sid}.mp3"
        audio_path: Path | None = target
        if not target.exists():
            try:
                # Creative Center doesn't expose a direct CDN URL; the
                # caller may pass a different download_fn. For v1 we
                # just construct a TikTok sound URL convention.
                sound_url = f"https://www.tiktok.com/sound/{sid}"
                download_fn(sound_url, target)
            except Exception as exc:
                print(
                    f"sounds: download failed for {sid}: {exc!r}; "
                    f"upserting with audio_path=NULL",
                    file=sys.stderr,
                    flush=True,
                )
                audio_path = None
        try:
            queue.upsert_sound(
                db_path,
                tiktok_sound_id=sid,
                title=row["title"],
                artist=row["artist"],
                views=row["views"],
                audio_path=str(audio_path) if audio_path else None,
            )
            count += 1
        except Exception as exc:
            print(
                f"sounds: upsert failed for {sid}: {exc!r}",
                file=sys.stderr,
                flush=True,
            )
    return count
