"""Tests for the poster module."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stumbleupon import poster
from stumbleupon.models import Clip
from stumbleupon.config import Settings


def _make_clip(**overrides) -> Clip:
    defaults = dict(
        id=42,
        site_id=1,
        caption="A genuinely weird site from 2003 that still works",
        hashtags="weirdweb,oldsite,flash",
        final_path="data/final/42.mp4",
        edited_caption=None,
    )
    defaults.update(overrides)
    return Clip(**defaults)


def _make_settings(**overrides) -> Settings:
    defaults = dict(
        anthropic_api_key="anthropic-key",
        buffer_api_key="buffer-key",
        r2_access_key_id="r2ak",
        r2_secret_access_key="r2sk",
        r2_bucket_name="stumble",
        r2_public_url_base="https://media.example.com",
    )
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# build_caption_text
# ---------------------------------------------------------------------------


def test_build_caption_text_uses_edited_caption_when_set() -> None:
    """The reviewer may have edited the caption; prefer the edited version."""
    clip = _make_clip(
        caption="original caption",
        edited_caption="human-tweaked caption",
    )
    text = poster.build_caption_text(clip)
    assert "human-tweaked caption" in text
    assert "original caption" not in text


def test_build_caption_text_falls_back_to_caption_when_no_edit() -> None:
    clip = _make_clip(caption="original caption", edited_caption=None)
    text = poster.build_caption_text(clip)
    assert "original caption" in text


def test_build_caption_text_appends_hashtags() -> None:
    clip = _make_clip(caption="hello world", hashtags="a,b,c")
    text = poster.build_caption_text(clip)
    assert "hello world" in text
    assert "#a" in text
    assert "#b" in text
    assert "#c" in text


def test_build_caption_text_handles_empty_hashtags() -> None:
    clip = _make_clip(caption="hello world", hashtags="")
    text = poster.build_caption_text(clip)
    assert "hello world" in text


# ---------------------------------------------------------------------------
# build_buffer_request
# ---------------------------------------------------------------------------


def test_build_buffer_request_includes_api_key_in_url() -> None:
    """Buffer's API uses ?access_token=<KEY> in the query string."""
    settings = _make_settings(buffer_api_key="my-buffer-key")
    url, headers, body = poster.build_buffer_request(
        r2_url="https://media.example.com/42.mp4",
        caption_text="hello",
        settings=settings,
    )
    assert "my-buffer-key" in url
    assert "access_token" in url


def test_build_buffer_request_includes_video_url_and_caption() -> None:
    settings = _make_settings()
    url, headers, body = poster.build_buffer_request(
        r2_url="https://media.example.com/42.mp4",
        caption_text="hello world",
        settings=settings,
    )
    # Body should contain the video URL and caption text
    assert "https://media.example.com/42.mp4" in str(body)
    assert "hello world" in str(body)


def test_build_buffer_request_uses_post_method() -> None:
    """Buffer's update creation is a POST."""
    settings = _make_settings()
    url, headers, body = poster.build_buffer_request(
        r2_url="https://example.com/v.mp4",
        caption_text="x",
        settings=settings,
    )
    assert "update" in url


# ---------------------------------------------------------------------------
# upload_to_r2
# ---------------------------------------------------------------------------


def test_upload_to_r2_returns_public_url(tmp_path: Path) -> None:
    """The function should return the public URL of the uploaded file."""
    settings = _make_settings(
        r2_bucket_name="stumble",
        r2_public_url_base="https://media.example.com",
    )
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"fake mp4")

    # Mock the boto3 client
    with patch("stumbleupon.poster.boto3.client") as mock_boto3_client:
        mock_s3 = MagicMock()
        mock_boto3_client.return_value = mock_s3

        url = poster.upload_to_r2(mp4, settings=settings, clip_id=42)

    assert url == "https://media.example.com/42.mp4"
    # Verify boto3 was called with the right bucket
    mock_s3.upload_file.assert_called_once()
    call_args = mock_s3.upload_file.call_args
    assert call_args.args[0] == str(mp4)
    assert call_args.args[1] == "stumble"  # bucket
    assert call_args.args[2] == "42.mp4"  # key


# ---------------------------------------------------------------------------
# post_to_buffer
# ---------------------------------------------------------------------------


def test_post_to_buffer_returns_external_url() -> None:
    """The function should return the external (TikTok) URL from the API response."""
    settings = _make_settings(buffer_api_key="my-key")

    # Mock the httpx.AsyncClient.post
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "updates": [{"id": "abc123", "sent_at": 1234567890, "service_update": "https://tiktok.com/v/abc123"}]
    }
    mock_response.raise_for_status = lambda: None

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return mock_client

        async def __aexit__(self, exc_type, exc, tb):
            return None

    with patch("stumbleupon.poster.httpx.AsyncClient", _FakeAsyncClient):
        url = asyncio.run(poster.post_to_buffer(
            r2_url="https://media.example.com/42.mp4",
            caption_text="hello",
            settings=settings,
        ))

    assert url == "https://tiktok.com/v/abc123"
    mock_client.post.assert_called_once()


# ---------------------------------------------------------------------------
# post_pending_clips (orchestrator)
# ---------------------------------------------------------------------------


def test_post_pending_clips_uploads_and_posts_each_clip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end with mocked I/O. Verifies R2 upload + Buffer post per clip."""
    import sqlite3
    from stumbleupon.db import init_db
    from stumbleupon import poster, queue

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        a = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path, caption, hashtags) "
            "VALUES (?, 'approved', 'r.webm', 'f.mp4', 'cap A', 'a,b')",
            (site_id,),
        ).lastrowid
        b = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path, caption, hashtags) "
            "VALUES (?, 'approved', 'r.webm', 'f.mp4', 'cap B', 'c,d')",
            (site_id,),
        ).lastrowid
        conn.commit()

    finals_dir = tmp_path / "final"
    finals_dir.mkdir()
    (finals_dir / f"{a}.mp4").write_bytes(b"fake mp4 a")
    (finals_dir / f"{b}.mp4").write_bytes(b"fake mp4 b")

    # Track calls. The mock post identifies which clip it's handling by r2_url.
    upload_calls: list[tuple[int, Path]] = []
    post_calls: list[tuple[int, str, str]] = []

    def fake_upload(mp4_path, settings, clip_id):
        upload_calls.append((clip_id, mp4_path))
        return f"https://media.example.com/{clip_id}.mp4"

    async def fake_post(r2_url, caption_text, settings):
        # Extract clip_id from r2_url
        clip_id = int(r2_url.rsplit("/", 1)[-1].rsplit(".", 1)[0])
        post_calls.append((clip_id, r2_url, caption_text))
        return f"https://tiktok.com/v/{clip_id}"

    monkeypatch.setattr(poster, "upload_to_r2", fake_upload)
    monkeypatch.setattr(poster, "post_to_buffer", fake_post)

    settings = _make_settings()
    posted, failed = asyncio.run(poster.post_pending_clips(
        db_path=db_path, settings=settings, finals_dir=finals_dir, limit=5,
    ))

    # Both clips should be uploaded and posted
    assert len(upload_calls) == 2
    assert sorted(c[0] for c in upload_calls) == [a, b]
    assert len(post_calls) == 2
    assert sorted(p[0] for p in post_calls) == [a, b]

    # Results list has both clips; no failures
    assert len(posted) == 2
    assert sorted(r["clip_id"] for r in posted) == [a, b]
    assert failed == []

    # DB state: both clips should have r2_public_url and status='posted'
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, status, r2_public_url FROM clips ORDER BY id").fetchall()
    by_id = {r["id"]: r for r in rows}
    assert by_id[a]["status"] == "posted"
    assert by_id[a]["r2_public_url"] == f"https://media.example.com/{a}.mp4"
    assert by_id[b]["status"] == "posted"
    assert by_id[b]["r2_public_url"] == f"https://media.example.com/{b}.mp4"


def test_post_pending_clips_marks_failures_without_crashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One bad clip doesn't sink the batch — the failure is recorded."""
    import sqlite3
    from stumbleupon.db import init_db
    from stumbleupon import poster, queue

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        a = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path, caption, hashtags) "
            "VALUES (?, 'approved', 'r.webm', 'f.mp4', 'cap A', 'a,b')",
            (site_id,),
        ).lastrowid
        b = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path, caption, hashtags) "
            "VALUES (?, 'approved', 'r.webm', 'f.mp4', 'cap B', 'c,d')",
            (site_id,),
        ).lastrowid
        conn.commit()

    finals_dir = tmp_path / "final"
    finals_dir.mkdir()
    (finals_dir / f"{a}.mp4").write_bytes(b"fake a")
    (finals_dir / f"{b}.mp4").write_bytes(b"fake b")

    def fake_upload(mp4_path, settings, clip_id):
        if clip_id == b:
            raise RuntimeError("R2 upload failed")
        return f"https://media.example.com/{clip_id}.mp4"

    async def fake_post(r2_url, caption_text, settings):
        clip_id = int(r2_url.rsplit("/", 1)[-1].rsplit(".", 1)[0])
        return f"https://tiktok.com/v/{clip_id}"

    monkeypatch.setattr(poster, "upload_to_r2", fake_upload)
    monkeypatch.setattr(poster, "post_to_buffer", fake_post)

    settings = _make_settings()
    posted, failed = asyncio.run(poster.post_pending_clips(
        db_path=db_path, settings=settings, finals_dir=finals_dir, limit=5,
    ))

    # Only 'a' should succeed; 'b' failed (R2 upload error) but the batch continued.
    assert len(posted) == 1
    assert posted[0]["clip_id"] == a
    # The failure should be surfaced with the clip_id and an error message.
    assert len(failed) == 1
    assert failed[0]["clip_id"] == b
    assert "r2 upload" in failed[0]["error"]

    # DB state: 'a' is posted; 'b' is still 'approved' (left for retry).
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, status, r2_public_url FROM clips ORDER BY id").fetchall()
    by_id = {r["id"]: r for r in rows}
    assert by_id[a]["status"] == "posted"
    assert by_id[b]["status"] == "approved"
    assert by_id[b]["r2_public_url"] is None

    # The failure is recorded in the postings table.
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        failed = conn.execute(
            "SELECT clip_id, status, error FROM postings WHERE clip_id=? AND status='failed'",
            (b,),
        ).fetchall()
    assert len(failed) == 1
    assert "r2 upload" in failed[0]["error"]


def test_post_pending_clips_returns_empty_failures_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When all clips post successfully, the failures list is empty (not None, not absent)."""
    import sqlite3
    from stumbleupon.db import init_db
    from stumbleupon import poster

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        a = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path, caption, hashtags) "
            "VALUES (?, 'approved', 'r.webm', 'f.mp4', 'cap A', 'a,b')",
            (site_id,),
        ).lastrowid
        conn.commit()

    finals_dir = tmp_path / "final"
    finals_dir.mkdir()
    (finals_dir / f"{a}.mp4").write_bytes(b"fake a")

    def fake_upload(mp4_path, settings, clip_id):
        return f"https://media.example.com/{clip_id}.mp4"

    async def fake_post(r2_url, caption_text, settings):
        clip_id = int(r2_url.rsplit("/", 1)[-1].rsplit(".", 1)[0])
        return f"https://tiktok.com/v/{clip_id}"

    monkeypatch.setattr(poster, "upload_to_r2", fake_upload)
    monkeypatch.setattr(poster, "post_to_buffer", fake_post)

    settings = _make_settings()
    posted, failed = asyncio.run(poster.post_pending_clips(
        db_path=db_path, settings=settings, finals_dir=finals_dir, limit=5,
    ))

    # Success: posted has the clip, failed is an empty list (not None, not missing).
    assert len(posted) == 1
    assert posted[0]["clip_id"] == a
    assert failed == []


def test_post_pending_clips_returns_failures_with_error_info(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When a clip fails, the failure entry has clip_id and a non-empty error string."""
    import sqlite3
    from stumbleupon.db import init_db
    from stumbleupon import poster

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        a = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, final_path, caption, hashtags) "
            "VALUES (?, 'approved', 'r.webm', 'f.mp4', 'cap A', 'a,b')",
            (site_id,),
        ).lastrowid
        conn.commit()

    finals_dir = tmp_path / "final"
    finals_dir.mkdir()
    (finals_dir / f"{a}.mp4").write_bytes(b"fake a")

    def fake_upload(mp4_path, settings, clip_id):
        raise RuntimeError("network unreachable")

    async def fake_post(r2_url, caption_text, settings):
        return "https://tiktok.com/v/x"  # never reached

    monkeypatch.setattr(poster, "upload_to_r2", fake_upload)
    monkeypatch.setattr(poster, "post_to_buffer", fake_post)

    settings = _make_settings()
    posted, failed = asyncio.run(poster.post_pending_clips(
        db_path=db_path, settings=settings, finals_dir=finals_dir, limit=5,
    ))

    # Nothing posted; one failure surfaced.
    assert posted == []
    assert len(failed) == 1
    entry = failed[0]
    assert set(entry.keys()) == {"clip_id", "error"}
    assert entry["clip_id"] == a
    assert "r2 upload" in entry["error"]
    assert "network unreachable" in entry["error"]
