# Scaffold Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address the non-blocking cleanup items flagged by the final code review of the scaffold plan — 3 polish edits in `config.py` / `main.py` / `queue.py`, 1 deprecation fix in `queue.py`, and a venv rebuild to repair the broken pip.

**Architecture:** Three small tasks. The polish task is mechanical and verifiable by re-running the existing 27-test suite. The deprecation fix uses the `models._utcnow()` helper instead of the deprecated `datetime.utcnow()`. The venv rebuild is a one-shot environment fix.

**Tech Stack:** Existing — Python 3.14.5, `python-dotenv`, `pytest`, stdlib `sqlite3` / `dataclasses`.

**Scope:** Tasks 1-3. The `data/.gitkeep` follow-up the final review mentioned is **not needed** — the file is already present and committed (verified: `data/.gitkeep` exists at `/Users/paullehn/Desktop/stumbleUpon/data/.gitkeep`).

---

## File Structure

| Path | Change |
|---|---|
| `src/stumbleupon/config.py` | Move `import os` to the top of the file; delete the trailing import + misleading comment (lines 58-59). |
| `src/stumbleupon/main.py` | Change `from .config import load_settings` (already relative — verify) and any other non-relative imports. |
| `src/stumbleupon/queue.py` | Remove unused `from contextlib import contextmanager` (line 10) and `CLIP_STATUSES` from `from .models import ...` (line 15). Replace `datetime.utcnow()` with `datetime.now(timezone.utc)`. |
| `.venv/` | Recreate from scratch with `python3.14 -m venv .venv && .venv/bin/pip install -e ".[dev]"`. |

No new files. No new tests (existing 27 tests cover the behavior; cleanups are refactors).

---

## Task 1: Polish — `config.py` trailing import, `main.py` relative imports, `queue.py` unused imports

**Files:**
- Modify: `src/stumbleupon/config.py:1-15` (move import os) and `:55-59` (delete trailing import)
- Modify: `src/stumbleupon/main.py:37` (verify relative import)
- Modify: `src/stumbleupon/queue.py:9-15` (remove unused imports)

- [ ] **Step 1: Fix `src/stumbleupon/config.py`**

Replace the entire current file content with:

```python
"""Configuration loaded from .env at startup.

All other modules receive a `Settings` instance; they do not read env vars directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    buffer_api_key: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket_name: str
    r2_public_url_base: str
    ad_block_keywords: list[str] = field(default_factory=lambda: ["nsfw", "adult", "xxx", "porn"])
    pipeline_daily_runs: int = 2
    pipeline_run_times: list[str] = field(default_factory=lambda: ["10:00", "20:00"])
    posts_per_day: int = 2
    proxy_url: str | None = None
    openai_api_key: str | None = None


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def load_settings(env_file: Path | None = None) -> Settings:
    """Load settings from a .env file. Falls back to environment variables."""
    sources: dict[str, str | None] = dict(os.environ)  # type: ignore[arg-type]
    if env_file is not None and env_file.exists():
        sources = {**sources, **dotenv_values(env_file)}

    return Settings(
        anthropic_api_key=sources.get("ANTHROPIC_API_KEY", "") or "",
        buffer_api_key=sources.get("BUFFER_API_KEY", "") or "",
        r2_access_key_id=sources.get("R2_ACCESS_KEY_ID", "") or "",
        r2_secret_access_key=sources.get("R2_SECRET_ACCESS_KEY", "") or "",
        r2_bucket_name=sources.get("R2_BUCKET_NAME", "") or "",
        r2_public_url_base=sources.get("R2_PUBLIC_URL_BASE", "") or "",
        ad_block_keywords=_split_csv(sources.get("AD_BLOCK_KEYWORDS")) or ["nsfw", "adult", "xxx", "porn"],
        pipeline_daily_runs=int(sources.get("PIPELINE_DAILY_RUNS", "2")),
        pipeline_run_times=_split_csv(sources.get("PIPELINE_RUN_TIMES")) or ["10:00", "20:00"],
        posts_per_day=int(sources.get("POSTS_PER_DAY", "2")),
        proxy_url=sources.get("PROXY_URL") or None,
        openai_api_key=sources.get("OPENAI_API_KEY") or None,
    )
```

Verify by reading the file. The new file:
- Has `import os` near the top (line 7)
- No trailing import at the bottom
- No `# noqa: E402`
- Same imports, same dataclass, same helper, same `load_settings` function as before

- [ ] **Step 2: Fix `src/stumbleupon/main.py` (verify imports are relative)**

Read the current file. Verify the `cmd_show_config` function uses a relative import:

```python
def cmd_show_config(args: argparse.Namespace) -> int:
    from .config import load_settings
    ...
```

If it's already relative (it should be — the plan's Task 6 content used `from .config`), no change is needed. If it's not relative, change it to `from .config import load_settings`.

Note: looking at the current state, `main.py` line 37 already says `from .config import load_settings`. So this step may be a no-op. Verify and move on.

- [ ] **Step 3: Remove unused imports from `src/stumbleupon/queue.py`**

Read the current file. The current imports are:

```python
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .db import get_connection
from .models import CLIP_STATUSES, Clip, Posting
```

Two imports are unused:
- `from contextlib import contextmanager` (line 10) — not used anywhere in the file
- `CLIP_STATUSES` (line 15) — not used in this file (only `Clip` and `Posting` are)

Change the imports to:

```python
import sqlite3
from datetime import datetime
from pathlib import Path

from .db import get_connection
from .models import Clip, Posting
```

Verify by reading the file. The change is a 2-line deletion in the import block.

- [ ] **Step 4: Run the full test suite to confirm nothing broke**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && PYTHONPATH=src .venv/bin/python -m pytest -v`
Expected: 27 passed.

- [ ] **Step 5: Commit**

```bash
git add src/stumbleupon/config.py src/stumbleupon/main.py src/stumbleupon/queue.py
git commit -m "chore: polish config.py imports, remove unused queue.py imports"
```

---

## Task 2: Fix `datetime.utcnow()` deprecation in `queue.py`

**Files:**
- Modify: `src/stumbleupon/queue.py:11` (import) and `:101-104` (use `datetime.now(timezone.utc)`)

- [ ] **Step 1: Update the `datetime` import in `queue.py`**

Change the import at line 11 from:

```python
from datetime import datetime
```

to:

```python
from datetime import datetime, timezone
```

- [ ] **Step 2: Update the default `now` argument in `get_approved_ready_to_post`**

Read the current `get_approved_ready_to_post` function (around line 101). It currently has:

```python
def get_approved_ready_to_post(db_path: Path, now: datetime | None = None) -> list[Clip]:
    """Approved clips whose scheduled_for is in the past (or unset)."""
    if now is None:
        now = datetime.utcnow()
    ...
```

Change the `if now is None:` block to:

```python
def get_approved_ready_to_post(db_path: Path, now: datetime | None = None) -> list[Clip]:
    """Approved clips whose scheduled_for is in the past (or unset)."""
    if now is None:
        now = datetime.now(timezone.utc)
    ...
```

This:
- Uses the recommended `datetime.now(timezone.utc)` instead of the deprecated `datetime.utcnow()`
- Matches the pattern already used in `models._utcnow()`
- Is what the existing test `test_get_approved_ready_to_post_respects_schedule` exercises (it passes `now=datetime.now(timezone.utc)` explicitly, so the default value is just for production callers)

- [ ] **Step 3: Run the full test suite to confirm nothing broke**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && PYTHONPATH=src .venv/bin/python -m pytest -v`
Expected: 27 passed.

- [ ] **Step 4: Run with `-W error::DeprecationWarning` to confirm the deprecation is gone**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && PYTHONPATH=src .venv/bin/python -W error::DeprecationWarning -c "from datetime import datetime, timezone; from stumbleupon import queue; import tempfile, pathlib; from stumbleupon.db import init_db; p = pathlib.Path(tempfile.mkdtemp()) / 't.db'; init_db(p); print(queue.get_approved_ready_to_post(p))"`
Expected: prints `[]` with no DeprecationWarning. If a warning fires, it will surface as a hard error and the command will exit non-zero.

- [ ] **Step 5: Commit**

```bash
git add src/stumbleupon/queue.py
git commit -m "fix: replace deprecated datetime.utcnow() with datetime.now(timezone.utc)"
```

---

## Task 3: Rebuild the broken venv

**Files:**
- Delete: `.venv/`
- Recreate: `.venv/`

- [ ] **Step 1: Verify the current venv's pip is broken**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/pip list 2>&1 | tail -3`
Expected: A `ModuleNotFoundError: No module named 'pip._vendor.pygments.modeline'` or similar pip-internal error. This confirms the venv needs rebuilding.

- [ ] **Step 2: Remove the broken venv**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && rm -rf .venv`

Verify the directory is gone:

Run: `cd /Users/paullehn/Desktop/stumbleUpon && ls -la .venv 2>&1`
Expected: `ls: .venv: No such file or directory`

- [ ] **Step 3: Recreate the venv with the system Python**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && python3.14 -m venv .venv`

If `python3.14` is not on the PATH, try `python3` or `python` and adjust. The pyproject.toml requires `>=3.11`, so anything 3.11+ works.

Verify:

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python --version`
Expected: `Python 3.14.5` (or whatever 3.11+ is available).

- [ ] **Step 4: Upgrade pip in the new venv**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python -m pip install --upgrade pip`
Expected: pip upgraded to a version compatible with Python 3.14 (25.x or newer).

- [ ] **Step 5: Install the package in editable mode with dev extras**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/pip install -e ".[dev]"`
Expected: `Successfully installed stumbleupon-0.1.0 ...` plus all deps (python-dotenv, pytest, pytest-cov).

- [ ] **Step 6: Verify pip is no longer broken**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/pip list 2>&1 | tail -10`
Expected: a clean list of installed packages including `stumbleupon 0.1.0`, `python-dotenv`, `pytest`, `pytest-cov`. No `ModuleNotFoundError`.

- [ ] **Step 7: Verify the package is now importable WITHOUT `PYTHONPATH=src`**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python -c "import stumbleupon; print(stumbleupon.__version__)"`
Expected: `0.1.0`

- [ ] **Step 8: Verify the CLI works WITHOUT `PYTHONPATH=src`**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && ANTHROPIC_API_KEY=sk-test .venv/bin/python -m stumbleupon.main show-config`
Expected: printout with `anthropic_api_key = ***`, etc.

- [ ] **Step 9: Run the full test suite WITHOUT `PYTHONPATH=src`**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && .venv/bin/python -m pytest -v`
Expected: 27 passed.

- [ ] **Step 10: Commit (the rebuild leaves no git-tracked changes, but commit the plan reference)**

The rebuild itself produces no git diff. To mark the cleanup as a unit, create a small file noting the rebuild:

Run:
```bash
cat > .venv-rebuild-note.md <<'EOF'
# venv note

The `.venv/` directory is gitignored. After a fresh clone, recreate it with:

    python3.14 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/pip install -e ".[dev]"

This is the canonical setup on Python 3.14.5 (Homebrew). Older Pythons (3.11-3.13) work too.
EOF

git add .venv-rebuild-note.md
git commit -m "docs: add venv rebuild note"
```

---

## Self-Review

**1. Spec coverage (cleanup items):**
- Item 1: trailing `import os` in `config.py` → Task 1 Step 1
- Item 3: non-relative imports in `main.py` → Task 1 Step 2 (likely a no-op since they're already relative)
- Item 4: unused imports in `queue.py` → Task 1 Step 3
- Item 2: `datetime.utcnow()` deprecation → Task 2
- Item 5: broken venv pip → Task 3
- Item 6: missing `data/.gitkeep` → NOT NEEDED (file already exists at `data/.gitkeep`, mentioned in Task 1's plan note and verified by `ls`)

**2. Placeholder scan:** No "TBD" or "TODO" in any code block. Every step has the actual content.

**3. Type consistency:** The change in Task 2 keeps the same function signature (`now: datetime | None = None`) — only the default-value expression changes. No call site breaks.

**4. Test impact:** No new tests added. The existing 27 tests are the safety net for the refactors. Task 2 adds a one-liner verification with `-W error::DeprecationWarning` to catch the deprecation.

**One nuance worth flagging:** The deprecation fix in Task 2 changes the *default* value of `now` in `get_approved_ready_to_post`. The test `test_get_approved_ready_to_post_respects_schedule` always passes an explicit `now=`, so the default isn't exercised in tests. To be safe, the Step 4 verification calls the function with the default (no `now=` argument) under `-W error::DeprecationWarning`. If a warning still fires (from any code path), the command will fail loudly.

**Plan coverage of the 5 remaining cleanup items:** ✓ all 5. The `data/.gitkeep` item is dropped because the file already exists.
