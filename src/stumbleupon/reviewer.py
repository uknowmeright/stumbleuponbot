"""Reviewer: human-in-the-loop CLI for approving/rejecting pending clips.

The pure-logic parts (input parsing, summary formatting) are unit-tested.
The I/O parts (ffmpeg thumbnail extraction, `open` on macOS, interactive
prompt loop) are exercised via manual smoke commands.

Persistence: quitting mid-session just leaves clips in 'pending' status;
the next run picks up where it left off. No special session state needed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import queue
from .models import Clip


# Action names (the parse_user_choice return type)
APPROVE = "approve"
REJECT = "reject"
EDIT = "edit"
SKIP = "skip"
QUIT = "quit"

# Aliases for short-form input
_ALIASES = {
    "a": APPROVE, "approve": APPROVE,
    "r": REJECT, "reject": REJECT,
    "e": EDIT, "edit": EDIT,
    "s": SKIP, "skip": SKIP,
    "q": QUIT, "quit": QUIT,
}


def parse_user_choice(input_str: str) -> str | None:
    """Parse the user's menu input into an action name.

    Accepts single letters (a/r/e/s/q), full words, and any case.
    Returns None for empty or unrecognized input.
    """
    if not input_str:
        return None
    return _ALIASES.get(input_str.strip().lower())


def format_clip_summary(clip: Clip, sound_used: str | None = None) -> str:
    """Pretty-print a clip for the reviewer's interactive prompt."""
    parts = [
        f"Clip {clip.id} (site {clip.site_id})",
        f"  File: {clip.final_path}",
    ]
    if sound_used:
        parts.append(f"  Sound: {sound_used}")
    else:
        parts.append("  Sound: (none)")
    parts.append(f"  Caption: {clip.caption or ''}")
    hashtags = clip.hashtags or ""
    parts.append(f"  Hashtags: {hashtags}")
    parts.append("")
    parts.append("  [a]pprove  [r]eject  [e]dit caption  [s]kip  [q]uit")
    return "\n".join(parts)


# Thumbnail extraction: pull a single frame at `at_sec` from the mp4
_THUMBNAIL_DEFAULT_AT_SEC = 1.0

# open_clip_in_player uses macOS `open`. On Linux this would be `xdg-open`,
# but the spec says macOS-only (the user is on Mac).
_OPEN_CMD = "open"


def extract_thumbnail(
    mp4_path: Path,
    output_path: Path,
    at_sec: float = _THUMBNAIL_DEFAULT_AT_SEC,
) -> None:
    """Extract a single frame from an mp4 to a JPG (or PNG) for the reviewer's preview.

    Uses ffmpeg via subprocess. The output directory is created if it
    doesn't exist. Raises subprocess.CalledProcessError on ffmpeg failure.
    """
    mp4_path = Path(mp4_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(at_sec),
        "-i", str(mp4_path),
        "-vframes", "1",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def open_clip_in_player(mp4_path: Path) -> None:
    """Open the mp4 in the system's default video player (QuickTime on macOS)."""
    subprocess.run([_OPEN_CMD, str(mp4_path)], check=True, capture_output=True)


def review_pending_clips(
    db_path: Path,
    limit: int = 5,
    prompt_func=None,
    player_opener=None,
) -> dict[int, str]:
    """Walk the user through reviewing pending clips.

    For each clip:
      1. Print a summary (caption, hashtags, final_path)
      2. Open the mp4 in the default video player (QuickTime on macOS)
      3. Prompt the user for an action (a/r/e/s/q)
      4. Apply the action to the DB

    `q` exits early (clips not yet processed stay in 'pending' status and
    are picked up by the next run).

    Returns {clip_id: action_name} for clips the user acted on.

    `prompt_func` defaults to the built-in input() at call time (so
    `monkeypatch.setattr("builtins.input", ...)` works in tests); pass a
    callable to override.
    `player_opener` defaults to `open_clip_in_player`; pass a fake to test.
    """
    if prompt_func is None:
        prompt_func = input
    if player_opener is None:
        player_opener = open_clip_in_player

    clips = queue.get_clips_to_review(db_path, limit=limit)
    out: dict[int, str] = {}

    for clip in clips:
        print(f"\n{'-' * 60}")
        print(format_clip_summary(clip))
        try:
            player_opener(Path(clip.final_path))
        except Exception as exc:
            print(f"reviewer: failed to open {clip.final_path}: {exc!r}", file=sys.stderr, flush=True)

        raw = prompt_func("> ")
        action = parse_user_choice(raw)

        if action is None:
            print(f"  (unrecognized input: {raw!r})")
            continue
        if action == "quit":
            print("  (quitting)")
            break
        if action == "skip":
            print("  (skipped)")
            continue
        if action == "approve":
            queue.approve_clip(db_path, clip.id, reviewer="human")
            out[clip.id] = "approved"
            print(f"  → approved clip {clip.id}")
        elif action == "reject":
            notes = prompt_func("  rejection notes (optional): ")
            queue.reject_clip(db_path, clip.id, reviewer="human", notes=notes or "")
            out[clip.id] = "rejected"
            print(f"  → rejected clip {clip.id}")
        elif action == "edit":
            print(f"  current caption: {clip.caption}")
            new_caption = prompt_func("  new caption: ")
            if new_caption:
                queue.edit_caption(db_path, clip.id, new_caption)
                out[clip.id] = "edited"
                print(f"  → edited caption for clip {clip.id}")
            else:
                print("  (empty caption, no edit applied)")

    return out
