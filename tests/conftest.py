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
