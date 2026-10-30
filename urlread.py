"""Turn any hotel link into a HotelQuery: parse the URL, follow share/short links, then read the page title."""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from parsing import HotelQuery, is_airbnb, name_from_title, parse_hotel_url, query_dates, site_name

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
META_RE = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:title|twitter:title)["\'][^>]*content=["\']([^"\']+)', re.I)
META_RE2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](?:og:title|twitter:title)', re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


@dataclass
class LinkResult:
    query: HotelQuery | None
    kind: str          # ok | airbnb | unreadable
    site: str | None = None


async def fetch(url: str, max_bytes: int = 400_000) -> tuple[str, str]:
    """Follow redirects; return (final_url, start of the HTML). Raises on network errors."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=10, headers={
            "User-Agent": UA, "Accept-Language": "he-IL,he;q=0.9,en;q=0.8"}) as c:
        async with c.stream("GET", url) as r:
            chunks, size = [], 0
            async for chunk in r.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= max_bytes:
                    break
            return str(r.url), b"".join(chunks).decode(r.encoding or "utf-8", errors="ignore")


def title_of(page: str) -> str | None:
    for rx in (META_RE, META_RE2, TITLE_RE):
        m = rx.search(page or "")
        if m:
            return html.unescape(m.group(1)).strip()
    return None


async def read_link(url: str, fetcher=fetch) -> LinkResult:
    if is_airbnb(url):
        return LinkResult(None, "airbnb", "Airbnb")

    q = parse_hotel_url(url)
    if q:
        return LinkResult(q, "ok", q.site)

    # Share links / short links / unknown site: open it once.
    try:
        final, page = await fetcher(url)
    except Exception as e:
        log.info("link fetch failed %s: %s", urlparse(url).netloc, e)
        return LinkResult(None, "unreadable", site_name(url))

    if is_airbnb(final):
        return LinkResult(None, "airbnb", "Airbnb")
    q = parse_hotel_url(final)
    if q:
        return LinkResult(q, "ok", q.site)

    name = name_from_title(title_of(page) or "")
    if not name:
        return LinkResult(None, "unreadable", site_name(final) or site_name(url))
    ci, co, adults = query_dates(urlparse(final).query)
    q = HotelQuery(name=name, checkin=ci, checkout=co, adults=adults,
                   site=site_name(final) or site_name(url), source_url=final)
    return LinkResult(q, "ok", q.site)
