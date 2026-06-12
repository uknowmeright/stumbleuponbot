"""Tests for the scraper module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from stumbleupon import scraper


SAMPLE_API_RESPONSE: list[dict[str, Any]] = [
    {
        "id": "914fd020-43dc-46ef-8bfd-cc0108986a5a",
        "url": "https://homestarrunner.com",
        "title": "Homestar Runner",
        "description": "Official website for the Homestar Runner animated web series.",
        "category": "fun",
        "og_image": None,
        "like_count": 0,
        "dislike_count": 0,
        "embeddable": None,
    },
    {
        "id": "4fa0594e-5f72-4df6-8890-63157050fa3f",
        "url": "https://epinions.cc",
        "title": "Epinions",
        "description": "App Store review intelligence.",
        "category": "tools",
        "og_image": None,
        "like_count": 0,
        "dislike_count": 0,
        "embeddable": None,
    },
]


@pytest.mark.asyncio
async def test_fetch_sites_from_api_returns_parsed_json() -> None:
    """fetch_sites_from_api hits the URL and returns the parsed JSON list."""
    mock_response = AsyncMock()
    mock_response.json = lambda: SAMPLE_API_RESPONSE
    mock_response.raise_for_status = lambda: None

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("stumbleupon.scraper.httpx.AsyncClient", return_value=mock_client):
        result = await scraper.fetch_sites_from_api(
            api_url="https://example.com/sites",
            api_key="test-key",
        )

    assert result == SAMPLE_API_RESPONSE
    mock_client.get.assert_called_once()
    # Verify the URL and auth header were sent correctly
    call_args = mock_client.get.call_args
    assert call_args.args[0] == "https://example.com/sites"
    assert call_args.kwargs["headers"]["apikey"] == "test-key"
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"


from stumbleupon.scraper import filter_sites


def test_filter_sites_drops_blocked_keyword_in_url() -> None:
    sites = [
        {"url": "https://clean-site.com", "title": "Cool", "description": "A site"},
        {"url": "https://xxx-stuff.com", "title": "Cool", "description": "A site"},
    ]
    out = filter_sites(sites, ad_block_keywords=["xxx"])
    assert [s["url"] for s in out] == ["https://clean-site.com"]


def test_filter_sites_drops_blocked_keyword_in_title() -> None:
    sites = [
        {"url": "https://clean.com", "title": "Cool", "description": "A site"},
        {"url": "https://other.com", "title": "NSFW content", "description": "A site"},
    ]
    out = filter_sites(sites, ad_block_keywords=["nsfw"])
    assert [s["url"] for s in out] == ["https://clean.com"]


def test_filter_sites_drops_blocked_keyword_in_description() -> None:
    sites = [
        {"url": "https://clean.com", "title": "Cool", "description": "A normal site"},
        {"url": "https://other.com", "title": "Cool", "description": "Contains adult content"},
    ]
    out = filter_sites(sites, ad_block_keywords=["adult"])
    assert [s["url"] for s in out] == ["https://clean.com"]


def test_filter_sites_is_case_insensitive() -> None:
    sites = [{"url": "https://x.com", "title": "ADULT stuff", "description": ""}]
    out = filter_sites(sites, ad_block_keywords=["adult"])
    assert out == []


def test_filter_sites_handles_null_title_and_description() -> None:
    """The API can return null for title/description; we shouldn't crash."""
    sites = [
        {"url": "https://x.com", "title": None, "description": None},
        {"url": "https://y.com", "title": "", "description": ""},
    ]
    out = filter_sites(sites, ad_block_keywords=["nsfw"])
    assert len(out) == 2  # nothing to match against, both pass through


def test_filter_sites_preserves_input_order() -> None:
    sites = [
        {"url": "https://a.com", "title": "A", "description": ""},
        {"url": "https://b.com", "title": "B", "description": ""},
        {"url": "https://c.com", "title": "C", "description": ""},
    ]
    out = filter_sites(sites, ad_block_keywords=[])
    assert [s["url"] for s in out] == ["https://a.com", "https://b.com", "https://c.com"]


def test_filter_sites_empty_input() -> None:
    assert filter_sites([], ad_block_keywords=["nsfw"]) == []


from stumbleupon.db import init_db
from stumbleupon.scraper import dedup_against_db


def _insert_site(db_path: Path, url: str) -> None:
    """Insert a site directly via SQL (bypasses the scraper's own insert fn)."""
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO sites (url, title) VALUES (?, ?)", (url, "test"))
        conn.commit()


def test_dedup_drops_sites_already_in_db(tmp_path: Path) -> None:
    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)
    _insert_site(db_path, "https://already-seen.com")

    candidates = [
        {"url": "https://already-seen.com", "title": "X", "description": ""},
        {"url": "https://new-one.com", "title": "Y", "description": ""},
    ]
    out = dedup_against_db(candidates, db_path)
    assert [s["url"] for s in out] == ["https://new-one.com"]


def test_dedup_keeps_all_when_db_is_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    candidates = [
        {"url": "https://a.com", "title": "A", "description": ""},
        {"url": "https://b.com", "title": "B", "description": ""},
    ]
    out = dedup_against_db(candidates, db_path)
    assert len(out) == 2


def test_dedup_handles_empty_input(tmp_path: Path) -> None:
    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)
    assert dedup_against_db([], db_path) == []


def test_dedup_returns_empty_when_all_already_seen(tmp_path: Path) -> None:
    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)
    _insert_site(db_path, "https://a.com")
    _insert_site(db_path, "https://b.com")

    candidates = [
        {"url": "https://a.com", "title": "A", "description": ""},
        {"url": "https://b.com", "title": "B", "description": ""},
    ]
    assert dedup_against_db(candidates, db_path) == []
