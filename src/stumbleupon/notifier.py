"""Notifier: best-effort macOS notifications via osascript.

No-op on non-macOS. Never raises — notifications are best-effort and
shouldn't fail the pipeline.
"""

from __future__ import annotations

import subprocess
import sys


def is_macos() -> bool:
    """True iff sys.platform == 'darwin'."""
    return sys.platform == "darwin"


def notify(title: str, body: str, sound: str = "default") -> None:
    """Display a macOS notification. No-op on non-macOS. Never raises.

    The osascript call is wrapped in try/except — failures (timeout,
    permission denied, etc.) are logged to stderr but do not propagate.
    """
    if not is_macos():
        return
    script = (
        f'display notification "{body}" with title "{title}" '
        f'sound name "{sound}"'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except Exception as exc:
        # Notifications are best-effort. Log and move on.
        print(
            f"notifier: osascript failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
