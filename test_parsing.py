from datetime import date

from parsing import parse_booking_url, parse_free_text, extract_url, is_booking_share_link
from links import comparison_links

TODAY = date(2026, 10, 28)


def test_booking_iso():
    q = parse_booking_url("https://www.booking.com/hotel/il/dan-tel-aviv.he.html?aid=1&checkin=2026-11-12&checkout=2026-11-15&group_adults=3&no_rooms=1&group_children=0")
    assert q.name == "Dan Tel Aviv" and q.country == "il"
    assert q.checkin == date(2026, 11, 12) and q.nights == 3 and q.adults == 3


def test_booking_old_params():
    q = parse_booking_url("https://www.booking.com/hotel/gr/minos-beach.html?checkin_year=2027&checkin_month=6&checkin_monthday=1&checkout_year=2027&checkout_month=6&checkout_monthday=5")
    assert q.checkin == date(2027, 6, 1) and q.nights == 4 and q.adults == 2


def test_booking_no_dates():
    q = parse_booking_url("https://www.booking.com/hotel/ge/rooms-tbilisi.en-gb.html")
    assert q.name == "Rooms Tbilisi" and not q.has_dates


def test_not_hotel():
    assert parse_booking_url("https://www.booking.com/searchresults.html?ss=x") is None
    assert parse_booking_url("https://example.com/hotel/il/x.html") is None


def test_share():
    assert is_booking_share_link("https://www.booking.com/Share-AbC123")
    assert not is_booking_share_link("https://www.booking.com/hotel/il/x.html")


def test_extract():
    assert extract_url("תראה את זה https://www.booking.com/Share-x1 מה דעתך") == "https://www.booking.com/Share-x1"


def test_free_text_he():
    q = parse_free_text("דן תל אביב 12/11 - 15/11 2 מבוגרים", today=TODAY)
    assert q.name == "דן תל אביב" and q.checkin == date(2026, 11, 12) and q.nights == 3


def test_free_text_year_wrap():
    q = parse_free_text("Hilton Eilat 30.12-2.1", today=TODAY)
    assert q.checkin == date(2026, 12, 30) and q.checkout == date(2027, 1, 2) and q.adults == 2


def test_free_text_past_date_next_year():
    q = parse_free_text("מלון 3/2 - 5/2", today=TODAY)
    assert q.checkin.year == 2027


def test_free_text_none():
    assert parse_free_text("שלום מה נשמע", today=TODAY) is None


def test_links():
    q = parse_free_text("Hilton Eilat 3/12 - 6/12", today=TODAY)
    links = dict(comparison_links(q))
    assert "checkIn=2026-12-03" in links["Agoda"] and "los=3" in links["Agoda"]
    assert "checkin=2026-12-03" in links["Booking"]
    assert "checkin=2026-12-03" in links["Trip.com"]


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
