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
