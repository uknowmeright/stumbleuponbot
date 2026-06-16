# Launchd Plists: macOS Scheduling

> **Status:** Approved 2026-06-16. Final v1 feature.

## Goal

Add 3 launchd plists to schedule the stumbleUpon pipeline on macOS, plus `stumbleupon install` / `uninstall` subcommands to manage them. Optionally surface macOS notifications from the pipeline when clips are ready to review or when posting succeeds/fails. Closes out the v1 feature set.

**Scope decisions made during brainstorm:**
- **Install mechanism:** Python subcommands (`stumbleupon install` / `uninstall`) — testable, cross-platform aware.
- **Notifications:** Python triggers `osascript` from the pipeline at meaningful points; no-op on non-macOS.
- **Sounds refresh:** 3rd plist (daily at 3am) — keeps the catalog fresh without manual intervention.
- **Plist location:** `launchd/` directory in the repo.

## Architecture

3 plists + 2 subcommands + 1 notifier helper:

| Path | Change | Responsibility |
|---|---|---|
| `launchd/com.user.stumbleupon.pipeline.plist` | create | 2x/day at 10am + 8pm → `python -m stumbleupon run` |
| `launchd/com.user.stumbleupon.sounds.plist` | create | 1x/day at 3am → `python -m stumbleupon scrape-sounds` |
| `launchd/com.user.stumbleupon.poster.plist` | create | Every 15 min → `python -m stumbleupon post` |
| `src/stumbleupon/launchd.py` | create | Pure helpers: plist render fns, paths, install/unload logic |
| `src/stumbleupon/notifier.py` | create | `notify(title, body, sound="default")` — osascript on macOS, no-op elsewhere |
| `src/stumbleupon/main.py` | modify | Add `cmd_install` / `cmd_uninstall`; call notifier at end of `cmd_run` (new clips) and `cmd_post` (success/failure) |
| `tests/test_launchd.py` | create | TDD for render fns and install/unload helpers (mocked subprocess) |
| `tests/test_notifier.py` | create | TDD: osascript call on macOS, no-op on other platforms |
| `README.md` | modify | Document plists, install/uninstall, macOS-only caveat, manual smoke steps |

## Plist Contents (rendered from dicts via `plistlib.dumps`)

Each plist is a dict rendered with `plistlib.dumps(...)` (binary plist format). This is the macOS-recommended approach — no XML string templating.

### `com.user.stumbleupon.pipeline.plist`

- `Label`: `com.user.stumbleupon.pipeline`
- `ProgramArguments`: `[<python_path>, "-m", "stumbleupon", "run"]`
- `WorkingDirectory`: `<project_root>`
- `StandardOutPath`: `<project_root>/data/logs/pipeline.out.log`
- `StandardErrorPath`: `<project_root>/data/logs/pipeline.err.log`
- `StartCalendarInterval`:
  - `Hour: 10, Minute: 0`
  - `Hour: 20, Minute: 0`
- `RunAtLoad`: `False`

### `com.user.stumbleupon.sounds.plist`

- `Label`: `com.user.stumbleupon.sounds`
- `ProgramArguments`: `[<python_path>, "-m", "stumbleupon", "scrape-sounds"]`
- Same WorkingDirectory, log paths, RunAtLoad pattern
- `StartCalendarInterval`: `Hour: 3, Minute: 0`

### `com.user.stumbleupon.poster.plist`

- `Label`: `com.user.stumbleupon.poster`
- `ProgramArguments`: `[<python_path>, "-m", "stumbleupon", "post"]`
- Same WorkingDirectory, log paths, RunAtLoad pattern
- `StartInterval`: `900` (15 minutes in seconds)

`<python_path>` and `<project_root>` are captured at install time, not hardcoded.

## Function Signatures & Behavior

### `launchd.py` — pure helpers

```python
def is_macos() -> bool:
    """True iff sys.platform == 'darwin'."""


def default_python_path() -> str:
    """Path to the Python interpreter to invoke (sys.executable)."""


def default_project_root() -> Path:
    """Project root = cwd at install time. The plist's WorkingDirectory."""


def default_log_dir() -> Path:
    """data/logs/ under the project root."""


def render_plist(label: str, program_args: list[str],
                 working_dir: Path, log_dir: Path) -> bytes:
    """Build a binary plist (as bytes) for a job with the given label + args.

    StandardOutPath and StandardErrorPath go to <log_dir>/<label>.out.log
    and <log_dir>/<label>.err.log. RunAtLoad is False; the schedule
    (StartCalendarInterval or StartInterval) is set by the caller via
    `merge_schedule` (see below).
    """


def merge_calendar_schedule(plist_dict: dict, hour: int, minute: int) -> dict:
    """Add a single StartCalendarInterval entry to the plist dict.
    Returns the (mutated) dict for fluent use."""


def merge_calendar_schedule_multi(plist_dict: dict, hours_minutes: list[tuple[int, int]]) -> dict:
    """Add multiple StartCalendarInterval entries (for 2x/day patterns)."""


def merge_interval_schedule(plist_dict: dict, seconds: int) -> dict:
    """Set StartInterval = seconds for the plist dict."""


def installed_plist_path(label: str) -> Path:
    """Where the plist lives once installed: ~/Library/LaunchAgents/<label>.plist."""


def install_plist(label: str, plist_bytes: bytes,
                  *, run_loadctl: bool = True) -> Path:
    """Copy the rendered plist to ~/Library/LaunchAgents/ and optionally
    run `launchctl load -w`. Returns the destination path.

    Raises FileNotFoundError if the parent dir doesn't exist.
    Caller is responsible for is_macos() guard.
    """


def uninstall_plist(label: str) -> bool:
    """`launchctl unload` then delete the plist file.
    Returns True if the plist existed and was removed, False if absent.
    """


def install_all() -> dict[str, str]:
    """Render + install all 3 plists. Returns {label: installed_path}.

    On non-macOS: prints a message, returns {} (no-op).
    """


def uninstall_all() -> dict[str, bool]:
    """Uninstall all 3 plists. Returns {label: removed?}.

    On non-macOS: prints a message, returns {} (no-op).
    """
```

All functions are pure or use `pathlib`/`subprocess` (no DB or external state). Mockable for tests.

### `notifier.py`

```python
def is_macos() -> bool:
    """Cached check: sys.platform == 'darwin'. Duplicated in launchd.py
    to keep notifier usable without importing launchd (CLI may not have
    launchctl available)."""


def notify(title: str, body: str, sound: str = "default") -> None:
    """Display a macOS notification. No-op on non-macOS. Errors are
    logged to stderr but never raised — notifications are best-effort
    and shouldn't fail the pipeline.
    """
```

Implementation: `subprocess.run(["osascript", "-e", f'display notification "{body}" with title "{title}" sound name "{sound}"'], check=False, capture_output=True, timeout=5)`. On non-macOS, return immediately. On any exception, log to stderr and return.

### `main.py` changes

- Add 2 new subcommands: `install` and `uninstall`, both calling into `launchd`.
- Modify `cmd_run` to call `notifier.notify("stumbleupon", f"{len(new_clips)} new clips ready to review")` if any clips are produced by captioner.
- Modify `cmd_post` to call `notifier.notify("stumbleupon", f"Posted {len(posted)} clips")` on success, and `notifier.notify("stumbleupon error", f"Posting failed: {first_error}", sound="Basso")` on any failure (per-clip, but aggregated into one notification per run).

## Failure Modes

| Situation | Behavior |
|---|---|
| `stumbleupon install` on non-macOS | Print "launchd is macOS-only; nothing to install" and exit 0 |
| `launchctl load` fails | Raise `subprocess.CalledProcessError` from `install_all`; caller prints error |
| `~/Library/LaunchAgents/` doesn't exist | Raise `FileNotFoundError` from `install_plist`; user runs `mkdir -p ~/Library/LaunchAgents/` and retries |
| `.venv/bin/python` doesn't exist | `default_python_path` returns `sys.executable` instead (works in dev without a venv) |
| Notifier's `osascript` times out | Log to stderr, return; pipeline continues |
| Plist re-installed (overwrite) | `launchctl unload` the old, then `load` the new |
| `cmd_run` crashes before notify call | Notification simply doesn't fire; nothing to handle |

## Testing Strategy

**Pure logic (TDD):**
- `render_plist` returns valid binary plist (round-trip via `plistlib.loads`).
- `merge_calendar_schedule` / `merge_calendar_schedule_multi` / `merge_interval_schedule` produce the right dict shape.
- `installed_plist_path` returns the right path.
- `default_log_dir` is `data/logs/` under cwd.

**Install/uninstall helpers (mocked subprocess + filesystem):**
- `install_plist` calls `launchctl load -w` on the right path (monkeypatch `subprocess.run`).
- `install_plist` copies the plist bytes to `~/Library/LaunchAgents/<label>.plist`. Tests monkeypatch `Path.home` to return a `tmp_path` so the copy lands in the temp dir (no real `~/Library/LaunchAgents/` mutation in tests).
- `uninstall_plist` calls `launchctl unload` then deletes the file (monkeypatch `Path.home` + `subprocess.run`).
- `install_all` returns a dict with all 3 labels; no-op on non-macOS (monkeypatch `sys.platform`).
- `uninstall_all` is the symmetric no-op-on-non-macOS.

**Notifier (mocked subprocess):**
- `notify` on darwin calls `osascript -e ...` with the right script.
- `notify` on non-darwin is a no-op.
- `notify` doesn't raise on osascript failure (logs + returns).

**Manual smoke (documented, not automated):**
```bash
# On macOS:
.venv/bin/stumbleupon install
launchctl list | grep stumbleupon   # should show 3 jobs
.venv/bin/stumbleupon uninstall
launchctl list | grep stumbleupon   # should show 0 jobs
```

## Pipeline Integration

`cmd_run` ends with a notification if clips were produced:

```python
# existing
clips = asyncio.run(caption_pending_recordings(...))
print(f"captioner: {len(clips)} clips queued for review", file=sys.stderr)
# new
if clips:
    notifier.notify("stumbleupon", f"{len(clips)} new clips ready to review")
```

`cmd_post` aggregates results and notifies on outcome:

```python
posted = asyncio.run(post_pending_clips(...))
print(f"poster: {len(posted)} clips posted", file=sys.stderr)
# new
if posted:
    notifier.notify("stumbleupon", f"Posted {len(posted)} clip(s)")
else:
    notifier.notify("stumbleupon", "No clips to post (or all failed)", sound="Basso")
```

## File Structure (concise)

```
src/stumbleupon/
├── launchd.py   # NEW: render, paths, install/uninstall
├── notifier.py  # NEW: osascript wrapper
└── main.py      # MODIFY: add cmd_install, cmd_uninstall, notifications

launchd/
├── com.user.stumbleupon.pipeline.plist   # NEW
├── com.user.stumbleupon.sounds.plist     # NEW
└── com.user.stumbleupon.poster.plist     # NEW

tests/
├── test_launchd.py   # NEW
└── test_notifier.py  # NEW
```

## Out of Scope (Deferred)

- Daily log rotation (`pipeline-2026-06-16.log` style). v1 uses single append-only files; rotation is a future logrotate(8) or Python helper.
- "Wake for network access" reminder. Documented in README; user enables in System Settings.
- `install --dry-run` to render plists without copying.
- Linux (systemd) and Windows (schtasks) equivalents. v1 is macOS-only.
- Per-plist `EnvironmentVariables` (e.g., `PATH` overrides). Not needed for v1.
- Per-run unique log filenames. v1 uses fixed per-plist logs.

## Open Questions

None blocking. Implementation can proceed.
