"""Reviewer: human-in-the-loop CLI for approving/rejecting pending clips.

The pure-logic parts (input parsing, summary formatting) are unit-tested.
The I/O parts (ffmpeg thumbnail extraction, `open` on macOS, interactive
prompt loop) are exercised via manual smoke commands.

Persistence: quitting mid-session just leaves clips in 'pending' status;
the next run picks up where it left off. No special session state needed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

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
