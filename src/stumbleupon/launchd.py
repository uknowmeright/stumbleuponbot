"""Launchd: render and install macOS launchd plists for the pipeline.

The pure parts (render, merge, path helpers) are unit-tested. The
install/uninstall parts (subprocess + filesystem) are exercised via
mocked tests + manual smoke (`stumbleupon install` on a real Mac).
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path


# Label constants (used in tests and the all-helpers)
LABEL_PIPELINE = "com.user.stumbleupon.pipeline"
LABEL_SOUNDS = "com.user.stumbleupon.sounds"
LABEL_POSTER = "com.user.stumbleupon.poster"
ALL_LABELS = (LABEL_PIPELINE, LABEL_SOUNDS, LABEL_POSTER)


def is_macos() -> bool:
    """True iff sys.platform == 'darwin'."""
    return sys.platform == "darwin"


def default_python_path() -> str:
    """Python interpreter to invoke. sys.executable at install time."""
    return sys.executable


def default_project_root() -> Path:
    """Project root captured at install time. cwd."""
    return Path(os.getcwd())


def default_log_dir() -> Path:
    """<project_root>/data/logs."""
    return default_project_root() / "data" / "logs"


def render_plist(
    label: str,
    program_args: list[str],
    *,
    working_dir: Path,
    log_dir: Path,
) -> bytes:
    """Build a binary plist (as bytes) for a job with the given label + args.

    StandardOutPath → <log_dir>/<label>.out.log
    StandardErrorPath → <log_dir>/<label>.err.log
    RunAtLoad is False; the schedule (calendar or interval) is added by
    the caller via merge_*_schedule helpers.
    """
    plist: dict = {
        "Label": label,
        "ProgramArguments": program_args,
        "WorkingDirectory": str(working_dir),
        "StandardOutPath": str(log_dir / f"{label}.out.log"),
        "StandardErrorPath": str(log_dir / f"{label}.err.log"),
        "RunAtLoad": False,
    }
    return plistlib.dumps(plist)


def merge_calendar_schedule(plist: dict, *, hour: int, minute: int) -> dict:
    """Add a single StartCalendarInterval entry to the plist dict."""
    plist.setdefault("StartCalendarInterval", []).append(
        {"Hour": hour, "Minute": minute}
    )
    return plist


def merge_calendar_schedule_multi(
    plist: dict, hours_minutes: list[tuple[int, int]]
) -> dict:
    """Add multiple StartCalendarInterval entries (for 2x/day patterns)."""
    for hour, minute in hours_minutes:
        merge_calendar_schedule(plist, hour=hour, minute=minute)
    return plist


def merge_interval_schedule(plist: dict, *, seconds: int) -> dict:
    """Set StartInterval = seconds for the plist dict."""
    plist["StartInterval"] = seconds
    return plist


def installed_plist_path(label: str) -> Path:
    """Where the plist lives once installed: ~/Library/LaunchAgents/<label>.plist."""
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def install_plist(
    label: str,
    plist_bytes: bytes,
    *,
    run_loadctl: bool = True,
) -> Path:
    """Copy the rendered plist to ~/Library/LaunchAgents/ and optionally
    run `launchctl load -w <path>`. Returns the destination path.

    The parent directory must exist (the user is responsible for
    `mkdir -p ~/Library/LaunchAgents/` on first install). Overwrites
    any existing plist with the same label.

    Caller is responsible for the is_macos() guard.
    """
    dest = installed_plist_path(label)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(plist_bytes)
    if run_loadctl:
        subprocess.run(
            ["launchctl", "load", "-w", str(dest)],
            check=True,
            capture_output=True,
        )
    return dest


def uninstall_plist(label: str) -> bool:
    """`launchctl unload` (best-effort) then delete the plist file.

    Returns True if the plist existed and was removed, False if it
    was already absent. launchctl unload is best-effort: if it fails
    (e.g., the job isn't loaded), we still proceed to delete the file.
    """
    dest = installed_plist_path(label)
    if not dest.exists():
        return False
    # Unload is best-effort. If the job isn't loaded, launchctl returns
    # non-zero, but we still want to delete the stale plist.
    try:
        subprocess.run(
            ["launchctl", "unload", str(dest)],
            check=False,
            capture_output=True,
        )
    except Exception as exc:
        print(
            f"launchd: launchctl unload failed for {label}: {exc!r}; "
            f"continuing to delete the plist",
            file=sys.stderr,
            flush=True,
        )
    dest.unlink()
    return True


def _build_pipeline_plist(*, python: str, project: Path, log_dir: Path) -> bytes:
    """Render the pipeline plist (2x/day at 10am + 8pm)."""
    plist_bytes = render_plist(
        LABEL_PIPELINE,
        [python, "-m", "stumbleupon", "run"],
        working_dir=project,
        log_dir=log_dir,
    )
    plist = plistlib.loads(plist_bytes)
    merge_calendar_schedule_multi(plist, [(10, 0), (20, 0)])
    return plistlib.dumps(plist)


def _build_sounds_plist(*, python: str, project: Path, log_dir: Path) -> bytes:
    """Render the sounds plist (1x/day at 3am)."""
    plist_bytes = render_plist(
        LABEL_SOUNDS,
        [python, "-m", "stumbleupon", "scrape-sounds"],
        working_dir=project,
        log_dir=log_dir,
    )
    plist = plistlib.loads(plist_bytes)
    merge_calendar_schedule(plist, hour=3, minute=0)
    return plistlib.dumps(plist)


def _build_poster_plist(*, python: str, project: Path, log_dir: Path) -> bytes:
    """Render the poster plist (every 15 minutes)."""
    plist_bytes = render_plist(
        LABEL_POSTER,
        [python, "-m", "stumbleupon", "post"],
        working_dir=project,
        log_dir=log_dir,
    )
    plist = plistlib.loads(plist_bytes)
    merge_interval_schedule(plist, seconds=900)
    return plistlib.dumps(plist)


def install_all() -> dict[str, Path]:
    """Render + install all 3 plists. Returns {label: installed_path}.

    On non-macOS: prints a message, returns {} (no-op).
    """
    if not is_macos():
        print(
            "launchd: install_all is a no-op on non-macOS platforms",
            file=sys.stderr,
        )
        return {}
    python = default_python_path()
    project = default_project_root()
    log_dir = default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    builders = {
        LABEL_PIPELINE: _build_pipeline_plist,
        LABEL_SOUNDS: _build_sounds_plist,
        LABEL_POSTER: _build_poster_plist,
    }
    result: dict[str, Path] = {}
    for label, builder in builders.items():
        plist_bytes = builder(python=python, project=project, log_dir=log_dir)
        result[label] = install_plist(label, plist_bytes)
    return result


def uninstall_all() -> dict[str, bool]:
    """Uninstall all 3 plists. Returns {label: removed?}.

    On non-macOS: prints a message, returns {} (no-op).
    """
    if not is_macos():
        print(
            "launchd: uninstall_all is a no-op on non-macOS platforms",
            file=sys.stderr,
        )
        return {}
    return {label: uninstall_plist(label) for label in ALL_LABELS}
