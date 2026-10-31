"""Build comparison links for a HotelQuery, wrapped with affiliate tracking when configured.

NOTE: the deep-link URL formats below are best-known public formats. Verify each one in
a browser once (and against the affiliate dashboards) before launch - sites change them.
"""
from __future__ import annotations

from urllib.parse import urlencode, quote

import config
from parsing import HotelQuery


def _search_term(q: HotelQuery) -> str:
    # Adding the city avoids landing on a same-name hotel elsewhere.
    if q.city and q.city.lower() not in q.name.lower():
        return f"{q.name} {q.city}"
    return q.name


def _ids(q: HotelQuery, site: str) -> dict:
    """Ids resolved by resolve.enrich(); {} when unknown (-> search link)."""
    return ((q.site_ids or {}).get(site) or {}) if q.site_ids else {}


def booking_link(q: HotelQuery) -> str:
    params = {"ss": _search_term(q), "group_adults": q.adults, "no_rooms": q.rooms,
              "group_children": q.children}
    if q.has_dates:
        params.update(checkin=q.checkin.isoformat(), checkout=q.checkout.isoformat())
    if config.BOOKING_AID:
        params["aid"] = config.BOOKING_AID
    return "https://www.booking.com/searchresults.html?" + urlencode(params)


def agoda_link(q: HotelQuery) -> str:
    hid = _ids(q, "agoda").get("hotel_id")
    if hid:  # Agoda's partner deep link opens the hotel page itself
        params = {"hid": hid, "cid": config.AGODA_CID or "-1", "NumberofAdults": q.adults,
                  "NumberofChildren": q.children, "Rooms": q.rooms, "currency": "ILS"}
        if q.has_dates:
            params.update(checkIn=q.checkin.isoformat(), checkOut=q.checkout.isoformat())
        return "https://www.agoda.com/partners/partnersearch.aspx?" + urlencode(params)
    params = {"textToSearch": _search_term(q), "adults": q.adults, "rooms": q.rooms,
              "children": q.children}
    if q.has_dates:
        params.update(checkIn=q.checkin.isoformat(), los=q.nights)
    if config.AGODA_CID:
        params["cid"] = config.AGODA_CID
    return "https://www.agoda.com/search?" + urlencode(params)


TRIP_LABEL = "Trip.com"


def is_trip(site: str | None) -> bool:
    return "trip.com" in (site or "").lower() or (site or "").lower() == "trip"


def trip_link(q: HotelQuery, sub: str = "telegram") -> str:
    """Trip.com link: the hotel's own page when its id was resolved, otherwise a search list.
    With the direct affiliate configured it carries Allianceid + SID and trip_sub1=<source>
    (telegram / site / ig ...)."""
    ids = _ids(q, "trip")
    if ids.get("hotel_id"):
        params = {"hotelId": ids["hotel_id"]}
        if ids.get("city_id"):
            params["cityId"] = ids["city_id"]
        params.update(adult=q.adults, children=q.children, crn=q.rooms, curr="ILS", barCurr="ILS")
        base = "https://www.trip.com/hotels/detail/?"
    else:
        term = _search_term(q)
        params = {"searchWord": term, "destName": term, "adult": q.adults, "crn": q.rooms,
                  "children": q.children, "barCurr": "ILS"}
        base = "https://www.trip.com/hotels/list?"
    if q.has_dates:
        params.update(checkin=q.checkin.isoformat(), checkout=q.checkout.isoformat())
    if config.TRIP_ALLIANCE_ID and config.TRIP_SID:
        params.update(Allianceid=config.TRIP_ALLIANCE_ID, SID=config.TRIP_SID, trip_sub1=sub)
    return base + urlencode(params)


def _trip(q: HotelQuery, sub: str) -> str:
    # direct Trip.com affiliate wins over the Travelpayouts wrapper
    if config.TRIP_ALLIANCE_ID and config.TRIP_SID:
        return trip_link(q, sub)
    return wrap_affiliate(trip_link(q, sub), config.TP_PROGRAM_TRIP)


def expedia_link(q: HotelQuery) -> str:
    hid = _ids(q, "expedia").get("hotel_id")
    if hid:  # hotel page: /h<id>.Hotel-Information
        params = {"rm1": f"a{q.adults}"}
        if q.has_dates:
            params.update(chkin=q.checkin.isoformat(), chkout=q.checkout.isoformat())
        return f"https://www.expedia.com/h{hid}.Hotel-Information?" + urlencode(params)
    params = {"destination": _search_term(q), "adults": q.adults, "rooms": q.rooms}
    if q.has_dates:
        params.update(startDate=q.checkin.isoformat(), endDate=q.checkout.isoformat())
    return "https://www.expedia.com/Hotel-Search?" + urlencode(params)


def wrap_affiliate(url: str, program_id: str | None) -> str:
    """Wrap a link with Travelpayouts tracking if marker + program id are configured.

    The exact redirect format must be confirmed from the Travelpayouts link generator
    once the account is open (TP_REDIRECT_TEMPLATE in .env).
    """
    if not (config.TP_MARKER and program_id and config.TP_REDIRECT_TEMPLATE):
        return url
    return config.TP_REDIRECT_TEMPLATE.format(
        marker=config.TP_MARKER, program=program_id, url=quote(url, safe=""))


def link_for_site(site: str, q: HotelQuery, sub: str = "telegram") -> str | None:
    """Fallback link when a price offer has no deep link of its own."""
    s = site.lower()
    if "agoda" in s:
        return wrap_affiliate(agoda_link(q), config.TP_PROGRAM_AGODA)
    if is_trip(site):
        return _trip(q, sub)
    if "booking" in s:
        return wrap_affiliate(booking_link(q), config.TP_PROGRAM_BOOKING)
    if "expedia" in s or "hotels.com" in s:
        return wrap_affiliate(expedia_link(q), config.TP_PROGRAM_EXPEDIA)
    return None


def comparison_links(q: HotelQuery, sub: str = "telegram", include_trip: bool = True) -> list[tuple[str, str]]:
    """Return [(label, url)] in display order. The source site goes last.

    include_trip=False drops Trip.com: its contract forbids showing it next to
    competitors' prices, so it is left out of any message that shows prices."""
    items = [
        ("Agoda", wrap_affiliate(agoda_link(q), config.TP_PROGRAM_AGODA)),
        (TRIP_LABEL, _trip(q, sub)),
        ("Expedia", wrap_affiliate(expedia_link(q), config.TP_PROGRAM_EXPEDIA)),
        ("Booking", wrap_affiliate(booking_link(q), config.TP_PROGRAM_BOOKING)),
    ]
    if not include_trip:
        items = [it for it in items if it[0] != TRIP_LABEL]
    src = (q.site or "").lower().replace("hotels.com", "expedia")
    if src:
        items.sort(key=lambda it: it[0].lower() in src or src in it[0].lower())
    return items
