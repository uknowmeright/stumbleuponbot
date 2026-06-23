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
        "R2_ENDPOINT_URL=https://account.r2.cloudflarestorage.com\n"
        "R2_PUBLIC_URL_BASE=https://media.example.com\n"
    )
    settings = load_settings(env_file=env_file)
    assert settings.anthropic_api_key == "sk-test"
    assert settings.buffer_api_key == "buf-test"
    assert settings.r2_bucket_name == "stumble"
    assert settings.r2_endpoint_url == "https://account.r2.cloudflarestorage.com"
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
        r2_endpoint_url="https://account.r2.cloudflarestorage.com",
        r2_public_url_base="d",
    )
    with pytest.raises(Exception):  # FrozenInstanceError subclass
        settings.anthropic_api_key = "z"  # type: ignore[misc]
