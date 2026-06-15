"""Captioner: generate TikTok captions + hashtags for recorded clips via Claude.

The pure-logic parts (prompt construction, response parsing, length validation)
are unit-tested. The Claude API call is exercised with a mocked anthropic
client. The orchestrator (`caption_pending_recordings`) is end-to-end
tested with mocked HTTP and a real DB.
"""

from __future__ import annotations

from pathlib import Path

import anthropic

from . import queue
from .config import Settings
from .db import get_connection


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


def _load_tone_guide() -> str:
    """Read the channel's tone guide from the repo."""
    # docs/tone-guide.md is at the repo root, two levels up from this file
    tone_path = Path(__file__).resolve().parent.parent.parent / "docs" / "tone-guide.md"
    return tone_path.read_text(encoding="utf-8")


async def caption_pending_recordings(
    db_path: Path,
    settings: Settings,
    recordings_dir: Path,
    limit: int = 5,
) -> dict[int, tuple[str, list[str]]]:
    """For each recorded site that doesn't yet have a clip, generate a caption
    and create the clip row.

    Returns {clip_id: (caption, hashtags)} for the clips that were created.
    Per-site failures are caught: the site is marked 'failed' in the DB and
    the batch continues. The orchestrator never raises.
    """
    recordings_dir = Path(recordings_dir)
    tone_guide = _load_tone_guide()

    sites = queue.get_recorded_sites_without_clips(db_path)
    sites = sites[:limit]
    past_captions = queue.get_posted_caption_examples(db_path, limit=5)

    out: dict[int, tuple[str, list[str]]] = {}
    for site_id, url in sites:
        site_info = {"url": url}
        # Look up title/description from the sites row (cheap)
        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT title, description FROM sites WHERE id=?",
                (site_id,),
            ).fetchone()
        site_info["title"] = row["title"] or ""
        site_info["description"] = row["description"] or ""

        recording_path = str(recordings_dir / f"{site_id}.webm")
        try:
            caption, hashtags = await generate_caption(
                site_info=site_info,
                past_captions=past_captions,
                tone_guide=tone_guide,
                settings=settings,
            )
        except Exception as exc:
            queue.mark_site_failed(db_path, site_id, error=f"{type(exc).__name__}: {exc}")
            print(f"captioner: site {site_id} ({url}) failed: {exc!r}", flush=True)
            continue

        # Validate (non-fatal — just log warnings)
        warnings = validate_caption(caption, hashtags)
        for w in warnings:
            print(f"captioner: site {site_id} warning: {w}", flush=True)

        clip_id = queue.create_clip(
            db_path=db_path,
            site_id=site_id,
            recording_path=recording_path,
            caption=caption,
            hashtags=",".join(hashtags),
        )
        out[clip_id] = (caption, hashtags)
        print(f"captioner: site {site_id} -> clip {clip_id} (caption: {len(caption)} chars, {len(hashtags)} hashtags)", flush=True)

    return out
