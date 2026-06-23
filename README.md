# stumbleupon

A local TikTok pipeline for the weird web. Scrapes `stumbleupon.cc`, records short clips of using each site, generates captions via Claude, attaches trending sounds, and posts to TikTok via Buffer.

**Status:** v1 complete. All 8 components (scraper, recorder, captioner, sounds, composer, reviewer, poster, launchd) are implemented and tested. 192 tests passing. macOS launchd plists available via `stumbleupon install`.

## Setup

The fastest path is the bootstrap script — it does steps 1-5 below in one shot and is safe to re-run:

```bash
./scripts/setup.sh          # interactive (asks before opening $EDITOR)
./scripts/setup.sh --yes    # non-interactive (no editor prompt)
```

The script skips any step that's already done, checks for `ffmpeg` (and tells you the right install command for your platform), copies `.env.example` to `.env` if missing, and finishes with `show-config` so you can see what's still unset.

### Manual setup (the long form)

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

# 5. Install ffmpeg system-wide (one-time)
brew install ffmpeg  # macOS; for Linux: apt-get install ffmpeg
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

## Scheduling (macOS)

Three `launchd` plists schedule the pipeline on macOS:

| Label | Schedule | Command |
|---|---|---|
| `com.user.stumbleupon.pipeline` | 2x/day at 10am + 8pm | `python -m stumbleupon run` |
| `com.user.stumbleupon.sounds` | 1x/day at 3am | `python -m stumbleupon scrape-sounds` |
| `com.user.stumbleupon.poster` | every 15 min | `python -m stumbleupon post` |

Install / uninstall:

```bash
.venv/bin/stumbleupon install      # copies 3 plists to ~/Library/LaunchAgents/ + launchctl load
.venv/bin/stumbleupon uninstall    # launchctl unload + delete the 3 plists
launchctl list | grep stumbleupon   # verify the 3 jobs are scheduled
```

Logs are written to `data/logs/com.user.stumbleupon.<name>.{out,err}.log`.

**macOS-only caveat:** `launchd` will not run scheduled jobs while the Mac is asleep. If the lid is closed overnight, the morning job runs as soon as the Mac wakes. If the Mac is shut down, jobs are missed (no catch-up). For long sleeps, enable "Wake for network access" in System Settings → Energy.

Sample plists live in `launchd/` (the install command renders them with your real paths and writes the binary plist to `~/Library/LaunchAgents/`).

## Project layout

See [docs/superpowers/specs/2026-06-10-stumbleupon-pipeline-design.md](docs/superpowers/specs/2026-06-10-stumbleupon-pipeline-design.md) for the full v1 design and `docs/tone-guide.md` for the captioner tone.

```
src/stumbleupon/
├── captioner.py # Claude API → caption + hashtags
├── composer.py  # ffmpeg → final 1080x1920 mp4
├── config.py    # Settings loaded from .env
├── db.py        # SQLite schema + connection helpers
├── launchd.py   # macOS launchd plist render + install/uninstall
├── main.py      # CLI entry point
├── models.py    # Site, Clip, Sound, Posting dataclasses
├── poster.py    # R2 upload + Buffer post
├── queue.py     # The only module that mutates clips.status
├── recorder.py  # 30s vertical video clips via Playwright
├── reviewer.py  # human-in-the-loop CLI for clip approval
├── scraper.py   # stumbleupon.cc → fresh sites (Supabase API)
└── sounds.py    # Trending TikTok sounds catalog + download
```

## Roadmap

This plan covers the scaffold. Future plans:
- Scraper (stumbleupon.cc crawl) — done (now uses Supabase API)
- Recorder (Playwright 30s vertical video) — done
- Captioner (Claude + tone guide) — done
- Sounds (TikTok trending scrape) — done
- Composer (ffmpeg) — done
- Reviewer (CLI) — done
- Poster (Buffer + R2) — done
- launchd plists — done
