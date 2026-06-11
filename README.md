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
