"""Captioner: generate TikTok captions + hashtags for recorded clips via Claude.

The pure-logic parts (prompt construction, response parsing, length validation)
are unit-tested. The Claude API call is exercised with a mocked anthropic
client. The orchestrator (`caption_pending_recordings`) is end-to-end
tested with mocked HTTP and a real DB.
"""

from __future__ import annotations

import anthropic

from .config import Settings


# Default model — can be overridden by Settings later
_CLAUDE_MODEL = "claude-sonnet-4-6"

# Caption length bounds (TikTok sweet spot per spec)
_DEFAULT_MIN_CAPTION_LEN = 80
_DEFAULT_MAX_CAPTION_LEN = 150

# Hashtag count bounds
_DEFAULT_MIN_HASHTAGS = 3
_DEFAULT_MAX_HASHTAGS = 5

# Tool schema for structured output
_CAPTION_TOOL = {
    "name": "save_caption",
    "description": "Save the generated caption and hashtags for the TikTok clip.",
    "input_schema": {
        "type": "object",
        "properties": {
            "caption": {
                "type": "string",
                "description": "A short, punchy caption (80-150 chars) describing the site.",
            },
            "hashtags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-5 hashtags for the post (e.g., weirdweb, oldsite, flash).",
            },
        },
        "required": ["caption", "hashtags"],
    },
}


def build_prompt(
    site_info: dict,
    past_captions: list[str],
    tone_guide: str,
) -> list[dict]:
    """Build the messages list for a Claude API caption-generation call."""
    parts = [
        f"Site title: {site_info.get('title', '')}",
        f"URL: {site_info.get('url', '')}",
        f"Description: {site_info.get('description', '')}",
    ]
    if past_captions:
        parts.append("")
        parts.append("Recent successful captions for reference:")
        for c in past_captions:
            parts.append(f"- {c}")
    parts.append("")
    parts.append(
        "Generate a caption (80-150 chars) and 3-5 hashtags for this site. "
        "Follow the tone guide above."
    )
    return [
        {"role": "system", "content": tone_guide},
        {"role": "user", "content": "\n".join(parts)},
    ]


def parse_caption_response(content_blocks: list) -> dict:
    """Extract the {caption, hashtags} payload from an Anthropic response."""
    for block in content_blocks:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "save_caption":
            return dict(block.input)
    raise ValueError("Anthropic response did not contain a save_caption tool_use block")


def validate_caption(
    caption: str,
    hashtags: list[str],
    min_len: int = _DEFAULT_MIN_CAPTION_LEN,
    max_len: int = _DEFAULT_MAX_CAPTION_LEN,
    min_hashtags: int = _DEFAULT_MIN_HASHTAGS,
    max_hashtags: int = _DEFAULT_MAX_HASHTAGS,
) -> list[str]:
    """Return a list of human-readable validation warnings. Empty if valid."""
    warnings: list[str] = []
    if len(caption) < min_len:
        warnings.append(
            f"caption is {len(caption)} chars, below the {min_len}-char minimum"
        )
    if len(caption) > max_len:
        warnings.append(
            f"caption is {len(caption)} chars, above the {max_len}-char maximum"
        )
    if len(hashtags) < min_hashtags:
        warnings.append(
            f"only {len(hashtags)} hashtags, below the {min_hashtags}-hashtag minimum"
        )
    if len(hashtags) > max_hashtags:
        warnings.append(
            f"{len(hashtags)} hashtags, above the {max_hashtags}-hashtag maximum"
        )
    return warnings


async def generate_caption(
    site_info: dict,
    past_captions: list[str],
    tone_guide: str,
    settings: Settings,
) -> tuple[str, list[str]]:
    """Call the Claude API to generate a caption and hashtags."""
    messages = build_prompt(site_info, past_captions, tone_guide)
    async with anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key) as client:
        response = await client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=1024,
            messages=messages,
            tools=[_CAPTION_TOOL],
            tool_choice={"type": "tool", "name": "save_caption"},
        )
    payload = parse_caption_response(response.content)
    return payload["caption"], payload["hashtags"]
