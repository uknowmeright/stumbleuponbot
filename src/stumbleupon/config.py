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
