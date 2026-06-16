# Captioner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `captioner` module that generates TikTok captions + hashtags for recorded clips via the Claude API. Reads the channel's tone guide, references past successful captions, and creates `clips` rows in the database ready for human review.

**Architecture:** A 2-task plan. The pure-logic parts (prompt construction, response parsing, length validation) get TDD. The Claude API call gets a mocked test (the anthropic SDK is similar to httpx — we mock `AsyncAnthropic`). The orchestrator (`caption_pending_recordings`) wires the call to the DB: it finds sites with `status='recorded'` that don't yet have a clip, generates captions for each, and creates the corresponding `clips` row with `status='pending'`. The pipeline (`cmd_run`) gains a third stage: scrape → record → caption.

**Tech Stack:** Python 3.11+, `anthropic>=0.40` (async SDK), existing `models.py` / `db.py` / `queue.py` / `config.py`. Reads `docs/tone-guide.md` from the repo root (created earlier; see commit `3489f40`).

**Scope:** Tasks 1-2. Composer, sounds, reviewer, poster, launchd plists are separate future plans.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Add `anthropic>=0.40` to dependencies |
| `src/stumbleupon/captioner.py` | All captioner functions: `build_prompt`, `parse_caption_response`, `validate_caption`, `generate_caption`, top-level `caption_pending_recordings` |
| `src/stumbleupon/queue.py` | Add `get_posted_caption_examples`, `get_recorded_sites_without_clips`, `create_clip` |
| `src/stumbleupon/main.py` | Extend `cmd_run` to call captioner after recorder |
| `tests/test_captioner.py` | TDD tests for all 5 functions (build_prompt, parse, validate, generate, orchestrator) |
| `tests/test_queue.py` | Tests for the 3 new queue functions |
| `README.md` | Mention `captioner.py` in layout, update Roadmap bullet |

No new HTML fixtures needed.

---

## Task 1: Captioner module — pure logic + mocked API (TDD)

**Files:**
- Modify: `pyproject.toml` (add `anthropic>=0.40` to dependencies)
- Create: `src/stumbleupon/captioner.py` (skeleton + 4 functions)
- Create: `tests/test_captioner.py` (TDD tests for all 4 functions)

- [ ] **Step 1: Add `anthropic` to `pyproject.toml`**

In `pyproject.toml`, change the `dependencies` list to:

```toml
dependencies = [
    "python-dotenv>=1.0.0",
    "httpx>=0.27.0",
    "playwright>=1.40.0",
    "anthropic>=0.40.0",
]
```

- [ ] **Step 2: Install the new dep**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/pip install -e ".[dev]"`
Expected: pip installs `anthropic` and any of its deps. `pip show anthropic | head -3` should show version 0.40 or newer.

Note: this only installs the Python `anthropic` package. The user provides the `ANTHROPIC_API_KEY` via `.env`. No browser-style binary download is needed.

- [ ] **Step 3: Write the failing tests for `build_prompt` and `parse_caption_response`**

Create `tests/test_captioner.py` with this initial content (more tests added later):

```python
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


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------


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
        site_info=SAMPLE_SITE,
        past_captions=[],
        tone_guide=SAMPLE_TONE_GUIDE,
    )
    assert SAMPLE_TONE_GUIDE in messages[0]["content"]


def test_build_prompt_user_message_includes_site_info() -> None:
    messages = captioner.build_prompt(
        site_info=SAMPLE_SITE,
        past_captions=[],
        tone_guide=SAMPLE_TONE_GUIDE,
    )
    user_content = messages[1]["content"]
    assert "Homestar Runner" in user_content
    assert "https://homestarrunner.com" in user_content
    assert "animated web series" in user_content


def test_build_prompt_user_message_includes_past_captions_when_provided() -> None:
    messages = captioner.build_prompt(
        site_info=SAMPLE_SITE,
        past_captions=SAMPLE_PAST_CAPTIONS,
        tone_guide=SAMPLE_TONE_GUIDE,
    )
    user_content = messages[1]["content"]
    assert "Found a site" in user_content
    assert "library catalog" in user_content


def test_build_prompt_omits_past_captions_section_when_empty() -> None:
    messages = captioner.build_prompt(
        site_info=SAMPLE_SITE,
        past_captions=[],
        tone_guide=SAMPLE_TONE_GUIDE,
    )
    user_content = messages[1]["content"]
    # No "Recent successful captions" header when there are no past captions
    assert "Recent successful captions" not in user_content


# ---------------------------------------------------------------------------
# parse_caption_response
# ---------------------------------------------------------------------------


def _make_tool_use_response(input_dict: dict) -> list:
    """Build a fake Anthropic response content list with one tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
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
    # Plain text block, no tool_use
    block = MagicMock()
    block.type = "text"
    response = [block]
    with pytest.raises(ValueError, match="tool_use"):
        captioner.parse_caption_response(response)


# ---------------------------------------------------------------------------
# validate_caption
# ---------------------------------------------------------------------------


def test_validate_caption_returns_no_warnings_for_valid_caption() -> None:
    warnings = captioner.validate_caption(
        caption="A genuinely weird site from 2003 that somehow still works",
        hashtags=["weirdweb", "oldsite", "flash"],
    )
    assert warnings == []


def test_validate_caption_warns_when_caption_too_short() -> None:
    warnings = captioner.validate_caption(
        caption="too short",
        hashtags=["a", "b", "c"],
    )
    assert any("short" in w.lower() or "80" in w for w in warnings)


def test_validate_caption_warns_when_caption_too_long() -> None:
    warnings = captioner.validate_caption(
        caption="x" * 200,
        hashtags=["a", "b", "c"],
    )
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
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python -m pytest tests/test_captioner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stumbleupon.captioner'`.

- [ ] **Step 5: Implement `build_prompt`, `parse_caption_response`, and `validate_caption`**

Create `src/stumbleupon/captioner.py` with this content (`generate_caption` added in Step 7):

```python
"""Captioner: generate TikTok captions + hashtags for recorded clips via Claude.

The pure-logic parts (prompt construction, response parsing, length validation)
are unit-tested. The Claude API call is exercised with a mocked anthropic
client. The orchestrator (`caption_pending_recordings`) is end-to-end
tested with mocked HTTP and a real DB.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

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
    """Build the messages list for a Claude API caption-generation call.

    The system message carries the channel's tone guide; the user message
    carries the site info plus 3-5 recent successful captions as examples.
    Tool use is configured separately in `generate_caption` (not in the
    messages list).
    """
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
    """Extract the {caption, hashtags} payload from an Anthropic response.

    Looks for a content block of type 'tool_use' with name 'save_caption'
    and returns its input. Raises ValueError if no such block is found.
    """
    for block in content_blocks:
        # Real blocks have a `.type` attribute; MagicMock objects in tests
        # have the same attribute. We use getattr for safety.
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
    """Return a list of human-readable validation warnings. Empty if valid.

    Validation is non-fatal — the captioner logs warnings but accepts the
    caption anyway. Reviewer humans can still approve/reject.
    """
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python -m pytest tests/test_captioner.py -v`
Expected: 12 passed (5 build_prompt + 2 parse + 5 validate).

- [ ] **Step 7: Write the failing test for `generate_caption` (mocked API)**

Append to `tests/test_captioner.py`:

```python
@pytest.mark.asyncio
async def test_generate_caption_calls_anthropic_and_returns_payload() -> None:
    """End-to-end with mocked Anthropic. Verifies the prompt is built and the
    tool_use payload is extracted correctly."""
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
        anthropic_api_key="test-key",
        buffer_api_key="y",
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

    # Verify the API was called with the right model and tool
    call_args = mock_client.messages.create.call_args
    assert call_args.kwargs["model"] == "claude-sonnet-4-6"
    assert "save_caption" in [t["name"] for t in call_args.kwargs["tools"]]
    # Messages were built from the tone guide + site info
    assert call_args.kwargs["messages"][0]["role"] == "system"
    assert SAMPLE_TONE_GUIDE in call_args.kwargs["messages"][0]["content"]
```

- [ ] **Step 8: Run the test to verify it fails**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python -m pytest tests/test_captioner.py::test_generate_caption_calls_anthropic_and_returns_payload -v`
Expected: FAIL (no `generate_caption` function yet).

- [ ] **Step 9: Implement `generate_caption`**

Append to `src/stumbleupon/captioner.py`:

```python
async def generate_caption(
    site_info: dict,
    past_captions: list[str],
    tone_guide: str,
    settings: Settings,
) -> tuple[str, list[str]]:
    """Call the Claude API to generate a caption and hashtags.

    Returns (caption, hashtags). Raises on API error or unexpected
    response shape (caller is expected to handle these — see
    `caption_pending_recordings` in Task 2).
    """
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
```

- [ ] **Step 10: Run the test to verify it passes**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python -m pytest tests/test_captioner.py -v`
Expected: 13 passed (12 from Steps 5-6 + 1 new).

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml src/stumbleupon/captioner.py tests/test_captioner.py
git commit -m "feat(captioner): add build_prompt, parse, validate, and generate_caption"
```

---

## Task 2: Queue additions + orchestrator + `cmd_run` wiring + README

**Files:**
- Modify: `src/stumbleupon/queue.py` (add 3 functions)
- Modify: `tests/test_queue.py` (add 5 tests for the new functions)
- Modify: `src/stumbleupon/captioner.py` (add `caption_pending_recordings` orchestrator)
- Modify: `tests/test_captioner.py` (add 2 tests for the orchestrator with mocked `generate_caption`)
- Modify: `src/stumbleupon/main.py` (extend `cmd_run` to call captioner after recorder)
- Modify: `README.md` (mention `captioner.py` in layout, update Roadmap bullet)

- [ ] **Step 1: Write the failing tests for the 3 new queue functions**

Append to `tests/test_queue.py`:

```python
def test_get_posted_caption_examples_returns_published_captions(db_path: Path) -> None:
    """Returns the `caption` text of recently `posted` clips, newest first."""
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        for i, status in enumerate(["posted", "posted", "pending", "posted", "failed"]):
            conn.execute(
                "INSERT INTO clips (site_id, status, caption, hashtags) VALUES (?, ?, ?, ?)",
                (site_id, status, f"caption-{i}", "a,b"),
            )
        conn.commit()

    examples = queue.get_posted_caption_examples(db_path, limit=5)
    # Only the 3 'posted' captions, newest first
    assert examples == ["caption-2", "caption-1", "caption-0"]


def test_get_posted_caption_examples_respects_limit(db_path: Path) -> None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        for i in range(5):
            conn.execute(
                "INSERT INTO clips (site_id, status, caption) VALUES (?, 'posted', ?)",
                (site_id, f"cap-{i}"),
            )
        conn.commit()
    assert len(queue.get_posted_caption_examples(db_path, limit=2)) == 2


def test_get_recorded_sites_without_clips_picks_recorded_sites(db_path: Path) -> None:
    """Returns (id, url) for sites with status='recorded' that have no clip row."""
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO sites (url, status) VALUES ('https://a.com', 'recorded')")
        conn.execute("INSERT INTO sites (url, status) VALUES ('https://b.com', 'fresh')")
        conn.execute("INSERT INTO sites (url, status) VALUES ('https://c.com', 'failed')")
        site_with_clip = conn.execute(
            "INSERT INTO sites (url, status) VALUES ('https://d.com', 'recorded')"
        ).lastrowid
        conn.execute(
            "INSERT INTO clips (site_id, status) VALUES (?, 'pending')",
            (site_with_clip,),
        )
        conn.commit()

    rows = queue.get_recorded_sites_without_clips(db_path)
    urls = [url for _id, url in rows]
    assert urls == ["https://a.com"]


def test_create_clip_inserts_a_new_clip_row(db_path: Path) -> None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        conn.commit()

    clip_id = queue.create_clip(
        db_path,
        site_id=site_id,
        recording_path="data/recordings/1.webm",
        caption="A test caption",
        hashtags="weirdweb,oldsite,flash",
    )
    assert clip_id > 0

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT site_id, recording_path, caption, hashtags, status FROM clips WHERE id=?",
            (clip_id,),
        ).fetchone()
    assert row["site_id"] == site_id
    assert row["recording_path"] == "data/recordings/1.webm"
    assert row["caption"] == "A test caption"
    assert row["hashtags"] == "weirdweb,oldsite,flash"
    assert row["status"] == "pending"


def test_create_clip_returns_unique_ids(db_path: Path) -> None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        conn.commit()
    a = queue.create_clip(db_path, site_id, "a.webm", "cap a", "a")
    b = queue.create_clip(db_path, site_id, "b.webm", "cap b", "b")
    assert a != b
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python -m pytest tests/test_queue.py -v`
Expected: 5 failures on the new tests.

- [ ] **Step 3: Implement the 3 new functions in `queue.py`**

Append to `src/stumbleupon/queue.py`:

```python
# ---------------------------------------------------------------------------
# Captioner-driven queries + transitions
# ---------------------------------------------------------------------------


def get_posted_caption_examples(db_path: Path, limit: int = 5) -> list[str]:
    """Return the captions of up to `limit` recently-posted clips, newest first.

    Used by the captioner to seed its prompt with examples of what
    on-brand copy looks like. The captions come from clips whose status
    reached 'posted' (i.e., real published content), not just approved.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT caption FROM clips WHERE status='posted' "
            "AND caption IS NOT NULL AND caption != '' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [row["caption"] for row in rows]


def get_recorded_sites_without_clips(db_path: Path) -> list[tuple[int, str]]:
    """Return [(id, url), ...] for sites with status='recorded' that don't yet
    have a clip row. The recording_path is NOT included here — the orchestrator
    computes it by convention as `data/recordings/<site_id>.webm`.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT s.id, s.url FROM sites s "
            "WHERE s.status='recorded' "
            "AND NOT EXISTS (SELECT 1 FROM clips c WHERE c.site_id = s.id) "
            "ORDER BY s.discovered_at ASC"
        ).fetchall()
    return [(row["id"], row["url"]) for row in rows]


def create_clip(
    db_path: Path,
    site_id: int,
    recording_path: str,
    caption: str,
    hashtags: str,
) -> int:
    """Insert a new clip row with status='pending' (awaiting human review).

    `hashtags` is stored as a comma-separated string (the schema's
    convention). The clip_id is returned so the caller can map back.
    """
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO clips (site_id, recording_path, caption, hashtags, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (site_id, recording_path, caption, hashtags),
        )
        return cur.lastrowid or 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python -m pytest tests/test_queue.py -v`
Expected: 17 passed (12 prior + 5 new).

- [ ] **Step 5: Write the failing tests for the orchestrator**

Append to `tests/test_captioner.py`:

```python
def test_caption_pending_recordings_writes_clips_to_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end with mocked Claude. Verifies clips are created with the right fields."""
    from stumbleupon.db import init_db
    from stumbleupon import captioner, queue

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    # Seed a 'recorded' site with no clip yet
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        site_id = conn.execute(
            "INSERT INTO sites (url, title, description, status) VALUES (?, ?, ?, 'recorded')",
            ("https://a.com", "Cool Site", "A test site"),
        ).lastrowid
        conn.commit()
    recordings_dir = tmp_path / "recordings"

    async def fake_generate_caption(site_info, past_captions, tone_guide, settings):
        return "A great caption about this site", ["weirdweb", "oldsite", "flash"]

    monkeypatch.setattr(captioner, "generate_caption", fake_generate_caption)

    from stumbleupon.config import Settings
    settings = Settings(
        anthropic_api_key="x", buffer_api_key="y",
        r2_access_key_id="a", r2_secret_access_key="b",
        r2_bucket_name="c", r2_public_url_base="d",
    )

    results = asyncio.run(captioner.caption_pending_recordings(
        db_path=db_path, settings=settings, recordings_dir=recordings_dir,
    ))

    assert len(results) == 1
    clip_id, (caption, hashtags) = list(results.items())[0]
    assert caption == "A great caption about this site"
    assert hashtags == ["weirdweb", "oldsite", "flash"]

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT site_id, recording_path, caption, hashtags, status FROM clips WHERE id=?",
            (clip_id,),
        ).fetchone()
    assert row["site_id"] == site_id
    assert row["recording_path"] == str(recordings_dir / f"{site_id}.webm")
    assert row["status"] == "pending"
    assert row["caption"] == "A great caption about this site"
    assert row["hashtags"] == "weirdweb,oldsite,flash"


def test_caption_pending_recordings_handles_per_site_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad site doesn't sink the batch."""
    from stumbleupon.db import init_db
    from stumbleupon import captioner, queue

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        a = conn.execute(
            "INSERT INTO sites (url, status) VALUES ('https://a.com', 'recorded')"
        ).lastrowid
        b = conn.execute(
            "INSERT INTO sites (url, status) VALUES ('https://b.com', 'recorded')"
        ).lastrowid
        conn.commit()
    recordings_dir = tmp_path / "recordings"

    async def fake_generate_caption(site_info, past_captions, tone_guide, settings):
        if "b.com" in site_info["url"]:
            raise RuntimeError("claude api 500")
        return f"caption for {site_info['url']}", ["weirdweb"]

    monkeypatch.setattr(captioner, "generate_caption", fake_generate_caption)

    from stumbleupon.config import Settings
    settings = Settings(
        anthropic_api_key="x", buffer_api_key="y",
        r2_access_key_id="a", r2_secret_access_key="b",
        r2_bucket_name="c", r2_public_url_base="d",
    )

    results = asyncio.run(captioner.caption_pending_recordings(
        db_path=db_path, settings=settings, recordings_dir=recordings_dir,
    ))

    # 'a' succeeded; 'b' failed and was marked failed in the sites table
    assert len(results) == 1
    with sqlite3.connect(db_path) as conn:
        rows = {
            r["url"]: (r["status"], r["skip_reason"])
            for r in conn.execute("SELECT url, status, skip_reason FROM sites").fetchall()
        }
    assert rows["https://a.com"][0] == "recorded"  # site stays recorded (it WAS recorded successfully)
    assert rows["https://b.com"][0] == "failed"
    assert "claude api 500" in rows["https://b.com"][1]
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python -m pytest tests/test_captioner.py -v`
Expected: 2 failures on the new orchestrator tests.

NOTE: the test imports `asyncio` at the top of the function body. Make sure `asyncio` is importable. The test file should have `import asyncio` near the top (added in this step). If not present, add it.

- [ ] **Step 7: Implement `caption_pending_recordings`**

Append to `src/stumbleupon/captioner.py`:

```python
from . import queue
from .db import get_connection


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
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python -m pytest tests/test_captioner.py -v`
Expected: 15 passed (13 from Task 1 + 2 new).

- [ ] **Step 9: Extend `cmd_run` in `main.py` to call captioner after recorder**

In `src/stumbleupon/main.py`, replace `cmd_run` with:

```python
def cmd_run(args: argparse.Namespace) -> int:
    """One pipeline pass: scrape, record, then caption."""
    import asyncio
    from pathlib import Path

    from .captioner import caption_pending_recordings
    from .config import load_settings
    from .db import init_db
    from .recorder import record_pending_sites
    from .scraper import scrape

    db_path = Path("data/stumbleupon.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    settings = load_settings()
    new_sites = asyncio.run(scrape(db_path=db_path, settings=settings))
    print(f"scrape: {len(new_sites)} new sites queued for review", file=sys.stderr)

    recordings_dir = db_path.parent / "recordings"
    recorded = record_pending_sites(
        db_path=db_path,
        recordings_dir=recordings_dir,
        duration_sec=30.0,
        limit=3,
    )
    print(f"recorder: {len(recorded)} sites recorded to {recordings_dir}", file=sys.stderr)

    clips = asyncio.run(caption_pending_recordings(
        db_path=db_path,
        settings=settings,
        recordings_dir=recordings_dir,
        limit=5,
    ))
    print(f"captioner: {len(clips)} clips queued for review", file=sys.stderr)
    return 0
```

Verify the CLI still works:

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python -m stumbleupon.main --help`
Expected: usage message listing all 5 subcommands.

- [ ] **Step 10: Update the README**

In `README.md`:

1. In the "Project layout" section, add a `captioner.py` line in alphabetical position:

```
├── captioner.py  # Claude API → caption + hashtags
```

2. In the "Roadmap" section, change the "Captioner" bullet:

```
- Captioner (Claude + tone guide) — done
```

- [ ] **Step 11: Run the full test suite**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python -m pytest -v`
Expected: 74 passed (57 prior + 17 new: 13 captioner pure + 2 captioner orchestrator + 2 ??? — wait, let me recount).

Counting:
- Task 1 adds 13 captioner tests (5 build_prompt + 2 parse + 5 validate + 1 generate = 13)
- Task 2 adds 5 queue tests + 2 orchestrator tests = 7 more
- Total new: 20 tests
- Prior: 57
- Expected: 77 passed

NOTE: the orchestrator tests use `asyncio.run(...)` to call the async orchestrator, so they don't need `pytest.mark.asyncio` or `pytest-asyncio`. The single `test_generate_caption_calls_anthropic_and_returns_payload` test does need `pytest.mark.asyncio` and runs as a real async test.

- [ ] **Step 12: Commit**

```bash
git add src/stumbleupon/queue.py src/stumbleupon/captioner.py src/stumbleupon/main.py tests/test_queue.py tests/test_captioner.py README.md
git commit -m "feat(captioner): add orchestrator + wire into cmd_run"
```

---

## Self-Review

**1. Spec coverage (§5.3):**
- "For each clip, builds a prompt with: site title, URL, description, sample of past successful captions (3-5 from `posted` clips), and the channel's tone guide" → ✓ Task 1 (`build_prompt`) + Task 2 (`get_posted_caption_examples`)
- "The tone guide lives at `docs/tone-guide.md` — read on every call" → ✓ Task 2 (`_load_tone_guide`)
- "Calls Claude API (`claude-sonnet-4-6`)" → ✓ Task 1 (hardcoded `_CLAUDE_MODEL`)
- "Output enforced via tool use / structured output: `{caption: str, hashtags: [str]}`" → ✓ Task 1 (`_CAPTION_TOOL` + `parse_caption_response`)
- "Length target: 80-150 chars for the caption (TikTok sweet spot)" → ✓ Task 1 (`_DEFAULT_MIN/MAX_CAPTION_LEN`, `validate_caption`)
- "Saves raw LLM response to logs for debugging" → partial. We log a summary to stderr but don't save the full response. The spec's `data/logs/` dir isn't yet created. Acceptable for v1; a follow-up plan can add full-response logging.
- "Returns: `clip_id → (caption, hashtags)` mapping" → ✓ Task 2 (`dict[int, tuple[str, list[str]]]` return type)
- "Failure mode: per-site try/except" → ✓ Task 2 (the orchestrator catches and continues)

**2. Placeholder scan:** No "TBD" or "TODO" in any code block. All functions fully implemented.

**3. Type consistency:** Cross-checked:
- `build_prompt(site_info: dict, past_captions: list[str], tone_guide: str) -> list[dict]` — consistent across tests and orchestrator
- `parse_caption_response(content_blocks: list) -> dict` — consistent
- `validate_caption(caption: str, hashtags: list[str], min_len=80, max_len=150, min_hashtags=3, max_hashtags=5) -> list[str]` — consistent
- `generate_caption(site_info, past_captions, tone_guide, settings) -> tuple[str, list[str]]` — consistent
- `caption_pending_recordings(db_path, settings, recordings_dir, limit=5) -> dict[int, tuple[str, list[str]]]` — consistent
- `get_posted_caption_examples(db_path, limit=5) -> list[str]`
- `get_recorded_sites_without_clips(db_path) -> list[tuple[int, str]]`
- `create_clip(db_path, site_id, recording_path, caption, hashtags) -> int`

**4. Notable scope decisions:**
- The tone guide is read from `docs/tone-guide.md` (relative to the repo root) on every call. The orchestrator's `_load_tone_guide` does the path resolution. No caching for v1.
- `parse_caption_response` checks both `type == "tool_use"` AND `name == "save_caption"` to be robust against Claude returning multiple content blocks (e.g., a text block and a tool_use block).
- The `recording_path` is NOT stored in the `sites` table. The orchestrator computes it by convention as `data/recordings/<site_id>.webm`. This is consistent with how `mark_site_recorded`'s arg is currently unused (sensible forward-compatibility).
- `validate_caption` is non-fatal — it logs warnings but accepts the caption. Reviewer humans can still approve/reject. The orchestrator doesn't enforce length.
- The `asyncio.run` in the orchestrator tests avoids needing `pytest-asyncio` for those tests. The single `test_generate_caption_calls_anthropic_and_returns_payload` does need it (already in place from Task 1).
- `caption_pending_recordings` uses async because `generate_caption` is async. `asyncio.run` is called in `cmd_run`.

**5. What this plan does NOT do (deferred to future plans):**
- Composer (ffmpeg)
- Sounds (TikTok trending scrape)
- Reviewer (CLI)
- Poster (Buffer + R2)
- launchd plists
- Logging full LLM responses to `data/logs/` (just stderr summary for v1)
- Adding `anthropic_model` to `Settings` (hardcoded for v1)

**6. Manual smoke test:** the plan ends with running `python -m stumbleupon.main --help`. The user can manually run `python -m stumbleupon.main run` with a real `ANTHROPIC_API_KEY` to see the full pipeline in action. The unit tests cover all the pure logic; the Claude API call is verified by mocked-HTTP test.
