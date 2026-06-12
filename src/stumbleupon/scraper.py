"""Scraper for stumbleupon.cc's public Supabase REST API.

The site is a JS-rendered single-site iframe. The actual data source is
a public Supabase endpoint that returns live sites as JSON.

The pipeline is: fetch → filter → dedup → insert. All pure-logic
functions are unit-tested; the HTTP fetch is exercised with mocked
transport and a manual smoke command.
"""

from __future__ import annotations

import httpx


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
