# stumbleupon

A local TikTok pipeline for the weird web. Scrapes `stumbleupon.cc`, records short clips of using each site, generates captions via Claude, attaches trending sounds, and posts to TikTok via Buffer.

**Status:** v1 scaffolding. Only the data model and CLI shell are in place; component plans follow.

## Setup

```bash
# 1. Create a venv and install the package (editable, with dev extras)
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install "hatchling>=1.30" "build>=1.5"   # see note below
.venv/bin/pip install -e ".[dev]"

# 2. Copy env template and fill in real values
cp .env.example .env
$EDITOR .env

# 3. Smoke test: settings load
.venv/bin/python -m stumbleupon.main show-config

# 4. Install Playwright's Chromium binary (one-time, downloads ~150MB)
.venv/bin/playwright install chromium
```

Python 3.11+ is required (per `pyproject.toml`). The setup above was verified on Python 3.14.5 (Homebrew).

**Note on the build-tool upgrade:** On Python 3.14, pip's bundled `hatchling` (the build backend) is too old to produce a working editable install — it generates a `.pth` file the runtime can't load, so `python -c "import stumbleupon"` fails even though `pytest` works (because `pyproject.toml` sets `pythonpath = ["src"]` for pytest). Upgrading `hatchling` to >=1.30 and `build` to >=1.5 fixes this. The same fix may apply on other Python 3.14 setups; on 3.11-3.13 the bundled tools usually work.

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
├── captioner.py # Claude API → caption + hashtags
├── config.py    # Settings loaded from .env
├── db.py        # SQLite schema + connection helpers
├── main.py      # CLI entry point
├── models.py    # Site, Clip, Sound, Posting dataclasses
├── queue.py     # The only module that mutates clips.status
├── recorder.py  # 30s vertical video clips via Playwright
└── scraper.py   # stumbleupon.cc → fresh sites (Supabase API)
```

## Roadmap

This plan covers the scaffold. Future plans:
- Scraper (stumbleupon.cc crawl) — done (now uses Supabase API)
- Recorder (Playwright 30s vertical video) — done
- Captioner (Claude + tone guide) — done
- Sounds (TikTok trending scrape)
- Composer (ffmpeg)
- Reviewer (CLI)
- Poster (Buffer + R2)
- launchd plists
