"""Resolve a hotel's own id on each comparison site, so buttons open that hotel's page
instead of a search list.

Best effort by design: every lookup has a short timeout, a name check guards against a
same-name hotel elsewhere, and any failure simply leaves the id out, so links.py falls
back to the search link. Results (including misses) are cached in bot.db.

The lookup endpoints are the sites' own public autocomplete calls (what their search box
uses). They are undocumented and can change: run `python3 tools/check_resolve.py "<hotel>"`
on the server to see the raw answers and what was resolved.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import quote

import httpx

from parsing import HotelQuery

log = logging.getLogger(__name__)

SITES = ("trip", "agoda", "expedia")
TIMEOUT = 3.0          # per request
TOTAL_TIMEOUT = 4.0    # all sites together, so the reply is never held up for long
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# ---------- name matching ----------

_STOP = {"hotel", "hotels", "the", "by", "and", "resort", "spa", "&", "a", "an", "at", "suites", "boutique"}


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower()
    return re.sub(r"[^\w ]+|_", " ", s)  # keeps Hebrew letters too


def _tokens(s: str) -> set[str]:
    return {t for t in _norm(s).split() if t not in _STOP}


def same_hotel(wanted: str, candidate: str, city: str | None = None, context: str = "") -> bool:
    """True when candidate is very likely the hotel we asked for.

    Every word of the hotel name must appear in the candidate's name, and when we know the
    city, the candidate record (name, city, address...) must mention it - so "Dan Tel Aviv"
    never matches "Dan Eilat"."""
    city_t = _tokens(city or "")
    want, cand = _tokens(wanted) - city_t, _tokens(candidate)
    if not want or not cand:
        return False
    if not (want <= cand or SequenceMatcher(None, " ".join(sorted(want)),
                                            " ".join(sorted(cand - city_t))).ratio() >= 0.85):
        return False
    if city_t:
        return bool(city_t & (cand | _tokens(context)))
    return True


def _strings(d) -> str:
    """All text in a result record (names, city, address), for the city check."""
    if isinstance(d, str):
        return d
    if isinstance(d, dict):
        return " ".join(_strings(v) for v in d.values())
    if isinstance(d, list):
        return " ".join(_strings(v) for v in d)
    return ""


def search_term(q: HotelQuery) -> str:
    if q.city and q.city.lower() not in q.name.lower():
        return f"{q.name} {q.city}"
    return q.name


# ---------- ids already in the link the user sent ----------

_TRIP_URL = re.compile(r"trip\.com/hotels/(?:[^/?#]*-)?(?:hotel-)?detail-(\d+)|trip\.com/hotels/detail/?\?(?:[^#]*&)?hotelId=(\d+)", re.I)
_TRIP_CITY = re.compile(r"[?&]city(?:Id)?=(\d+)", re.I)
_EXPEDIA_URL = re.compile(r"(?:expedia|hotels)\.[a-z.]+/[^?#]*?\.h(\d+)\.Hotel-Information", re.I)
_AGODA_HID = re.compile(r"agoda\.com/[^#]*[?&]hid=(\d+)", re.I)


def ids_from_url(url: str | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not url:
        return out
    m = _TRIP_URL.search(url)
    if m:
        c = _TRIP_CITY.search(url)
        out["trip"] = {"hotel_id": m.group(1) or m.group(2), "city_id": c.group(1) if c else None}
    m = _EXPEDIA_URL.search(url)
    if m:
        out["expedia"] = {"hotel_id": m.group(1)}
    m = _AGODA_HID.search(url)
    if m:
        out["agoda"] = {"hotel_id": m.group(1)}
    return out


# ---------- per-site lookups: request + tolerant parser ----------

def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", 0, "0"):
            return v
    return None


def _name_of(d: dict) -> str:
    for k in ("resultWord", "word", "name", "Name", "hotelName", "displayName", "regionName"):
        v = d.get(k)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, str) and vv:
                    return vv
    names = d.get("regionNames") or d.get("DisplayNames") or {}
    if isinstance(names, dict):
        for k in ("primaryDisplayName", "shortName", "fullName", "Name", "displayName"):
            if isinstance(names.get(k), str) and names[k]:
                return names[k]
    return ""


def parse_trip(data: dict, q: HotelQuery) -> dict | None:
    for r in (data or {}).get("keyWordSearchResults") or []:
        if "hotel" not in str(r.get("type", "")).lower() and str(r.get("type", "")).upper() not in ("H", "HTL"):
            continue
        hid = _first(r, "code", "id", "hotelId")
        city = r.get("city") or {}
        cid = _first(city, "geoCode", "id", "code") if isinstance(city, dict) else None
        if hid and same_hotel(q.name, _name_of(r), q.city, _strings(r)):
            return {"hotel_id": str(hid), "city_id": str(cid) if cid else None}
    return None


def parse_agoda(data: dict, q: HotelQuery) -> dict | None:
    for r in (data or {}).get("ViewModelList") or []:
        # hotels are "property" results; cities/areas/landmarks are skipped
        kind = str(_first(r, "SearchTypeName", "ResultType", "Type") or "").lower()
        is_hotel = "propert" in kind or "hotel" in kind or r.get("SearchType") in (4, "4") or r.get("HotelId")
        hid = _first(r, "HotelId", "ObjectId", "Id")
        if is_hotel and hid and same_hotel(q.name, _name_of(r), q.city, _strings(r)):
            return {"hotel_id": str(hid)}
    return None


def parse_expedia(data: dict, q: HotelQuery) -> dict | None:
    for r in (data or {}).get("sr") or []:
        if str(r.get("type", "")).upper() != "HOTEL":
            continue
        hid = _first(r, "hotelId", "id") or (r.get("hotelAddress") or {}).get("hotelId")
        if hid and same_hotel(q.name, _name_of(r), q.city, _strings(r)):
            return {"hotel_id": str(hid)}
    return None


async def _trip_fetch(client: httpx.AsyncClient, term: str) -> dict:
    body = {"code": 0, "codeType": "", "keyWord": term, "searchType": "D", "scenicCode": 0, "cityCodeOfUser": 0,
            "searchConditions": [{"type": "D_PROVINCE", "value": "T"}, {"type": "SupportNormalSearch", "value": "T"}],
            "head": {"platform": "PC", "bu": "ibu", "group": "TRIP", "locale": "en-XX", "currency": "ILS"}}
    r = await client.post("https://www.trip.com/htls/getKeyWordSearch", json=body)
    r.raise_for_status()
    return r.json()


async def _agoda_fetch(client: httpx.AsyncClient, term: str) -> dict:
    r = await client.get("https://www.agoda.com/api/cronos/search/GetUnifiedSuggestResult/3/1/1/0/en-us/",
                         params={"searchText": term, "origin": "IL", "cid": "-1", "pageTypeId": "1"})
    r.raise_for_status()
    return r.json()


async def _expedia_fetch(client: httpx.AsyncClient, term: str) -> dict:
    r = await client.get(f"https://www.expedia.com/api/v4/typeahead/{quote(term, safe='')}",
                         params={"client": "SearchForm", "lob": "HOTELS", "regiontype": "2047",
                                 "format": "json", "maxresults": "8"})
    r.raise_for_status()
    return r.json()


FETCH = {"trip": _trip_fetch, "agoda": _agoda_fetch, "expedia": _expedia_fetch}
PARSE = {"trip": parse_trip, "agoda": parse_agoda, "expedia": parse_expedia}


async def lookup(site: str, q: HotelQuery, client: httpx.AsyncClient) -> dict | None:
    """The ids ({} when the site answered but has no matching hotel), or None on an error."""
    try:
        return PARSE[site](await FETCH[site](client, search_term(q)), q) or {}
    except Exception as e:  # network, block page, JSON change: fall back silently
        log.info("resolve %s failed for %r: %s", site, q.name, e)
        return None


# ---------- entry point ----------

def cache_key(q: HotelQuery) -> str:
    return " ".join(sorted(_tokens(search_term(q))))


async def enrich(q: HotelQuery, store=None, sites=SITES) -> HotelQuery:
    """Fill q.site_ids with what we can resolve. Never raises."""
    ids = dict(q.site_ids or {})
    for site, val in ids_from_url(q.source_url).items():
        ids.setdefault(site, val)
    key = cache_key(q)
    todo = []
    for site in sites:
        if site in ids:
            continue
        hit = store.get_site_id(site, key) if store and key else None
        if hit is not None:            # cached (a miss is cached as {})
            if hit:
                ids[site] = hit
            continue
        todo.append(site)
    if todo and key:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": UA, "Accept": "application/json"},
                                         follow_redirects=True) as client:
                found = await asyncio.wait_for(
                    asyncio.gather(*(lookup(s, q, client) for s in todo)), TOTAL_TIMEOUT)
        except Exception as e:
            log.info("resolve timed out for %r: %s", q.name, e)
            found = [None] * len(todo)  # not cached: a timeout says nothing about the hotel
        else:
            for site, res in zip(todo, found):
                if store and res is not None:  # errors are not cached, so the next query retries
                    store.set_site_id(site, key, res)
        for site, res in zip(todo, found):
            if res:
                ids[site] = res
    q.site_ids = ids
    return q
