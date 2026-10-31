"""Check the hotel-id lookups live, from the server (needs internet).

    python3 tools/check_resolve.py "Dan Tel Aviv" "Tel Aviv" 2026-12-03 2026-12-06

Prints, per site, the HTTP status, the start of the raw answer, the resolved ids and the
final button link. Safe to paste in a GitHub issue: it prints no keys or tokens
(affiliate ids are masked).
"""
import asyncio
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import links  # noqa: E402
import resolve  # noqa: E402
from parsing import HotelQuery  # noqa: E402


def mask(url: str) -> str:
    return re.sub(r"(Allianceid|SID|cid|aid|marker)=[^&]+", r"\1=***", url)


async def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "Dan Tel Aviv"
    city = sys.argv[2] if len(sys.argv) > 2 else None
    ci = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else None
    co = date.fromisoformat(sys.argv[4]) if len(sys.argv) > 4 else None
    q = HotelQuery(name=name, city=city, checkin=ci, checkout=co)
    print(f"query: {resolve.search_term(q)!r}\n")
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": resolve.UA, "Accept": "application/json"},
                                 follow_redirects=True) as client:
        for site in resolve.SITES:
            print(f"== {site}")
            try:
                data = await resolve.FETCH[site](client, resolve.search_term(q))
                print("raw:", json.dumps(data, ensure_ascii=False)[:1500])
                print("resolved:", resolve.PARSE[site](data, q))
            except Exception as e:
                print("error:", repr(e)[:500])
            print()
    await resolve.enrich(q)
    print("site_ids:", q.site_ids)
    for label, url in links.comparison_links(q):
        print(f"{label}: {mask(url)}")


asyncio.run(main())
