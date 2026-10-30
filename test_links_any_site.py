"""Links from any booking site: URL parsing, share-link redirects, page-title fallback."""
import asyncio
from datetime import date

from parsing import parse_hotel_url, name_from_title, query_dates, site_name
from urlread import read_link, title_of
from links import comparison_links
import messages as M
from pricing import make_verdict


def test_agoda():
    q = parse_hotel_url("https://www.agoda.com/he-il/dan-tel-aviv-hotel/hotel/tel-aviv-yafo-il.html"
                        "?checkIn=2026-11-12&los=3&adults=2&rooms=1")
    assert q.site == "Agoda" and q.name == "Dan Tel Aviv" and q.city == "Tel Aviv Yafo"
    assert q.checkin == date(2026, 11, 12) and q.nights == 3


def test_agoda_checkout():
    q = parse_hotel_url("https://www.agoda.com/isrotel-royal-beach/hotel/eilat-il.html?checkIn=2026-12-03&checkOut=2026-12-06&adults=3")
    assert q.name == "Isrotel Royal Beach" and q.nights == 3 and q.adults == 3


def test_trip():
    q = parse_hotel_url("https://www.trip.com/hotels/eilat-hotel-detail-1234567/herods-palace/?checkin=2026/12/03&checkout=2026/12/06&adult=2")
    assert q.site == "Trip.com" and q.name == "Herods Palace" and q.city == "Eilat" and q.nights == 3


def test_expedia():
    q = parse_hotel_url("https://www.expedia.com/Tel-Aviv-Hotels-Dan-Tel-Aviv.h531293.Hotel-Information?chkin=2026-11-12&chkout=2026-11-15&rm1=a2")
    assert q.site == "Expedia" and q.name == "Dan Tel Aviv" and q.city == "Tel Aviv" and q.nights == 3 and q.adults == 2


def test_hotels_com():
    q = parse_hotel_url("https://www.hotels.com/ho123456/dan-tel-aviv-tel-aviv-israel/?chkin=2026-11-12&chkout=2026-11-14")
    assert q.site == "Hotels.com" and "Dan Tel Aviv" in q.name and q.nights == 2


def test_booking_still_works():
    q = parse_hotel_url("https://www.booking.com/hotel/il/dan-tel-aviv.he.html?checkin=2026-11-12&checkout=2026-11-15")
    assert q.site == "Booking" and q.name == "Dan Tel Aviv"


def test_query_dates_variants():
    assert query_dates("startDate=2026-11-12&endDate=2026-11-15&adults=4")[2] == 4
    assert query_dates("check_in=20261112&check_out=20261115")[0] == date(2026, 11, 12)
    assert query_dates("checkIn=2026-11-12&los=2")[1] == date(2026, 11, 14)


def test_title_cleanup():
    assert name_from_title("Dan Tel Aviv, Tel Aviv – Updated 2026 Prices") == "Dan Tel Aviv"
    assert name_from_title("Herods Palace Eilat | Trip.com") == "Herods Palace Eilat"
    assert name_from_title("מלון ים סוף | הזמינו עכשיו") == "מלון ים סוף"
    assert name_from_title("") is None


def test_title_of():
    assert title_of('<meta property="og:title" content="Hotel Ruth &amp; Spa"><title>x</title>') == "Hotel Ruth & Spa"
    assert title_of("<title> Plain </title>") == "Plain"


def test_read_link_share_redirect():
    async def fake(url):
        return ("https://www.agoda.com/dan-eilat/hotel/eilat-il.html?checkIn=2026-12-01&los=2", "")
    r = asyncio.run(read_link("https://www.agoda.com/sp/AbC123", fetcher=fake))
    assert r.kind == "ok" and r.query.name == "Dan Eilat" and r.query.nights == 2


def test_read_link_title_fallback():
    async def fake(url):
        return ("https://www.issta.co.il/hotel/12345?checkin=2026-12-01&checkout=2026-12-04",
                "<html><head><title>מלון לאונרדו פלאזה אילת | איסתא</title></head></html>")
    r = asyncio.run(read_link("https://www.issta.co.il/hotel/12345?checkin=2026-12-01&checkout=2026-12-04", fetcher=fake))
    assert r.kind == "ok" and r.query.site == "Issta" and r.query.name == "מלון לאונרדו פלאזה אילת" and r.query.nights == 3


def test_read_link_failures():
    async def boom(url):
        raise RuntimeError("blocked")
    assert asyncio.run(read_link("https://www.example-hotels.com/x", fetcher=boom)).kind == "unreadable"
    assert asyncio.run(read_link("https://www.airbnb.com/rooms/123?check_in=2026-12-01")).kind == "airbnb"


def test_source_site_last_and_labeled():
    q = parse_hotel_url("https://www.agoda.com/dan-eilat/hotel/eilat-il.html?checkIn=2026-12-01&los=2")
    labels = [l for l, _ in comparison_links(q)]
    assert labels[-1] == "Agoda" and "Booking" in labels and "Expedia" in labels
    text, kb = M.result(q, None, None, make_verdict(None, []), 1, False)
    btns = [r[0].text for r in kb.inline_keyboard]
    assert any("Agoda (האתר שלכם)" in b for b in btns) and "מ-Agoda" in text


def test_site_name():
    assert site_name("https://m.booking.com/x") == "Booking" and site_name("https://foo.bar") is None


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("ok  ", name)
            except Exception as e:
                fails += 1; print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)
