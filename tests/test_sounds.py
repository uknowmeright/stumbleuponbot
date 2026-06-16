"""Tests for the sounds module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stumbleupon import sounds


# ---------------------------------------------------------------------------
# build_creative_center_url
# ---------------------------------------------------------------------------


def test_build_creative_center_url_returns_discover_music_url() -> None:
    """The URL must point to Creative Center's discover/music page."""
    url = sounds.build_creative_center_url()
    assert "tiktok.com" in url
    assert "music" in url


# ---------------------------------------------------------------------------
# build_ytdlp_argv
# ---------------------------------------------------------------------------


def test_build_ytdlp_argv_extracts_audio_as_mp3() -> None:
    """yt-dlp must be called with -x (extract audio) and --audio-format mp3."""
    argv = sounds.build_ytdlp_argv(
        sound_url="https://tiktok.com/sound/abc",
        out_path=Path("/tmp/abc.mp3"),
    )
    assert "-x" in argv
    assert "--audio-format" in argv
    assert "mp3" in argv


def test_build_ytdlp_argv_includes_sound_url() -> None:
    argv = sounds.build_ytdlp_argv(
        sound_url="https://tiktok.com/sound/abc",
        out_path=Path("/tmp/abc.mp3"),
    )
    assert "https://tiktok.com/sound/abc" in argv


def test_build_ytdlp_argv_includes_output_path() -> None:
    """yt-dlp must be told where to write the file."""
    argv = sounds.build_ytdlp_argv(
        sound_url="https://tiktok.com/sound/abc",
        out_path=Path("/tmp/abc.mp3"),
    )
    assert "-o" in argv
    out_idx = argv.index("-o")
    assert argv[out_idx + 1] == "/tmp/abc.mp3"


def test_build_ytdlp_argv_no_playlist_or_warnings() -> None:
    """Avoid surprising downloads (playlists) and noisy stderr."""
    argv = sounds.build_ytdlp_argv(
        sound_url="https://tiktok.com/sound/abc",
        out_path=Path("/tmp/abc.mp3"),
    )
    assert "--no-playlist" in argv
    assert "--no-warnings" in argv


# ---------------------------------------------------------------------------
# parse_trending_rows
# ---------------------------------------------------------------------------


def test_parse_trending_rows_extracts_from_universal_data_json() -> None:
    """TikTok's hydration JSON contains the sound list; parse it first."""
    # Synthetic HTML mimicking TikTok's __UNIVERSAL_DATA_FOR_REHYDRATION__ script
    html = """
    <html>
    <head>
      <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
        {"__DEFAULT_SCOPE__": {"webapp.sound-list": {
          "items": [
            {"id": "abc123", "title": "Sound A", "authorName": "DJ X", "playCount": 1500000},
            {"id": "def456", "title": "Sound B", "authorName": "DJ Y", "playCount": 900000}
          ]
        }}}
      </script>
    </head>
    <body></body>
    </html>
    """
    rows = sounds.parse_trending_rows(html)
    assert len(rows) == 2
    assert rows[0]["tiktok_sound_id"] == "abc123"
    assert rows[0]["title"] == "Sound A"
    assert rows[0]["artist"] == "DJ X"
    assert rows[0]["views"] == 1500000


def test_parse_trending_rows_returns_empty_on_unparseable_html() -> None:
    """If the HTML doesn't match, return []; never raise."""
    rows = sounds.parse_trending_rows("<html><body>not tiktok</body></html>")
    assert rows == []


def test_parse_trending_rows_returns_empty_on_empty_input() -> None:
    assert sounds.parse_trending_rows("") == []


def test_parse_trending_rows_handles_missing_optional_fields() -> None:
    """Some sounds may not have an artist; default to empty string."""
    html = """
    <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {"__DEFAULT_SCOPE__": {"webapp.sound-list": {
      "items": [{"id": "x1", "title": "Untitled", "playCount": 100}]
    }}}
    </script>
    """
    rows = sounds.parse_trending_rows(html)
    assert len(rows) == 1
    assert rows[0]["tiktok_sound_id"] == "x1"
    assert rows[0]["artist"] == ""


# ---------------------------------------------------------------------------
# fetch_html (mocked — we don't hit TikTok in tests)
# ---------------------------------------------------------------------------


def test_fetch_html_returns_page_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_html should return page.content() from Playwright."""
    fake_page = MagicMock()
    fake_page.content.return_value = "<html>tiktok sounds</html>"

    fake_browser = MagicMock()
    fake_browser.new_page.return_value = fake_page

    fake_chromium = MagicMock()
    fake_chromium.launch.return_value = fake_browser

    fake_playwright = MagicMock()
    fake_playwright.chromium = fake_chromium

    # Patch the sync_playwright context manager
    class _FakePW:
        def __init__(self):
            self.chromium = fake_chromium

        def __enter__(self):
            return fake_playwright

        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(sounds, "sync_playwright", lambda: _FakePW())

    html = sounds.fetch_html("https://example.com/music", retries=1, backoff_sec=0.01)
    assert html == "<html>tiktok sounds</html>"


def test_fetch_html_retries_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """One failure should be retried; the second attempt should succeed."""
    fake_page = MagicMock()
    fake_page.content.return_value = "<html>ok</html>"

    fake_browser = MagicMock()

    call_count = {"n": 0}

    def fake_launch(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("first attempt fails")
        return fake_browser

    fake_browser.new_page.return_value = fake_page
    fake_chromium = MagicMock()
    fake_chromium.launch = fake_launch
    fake_playwright = MagicMock()
    fake_playwright.chromium = fake_chromium

    class _FakePW:
        def __init__(self):
            self.chromium = fake_chromium

        def __enter__(self):
            return fake_playwright

        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(sounds, "sync_playwright", lambda: _FakePW())
    monkeypatch.setattr(sounds.time, "sleep", lambda _s: None)  # skip backoff

    html = sounds.fetch_html("https://example.com/music", retries=3, backoff_sec=0.01)
    assert html == "<html>ok</html>"
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# download_audio (mocked — we don't actually download in tests)
# ---------------------------------------------------------------------------


def test_download_audio_runs_ytdlp_via_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """download_audio should invoke yt-dlp with the expected argv."""
    out_path = tmp_path / "abc.mp3"
    called: dict = {}

    def fake_run(argv, *args, **kwargs):
        called["argv"] = argv
        # Pretend yt-dlp created the output file
        out_path.write_bytes(b"fake mp3")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(sounds.subprocess, "run", fake_run)

    sounds.download_audio("https://tiktok.com/sound/abc", out_path, timeout_sec=10)

    argv = called["argv"]
    assert argv[0] == "yt-dlp"
    assert "https://tiktok.com/sound/abc" in argv
    assert str(out_path) in argv


def test_download_audio_creates_parent_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """If the parent dir doesn't exist, create it first."""
    nested = tmp_path / "deep" / "nested" / "abc.mp3"

    def fake_run(argv, *args, **kwargs):
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_bytes(b"fake")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(sounds.subprocess, "run", fake_run)
    sounds.download_audio("https://tiktok.com/sound/abc", nested, timeout_sec=10)
    assert nested.exists()


def test_download_audio_raises_on_ytdlp_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """If yt-dlp exits non-zero, raise (caller logs and skips)."""
    out_path = tmp_path / "abc.mp3"

    def fake_run(argv, *args, **kwargs):
        raise subprocess.CalledProcessError(1, argv, stderr=b"network error")

    monkeypatch.setattr(sounds.subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        sounds.download_audio("https://tiktok.com/sound/abc", out_path, timeout_sec=10)


# ---------------------------------------------------------------------------
# refresh_catalog (orchestrator with mocked I/O)
# ---------------------------------------------------------------------------


def test_refresh_catalog_uploads_top_n_to_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """End-to-end with fakes. Verifies scrape → parse → download → upsert."""
    import sqlite3
    from stumbleupon.db import init_db

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)
    audio_dir = tmp_path / "sounds"
    audio_dir.mkdir()

    fake_rows = [
        {"tiktok_sound_id": "a1", "title": "Sound A", "artist": "X", "views": 1000},
        {"tiktok_sound_id": "b2", "title": "Sound B", "artist": "Y", "views": 800},
    ]

    def fake_fetch(url, **kwargs):
        return "<html>fake</html>"

    monkeypatch.setattr(sounds, "fetch_html", fake_fetch)
    monkeypatch.setattr(sounds, "parse_trending_rows", lambda html: fake_rows)

    # Pretend yt-dlp "downloaded" each sound by writing the file
    def fake_download(sound_url, out_path, **_):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"fake mp3")

    monkeypatch.setattr(sounds, "download_audio", fake_download)

    count = sounds.refresh_catalog(
        db_path=db_path, audio_dir=audio_dir, limit=10,
    )
    assert count == 2

    # DB should have 2 sounds with audio_path set
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT tiktok_sound_id, title, artist, trending_score, audio_path "
            "FROM sounds ORDER BY trending_score DESC"
        ).fetchall()
    assert len(rows) == 2
    by_id = {r["tiktok_sound_id"]: r for r in rows}
    assert by_id["a1"]["title"] == "Sound A"
    assert by_id["a1"]["trending_score"] == 1000
    assert by_id["a1"]["audio_path"] == str(audio_dir / "a1.mp3")
    assert by_id["b2"]["trending_score"] == 800


def test_refresh_catalog_returns_zero_on_empty_scrape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """If the scrape yields 0 rows, return 0 (caller can detect empty catalog)."""
    from stumbleupon.db import init_db

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)
    audio_dir = tmp_path / "sounds"
    audio_dir.mkdir()

    monkeypatch.setattr(sounds, "fetch_html", lambda url, **_: "<html>empty</html>")
    monkeypatch.setattr(sounds, "parse_trending_rows", lambda html: [])

    count = sounds.refresh_catalog(db_path=db_path, audio_dir=audio_dir, limit=10)
    assert count == 0


def test_refresh_catalog_skips_individual_download_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """One bad download doesn't sink the batch."""
    from stumbleupon.db import init_db
    import sqlite3

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)
    audio_dir = tmp_path / "sounds"
    audio_dir.mkdir()

    fake_rows = [
        {"tiktok_sound_id": "ok", "title": "OK", "artist": "A", "views": 100},
        {"tiktok_sound_id": "bad", "title": "Bad", "artist": "B", "views": 200},
    ]

    monkeypatch.setattr(sounds, "fetch_html", lambda url, **_: "<html/>")
    monkeypatch.setattr(sounds, "parse_trending_rows", lambda html: fake_rows)

    def fake_download(sound_url, out_path, **_):
        if "bad" in sound_url:
            raise RuntimeError("yt-dlp failed for bad")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"mp3")

    monkeypatch.setattr(sounds, "download_audio", fake_download)

    count = sounds.refresh_catalog(db_path=db_path, audio_dir=audio_dir, limit=10)
    # Both rows were upserted (with audio_path NULL for the failed one)
    assert count == 2

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT tiktok_sound_id, audio_path FROM sounds ORDER BY tiktok_sound_id"
        ).fetchall()
    by_id = {r["tiktok_sound_id"]: r for r in rows}
    assert by_id["ok"]["audio_path"] is not None
    assert by_id["bad"]["audio_path"] is None
