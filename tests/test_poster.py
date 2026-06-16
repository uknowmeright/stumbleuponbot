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
