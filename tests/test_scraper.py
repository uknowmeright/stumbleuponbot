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


from stumbleupon.models import Site
from stumbleupon.scraper import insert_new_sites


def test_insert_new_sites_returns_site_rows(tmp_path: Path) -> None:
    from stumbleupon.db import init_db
    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    candidates = [
        {"url": "https://a.com", "title": "A", "description": "Desc A", "category": "fun"},
        {"url": "https://b.com", "title": "B", "description": None, "category": None},
    ]
    rows = insert_new_sites(candidates, db_path)

    assert len(rows) == 2
    assert all(isinstance(r, Site) for r in rows)
    assert {r.url for r in rows} == {"https://a.com", "https://b.com"}
    assert all(r.status == "fresh" for r in rows)
    assert all(r.source == "stumbleupon.cc" for r in rows)


def test_insert_new_sites_uses_url_unique_constraint(tmp_path: Path) -> None:
    """If the same URL is inserted twice in one call, the second is silently dropped."""
    from stumbleupon.db import init_db
    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    candidates = [
        {"url": "https://dup.com", "title": "First", "description": ""},
        {"url": "https://dup.com", "title": "Second", "description": ""},
    ]
    rows = insert_new_sites(candidates, db_path)
    assert len(rows) == 1
    assert rows[0].title == "First"  # first-write wins


def test_insert_new_sites_persists_to_db(tmp_path: Path) -> None:
    from stumbleupon.db import init_db
    from stumbleupon.db import get_connection
    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    candidates = [{"url": "https://x.com", "title": "X", "description": "Hello"}]
    insert_new_sites(candidates, db_path)

    with get_connection(db_path) as conn:
        row = conn.execute("SELECT url, title, status FROM sites").fetchone()
    assert row["url"] == "https://x.com"
    assert row["title"] == "X"
    assert row["status"] == "fresh"


@pytest.mark.asyncio
async def test_scrape_orchestrator_wires_fetch_filter_dedup_insert(tmp_path: Path) -> None:
    """End-to-end with mocked HTTP. Verifies the full pipeline runs in order."""
    from unittest.mock import patch, AsyncMock

    from stumbleupon.config import Settings
    from stumbleupon.db import init_db
    from stumbleupon.scraper import scrape

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    # Pre-insert one site so dedup has something to drop.
    _insert_site(db_path, "https://already-seen.com")

    api_response = [
        {"id": "1", "url": "https://already-seen.com", "title": "X", "description": "x"},
        {"id": "2", "url": "https://fresh.com", "title": "Fresh Site", "description": "A new one"},
        {"id": "3", "url": "https://nsfw.com", "title": "NSFW junk", "description": "blocked"},
    ]

    mock_response = AsyncMock()
    mock_response.json = lambda: api_response
    mock_response.raise_for_status = lambda: None

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    settings = Settings(
        anthropic_api_key="x", buffer_api_key="y",
        r2_access_key_id="a", r2_secret_access_key="b",
        r2_bucket_name="c", r2_endpoint_url="e", r2_public_url_base="d",
        stumbleupon_api_key="test-key",
        ad_block_keywords=["nsfw"],
    )

    with patch("stumbleupon.scraper.httpx.AsyncClient", return_value=mock_client):
        new_sites = await scrape(db_path=db_path, settings=settings)

    # Only "fresh.com" survives: not in DB, not blocked
    assert len(new_sites) == 1
    assert new_sites[0].url == "https://fresh.com"
    assert new_sites[0].status == "fresh"
