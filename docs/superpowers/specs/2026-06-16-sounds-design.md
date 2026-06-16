# Sounds: Trending TikTok Catalog + Round-Robin Selection

> **Status:** Approved 2026-06-16. Closes the v1 feature set after the poster.

## Goal

Add a `sounds.py` module that refreshes a small local catalog of trending TikTok sounds, and a `queue.py` selection function that the pipeline orchestrator calls when composing a clip. Per the v1 design doc §5.4, with these explicit decisions made during brainstorm:

- **Proxy:** none for v1. Playwright with realistic UA + delays + retry/backoff. Intermittent failures surface as `needs_attention` clips for human review.
- **Audio download:** `yt-dlp` (new dep).
- **Catalog size:** top 10 downloaded per refresh, up to 20 kept in catalog, 3-day no-repeat window for round-robin selection.
- **Failure mode:** no fallback. Empty/stale catalog → clip goes to `needs_attention`.

## Architecture

Two phases, two surfaces:

### Phase 1: Refresh (on-demand via `stumbleupon scrape-sounds`)

| Function | Purpose | Pure / I/O |
|---|---|---|
| `sounds.parse_trending_rows(html: str) -> list[dict]` | Extract `{tiktok_sound_id, title, artist, views}` from Creative Center HTML | Pure |
| `sounds.build_ytdlp_argv(sound_url: str, out_path: Path) -> list[str]` | Build yt-dlp argv (audio-only, mp3) | Pure |
| `sounds.build_creative_center_url() -> str` | Build the trending-sounds URL | Pure |
| `sounds.fetch_html(url: str, *, retries: int = 3) -> str` | Playwright fetch with retry/backoff | I/O |
| `sounds.download_audio(sound_url: str, out_path: Path) -> None` | yt-dlp subprocess to `data/sounds/<id>.mp3` | I/O |
| `sounds.refresh_catalog(db_path, *, limit=10) -> int` | Orchestrator: fetch → parse → download → upsert | I/O |

The refresh writes into the existing `sounds` table via `ON CONFLICT(tiktok_sound_id) DO UPDATE` (so re-appearing sounds get fresh `trending_score` + `last_seen_at` rather than duplicate rows).

### Phase 2: Selection (called by `cmd_run` between caption + compose)

| Function | Purpose | Pure / I/O |
|---|---|---|
| `queue.count_sounds(db_path) -> int` | Total sounds in catalog | I/O |
| `queue.get_next_sound(db_path, *, exclude_used_within_days=3) -> Sound \| None` | Highest `trending_score` not used in last N days | I/O |
| `queue.attach_sound_to_clip(db_path, clip_id, sound_id) -> None` | Set `clips.sound_id` + stamp `sounds.last_used_at` | I/O |
| `queue.mark_clip_needs_attention(db_path, clip_id) -> None` | Update existing clip's status to `'needs_attention'` (called when no sound is available) | I/O |
| `queue.get_clips_needing_sound(db_path) -> list[Clip]` | Pending clips with no sound yet, ready for the pick step | I/O |

Pipeline integration in `cmd_run`: after `caption_pending_recordings`, for each pending clip, call `get_next_sound`. If a sound is returned, attach it; if not, create the clip with `status='needs_attention'`. Then `compose_pending_clips` runs and reads `clips.sound_id` → looks up `sounds.audio_path` → passes to `compose_clip`.

## Data Model

**No schema change.** The `sounds` table (in `db.py:52`) already has all needed columns:

```sql
CREATE TABLE IF NOT EXISTS sounds (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tiktok_sound_id TEXT UNIQUE,        -- Creative Center sound id
  title           TEXT,
  artist          TEXT,
  audio_path      TEXT,               -- data/sounds/<id>.mp3
  trending_score  REAL DEFAULT 0.0,   -- views (proxy for "trending")
  fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_used_at    TIMESTAMP           -- updated when attached to a clip
);
```

The Clip row's existing `sound_id INTEGER REFERENCES sounds(id)` is the link.

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| `pyproject.toml` | modify | Add `yt-dlp>=2024.4.0` |
| `src/stumbleupon/sounds.py` | create | `parse_trending_rows`, `build_ytdlp_argv`, `build_creative_center_url`, `fetch_html`, `download_audio`, `refresh_catalog` |
| `src/stumbleupon/queue.py` | modify | Add `count_sounds`, `get_next_sound`, `attach_sound_to_clip`, `upsert_sound`, `create_clip_needs_attention` |
| `src/stumbleupon/main.py` | modify | Replace `cmd_scrape_sounds` stub; add sound-pick step to `cmd_run` |
| `tests/test_sounds.py` | create | TDD for all pure functions + mocked I/O for fetch/download |
| `tests/test_queue.py` | modify | Add tests for new queue functions |
| `README.md` | modify | Mention `sounds.py` in layout, update Roadmap, add `yt-dlp` to setup |

## Function Signatures & Behavior

### `sounds.parse_trending_rows(html: str) -> list[dict]`

Takes the rendered HTML of TikTok Creative Center's trending sounds page. Returns at most `limit` dicts shaped:

```python
{"tiktok_sound_id": "abc123", "title": "Some Sound", "artist": "DJ X", "views": 1_200_000}
```

For v1, `trending_score` is **just the view count** (no composite, no growth rate). The higher the views, the higher the score. If we need a better signal later, we can swap the parser to compute a composite from the JSON.

The HTML structure is brittle by nature (TikTok's anti-scrape changes). The function:
- Looks for JSON-embedded data first (the `__UNIVERSAL_DATA_FOR_REHYDRATION__` script tag pattern TikTok uses).
- Falls back to DOM scraping (cards with `data-e2e="sound-card"` etc.) if JSON parsing fails.
- Returns `[]` on parse error or empty input — never raises.

Pure function, fully unit-testable with fixture HTML strings.

### `sounds.build_ytdlp_argv(sound_url: str, out_path: Path) -> list[str]`

Returns the argv list to invoke yt-dlp:
- `-x` (extract audio)
- `--audio-format mp3`
- `--audio-quality 0` (best)
- `-o <out_path>` (template)
- `--no-playlist`
- `--no-warnings`
- the URL

Pure function. Test asserts the list contains the right flags.

### `sounds.build_creative_center_url() -> str`

Returns the Creative Center URL: `https://www.tiktok.com/discover/music?lang=en` (the discover/music page lists trending sounds, English locale for stability).

### `sounds.fetch_html(url: str, *, retries: int = 3, backoff_sec: float = 2.0) -> str`

Playwright fetcher:
- Launches Chromium headless, viewport `1280x900`, locale `en-US`, timezone `America/New_York`.
- Sets a recent Chrome desktop UA (e.g. `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36`).
- Navigates to `url`, waits for `domcontentloaded` + a fixed delay (2s) for client-side render.
- Returns `page.content()`.
- On exception: log + sleep `backoff_sec * 2 ** attempt` + retry. After `retries` failures, re-raise.

I/O. Tested with a `monkeypatch` that swaps the function for one returning fixture HTML.

### `sounds.download_audio(sound_url: str, out_path: Path, *, timeout_sec: int = 60) -> None`

- `out_path.parent.mkdir(parents=True, exist_ok=True)` first.
- `subprocess.run(build_ytdlp_argv(...), check=True, capture_output=True, timeout=timeout_sec)`.
- On `CalledProcessError` or `TimeoutExpired`, log stderr + re-raise.

I/O. Tested with a `monkeypatch` on `subprocess.run`.

### `sounds.refresh_catalog(db_path, *, limit=10, fetch_fn=None, download_fn=None) -> int`

Orchestrator:
1. Call `fetch_fn` (defaults to `sounds.fetch_html`) with `build_creative_center_url()`.
2. `rows = parse_trending_rows(html)[:limit]`.
3. If `rows` empty, log "no trending sounds found" + return 0.
4. For each row:
   - `audio_path = audio_dir / f"{tiktok_sound_id}.mp3"`
   - If `audio_path` doesn't exist on disk, call `download_fn(sound_url_for(row), audio_path)`.
   - `queue.upsert_sound(db_path, **row, audio_path=str(audio_path))`.
5. Return count of upserted rows.

I/O. Tested with a `monkeypatch` that swaps `fetch_html` and `download_audio` for fakes.

### `queue.count_sounds(db_path) -> int`

`SELECT COUNT(*) FROM sounds`. Returns 0 if empty.

### `queue.get_next_sound(db_path, *, exclude_used_within_days=3) -> Sound | None`

```sql
SELECT * FROM sounds
WHERE audio_path IS NOT NULL
  AND (last_used_at IS NULL OR last_used_at < datetime('now', ?))
ORDER BY trending_score DESC, fetched_at DESC
LIMIT 1
```

Where the `?` is `f'-{N} days'` for the SQL `datetime()` modifier. Returns `None` if no candidate.

### `queue.attach_sound_to_clip(db_path, clip_id: int, sound_id: int) -> None`

Two statements in one transaction:
1. `UPDATE clips SET sound_id=? WHERE id=?`
2. `UPDATE sounds SET last_used_at=CURRENT_TIMESTAMP WHERE id=?`

### `queue.mark_clip_needs_attention(db_path, clip_id: int) -> None`

```sql
UPDATE clips SET status='needs_attention', last_attempted=CURRENT_TIMESTAMP WHERE id=?
```

Called when `get_next_sound` returns `None` and the clip already exists (it was created by the captioner). Distinct from clip creation; the clip's `caption` and `recording_path` are preserved so a human can attach a sound later.

### `queue.get_clips_needing_sound(db_path) -> list[Clip]`

```sql
SELECT * FROM clips
WHERE status='pending'
  AND sound_id IS NULL
  AND recording_path IS NOT NULL
  AND caption IS NOT NULL
ORDER BY created_at ASC
```

These are clips the captioner has finished and the composer hasn't picked up yet — the sound-pick step runs in between.

### `queue.upsert_sound(db_path, tiktok_sound_id, title, artist, views, audio_path) -> int`

```sql
INSERT INTO sounds (tiktok_sound_id, title, artist, trending_score, audio_path)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(tiktok_sound_id) DO UPDATE SET
  title=excluded.title,
  artist=excluded.artist,
  trending_score=excluded.trending_score,
  audio_path=COALESCE(excluded.audio_path, sounds.audio_path),
  fetched_at=CURRENT_TIMESTAMP
RETURNING id;
```

Returns the row id. The `COALESCE` keeps a previously-downloaded `audio_path` if the new row omits it.

### `queue.create_clip_needs_attention(...)`

Variant of `create_clip` that sets `status='needs_attention'`. Same shape otherwise.

## Failure Modes

| Situation | Behavior |
|---|---|
| Creative Center unreachable after 3 retries | `refresh_catalog` returns 0; log warning |
| HTML parse yields 0 rows | `refresh_catalog` returns 0; log warning |
| yt-dlp fails for one sound | Log error, skip that sound, continue with others |
| All sounds fail to download | `refresh_catalog` returns count of upserts with `audio_path` still NULL; selection will skip them (see `get_next_sound` SQL: `audio_path IS NOT NULL`) |
| Catalog empty when pipeline runs | `get_next_sound` returns None; clip created with `status='needs_attention'` |
| Catalog has sounds but all used in last 3 days | `get_next_sound` returns None; same as above |
| Pipeline crash mid-sound-attach | Existing per-clip error handling in `cmd_run` catches and logs; the clip stays `pending` (consistent with recorder/captioner behavior) |

## Testing Strategy

**Pure logic (TDD, no I/O):**
- `parse_trending_rows` with 4-5 fixture HTML strings (happy path, JSON-embedded, DOM-only, empty, malformed).
- `build_ytdlp_argv` asserts the flag list.
- `build_creative_center_url` asserts the URL.

**Queue functions (TDD with real SQLite):**
- `count_sounds` empty + populated.
- `upsert_sound` insert + update on conflict (idempotent).
- `get_next_sound` ordering by `trending_score`; exclusion by `last_used_at`; null `last_used_at` always eligible.
- `attach_sound_to_clip` sets both rows.

**Mocked I/O:**
- `fetch_html` retried failure path (monkeypatch the function).
- `download_audio` timeout / CalledProcessError.
- `refresh_catalog` end-to-end with fakes for `fetch_html` and `download_audio`.

**Manual smoke:** `stumbleupon scrape-sounds` against the real Creative Center; verify `data/sounds/*.mp3` and `sounds` table rows.

## Pipeline Integration (in `cmd_run`)

The current flow is: `scrape → record → caption → compose`. Insert a sound-pick step after caption:

```
1. scrape          (existing)
2. record          (existing)
3. caption         (existing)
4. **pick_sound**  (new) — for each pending clip without sound_id, call get_next_sound; attach or mark needs_attention
5. compose         (existing) — reads clips.sound_id → audio_path
```

Step 4 is a small loop:
```python
pending_for_sound = queue.get_clips_needing_sound(db_path)
for clip in pending_for_sound:
    sound = queue.get_next_sound(db_path)
    if sound is None:
        queue.mark_clip_needs_attention(db_path, clip.id)
    else:
        queue.attach_sound_to_clip(db_path, clip.id, sound.id)
```

`get_clips_needing_sound` is defined above in Phase 2.

## Out of Scope (Deferred)

- **Residential proxy** — re-evaluate if v1 reliability is unacceptable.
- **Auto-refresh** — `cmd_scrape-sounds` is manual; launchd plist is on the roadmap.
- **Sound de-duplication across refreshes beyond score update** — a sound dropping off the top-10 just goes stale; we don't delete it (lets humans re-attach if needed).
- **Burned-in lower-third caption in the composer** — spec mentions this as optional/off in v1.
- **Multi-platform audio** (Instagram Reels, YouTube Shorts) — v1 is TikTok-only.
- **Per-clip `posted_with_sound` analytics** — log line in `cmd_run` is enough for v1.

## Open Questions

None blocking. Implementation can proceed.
