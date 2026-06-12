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
