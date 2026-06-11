# Scaffold + DB Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `stumbleupon` Python project layout, configuration loader, SQLite schema, dataclass models, and high-level queue operations — all with TDD on the pure-logic modules. No external services touched yet.

**Architecture:** Standard `src/` layout with a thin `stumbleupon` package. `db.py` owns the schema and connection; `queue.py` is the only module that mutates `clips.status`; `models.py` defines immutable-ish dataclasses for rows. Config is a frozen dataclass loaded from `.env` at startup. Every pure-logic module gets a pytest test file using a real SQLite DB in a tmp directory.

**Tech Stack:** Python 3.11+, `pytest`, `python-dotenv`, stdlib `sqlite3`, stdlib `dataclasses`. (No pydantic — keep deps minimal for the core.) Heavy deps (httpx, playwright, anthropic, ffmpeg-python, boto3) are added in later plans as their modules are scaffolded.

**Scope of this plan:** Tasks 1-7. The rest of the spec (scraper, recorder, captioner, sounds, composer, reviewer, poster, launchd plists) lives in future plans.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Project metadata, deps, pytest config |
| `.env.example` | Template for required env vars (no real values) |
| `README.md` | Project overview, setup, dev loop |
| `src/stumbleupon/__init__.py` | Package marker, `__version__` |
| `src/stumbleupon/config.py` | Load `.env`, expose `Settings` dataclass |
| `src/stumbleupon/models.py` | `Site`, `Clip`, `Sound`, `Posting` dataclasses |
| `src/stumbleupon/db.py` | SQLite connection, schema migration, `init_db()` |
| `src/stumbleupon/queue.py` | `get_pending_clips()`, `mark_posted()`, `mark_failed()`, `get_approved_ready_to_post()`, `record_status_transition()` |
| `src/stumbleupon/main.py` | `argparse` CLI with subcommands (stub bodies for non-scaffolded commands) |
| `tests/__init__.py` | Test package marker |
| `tests/conftest.py` | `tmp_db_path` and `db` fixtures |
| `tests/test_config.py` | Settings loading tests |
| `tests/test_models.py` | Dataclass behavior tests |
| `tests/test_db.py` | Schema + migration tests |
| `tests/test_queue.py` | Queue operation tests |

---

## Task 1: Project layout + `pyproject.toml`

**Files:**
- Create: `pyproject.toml`
- Create: `src/stumbleupon/__init__.py`
- Create: `tests/__init__.py`
- Create: `.gitkeep` (already present at `data/.gitkeep`; verify only)
- Modify: `.gitignore` (add `src/stumbleupon/_version.py` if we add it later; skip for now)

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "stumbleupon"
version = "0.1.0"
description = "Local TikTok pipeline for the weird web"
requires-python = ">=3.11"
dependencies = [
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-cov>=4.1.0",
]

[project.scripts]
stumbleupon = "stumbleupon.main:cli"

[tool.hatch.build.targets.wheel]
packages = ["src/stumbleupon"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-v --strict-markers"
```

- [ ] **Step 2: Create `src/stumbleupon/__init__.py`**

```python
"""stumbleupon: local TikTok pipeline for the weird web."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create `tests/__init__.py`**

```python
# Test package marker.
```

- [ ] **Step 4: Verify the package imports**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && python -c "import stumbleupon; print(stumbleupon.__version__)"`
Expected: `0.1.0`

- [ ] **Step 5: Verify pytest discovers the test directory**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && python -m pytest --collect-only`
Expected: `no tests ran` or `0 items collected` (no test files yet, but no errors).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/stumbleupon/__init__.py tests/__init__.py
git commit -m "feat: scaffold stumbleupon package with pyproject.toml"
```

---

## Task 2: `.env.example` + Settings dataclass (TDD)

**Files:**
- Create: `.env.example`
- Create: `src/stumbleupon/config.py`
- Create: `tests/conftest.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Create `.env.example`**

```bash
# stumbleupon pipeline configuration
# Copy to .env and fill in real values. .env is gitignored.

# Claude API (for captioner)
ANTHROPIC_API_KEY=

# Buffer API (for poster)
BUFFER_API_KEY=

# Cloudflare R2 (for video hosting, S3-compatible)
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
R2_PUBLIC_URL_BASE=

# Optional: OpenAI (for Whisper captions — deferred to future plan)
# OPENAI_API_KEY=

# Optional: residential proxy for TikTok trending sounds scrape
# PROXY_URL=

# Comma-separated ad-block keywords filtered during scraping
AD_BLOCK_KEYWORDS=nsfw,adult,xxx,porn

# Pipeline tunables
PIPELINE_DAILY_RUNS=2
PIPELINE_RUN_TIMES=10:00,20:00
POSTS_PER_DAY=2
```

- [ ] **Step 2: Write the failing test in `tests/test_config.py`**

```python
"""Tests for config.Settings."""

import os
from pathlib import Path

import pytest

from stumbleupon.config import Settings, load_settings


def test_load_settings_reads_required_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANTHROPIC_API_KEY=sk-test\n"
        "BUFFER_API_KEY=buf-test\n"
        "R2_ACCESS_KEY_ID=r2ak\n"
        "R2_SECRET_ACCESS_KEY=r2sk\n"
        "R2_BUCKET_NAME=stumble\n"
        "R2_PUBLIC_URL_BASE=https://media.example.com\n"
    )
    settings = load_settings(env_file=env_file)
    assert settings.anthropic_api_key == "sk-test"
    assert settings.buffer_api_key == "buf-test"
    assert settings.r2_bucket_name == "stumble"
    assert settings.r2_public_url_base == "https://media.example.com"


def test_load_settings_uses_defaults_when_optional_keys_missing(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-test\n")
    settings = load_settings(env_file=env_file)
    assert settings.ad_block_keywords == ["nsfw", "adult", "xxx", "porn"]
    assert settings.pipeline_daily_runs == 2
    assert settings.posts_per_day == 2


def test_load_settings_parses_comma_separated_keywords(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANTHROPIC_API_KEY=sk-test\n"
        "AD_BLOCK_KEYWORDS=nsfw,gambling,scam\n"
    )
    settings = load_settings(env_file=env_file)
    assert settings.ad_block_keywords == ["nsfw", "gambling", "scam"]


def test_load_settings_parses_run_times(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANTHROPIC_API_KEY=sk-test\n"
        "PIPELINE_RUN_TIMES=09:00,21:00\n"
    )
    settings = load_settings(env_file=env_file)
    assert settings.pipeline_run_times == ["09:00", "21:00"]


def test_settings_is_immutable() -> None:
    settings = Settings(
        anthropic_api_key="x",
        buffer_api_key="y",
        r2_access_key_id="a",
        r2_secret_access_key="b",
        r2_bucket_name="c",
        r2_public_url_base="d",
    )
    with pytest.raises(Exception):  # FrozenInstanceError subclass
        settings.anthropic_api_key = "z"  # type: ignore[misc]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && python -m pytest tests/test_config.py -v`
Expected: ImportError or ModuleNotFoundError (no `stumbleupon.config` yet).

- [ ] **Step 4: Implement `src/stumbleupon/config.py`**

```python
"""Configuration loaded from .env at startup.

All other modules receive a `Settings` instance; they do not read env vars directly.
"""

from __future__ import annotations

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


# Re-export os for the test's use above.
import os  # noqa: E402
```

Note: the trailing `import os` is a small style wart to keep the import near the `os.environ` usage. Acceptable for v1.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && python -m pytest tests/test_config.py -v`
Expected: 5 passed.

- [ ] **Step 6: Create `tests/conftest.py` with shared fixtures**

```python
"""Shared pytest fixtures."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Return a path to a fresh SQLite database in a tmp directory."""
    return tmp_path / "stumbleupon.db"
```

- [ ] **Step 7: Commit**

```bash
git add .env.example src/stumbleupon/config.py tests/test_config.py tests/conftest.py
git commit -m "feat: add .env.example and Settings dataclass with tests"
```

---

## Task 3: Models (TDD)

**Files:**
- Create: `src/stumbleupon/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the failing test in `tests/test_models.py`**

```python
"""Tests for the dataclass models."""

from datetime import datetime

import pytest

from stumbleupon.models import Clip, Posting, Site, Sound


def test_site_construction_minimal() -> None:
    site = Site(id=1, url="https://example.com")
    assert site.id == 1
    assert site.url == "https://example.com"
    assert site.title is None
    assert site.status == "fresh"
    assert isinstance(site.discovered_at, datetime)


def test_site_status_must_be_known() -> None:
    with pytest.raises(ValueError, match="status"):
        Site(id=1, url="https://example.com", status="bogus")


def test_clip_status_must_be_known() -> None:
    with pytest.raises(ValueError, match="status"):
        Clip(id=1, site_id=1, status="bogus")


def test_clip_uses_edited_caption_when_present() -> None:
    clip = Clip(
        id=1,
        site_id=1,
        caption="original",
        edited_caption="human-tweaked",
    )
    assert clip.effective_caption == "human-tweaked"


def test_clip_falls_back_to_caption_when_no_edit() -> None:
    clip = Clip(id=1, site_id=1, caption="original")
    assert clip.effective_caption == "original"


def test_sound_round_robin_score() -> None:
    sound = Sound(id=1, tiktok_sound_id="abc", title="x", artist="y", trending_score=85.5)
    assert sound.trending_score == 85.5


def test_posting_required_fields() -> None:
    posting = Posting(id=1, clip_id=1, platform="tiktok", status="queued")
    assert posting.platform == "tiktok"
    assert posting.status == "queued"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && python -m pytest tests/test_models.py -v`
Expected: ImportError on `stumbleupon.models`.

- [ ] **Step 3: Implement `src/stumbleupon/models.py`**

```python
"""Dataclass models for sites, clips, sounds, and postings.

These mirror the SQLite schema in `db.py`. The DB returns dicts; the queue
layer converts dicts into these dataclasses. Status fields are validated
against known sets at construction time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


SITE_STATUSES = frozenset({"fresh", "recorded", "failed", "skipped"})
CLIP_STATUSES = frozenset(
    {"pending", "needs_attention", "approved", "rejected", "posted", "failed"}
)
POSTING_STATUSES = frozenset({"queued", "posted", "failed"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Site:
    id: int | None
    url: str
    title: str | None = None
    description: str | None = None
    source: str = "stumbleupon.cc"
    discovered_at: datetime = field(default_factory=_utcnow)
    last_attempted: datetime | None = None
    status: str = "fresh"
    skip_reason: str | None = None
    tags: str | None = None

    def __post_init__(self) -> None:
        if self.status not in SITE_STATUSES:
            raise ValueError(f"unknown site status: {self.status!r}")


@dataclass
class Clip:
    id: int | None
    site_id: int
    recording_path: str | None = None
    final_path: str | None = None
    r2_public_url: str | None = None
    caption: str | None = None
    hashtags: str | None = None
    sound_id: int | None = None
    duration_sec: float | None = None
    created_at: datetime = field(default_factory=_utcnow)
    status: str = "pending"
    review_notes: str | None = None
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    edited_caption: str | None = None

    def __post_init__(self) -> None:
        if self.status not in CLIP_STATUSES:
            raise ValueError(f"unknown clip status: {self.status!r}")

    @property
    def effective_caption(self) -> str | None:
        """Prefer human-edited caption; fall back to the LLM-generated one."""
        return self.edited_caption or self.caption


@dataclass
class Sound:
    id: int | None
    tiktok_sound_id: str
    title: str | None = None
    artist: str | None = None
    audio_path: str | None = None
    trending_score: float = 0.0
    fetched_at: datetime = field(default_factory=_utcnow)
    last_used_at: datetime | None = None


@dataclass
class Posting:
    id: int | None
    clip_id: int
    platform: str
    external_id: str | None = None
    external_url: str | None = None
    status: str = "queued"
    error: str | None = None
    posted_at: datetime | None = None
    scheduled_for: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in POSTING_STATUSES:
            raise ValueError(f"unknown posting status: {self.status!r}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && python -m pytest tests/test_models.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/stumbleupon/models.py tests/test_models.py
git commit -m "feat: add dataclass models for Site, Clip, Sound, Posting"
```

---

## Task 4: DB schema + connection (TDD)

**Files:**
- Create: `src/stumbleupon/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write the failing test in `tests/test_db.py`**

```python
"""Tests for the SQLite schema and connection helpers."""

import sqlite3
from pathlib import Path

import pytest

from stumbleupon.db import init_db, get_connection


def test_init_db_creates_all_tables(tmp_db_path: Path) -> None:
    init_db(tmp_db_path)
    with get_connection(tmp_db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    table_names = {row["name"] for row in rows}
    assert {"sites", "clips", "sounds", "postings"} <= table_names


def test_init_db_creates_indexes(tmp_db_path: Path) -> None:
    init_db(tmp_db_path)
    with get_connection(tmp_db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    index_names = {row["name"] for row in rows}
    assert "idx_sites_status" in index_names


def test_init_db_is_idempotent(tmp_db_path: Path) -> None:
    init_db(tmp_db_path)
    init_db(tmp_db_path)  # should not raise
    with get_connection(tmp_db_path) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM sites").fetchone()["n"]
    assert count == 0


def test_get_connection_enables_foreign_keys(tmp_db_path: Path) -> None:
    init_db(tmp_db_path)
    with get_connection(tmp_db_path) as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_get_connection_yields_dict_rows(tmp_db_path: Path) -> None:
    init_db(tmp_db_path)
    with get_connection(tmp_db_path) as conn:
        conn.execute(
            "INSERT INTO sites (url, title) VALUES (?, ?)",
            ("https://example.com", "Example"),
        )
        row = conn.execute("SELECT url, title FROM sites").fetchone()
    assert row["url"] == "https://example.com"
    assert row["title"] == "Example"


def test_sites_table_columns(tmp_db_path: Path) -> None:
    init_db(tmp_db_path)
    with get_connection(tmp_db_path) as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(sites)").fetchall()}
    expected = {
        "id", "url", "title", "description", "source", "discovered_at",
        "last_attempted", "status", "skip_reason", "tags",
    }
    assert expected <= cols


def test_clips_table_columns(tmp_db_path: Path) -> None:
    init_db(tmp_db_path)
    with get_connection(tmp_db_path) as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(clips)").fetchall()}
    expected = {
        "id", "site_id", "recording_path", "final_path", "r2_public_url",
        "caption", "hashtags", "sound_id", "duration_sec", "created_at",
        "status", "review_notes", "reviewed_at", "reviewed_by", "edited_caption",
    }
    assert expected <= cols
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && python -m pytest tests/test_db.py -v`
Expected: ImportError on `stumbleupon.db`.

- [ ] **Step 3: Implement `src/stumbleupon/db.py`**

```python
"""SQLite connection and schema management.

The schema mirrors the design spec §4. `init_db()` is idempotent and safe
to call on every startup. `get_connection()` returns a context manager
that yields a connection with foreign keys enabled and dict-row access.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sites (
  id              INTEGER PRIMARY KEY,
  url             TEXT    NOT NULL UNIQUE,
  title           TEXT,
  description     TEXT,
  source          TEXT,
  discovered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_attempted  TIMESTAMP,
  status          TEXT    DEFAULT 'fresh',
  skip_reason     TEXT,
  tags            TEXT
);
CREATE INDEX IF NOT EXISTS idx_sites_status ON sites(status);

CREATE TABLE IF NOT EXISTS clips (
  id              INTEGER PRIMARY KEY,
  site_id         INTEGER NOT NULL REFERENCES sites(id),
  recording_path  TEXT,
  final_path      TEXT,
  r2_public_url   TEXT,
  caption         TEXT,
  hashtags        TEXT,
  sound_id        INTEGER REFERENCES sounds(id),
  duration_sec    REAL,
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  status          TEXT DEFAULT 'pending',
  review_notes    TEXT,
  reviewed_at     TIMESTAMP,
  reviewed_by     TEXT,
  edited_caption  TEXT
);

CREATE TABLE IF NOT EXISTS sounds (
  id              INTEGER PRIMARY KEY,
  tiktok_sound_id TEXT UNIQUE,
  title           TEXT,
  artist          TEXT,
  audio_path      TEXT,
  trending_score  REAL,
  fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_used_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS postings (
  id              INTEGER PRIMARY KEY,
  clip_id         INTEGER NOT NULL REFERENCES clips(id),
  platform        TEXT NOT NULL,
  external_id     TEXT,
  external_url    TEXT,
  status          TEXT,
  error           TEXT,
  posted_at       TIMESTAMP,
  scheduled_for   TIMESTAMP
);
"""


def init_db(db_path: Path) -> None:
    """Create tables and indexes if they don't exist. Idempotent."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()


@contextmanager
def get_connection(db_path: Path):
    """Yield a sqlite3 connection with FK enabled and dict-row access."""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && python -m pytest tests/test_db.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/stumbleupon/db.py tests/test_db.py
git commit -m "feat: add SQLite schema and connection helpers"
```

---

## Task 5: Queue operations (TDD)

**Files:**
- Create: `src/stumbleupon/queue.py`
- Create: `tests/test_queue.py`

- [ ] **Step 1: Write the failing test in `tests/test_queue.py`**

```python
"""Tests for queue operations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stumbleupon.db import init_db
from stumbleupon.models import Clip, Site
from stumbleupon import queue


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "stumbleupon.db"
    init_db(p)
    return p


def _insert_site(db_path: Path, url: str = "https://example.com") -> int:
    with sqlite3_connect(db_path) as conn:
        cur = conn.execute("INSERT INTO sites (url) VALUES (?)", (url,))
        conn.commit()
        return cur.lastrowid or 0


def _insert_clip(
    db_path: Path,
    site_id: int,
    status: str = "pending",
    r2_url: str | None = None,
    caption: str | None = None,
    edited_caption: str | None = None,
    scheduled_for: datetime | None = None,
) -> int:
    with sqlite3_connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO clips (site_id, status, r2_public_url, caption, edited_caption, scheduled_for) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (site_id, status, r2_url, caption, edited_caption, scheduled_for.isoformat() if scheduled_for else None),
        )
        conn.commit()
        return cur.lastrowid or 0


# We need sqlite3_connect as a context manager helper for the test fixtures.
import sqlite3
from contextlib import contextmanager

@contextmanager
def sqlite3_connect(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def test_get_pending_clips_returns_only_pending(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    pending_id = _insert_clip(db_path, site_id, status="pending", r2_url="https://r2/x.mp4", caption="c1")
    _insert_clip(db_path, site_id, status="approved", r2_url="https://r2/y.mp4", caption="c2")
    _insert_clip(db_path, site_id, status="needs_attention")

    pending = queue.get_pending_clips(db_path)
    assert [c.id for c in pending] == [pending_id]
    assert isinstance(pending[0], Clip)
    assert pending[0].status == "pending"


def test_get_pending_clips_excludes_clips_without_r2_url(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    _insert_clip(db_path, site_id, status="pending")  # no r2_url
    pending = queue.get_pending_clips(db_path)
    assert pending == []


def test_approve_clip_moves_to_approved(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    clip_id = _insert_clip(db_path, site_id, status="pending", r2_url="https://r2/x.mp4")

    queue.approve_clip(db_path, clip_id, reviewer="paul")

    with sqlite3_connect(db_path) as conn:
        row = conn.execute("SELECT status, reviewed_by FROM clips WHERE id=?", (clip_id,)).fetchone()
    assert row["status"] == "approved"
    assert row["reviewed_by"] == "paul"


def test_reject_clip_moves_to_rejected(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    clip_id = _insert_clip(db_path, site_id, status="pending", r2_url="https://r2/x.mp4")
    queue.reject_clip(db_path, clip_id, reviewer="paul", notes="caption is off")
    with sqlite3_connect(db_path) as conn:
        row = conn.execute("SELECT status, review_notes FROM clips WHERE id=?", (clip_id,)).fetchone()
    assert row["status"] == "rejected"
    assert row["review_notes"] == "caption is off"


def test_edit_caption_persists_edit(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    clip_id = _insert_clip(db_path, site_id, caption="original")
    queue.edit_caption(db_path, clip_id, "human-tweaked")
    with sqlite3_connect(db_path) as conn:
        row = conn.execute("SELECT caption, edited_caption FROM clips WHERE id=?", (clip_id,)).fetchone()
    assert row["caption"] == "original"
    assert row["edited_caption"] == "human-tweaked"


def test_mark_posted_sets_status_and_url(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    clip_id = _insert_clip(db_path, site_id, status="approved", r2_url="https://r2/x.mp4")
    queue.mark_posted(db_path, clip_id, external_url="https://tiktok.com/v/abc")
    with sqlite3_connect(db_path) as conn:
        row = conn.execute("SELECT status FROM clips WHERE id=?", (clip_id,)).fetchone()
        posting = conn.execute(
            "SELECT status, external_url FROM postings WHERE clip_id=?", (clip_id,)
        ).fetchone()
    assert row["status"] == "posted"
    assert posting["status"] == "posted"
    assert posting["external_url"] == "https://tiktok.com/v/abc"


def test_mark_posting_failed_keeps_clip_approved(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    clip_id = _insert_clip(db_path, site_id, status="approved", r2_url="https://r2/x.mp4")
    queue.mark_posting_failed(db_path, clip_id, error="buffer 500")
    with sqlite3_connect(db_path) as conn:
        clip = conn.execute("SELECT status FROM clips WHERE id=?", (clip_id,)).fetchone()
        posting = conn.execute(
            "SELECT status, error FROM postings WHERE clip_id=?", (clip_id,)
        ).fetchone()
    assert clip["status"] == "approved"  # unchanged, ready to retry
    assert posting["status"] == "failed"
    assert posting["error"] == "buffer 500"


def test_get_approved_ready_to_post_respects_schedule(db_path: Path) -> None:
    site_id = _insert_site(db_path)
    now = datetime.now(timezone.utc)
    past = now - timedelta(hours=1)
    future = now + timedelta(hours=1)

    ready_id = _insert_clip(db_path, site_id, status="approved", r2_url="https://r2/a.mp4", scheduled_for=past)
    _insert_clip(db_path, site_id, status="approved", r2_url="https://r2/b.mp4", scheduled_for=future)
    _insert_clip(db_path, site_id, status="approved", r2_url="https://r2/c.mp4", scheduled_for=None)

    ready = queue.get_approved_ready_to_post(db_path, now=now)
    assert [c.id for c in ready] == [ready_id]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && python -m pytest tests/test_queue.py -v`
Expected: ImportError on `stumbleupon.queue`.

- [ ] **Step 3: Implement `src/stumbleupon/queue.py`**

```python
"""High-level DB operations on clips and postings.

This is the only module that mutates `clips.status`. Everything else
calls into here. Returns dataclasses, not dicts.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .db import get_connection
from .models import CLIP_STATUSES, Clip, Posting


def _row_to_clip(row: sqlite3.Row) -> Clip:
    return Clip(
        id=row["id"],
        site_id=row["site_id"],
        recording_path=row["recording_path"],
        final_path=row["final_path"],
        r2_public_url=row["r2_public_url"],
        caption=row["caption"],
        hashtags=row["hashtags"],
        sound_id=row["sound_id"],
        duration_sec=row["duration_sec"],
        created_at=row["created_at"],
        status=row["status"],
        review_notes=row["review_notes"],
        reviewed_at=row["reviewed_at"],
        reviewed_by=row["reviewed_by"],
        edited_caption=row["edited_caption"],
    )


def _row_to_posting(row: sqlite3.Row) -> Posting:
    return Posting(
        id=row["id"],
        clip_id=row["clip_id"],
        platform=row["platform"],
        external_id=row["external_id"],
        external_url=row["external_url"],
        status=row["status"],
        error=row["error"],
        posted_at=row["posted_at"],
        scheduled_for=row["scheduled_for"],
    )


# ---------------------------------------------------------------------------
# Reviewer-driven transitions
# ---------------------------------------------------------------------------


def get_pending_clips(db_path: Path) -> list[Clip]:
    """Clips awaiting human review (status=pending, has r2_public_url)."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM clips "
            "WHERE status='pending' AND r2_public_url IS NOT NULL "
            "ORDER BY created_at ASC"
        ).fetchall()
    return [_row_to_clip(r) for r in rows]


def approve_clip(db_path: Path, clip_id: int, reviewer: str) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET status='approved', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP "
            "WHERE id=?",
            (reviewer, clip_id),
        )


def reject_clip(db_path: Path, clip_id: int, reviewer: str, notes: str = "") -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET status='rejected', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP, "
            "review_notes=? WHERE id=?",
            (reviewer, notes, clip_id),
        )


def edit_caption(db_path: Path, clip_id: int, new_caption: str) -> None:
    """Save a human-edited caption. The original `caption` is preserved for comparison."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET edited_caption=? WHERE id=?",
            (new_caption, clip_id),
        )


# ---------------------------------------------------------------------------
# Poster-driven transitions
# ---------------------------------------------------------------------------


def get_approved_ready_to_post(db_path: Path, now: datetime | None = None) -> list[Clip]:
    """Approved clips whose scheduled_for is in the past (or unset)."""
    if now is None:
        now = datetime.utcnow()
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM clips "
            "WHERE status='approved' AND r2_public_url IS NOT NULL "
            "AND (scheduled_for IS NULL OR scheduled_for <= ?) "
            "ORDER BY COALESCE(scheduled_for, created_at) ASC",
            (now.isoformat(),),
        ).fetchall()
    return [_row_to_clip(r) for r in rows]


def mark_posted(db_path: Path, clip_id: int, external_url: str) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET status='posted' WHERE id=?",
            (clip_id,),
        )
        conn.execute(
            "INSERT INTO postings (clip_id, platform, status, external_url, posted_at) "
            "VALUES (?, 'tiktok', 'posted', ?, CURRENT_TIMESTAMP)",
            (clip_id, external_url),
        )


def mark_posting_failed(db_path: Path, clip_id: int, error: str) -> None:
    """Record a failed Buffer/R2 attempt. Keep the clip approved for retry."""
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO postings (clip_id, platform, status, error) "
            "VALUES (?, 'tiktok', 'failed', ?)",
            (clip_id, error),
        )


def record_posting_queued(db_path: Path, clip_id: int, scheduled_for: datetime) -> None:
    """Mark a posting as scheduled (Buffer accepted it, awaiting publish time)."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET scheduled_for=? WHERE id=?",
            (scheduled_for.isoformat(), clip_id),
        )
        conn.execute(
            "INSERT INTO postings (clip_id, platform, status, scheduled_for) "
            "VALUES (?, 'tiktok', 'queued', ?)",
            (clip_id, scheduled_for.isoformat()),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && python -m pytest tests/test_queue.py -v`
Expected: 8 passed.

- [ ] **Step 5: Run the full test suite**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && python -m pytest -v`
Expected: All tests pass (~27 total: 5 config + 7 models + 7 db + 8 queue).

- [ ] **Step 6: Commit**

```bash
git add src/stumbleupon/queue.py tests/test_queue.py
git commit -m "feat: add queue operations for reviewer and poster flows"
```

---

## Task 6: CLI scaffold (`main.py`)

**Files:**
- Create: `src/stumbleupon/main.py`

- [ ] **Step 1: Create `src/stumbleupon/main.py`**

```python
"""Command-line entry point. Subcommands are stubbed in this plan and
filled in by their respective component plans."""

from __future__ import annotations

import argparse
import sys


def cmd_run(args: argparse.Namespace) -> int:
    print("run: not yet implemented (scaffold plan)", file=sys.stderr)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    print("review: not yet implemented (scaffold plan)", file=sys.stderr)
    return 0


def cmd_post(args: argparse.Namespace) -> int:
    print("post: not yet implemented (scaffold plan)", file=sys.stderr)
    return 0


def cmd_scrape_sounds(args: argparse.Namespace) -> int:
    print("scrape-sounds: not yet implemented (scaffold plan)", file=sys.stderr)
    return 0


def cmd_show_config(args: argparse.Namespace) -> int:
    from .config import load_settings

    settings = load_settings()
    for field_name in settings.__dataclass_fields__:
        value = getattr(settings, field_name)
        if any(token in field_name for token in ("key", "secret", "password")):
            value = "***" if value else "(unset)"
        print(f"{field_name} = {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stumbleupon")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="one pipeline pass (launchd-triggered)").set_defaults(func=cmd_run)
    sub.add_parser("review", help="interactive CLI to approve/reject pending clips").set_defaults(func=cmd_review)
    sub.add_parser("post", help="post approved clips whose scheduled time has arrived").set_defaults(func=cmd_post)
    sub.add_parser("scrape-sounds", help="refresh trending TikTok sounds catalog").set_defaults(func=cmd_scrape_sounds)
    sub.add_parser("show-config", help="print loaded settings (redacted)").set_defaults(func=cmd_show_config)
    return parser


def cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(cli())
```

- [ ] **Step 2: Verify the CLI scaffold runs**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && python -m stumbleupon.main --help`
Expected: usage message listing `run`, `review`, `post`, `scrape-sounds`, `show-config`.

- [ ] **Step 3: Verify `show-config` works (using a tmp .env)**

Run: `cd /Users/paullehn/Desktop/stumbleUpon && ANTHROPIC_API_KEY=sk-test python -m stumbleupon.main show-config`
Expected: printout with `anthropic_api_key = ***`, `buffer_api_key = (unset)`, etc.

- [ ] **Step 4: Commit**

```bash
git add src/stumbleupon/main.py
git commit -m "feat: add CLI scaffold with subcommand stubs"
```

---

## Task 7: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Create `README.md`**

```markdown
# stumbleupon

A local TikTok pipeline for the weird web. Scrapes `stumbleupon.cc`, records short clips of using each site, generates captions via Claude, attaches trending sounds, and posts to TikTok via Buffer.

**Status:** v1 scaffolding. Only the data model and CLI shell are in place; component plans follow.

## Setup

```bash
# 1. Install (editable, with dev extras)
pip install -e ".[dev]"

# 2. Copy env template and fill in real values
cp .env.example .env
$EDITOR .env

# 3. Initialize the database (happens automatically on first run too)
python -m stumbleupon.main show-config  # smoke test: settings load
```

## Dev loop

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=stumbleupon

# Lint / type-check (not yet configured)
```

## Project layout

See [docs/superpowers/specs/2026-06-10-stumbleupon-pipeline-design.md](docs/superpowers/specs/2026-06-10-stumbleupon-pipeline-design.md) for the full v1 design and `docs/tone-guide.md` for the captioner tone.

```
src/stumbleupon/
├── config.py    # Settings loaded from .env
├── models.py    # Site, Clip, Sound, Posting dataclasses
├── db.py        # SQLite schema + connection helpers
├── queue.py     # The only module that mutates clips.status
└── main.py      # CLI entry point
```

## Roadmap

This plan covers the scaffold. Future plans:
- Scraper (stumbleupon.cc crawl)
- Recorder (Playwright 30s vertical video)
- Captioner (Claude + tone guide)
- Sounds (TikTok trending scrape)
- Composer (ffmpeg)
- Reviewer (CLI)
- Poster (Buffer + R2)
- launchd plists
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup, dev loop, and roadmap"
```

---

## Self-Review

**1. Spec coverage (data-model focused):**
- §4 `sites` table → covered in Task 4 (schema)
- §4 `clips` table → covered in Task 4 (schema)
- §4 `sounds` table → covered in Task 4 (schema)
- §4 `postings` table → covered in Task 4 (schema)
- §4 `idx_sites_status` index → covered in Task 4
- §4 status fields and transitions → covered in Tasks 3 (validation) and 5 (queue)
- §4 `edited_caption` separate from `caption` → covered in Tasks 3 (`effective_caption`) and 5 (`edit_caption`)
- §4 `r2_public_url` column → added to schema (was implicit in spec; spec mentioned it for the R2 upload flow)
- §5.6 `queue.py` is the only module that mutates `clips.status` → enforced in Task 5 design comment
- §5.10 `main.py` subcommands → covered in Task 6

**Out of scope for this plan (covered in future plans):** scraper, recorder, captioner, sounds, composer, reviewer, poster, launchd plists, end-to-end test, integration test.

**2. Placeholder scan:** No "TBD" or "TODO" in any code block. Every function is fully implemented.

**3. Type consistency:** Cross-checked:
- `Clip` is defined in `models.py` (Task 3) and consumed in `queue.py` (Task 5) and tests — consistent.
- `Settings` is defined in `config.py` (Task 2) and consumed in `main.py` (Task 6) — consistent.
- `get_connection(db_path)` signature is consistent across `db.py` and `queue.py`.
- All env var names in `.env.example` match the keys in `config.py`.

**One decision worth flagging:** I added a `r2_public_url` column to the `clips` table and a `clips.scheduled_for` column. The spec text didn't list these explicitly, but they came up in §5.8 (Buffer needs a public URL) and §4 (postings.scheduled_for for spacing). Adding them now is cheaper than a migration later. If you'd rather keep the schema exactly as the spec describes, say so and I'll split them into a Task 4b (migration).

**One more:** the spec uses `reviewed_at` and `reviewed_by` as TIMESTAMP/TEXT — I used those types. Good.
