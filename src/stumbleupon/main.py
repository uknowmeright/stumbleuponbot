"""Command-line entry point. Subcommands are stubbed in this plan and
filled in by their respective component plans."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import queue
from .models import Clip, Sound


def attach_sounds_to_pending_clips(
    db_path: Path,
    pending_for_sound: list[Clip],
    sounds_batch: list[Sound],
) -> int:
    """Attach a sound to each pending clip, or mark `needs_attention` if none left.

    Pairwise by index. Per-clip failures are caught so one bad row
    doesn't sink the batch — the rest still get processed. Returns
    the count of clips that received a sound.
    """
    sounds_attached = 0
    for i, clip in enumerate(pending_for_sound):
        try:
            if i < len(sounds_batch):
                sound = sounds_batch[i]
                queue.attach_sound_to_clip(db_path, clip.id, sound.id)
                sounds_attached += 1
            else:
                queue.mark_clip_needs_attention(db_path, clip.id)
                print(
                    f"sounds: clip {clip.id} marked needs_attention (no sound available)",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(
                f"sounds: clip {clip.id} pick failed: {type(exc).__name__}: {exc}; "
                f"continuing with next clip",
                file=sys.stderr,
                flush=True,
            )
    return sounds_attached


def cmd_run(args: argparse.Namespace) -> int:
    """One pipeline pass: scrape, record, caption, then compose."""
    import asyncio

    from .captioner import caption_pending_recordings
    from .composer import compose_pending_clips
    from .config import load_settings
    from .db import init_db
    from .recorder import record_pending_sites
    from .scraper import scrape

    db_path = Path("data/stumbleupon.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    settings = load_settings()
    new_sites = asyncio.run(scrape(db_path=db_path, settings=settings))
    print(f"scrape: {len(new_sites)} new sites queued for review", file=sys.stderr)

    recordings_dir = db_path.parent / "recordings"
    recorded = record_pending_sites(
        db_path=db_path,
        recordings_dir=recordings_dir,
        duration_sec=30.0,
        limit=3,
    )
    print(f"recorder: {len(recorded)} sites recorded to {recordings_dir}", file=sys.stderr)

    clips = asyncio.run(caption_pending_recordings(
        db_path=db_path,
        settings=settings,
        recordings_dir=recordings_dir,
        limit=5,
    ))
    print(f"captioner: {len(clips)} clips queued for review", file=sys.stderr)

    # Step 4: pick a trending sound for each pending clip.
    # Pick the batch up front (not one-at-a-time) so we don't fall
    # victim to the `last_used_at < now - 3 days` filter stamping
    # earlier picks as recently-used and starving later clips in the
    # same run.
    pending_for_sound = queue.get_clips_needing_sound(db_path, limit=5)
    sounds_batch = queue.get_next_sounds(db_path, limit=len(pending_for_sound))
    sounds_attached = attach_sounds_to_pending_clips(
        db_path, pending_for_sound, sounds_batch,
    )
    print(
        f"sounds: {sounds_attached} sounds attached, "
        f"{len(pending_for_sound) - sounds_attached} marked needs_attention",
        file=sys.stderr,
    )


    finals_dir = db_path.parent / "final"
    finals_dir.mkdir(parents=True, exist_ok=True)
    composed = compose_pending_clips(
        db_path=db_path,
        finals_dir=finals_dir,
        limit=5,
        duration_sec=30.0,
    )
    print(f"composer: {len(composed)} clips composed to {finals_dir}", file=sys.stderr)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """Interactive review of pending clips (opens mp4s in QuickTime, prompts for actions)."""
    from pathlib import Path

    from .db import init_db
    from .reviewer import review_pending_clips

    db_path = Path("data/stumbleupon.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    results = review_pending_clips(db_path=db_path, limit=10)
    print(f"review: {len(results)} clips acted on", file=sys.stderr)
    return 0


def cmd_post(args: argparse.Namespace) -> int:
    """Post approved clips (uploads to R2, then posts via Buffer)."""
    import asyncio
    from pathlib import Path

    from .config import load_settings
    from .db import init_db
    from .poster import post_pending_clips

    db_path = Path("data/stumbleupon.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    settings = load_settings()
    finals_dir = db_path.parent / "final"
    finals_dir.mkdir(parents=True, exist_ok=True)

    posted = asyncio.run(post_pending_clips(
        db_path=db_path, settings=settings, finals_dir=finals_dir, limit=5,
    ))
    print(f"poster: {len(posted)} clips posted", file=sys.stderr)
    return 0


def cmd_scrape_sounds(args: argparse.Namespace) -> int:
    """Refresh the trending TikTok sounds catalog."""
    from pathlib import Path

    from .config import load_settings
    from .db import init_db
    from .sounds import refresh_catalog

    db_path = Path("data/stumbleupon.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    settings = load_settings()  # currently unused; reserved for future proxy config
    audio_dir = db_path.parent / "sounds"
    audio_dir.mkdir(parents=True, exist_ok=True)

    count = refresh_catalog(db_path=db_path, audio_dir=audio_dir, limit=10)
    print(f"sounds: {count} sounds refreshed in {audio_dir}", file=sys.stderr)
    return 0


def cmd_show_config(args: argparse.Namespace) -> int:
    from .config import load_settings

    settings = load_settings()
    for field_name in settings.__dataclass_fields__:
        value = getattr(settings, field_name)
        if any(token in field_name for token in ("key", "secret", "password")):
            value = "***" if value else "(unset)"
        print(f"{field_name} = {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stumbleupon")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="one pipeline pass (launchd-triggered)").set_defaults(func=cmd_run)
    sub.add_parser("review", help="interactive CLI to approve/reject pending clips").set_defaults(func=cmd_review)
    sub.add_parser("post", help="post approved clips whose scheduled time has arrived").set_defaults(func=cmd_post)
    sub.add_parser("scrape-sounds", help="refresh trending TikTok sounds catalog").set_defaults(func=cmd_scrape_sounds)
    sub.add_parser("show-config", help="print loaded settings (redacted)").set_defaults(func=cmd_show_config)
    return parser


def cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(cli())
