"""Scraper for stumbleupon.cc's public Supabase REST API.

The site is a JavaScript single-page application that loads one site at a
time into an iframe. The actual data source is a public Supabase endpoint
that returns live sites as JSON.

The pipeline is: fetch → filter → dedup → insert. All pure-logic
functions are unit-tested; the HTTP fetch is exercised with mocked
transport and a manual smoke command.
"""

from __future__ import annotations

import httpx

from .db import get_connection
from .models import Site


# Fields we ask Supabase to return. Avoids pulling down columns we don't use.
_SUPABASE_SELECT = "id,url,title,description,category,og_image,like_count,dislike_count,embeddable"


async def fetch_sites_from_api(api_url: str, api_key: str) -> list[dict]:
    """Fetch all live sites from the public Supabase REST endpoint.

    The Supabase anon key is intentionally public (designed to be embedded
    in client-side code). We pass it in the `apikey` and `Authorization`
    headers per Supabase's REST conventions.
    """
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
    }
    params = {
        "select": _SUPABASE_SELECT,
        "status": "eq.live",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(api_url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()


def filter_sites(sites: list[dict], ad_block_keywords: list[str]) -> list[dict]:
    """Drop sites whose url, title, or description contains any blocked keyword.

    Case-insensitive. Null/missing title or description are treated as empty
    (they can't match, so the site passes through).
    """
    if not ad_block_keywords:
        return list(sites)

    keywords_lower = [k.lower() for k in ad_block_keywords]
    out: list[dict] = []
    for site in sites:
        url = (site.get("url") or "").lower()
        title = (site.get("title") or "").lower()
        description = (site.get("description") or "").lower()
        haystack = f"{url} {title} {description}"
        if any(kw in haystack for kw in keywords_lower):
            continue
        out.append(site)
    return out


def dedup_against_db(sites: list[dict], db_path) -> list[dict]:
    """Drop sites whose URL is already in the local `sites` table.

    The DB layer enforces URL uniqueness (schema constraint), so this
    is a query, not a list-comprehension against an in-memory cache.
    """
    if not sites:
        return []
    candidate_urls = [s["url"] for s in sites]
    placeholders = ",".join("?" * len(candidate_urls))
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT url FROM sites WHERE url IN ({placeholders})",
            candidate_urls,
        ).fetchall()
    seen = {row["url"] for row in rows}
    return [s for s in sites if s["url"] not in seen]


def insert_new_sites(sites: list[dict], db_path) -> list[Site]:
    """Insert new sites into the `sites` table. Returns the inserted Site rows.

    Uses `INSERT OR IGNORE` so duplicate URLs in the candidate list are
    silently dropped (the schema's UNIQUE constraint on `url` enforces this).
    """
    if not sites:
        return []
    with get_connection(db_path) as conn:
        for s in sites:
            conn.execute(
                "INSERT OR IGNORE INTO sites (url, title, description, source, status) "
                "VALUES (?, ?, ?, 'stumbleupon.cc', 'fresh')",
                (s["url"], s.get("title"), s.get("description")),
            )
        # Re-read to get the actual inserted rows (with IDs)
        urls = [s["url"] for s in sites]
        placeholders = ",".join("?" * len(urls))
        rows = conn.execute(
            f"SELECT * FROM sites WHERE url IN ({placeholders})",
            urls,
        ).fetchall()

    return [
        Site(
            id=row["id"],
            url=row["url"],
            title=row["title"],
            description=row["description"],
            source=row["source"] or "stumbleupon.cc",
            discovered_at=row["discovered_at"],
            status=row["status"],
        )
        for row in rows
    ]


async def scrape(db_path, settings) -> list[Site]:
    """Top-level: fetch from API, filter, dedup, insert. Returns new Site rows.

    On any failure during fetch (network down, 5xx, bad JSON), returns an
    empty list and logs. The pipeline doesn't crash; we just don't get new
    sites today.
    """
    try:
        raw_sites = await fetch_sites_from_api(
            api_url=settings.stumbleupon_api_url,
            api_key=settings.stumbleupon_api_key,
        )
    except Exception as exc:  # network, HTTP, JSON decode, anything
        print(f"scraper: fetch failed: {exc!r}", flush=True)
        return []

    filtered = filter_sites(raw_sites, ad_block_keywords=settings.ad_block_keywords)
    fresh = dedup_against_db(filtered, db_path)
    return insert_new_sites(fresh, db_path)
