"""Command-line entry point. Subcommands are stubbed in this plan and
filled in by their respective component plans."""

from __future__ import annotations

import argparse
import sys


def cmd_run(args: argparse.Namespace) -> int:
    """One pipeline pass. For now this is just the scrape stage."""
    import asyncio
    from pathlib import Path

    from .config import load_settings
    from .db import init_db
    from .scraper import scrape

    db_path = Path("data/stumbleupon.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    settings = load_settings()
    new_sites = asyncio.run(scrape(db_path=db_path, settings=settings))
    print(f"scrape: {len(new_sites)} new sites queued for review", file=sys.stderr)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    print("review: not yet implemented (scaffold plan)", file=sys.stderr)
    return 0


def cmd_post(args: argparse.Namespace) -> int:
    print("post: not yet implemented (scaffold plan)", file=sys.stderr)
    return 0


def cmd_scrape_sounds(args: argparse.Namespace) -> int:
    print("scrape-sounds: not yet implemented (scaffold plan)", file=sys.stderr)
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
