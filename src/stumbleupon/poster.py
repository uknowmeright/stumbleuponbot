"""Poster: upload approved clips to Cloudflare R2 and post to TikTok via Buffer.

The pure-logic parts (caption building, Buffer API request construction)
are unit-tested. The I/O parts (R2 upload via boto3, Buffer POST via
httpx) are exercised via manual smoke commands.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import boto3
import httpx

from . import queue
from .config import Settings
from .models import Clip


# Buffer API endpoint
_BUFFER_API_BASE = "https://api.bufferapp.com/1"


def build_caption_text(clip: Clip) -> str:
    """Build the text that Buffer will post.

    Prefers the human-edited caption if set; falls back to the LLM-generated
    caption. Appends hashtags with a `#` prefix.
    """
    text = clip.edited_caption or clip.caption or ""
    hashtags = clip.hashtags or ""
    if hashtags:
        tags = " ".join(f"#{tag.strip()}" for tag in hashtags.split(",") if tag.strip())
        if tags:
            text = f"{text}\n\n{tags}"
    return text


def build_buffer_request(
    r2_url: str,
    caption_text: str,
    settings: Settings,
) -> tuple[str, dict[str, str], dict[str, str]]:
    """Build the (url, headers, body) for Buffer's update creation API.

    Buffer uses ?access_token=<KEY> in the query string. Body is a
    form-encoded dict with `text`, `media[link]`, and `profile_ids[]`.
    Returns (url, headers, body) ready for an httpx POST.
    """
    url = f"{_BUFFER_API_BASE}/updates/create.json?access_token={settings.buffer_api_key}"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    body = {
        "text": caption_text,
        "media[link]": r2_url,
        "now": "true",  # post immediately (no scheduling in v1)
    }
    return url, headers, body


def upload_to_r2(
    mp4_path: Path,
    settings: Settings,
    clip_id: int,
) -> str:
    """Upload an mp4 to Cloudflare R2 and return the public URL.

    Uses boto3 with the S3 client (R2 is S3-compatible). The object key
    is `<clip_id>.mp4`. The returned URL is `<public_url_base>/<key>`.

    `R2_ENDPOINT_URL` and `R2_PUBLIC_URL_BASE` are distinct settings:
    the endpoint is the S3-compatible API host (e.g.
    `https://<account>.r2.cloudflarestorage.com`); the public URL base
    is the CDN host where files are served (e.g.
    `https://media.example.com`). Mixing them up was a real v1 bug.
    """
    mp4_path = Path(mp4_path)
    if not settings.r2_endpoint_url:
        raise RuntimeError(
            "R2_ENDPOINT_URL is not set. Add it to your .env — see .env.example "
            "for the value to copy from the Cloudflare dashboard."
        )
    if not settings.r2_public_url_base:
        raise RuntimeError(
            "R2_PUBLIC_URL_BASE is not set. Add it to your .env — this is the "
            "public CDN URL where uploaded clips are served."
        )
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
    )
    key = f"{clip_id}.mp4"
    s3.upload_file(str(mp4_path), settings.r2_bucket_name, key)
    return f"{settings.r2_public_url_base.rstrip('/')}/{key}"


async def post_to_buffer(
    r2_url: str,
    caption_text: str,
    settings: Settings,
) -> str:
    """Post the clip to TikTok via Buffer. Returns the external URL on success.

    Raises httpx.HTTPStatusError on a 4xx/5xx response.
    """
    url, headers, body = build_buffer_request(r2_url, caption_text, settings)
    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=body, headers=headers)
        response.raise_for_status()
        result = response.json()
    # Buffer returns: {"updates": [{"service_update": "<url>", ...}]}
    updates = result.get("updates", [])
    if not updates:
        raise RuntimeError(f"Buffer returned no updates: {result}")
    return updates[0].get("service_update", "")


async def post_pending_clips(
    db_path: Path,
    settings: Settings,
    finals_dir: Path,
    limit: int = 3,
) -> tuple[list[dict], list[dict]]:
    """Post up to `limit` approved clips.

    For each approved clip:
      1. Upload the mp4 to R2 (idempotent: if the file is already there, this is fast)
      2. Post to Buffer with the R2 URL + caption
      3. On success: mark clip 'posted' and store the external URL
      4. On failure: mark 'failed' in postings, leave clip as 'approved' for retry

    Returns (posted, failed) where:
      posted: [{"clip_id", "external_url"}, ...] for clips that were successfully
              posted
      failed: [{"clip_id", "error"}, ...] for clips whose R2 upload or Buffer
              post attempt failed
    Per-clip failures are caught: the batch continues.
    """
    finals_dir = Path(finals_dir)
    rows = queue.get_approved_clips(db_path, limit=limit)

    posted: list[dict] = []
    failed: list[dict] = []
    for row in rows:
        clip_id = row.id
        final_path = finals_dir / f"{clip_id}.mp4"

        try:
            # Upload to R2 (idempotent: if the file is already there, this is fast)
            r2_url = upload_to_r2(final_path, settings=settings, clip_id=clip_id)
            queue.set_clip_r2_url(db_path, clip_id, r2_url=r2_url)
        except Exception as exc:
            error_msg = f"r2 upload: {type(exc).__name__}: {exc}"
            queue.mark_posting_failed(
                db_path, clip_id,
                error=error_msg,
            )
            failed.append({"clip_id": clip_id, "error": error_msg})
            print(f"poster: clip {clip_id} R2 upload failed: {exc!r}", file=sys.stderr, flush=True)
            continue

        try:
            caption_text = build_caption_text(row)
            external_url = await post_to_buffer(
                r2_url=r2_url, caption_text=caption_text, settings=settings,
            )
        except Exception as exc:
            error_msg = f"buffer post: {type(exc).__name__}: {exc}"
            queue.mark_posting_failed(
                db_path, clip_id,
                error=error_msg,
            )
            failed.append({"clip_id": clip_id, "error": error_msg})
            print(f"poster: clip {clip_id} buffer post failed: {exc!r}", file=sys.stderr, flush=True)
            continue

        queue.mark_posted(db_path, clip_id, external_url=external_url)
        posted.append({"clip_id": clip_id, "external_url": external_url})
        print(f"poster: clip {clip_id} -> {external_url}", file=sys.stderr, flush=True)

    return posted, failed
