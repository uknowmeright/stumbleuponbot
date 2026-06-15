"""Tests for the captioner module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stumbleupon import captioner


SAMPLE_TONE_GUIDE = """# Channel voice
Short, punchy, weird-web vibe.
# Length: 80-150 chars
# Hashtags: 3-5 per post
# Banned: NSFW, "in a world where"
"""


SAMPLE_SITE = {
    "url": "https://homestarrunner.com",
    "title": "Homestar Runner",
    "description": "Official website for the Homestar Runner animated web series.",
}


SAMPLE_PAST_CAPTIONS = [
    "Found a site where every page is just a different font rendering of 'no'",
    "A library catalog from 1998 that's still fully searchable",
]


# build_prompt tests
def test_build_prompt_returns_messages_with_system_and_user() -> None:
    messages = captioner.build_prompt(
        site_info=SAMPLE_SITE,
        past_captions=SAMPLE_PAST_CAPTIONS,
        tone_guide=SAMPLE_TONE_GUIDE,
    )
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_build_prompt_system_message_is_tone_guide() -> None:
    messages = captioner.build_prompt(
        site_info=SAMPLE_SITE, past_captions=[], tone_guide=SAMPLE_TONE_GUIDE,
    )
    assert SAMPLE_TONE_GUIDE in messages[0]["content"]


def test_build_prompt_user_message_includes_site_info() -> None:
    messages = captioner.build_prompt(
        site_info=SAMPLE_SITE, past_captions=[], tone_guide=SAMPLE_TONE_GUIDE,
    )
    user_content = messages[1]["content"]
    assert "Homestar Runner" in user_content
    assert "https://homestarrunner.com" in user_content
    assert "animated web series" in user_content


def test_build_prompt_user_message_includes_past_captions_when_provided() -> None:
    messages = captioner.build_prompt(
        site_info=SAMPLE_SITE, past_captions=SAMPLE_PAST_CAPTIONS, tone_guide=SAMPLE_TONE_GUIDE,
    )
    user_content = messages[1]["content"]
    assert "Found a site" in user_content
    assert "library catalog" in user_content


def test_build_prompt_omits_past_captions_section_when_empty() -> None:
    messages = captioner.build_prompt(
        site_info=SAMPLE_SITE, past_captions=[], tone_guide=SAMPLE_TONE_GUIDE,
    )
    user_content = messages[1]["content"]
    assert "Recent successful captions" not in user_content


# parse_caption_response tests
def _make_tool_use_response(input_dict: dict) -> list:
    block = MagicMock()
    block.type = "tool_use"
    block.name = "save_caption"
    block.input = input_dict
    return [block]


def test_parse_caption_response_extracts_caption_and_hashtags() -> None:
    response = _make_tool_use_response({
        "caption": "Homestar is back and the Strong Bad Email archive still slaps",
        "hashtags": ["weirdweb", "oldsite", "flash"],
    })
    payload = captioner.parse_caption_response(response)
    assert payload["caption"] == "Homestar is back and the Strong Bad Email archive still slaps"
    assert payload["hashtags"] == ["weirdweb", "oldsite", "flash"]


def test_parse_caption_response_raises_when_no_tool_use_block() -> None:
    block = MagicMock()
    block.type = "text"
    response = [block]
    with pytest.raises(ValueError, match="tool_use"):
        captioner.parse_caption_response(response)


# validate_caption tests
def test_validate_caption_returns_no_warnings_for_valid_caption() -> None:
    warnings = captioner.validate_caption(
        caption="A genuinely weird site from 2003 that somehow still works perfectly fine today!!",
        hashtags=["weirdweb", "oldsite", "flash"],
    )
    assert warnings == []


def test_validate_caption_warns_when_caption_too_short() -> None:
    warnings = captioner.validate_caption(caption="too short", hashtags=["a", "b", "c"])
    assert any("short" in w.lower() or "80" in w for w in warnings)


def test_validate_caption_warns_when_caption_too_long() -> None:
    warnings = captioner.validate_caption(caption="x" * 200, hashtags=["a", "b", "c"])
    assert any("long" in w.lower() or "150" in w for w in warnings)


def test_validate_caption_warns_when_too_few_hashtags() -> None:
    warnings = captioner.validate_caption(
        caption="a normal length caption that fits the requirements here",
        hashtags=["only", "two"],
    )
    assert any("hashtag" in w.lower() for w in warnings)


def test_validate_caption_warns_when_too_many_hashtags() -> None:
    warnings = captioner.validate_caption(
        caption="a normal length caption that fits the requirements here",
        hashtags=["a", "b", "c", "d", "e", "f", "g"],
    )
    assert any("hashtag" in w.lower() for w in warnings)


@pytest.mark.asyncio
async def test_generate_caption_calls_anthropic_and_returns_payload() -> None:
    from stumbleupon.config import Settings

    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.name = "save_caption"
    mock_block.input = {
        "caption": "Homestar is back and the Strong Bad Email archive still slaps",
        "hashtags": ["weirdweb", "oldsite", "flash"],
    }
    mock_response = MagicMock()
    mock_response.content = [mock_block]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    settings = Settings(
        anthropic_api_key="test-key", buffer_api_key="y",
        r2_access_key_id="a", r2_secret_access_key="b",
        r2_bucket_name="c", r2_public_url_base="d",
    )

    with patch("stumbleupon.captioner.anthropic.AsyncAnthropic", return_value=mock_client):
        caption, hashtags = await captioner.generate_caption(
            site_info=SAMPLE_SITE,
            past_captions=SAMPLE_PAST_CAPTIONS,
            tone_guide=SAMPLE_TONE_GUIDE,
            settings=settings,
        )

    assert caption == "Homestar is back and the Strong Bad Email archive still slaps"
    assert hashtags == ["weirdweb", "oldsite", "flash"]

    call_args = mock_client.messages.create.call_args
    assert call_args.kwargs["model"] == "claude-sonnet-4-6"
    assert "save_caption" in [t["name"] for t in call_args.kwargs["tools"]]
    assert call_args.kwargs["messages"][0]["role"] == "system"
    assert SAMPLE_TONE_GUIDE in call_args.kwargs["messages"][0]["content"]
