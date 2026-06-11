# StumbleUpon Pipeline — Design Spec

**Date:** 2026-06-10
**Status:** Finalized (v1 scope locked)
**Scope:** v1 — TikTok-first local pipeline

## 1. Overview

A scheduled Python pipeline that runs on a single Mac, scrapes "weird web" sites from a StumbleUpon-style directory, records short video clips of using each site, generates captions and hashtags with an LLM, attaches a trending TikTok sound, and queues the result for human review before posting to TikTok.

The system targets a cadence of 1-2 posts per day with a human-in-the-loop approval gate, then auto-posts approved clips at scheduled times. Lower volume chosen so each clip gets more review attention and the channel quality stays high.

## 2. Goals & Non-Goals

### Goals
- Scrape 10-30 fresh sites per day from `stumbleupon.cc`
- Record ~30s vertical video clips of using each site
- Generate on-brand captions and hashtags via Claude API
- Attach trending TikTok sounds (no fallback in v1 — surface to review queue on failure)
- Surface clips in a local review queue before they go public
- Auto-post approved clips to TikTok via Buffer + Cloudflare R2, spread across the day
- Be operable by one person from a single laptop

### Non-goals (v1)
- Multi-platform posting (X, YouTube come later)
- Multi-account support
- Web-based review UI (CLI only)
- Engagement / auto-reply bots
- Analytics ingestion
- Production-grade multi-tenant deployment
- Automated account creation (ToS violation, handled manually by the user)

## 3. Architecture

### Pipeline shape

```
                ┌──────────┐
                │ launchd  │  2x/day, 10am and 8pm
                └────┬─────┘
                     │ triggers
                     ▼
            ┌────────────────┐
            │  pipeline.run  │
            │                │
            │ 1. scrape      │  stumbleupon.cc → fresh sites
            │ 2. record      │  playwright → webm per site
            │ 3. caption     │  claude api → caption + hashtags
            │ 4. pick sound  │  trending sounds (skip if unavailable)
            │ 5. compose     │  ffmpeg → final mp4
            │ 6. upload      │  cloudflare r2 → public url
            │ 7. queue       │  clips.status = pending
            └────────┬───────┘
                     │ writes to
                     ▼
            ┌────────────────┐
            │  SQLite queue  │  data/stumbleupon.db
            └────────┬───────┘
                     │ reads
                     ▼
        ┌────────────────────────┐
        │  reviewer (manual CLI) │  python -m stumbleupon review
        │  approves / rejects    │
        └────────┬───────────────┘
                 │ status: approved
                 ▼
        ┌────────────────────────┐
        │  poster (launchd 15m)  │  python -m stumbleupon post
        │  buffer → tiktok       │
        └────────────────────────┘
```

### Why a queue
- Recording and posting are decoupled, so a single failed recording can't lose a site
- A human reviews before anything goes public
- A burst of approved clips can be spread across the day for natural posting cadence

### Directory layout

```
~/Projects/stumbleupon/
├── pyproject.toml
├── README.md
├── .env.example
├── src/stumbleupon/
│   ├── __init__.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── scraper.py
│   ├── recorder.py
│   ├── captioner.py
│   ├── sounds.py
│   ├── composer.py
│   ├── queue.py
│   ├── reviewer.py
│   ├── poster.py
│   ├── pipeline.py
│   └── main.py
├── data/
│   ├── stumbleupon.db
│   ├── recordings/
│   ├── final/
│   ├── sounds/
│   └── logs/
├── scripts/
│   ├── com.user.stumbleupon.pipeline.plist
│   └── com.user.stumbleupon.poster.plist
└── tests/
    ├── test_scraper.py
    ├── test_composer.py
    ├── test_db.py
    ├── test_queue.py
    ├── test_sounds.py
    ├── test_captioner.py
    ├── test_pipeline.py
    ├── test_end_to_end.py
    └── fixtures/
```

### Three commands the user runs

- `python -m stumbleupon run` — one pipeline pass (usually `launchd`-triggered)
- `python -m stumbleupon review` — interactive CLI to approve/reject pending clips
- `python -m stumbleupon post` — posts approved clips whose scheduled time has arrived (also `launchd`-triggered)

Additional subcommands: `scrape-sounds` (refresh trending sounds), `show-config` (debug helper), `run --dry-run --limit 1` (smoke test).

## 4. Data Model

Single SQLite database at `data/stumbleupon.db`. Four tables.

```sql
CREATE TABLE sites (
  id              INTEGER PRIMARY KEY,
  url             TEXT    NOT NULL UNIQUE,
  title           TEXT,
  description     TEXT,
  source          TEXT,                              -- 'stumbleupon.cc'
  discovered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_attempted  TIMESTAMP,
  status          TEXT    DEFAULT 'fresh',           -- fresh | recorded | failed | skipped
  skip_reason     TEXT,
  tags            TEXT                               -- comma-separated
);
CREATE INDEX idx_sites_status ON sites(status);

CREATE TABLE clips (
  id              INTEGER PRIMARY KEY,
  site_id         INTEGER NOT NULL REFERENCES sites(id),
  recording_path  TEXT,                              -- data/recordings/<id>.webm
  final_path      TEXT,                              -- data/final/<id>.mp4
  caption         TEXT,
  hashtags        TEXT,                              -- comma-separated
  sound_id        INTEGER REFERENCES sounds(id),
  duration_sec    REAL,
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  status          TEXT DEFAULT 'pending',            -- pending | needs_attention | approved | rejected | posted | failed
  review_notes    TEXT,
  reviewed_at     TIMESTAMP,
  reviewed_by     TEXT,
  edited_caption  TEXT                               -- if user edited; poster prefers this
);

CREATE TABLE sounds (
  id              INTEGER PRIMARY KEY,
  tiktok_sound_id TEXT UNIQUE,
  title           TEXT,
  artist          TEXT,
  audio_path      TEXT,                              -- data/sounds/<id>.mp3
  trending_score  REAL,                              -- 0-100
  fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_used_at    TIMESTAMP                          -- round-robin
);

CREATE TABLE postings (
  id              INTEGER PRIMARY KEY,
  clip_id         INTEGER NOT NULL REFERENCES clips(id),
  platform        TEXT NOT NULL,                     -- 'tiktok' (others later)
  external_id     TEXT,
  external_url    TEXT,
  status          TEXT,                              -- 'queued' | 'posted' | 'failed'
  error           TEXT,
  posted_at       TIMESTAMP,
  scheduled_for   TIMESTAMP                          -- for spacing posts
);
```

### Key design choices
- `sites.status` is the dedup mechanism — once a site is `recorded` or `skipped`, it isn't picked again. The scraper only fetches `fresh` sites.
- `clips.status` has 6 states: `pending` → `approved` | `rejected` | `needs_attention` → `posted` | `failed`. Status transitions are the queue. `needs_attention` is a "stuck but don't auto-retry" state used when, e.g., the LLM call exhausted retries.
- `clips.edited_caption` is separate from `caption` to keep the original LLM output for comparison. The poster uses `edited_caption` if set, else `caption`.
- `sounds` round-robin: we never repeat the same sound back-to-back; catalog refreshes daily.
- `postings.scheduled_for` spreads 1-2 posts/day across waking hours to avoid burst behavior.
- Files (videos, audio) live on disk; the DB stores paths. Smaller DB, easier to inspect.
- A clip's `recording_path` stays around after `final_path` exists, until the clip is `posted`, so a failed compose step doesn't require re-recording.

## 5. Components

Each component is a small module with one job. They communicate through the DB and the filesystem, not by calling each other directly. The orchestrator (`pipeline.py`) wires them together.

### 5.1 `scraper.py` — fetch fresh sites from stumbleupon.cc
- Async (httpx + beautifulsoup4) crawl of the homepage and a few category pages.
- Extracts: `url`, `title`, `description`, `tags`.
- Inserts into `sites` with `ON CONFLICT(url) DO NOTHING` so re-crawls are idempotent.
- Filters out: adult content (configurable blocklist of keywords), known-broken domains, sites we've already recorded.
- Returns: list of new `Site` rows.
- **Failure mode:** if the crawl fails, we just don't get new sites today; the pipeline doesn't crash.

### 5.2 `recorder.py` — record 30s of using the site
- Uses Playwright (sync API) with `record_video_dir`.
- Viewport: 1080×1920 portrait (TikTok format).
- Records a fixed 30s session. Mouse moves and scrolls are randomized so it looks like a real person exploring, not a static frame.
- Mutes the browser tab audio capture (we want TikTok's trending sound, not the site's audio).
- Output: `data/recordings/<site_id>.webm`.
- Updates `sites.status` to `recorded` (success) or `failed` (with error in `skip_reason`).
- Returns: list of `(site_id, webm_path)` tuples.
- **Failure mode:** per-site try/except. One bad site doesn't sink the batch.

### 5.3 `captioner.py` — generate caption + hashtags
- For each clip, builds a prompt with: site title, URL, description, sample of past successful captions (3-5 from `posted` clips), and the channel's tone guide.
- The tone guide lives at **`docs/tone-guide.md`** — read on every call. Treat as source of truth for voice, length, banned words, hashtag style.
- Calls **Claude API** (`claude-sonnet-4-6`) — chosen because the user is already in the Claude ecosystem and it's the strongest writer for short, punchy copy.
- Output enforced via tool use / structured output: `{caption: str, hashtags: [str]}`.
- Length target: 80-150 chars for the caption (TikTok sweet spot).
- Saves raw LLM response to logs for debugging.
- Returns: `clip_id → (caption, hashtags)` mapping.

### 5.4 `sounds.py` — fetch trending TikTok sounds
- Crawls TikTok's Creative Center trending sounds page (Playwright; residential proxy optional).
- Extracts: sound id, title, artist, view count (proxy for trending score).
- Downloads audio for the top 5-10 sounds via yt-dlp or direct URL → `data/sounds/<id>.mp3`.
- Refreshes daily (or on demand) so the catalog stays current.
- Round-robin selection: pick the highest-trending sound that hasn't been used in the last 3 days.
- **Failure behavior (v1):** if the scrape fails or the catalog is empty, do **not** invent a fallback. The clip is queued with `status='needs_attention'` and surfaced in the review queue so a human can attach a sound manually (or re-run the pipeline). Avoids the licensing question of shipping a bundled track.
- Returns: a single `Sound` row, or `None` on failure.

### 5.5 `composer.py` — combine video + sound into final mp4
- Uses **ffmpeg** via the `ffmpeg-python` library.
- Steps per clip:
  1. Trim recording to 30s (re-encode so the output is exactly 30s).
  2. Scale to 1080×1920 with letterboxing if needed.
  3. Mute the video's audio.
  4. Mix in the trending sound at ~70% volume.
  5. Output H.264 + AAC to `data/final/<clip_id>.mp4`.
- Optional: burn a single-line lower-third caption at the bottom (configurable, off by default for v1).
- Returns: final path.

### 5.6 `queue.py` — high-level DB operations
- Thin wrapper over `db.py`. Functions: `get_pending_clips()`, `mark_posted()`, `mark_failed()`, `get_approved_ready_to_post()`.
- This is the only module that mutates status. Everything else calls it.

### 5.7 `reviewer.py` — interactive CLI
- Run via `python -m stumbleupon review`.
- Lists pending clips with: thumbnail (extracted with ffmpeg from the final mp4), title, caption, hashtags, sound used.
- For each clip, opens the mp4 in QuickTime (`open` on macOS).
- Prompts: `[a]pprove  [r]eject  [e]dit caption  [s]kip  [q]uit`.
- Updates the clip's status via `queue.py`.
- Persists state so quitting mid-session resumes correctly.

### 5.8 `poster.py` — post approved clips to TikTok
- Posts via the **Buffer API** (GraphQL). Buffer handles the multi-platform OAuth and the TikTok-specific upload quirks.
- **Why Buffer over Ayrshare:** Buffer's free tier is $0/mo with 3,000 API requests/month and full TikTok business profile support. Ayrshare's cheapest equivalent is $149/mo. ~30× cost difference, same outcome.
- **Why Buffer over the direct TikTok API:** the direct API requires app review (weeks/months) and OAuth per account. Buffer already has TikTok approval.
- **Why Buffer free tier is enough:** at 1-2 posts/day with the rest of the pipeline being local, we estimate 100-300 API requests/day. 3,000/month is tight but workable; if we hit the limit, upgrade to Buffer Essentials ($5/mo, 7,500 req/mo) for headroom.
- **Video hosting:** Buffer's API requires a publicly accessible HTTPS URL to the mp4. We host on **Cloudflare R2** (S3-compatible, 10GB free storage, 10M free requests/month — far more than we need). The pipeline uploads to R2 in step 6 of the orchestrator and passes the resulting public URL to Buffer.
- **Buffer call shape** (from Buffer's GraphQL API):
  - `text` — caption + hashtags concatenated (Buffer has no separate hashtag field)
  - `dueAt` — ISO 8601 UTC, for scheduled posting
  - `assets: [{ video: { url: "<r2 public url>" } }]`
- Sets `postings.scheduled_for` if more than 2 clips are approved at once, spreading them across the next 12 hours.
- On success: marks clip `posted`, stores external URL.
- On failure: marks `failed`, stores error, leaves clip as `approved` for retry.

### 5.9 `pipeline.py` — the orchestrator
- Wires the components: scrape → for each fresh site, record → caption → pick sound → compose → save clip row as `pending`.
- Logs every step to `data/logs/pipeline-YYYY-MM-DD.log`.
- Exit code 0 on success, non-zero only if the whole pipeline crashed (vs a per-site failure).

### 5.10 `main.py` — CLI entry point
- argparse subcommands: `run`, `review`, `post`, `scrape-sounds`, `show-config`, plus `--dry-run --limit N` flag for `run`.

## 6. Scheduling

`launchd` is macOS's built-in scheduler — no extra services, no Docker, no cron weirdness. Two plist files in `~/Library/LaunchAgents/`:

- `com.user.stumbleupon.pipeline.plist` — runs 2×/day at **10am and 8pm**. A 30s recording × ~3 sites per run × 2 runs/day ≈ 6-10 minutes of compute per day, leaving plenty of room for the human review step. Tunable in the plist.
- `com.user.stumbleupon.poster.plist` — runs every 15 minutes, picks clips where `status='approved'` and `scheduled_for <= now()`.

**v1 caveat:** `launchd` will not run scheduled jobs while the Mac is asleep. If the lid is closed overnight, the morning job runs as soon as the Mac wakes. If the Mac is shut down, jobs are missed (no catch-up). Acceptable for v1; if the user travels with the laptop closed for days, they should set "Wake for network access" in Energy settings or leave the laptop open.

**Notifications:** macOS native notification (`osascript -e 'display notification ...'`) when:
- New clips are ready to review
- A clip is successfully posted
- A clip failed (with the error in the body)

That way nothing requires you to remember to check.

## 7. Error Handling

**Philosophy:** graceful degradation, loud logs. The pipeline should survive partial failures; only a complete system crash should require human attention.

| Failure | Behavior |
|---|---|
| stumbleupon.cc is down | Skip this run. Log a warning. Try again in 4h. |
| One site's recording fails (timeout, crash) | Mark site `failed`, log, continue with next site. The batch survives. |
| All recordings fail | Pipeline exits non-zero, you get a notification. |
| Claude API rate limit / 5xx | Retry with exponential backoff (3 attempts). If still failing, queue the clip with an empty caption and mark `needs_attention`. |
| TikTok trending sounds scrape fails | Mark the clip `needs_attention`, surface in review queue. No bundled fallback in v1. |
| ffmpeg compose fails | Keep the recording, mark the clip `failed`, surface in review queue so you can re-run composer manually. |
| R2 upload fails | Mark the clip `needs_attention` (recording is preserved, just no public URL). Don't burn a Buffer slot on a clip we can't link. |
| Buffer API call fails | Mark posting `failed`, store error. Retry next scheduled run. After 3 failures, surface in review queue. |
| Posting succeeds but platform rejects later (DMCA, ToS strike) | Manual handling. We store the external URL; you take it from there. We can't auto-detect this. |
| `launchd` double-fires | Idempotent: scraper's `ON CONFLICT` + status flags mean reruns do nothing harmful. |

**Secrets management:** `.env` file (gitignored) with: Claude API key, Buffer API key, Cloudflare R2 access key/secret/bucket name, OpenAI key (optional, for Whisper captions), proxy URL (optional), ad-block keywords list.

**Quota tracking:** daily GET to Buffer's profile endpoint to confirm we're under the 3,000 req/month free tier limit. Refuse to post if we'd push the month over the cap; surface in the review queue instead.

## 8. Testing

**Strategy:** test the pure logic hard, mock the I/O heavily, do one round-trip integration test, and trust your eyes on the rest.

### Unit tests (pytest)

| Module | What's tested | How |
|---|---|---|
| `db.py` | Schema migrations, status transitions, dedup queries | Real SQLite in tmp dir, no mocking |
| `scraper.py` | HTML parsing of stumbleupon.cc, dedup logic, blocklist filtering | Saved HTML fixtures in `tests/fixtures/`, real parser |
| `captioner.py` | Prompt construction, response parsing, length validation | Mock the Claude client, assert on prompt + parsed output |
| `sounds.py` | Round-robin selection (no repeats in last N), fallback when catalog is empty | Mock the scraper + DB |
| `composer.py` | ffmpeg command construction, output path resolution | Mock subprocess, assert on the args list — don't actually run ffmpeg |
| `queue.py` | Status transitions, "what's ready to post" logic | Real SQLite |
| `pipeline.py` | Orchestration: scraper returns N, recorder returns M, captions generated for all M | Mock every component, assert on call order and DB state |

### One integration test (`tests/test_end_to_end.py`)
- Records one real (short) clip from a tiny test HTML page
- Composes it
- Skips the actual TikTok upload
- Asserts the final mp4 plays (probe with ffprobe)

This catches the "ffmpeg args were right but the file is corrupt" class of bug.

### What we explicitly don't test
- Real TikTok API calls (no test account, expensive, flaky)
- Real Playwright recording of stumbleupon.cc (external dependency, slow)
- The launchd plist (verified by running it manually first)

### Test-driven development
- The pure-logic modules (`db.py`, `sounds.py`, `queue.py`, `captioner.py` prompt building) get TDD.
- The I/O-heavy modules (`recorder.py`, `scraper.py`, `poster.py`) get manual smoke tests with sample inputs.

### Smoke test
`python -m stumbleupon run --dry-run --limit 1` — runs the full pipeline on a single fake site without writing final files. Used for sanity-checking after any change.

### CI
Skipped for v1. Run `pytest` locally. If we push to a remote later, GitHub Actions is a 10-line `.yml` add.

## 9. Open Questions / Future Work

- Captions burned into the video (lower thirds) — currently off by default; turn on in `.env` if engagement data supports it.
- Adding X and YouTube Shorts as posting targets — just two new channels in Buffer + R2 upload (Buffer already has these channels; we just create new channel IDs in our config and add the same `post → Buffer` flow).
- Web UI for review — minor upgrade to `reviewer.py` if CLI gets annoying.
- Site-quality scoring — use a small LLM call during scraping to score sites by "weirdness" so we can pick the most interesting ones.
- Engagement loop — read post stats, double down on what's working.
- Multi-account / account rotation — only if scale demands it.
- Captions on video via Whisper — auto-generate subtitles from site audio for higher engagement.
