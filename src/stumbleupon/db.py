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
