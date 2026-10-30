"""Turn a user message (hotel link from any booking site, or free text) into a HotelQuery."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse, parse_qs, unquote

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?!\d)")
ADULTS_RE = re.compile(r"(\d{1,2})\s*(?:מבוגרים|אנשים|אורחים|adults?|guests?)", re.IGNORECASE)
MAX_NIGHTS = 30


@dataclass
class HotelQuery:
    name: str
    checkin: date | None = None
    checkout: date | None = None
    adults: int = 2
    children: int = 0
    rooms: int = 1
    country: str | None = None
    city: str | None = None
    site: str | None = None        # where the user found it, e.g. "Booking"
    source_url: str | None = None

    @property
    def nights(self) -> int | None:
        if self.checkin and self.checkout:
            return (self.checkout - self.checkin).days
        return None

    @property
    def has_dates(self) -> bool:
        n = self.nights
        return n is not None and 0 < n <= MAX_NIGHTS


    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["checkin"] = self.checkin.isoformat() if self.checkin else None
        d["checkout"] = self.checkout.isoformat() if self.checkout else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "HotelQuery":
        d = dict(d)
        d["checkin"] = _iso(d.get("checkin"))
        d["checkout"] = _iso(d.get("checkout"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


PRICE_RE = re.compile(r"(?<![\d/.])(\d{1,3}(?:,\d{3})+|\d{2,6})(?:\.\d{1,2})?(?![\d/])")


def parse_price(text: str) -> float | None:
    """'950', '₪1,250', '1250 ש"ח', 'זה 820 שקל' -> number. Ignores dates and links."""
    if not text or URL_RE.search(text) or DATE_RE.search(text):
        return None
    m = PRICE_RE.search(text)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    return value if 20 <= value <= 500_000 else None


def extract_url(text: str) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(").,") if m else None


def is_booking_share_link(url: str) -> bool:
    p = urlparse(url)
    host = p.netloc.lower()
    return "booking.com" in host and (p.path.startswith("/Share-") or host.startswith("bkng."))


def _int(v: str | None, default: int) -> int:
    try:
        return max(0, int(v)) if v is not None else default
    except ValueError:
        return default


def _iso(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def _ymd(y, m, d) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None


def parse_booking_url(url: str) -> HotelQuery | None:
    """Parse a full booking.com hotel page URL."""
    p = urlparse(url)
    if "booking.com" not in p.netloc.lower():
        return None
    m = re.match(r"^/hotel/([a-z]{2})/([^/.]+)", p.path)
    if not m:
        return None
    country, slug = m.groups()
    name = unquote(slug).replace("-", " ").strip()
    q = parse_qs(p.query)

    def first(k):
        v = q.get(k)
        return v[0] if v else None

    checkin = _iso(first("checkin")) or _ymd(first("checkin_year"), first("checkin_month"), first("checkin_monthday"))
    checkout = _iso(first("checkout")) or _ymd(first("checkout_year"), first("checkout_month"), first("checkout_monthday"))
    return HotelQuery(
        name=_pretty(name),
        checkin=checkin,
        checkout=checkout,
        adults=_int(first("group_adults"), 2) or 2,
        children=_int(first("group_children"), 0),
        rooms=_int(first("no_rooms"), 1) or 1,
        country=country,
        site="Booking",
        source_url=url,
    )


def _pretty(name: str) -> str:
    # Title-case Latin names; leave Hebrew as is.
    return name.title() if re.search(r"[a-zA-Z]", name) else name


def _mk_date(parts, today: date, not_before: date | None = None) -> date | None:
    d, m, y = parts
    try:
        day, month = int(d), int(m)
        if y:
            year = int(y)
            if year < 100:
                year += 2000
            return date(year, month, day)
        cand = date(today.year, month, day)
        floor = not_before or today
        if cand < floor:
            cand = date(today.year + 1, month, day)
        return cand
    except ValueError:
        return None


def parse_dates(text: str, today: date | None = None) -> tuple[date | None, date | None]:
    """First two dates in the text -> (checkin, checkout)."""
    today = today or date.today()
    dates = DATE_RE.findall(text or "")
    if len(dates) < 2:
        return None, None
    checkin = _mk_date(dates[0], today)
    checkout = _mk_date(dates[1], today, not_before=checkin) if checkin else None
    return checkin, checkout


def parse_free_text(text: str, today: date | None = None) -> HotelQuery | None:
    """e.g. 'דן תל אביב 12/11 - 15/11 2 מבוגרים'"""
    checkin, checkout = parse_dates(text, today)
    if not checkin:
        return None
    adults_m = ADULTS_RE.search(text)
    adults = int(adults_m.group(1)) if adults_m else 2

    name = DATE_RE.sub(" ", text)
    name = ADULTS_RE.sub(" ", name)
    name = re.sub(r"\b(עד|מ|to|from)\b", " ", name)
    name = re.sub(r"[-–—,|]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) < 2:
        return None
    return HotelQuery(name=_pretty(name), checkin=checkin, checkout=checkout, adults=max(1, adults))


# ---------------------------------------------------------------------------
# Other booking sites. Each one puts the hotel and dates in a different place
# in its URL. The formats below are the common public ones; verify with real
# links from time to time, sites change them.
# ---------------------------------------------------------------------------

CHECKIN_KEYS = ("checkin", "checkIn", "check_in", "chkin", "startDate", "start_date", "arrival", "arrivalDate",
                "fromDate", "dateFrom", "in")
CHECKOUT_KEYS = ("checkout", "checkOut", "check_out", "chkout", "endDate", "end_date", "departure", "departureDate",
                 "toDate", "dateTo", "out")
ADULT_KEYS = ("adults", "adult", "group_adults", "numberOfAdults", "rm1")
COUNTRY_SUFFIX = re.compile(r"-(?:[a-z]{2})$")


def _flex_date(v: str | None) -> date | None:
    """'2026-11-12', '2026/11/12', '20261112', '12/11/2026' -> date."""
    if not v:
        return None
    v = v.strip()
    m = re.match(r"^(\d{4})[-/.]?(\d{1,2})[-/.]?(\d{1,2})", v)
    if m:
        return _ymd(*m.groups())
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", v)
    if m:
        d, mo, y = m.groups()
        return _ymd(y, mo, d)
    return None


def _qfirst(q: dict, keys) -> str | None:
    for k in keys:
        if q.get(k):
            return q[k][0]
    lower = {k.lower(): v for k, v in q.items()}
    for k in keys:
        if lower.get(k.lower()):
            return lower[k.lower()][0]
    return None


def query_dates(query: str) -> tuple[date | None, date | None, int]:
    """Dates and adults from any common query-string naming. Also handles los= (length of stay)."""
    q = parse_qs(query)
    ci = _flex_date(_qfirst(q, CHECKIN_KEYS))
    co = _flex_date(_qfirst(q, CHECKOUT_KEYS))
    los = _qfirst(q, ("los", "nights", "lengthOfStay"))
    if ci and not co and los and los.isdigit():
        from datetime import timedelta
        co = ci + timedelta(days=int(los))
    a = _qfirst(q, ADULT_KEYS) or ""
    m = re.search(r"\d+", a)          # rm1=a2 (Expedia) -> 2
    adults = int(m.group(0)) if m else 2
    return ci, co, max(1, min(adults, 12))


def _words(slug: str) -> str:
    return re.sub(r"\s+", " ", unquote(slug).replace("-", " ").replace("_", " ").replace("+", " ")).strip()


def _make(name: str, site: str, url: str, city: str | None = None) -> HotelQuery | None:
    name = name.strip()
    if len(name) < 2:
        return None
    ci, co, adults = query_dates(urlparse(url).query)
    return HotelQuery(name=_pretty(name), checkin=ci, checkout=co, adults=adults,
                      city=_pretty(city) if city else None, site=site, source_url=url)


def parse_agoda_url(url: str) -> HotelQuery | None:
    # /he-il/dan-tel-aviv-hotel/hotel/tel-aviv-yafo-il.html
    p = urlparse(url)
    if "agoda." not in p.netloc.lower():
        return None
    m = re.search(r"/([^/]+)/hotel/([^/]+?)\.html", p.path)
    if not m:
        return None
    name = _words(m.group(1))
    name = re.sub(r"\s+hotel$", "", name, flags=re.I) or name
    city = _words(COUNTRY_SUFFIX.sub("", m.group(2)))
    return _make(name, "Agoda", url, city)


def parse_trip_url(url: str) -> HotelQuery | None:
    # /hotels/tel-aviv-hotel-detail-123456/dan-tel-aviv/   or  /hotels/detail/?hotelId=123&...
    p = urlparse(url)
    if "trip.com" not in p.netloc.lower():
        return None
    m = re.search(r"/hotels/([^/]+)-hotel-detail-\d+/([^/?]+)", p.path)
    if m:
        return _make(_words(m.group(2)), "Trip.com", url, _words(m.group(1)))
    return None


def parse_expedia_url(url: str) -> HotelQuery | None:
    # Expedia & Hotels.com: /Tel-Aviv-Hotels-Dan-Tel-Aviv.h123456.Hotel-Information
    # Hotels.com (old):    /ho123456/dan-tel-aviv-tel-aviv-israel/
    p = urlparse(url)
    host = p.netloc.lower()
    site = "Hotels.com" if "hotels.com" in host else "Expedia" if "expedia." in host else None
    if not site:
        return None
    m = re.search(r"/([^/]+?)\.h\d+\.Hotel-Information", p.path)
    if m:
        seg = m.group(1)
        if "-Hotels-" in seg:
            city, name = seg.split("-Hotels-", 1)
            return _make(_words(name), site, url, _words(city))
        return _make(_words(seg), site, url)
    m = re.search(r"/ho\d+/([^/?]+)", p.path)
    if m:
        return _make(_words(m.group(1)), site, url)
    return None


def is_airbnb(url: str) -> bool:
    return "airbnb." in urlparse(url).netloc.lower()


def parse_hotel_url(url: str) -> HotelQuery | None:
    """Any supported booking-site hotel URL -> HotelQuery, without network access."""
    for fn in (parse_booking_url, parse_agoda_url, parse_trip_url, parse_expedia_url):
        q = fn(url)
        if q:
            if not q.site:
                q.site = "Booking"
            return q
    return None


SITE_NAMES = {"booking.": "Booking", "agoda.": "Agoda", "trip.com": "Trip.com", "expedia.": "Expedia",
              "hotels.com": "Hotels.com", "airbnb.": "Airbnb", "kayak.": "Kayak", "skyscanner.": "Skyscanner",
              "tripadvisor.": "Tripadvisor", "issta.": "Issta", "gulliver.": "Gulliver"}


def site_name(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    for k, v in SITE_NAMES.items():
        if k in host:
            return v
    return None


TITLE_JUNK = re.compile(
    r"\s*[|\-–—:,]\s*(booking\.com|agoda|trip\.com|expedia|hotels\.com|tripadvisor|kayak|.*?(מחירים|prices|deals|"
    r"updated|עודכן|book now|הזמינו|best price|reviews|ביקורות)).*$", re.I)
TITLE_PREFIX = re.compile(r"^(?:hotel deals?|book|הזמינו|מלון)\s*[:\-–]\s*", re.I)


def name_from_title(title: str) -> str | None:
    """'Dan Tel Aviv, Tel Aviv – Updated 2026 Prices' -> 'Dan Tel Aviv'."""
    if not title:
        return None
    t = re.sub(r"\s+", " ", title).strip()
    t = TITLE_PREFIX.sub("", t)
    t = TITLE_JUNK.sub("", t)
    t = re.split(r"\s+[|–—]\s+", t)[0]
    t = re.sub(r"\(.*?\)", "", t).strip(" ,-–|")
    if "," in t:                       # 'Hotel Name, City' -> keep both parts for the search
        parts = [x.strip() for x in t.split(",") if x.strip()]
        t = " ".join(parts[:2])
    return t if 2 <= len(t) <= 90 else None
