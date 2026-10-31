"""Exact-hotel links: id resolution, fallbacks, cache, and the Trip.com contract."""
import asyncio
from datetime import date

import config
import links
import resolve
from parsing import HotelQuery
from storage import Store


def q(**kw):
    base = dict(name="Dan Tel Aviv", city="Tel Aviv", checkin=date(2026, 12, 3), checkout=date(2026, 12, 6),
                adults=2, site="Booking")
    base.update(kw)
    return HotelQuery(**base)


TRIP_JSON = {"keyWordSearchResults": [
    {"type": "City", "code": "3", "resultWord": "Tel Aviv"},
    {"type": "Hotel", "code": "687592", "resultWord": "Dan Tel Aviv", "city": {"geoCode": 4, "currentLocaleName": "Tel Aviv"}},
]}
AGODA_JSON = {"ViewModelList": [
    {"SearchType": 1, "ObjectId": 17193, "Name": "Tel Aviv"},
    {"SearchType": 4, "ObjectId": 297853, "Name": "Dan Tel Aviv Hotel"},
]}
EXPEDIA_JSON = {"sr": [
    {"type": "CITY", "gaiaId": "1", "regionNames": {"primaryDisplayName": "Tel Aviv"}},
    {"type": "HOTEL", "hotelId": "531293", "regionNames": {"primaryDisplayName": "Dan Tel Aviv"}},
]}


def _affiliate(monkeypatch):
    monkeypatch.setattr(config, "TRIP_ALLIANCE_ID", "11189452")
    monkeypatch.setattr(config, "TRIP_SID", "334834760")


def test_parsers_pick_the_hotel_not_the_city():
    assert resolve.parse_trip(TRIP_JSON, q()) == {"hotel_id": "687592", "city_id": "4"}
    assert resolve.parse_agoda(AGODA_JSON, q()) == {"hotel_id": "297853"}
    assert resolve.parse_expedia(EXPEDIA_JSON, q()) == {"hotel_id": "531293"}


def test_parsers_reject_a_different_hotel():
    other = {"keyWordSearchResults": [{"type": "Hotel", "code": "1", "resultWord": "Carlton Tel Aviv"}]}
    assert resolve.parse_trip(other, q()) is None
    assert resolve.parse_trip({}, q()) is None


def test_same_hotel():
    assert resolve.same_hotel("Dan Tel Aviv", "Dan Tel Aviv Hotel", "Tel Aviv")
    assert resolve.same_hotel("Herods Palace", "Herods Palace Hotels & Spa Eilat", "Eilat")
    assert not resolve.same_hotel("Dan Tel Aviv", "Dan Eilat", "Tel Aviv")
    assert not resolve.same_hotel("Dan", "Dan Eilat", "Tel Aviv", "Dan Eilat Eilat Israel")
    assert resolve.same_hotel("Dan", "Dan Hotel", "Tel Aviv Yafo", "Tel Aviv, Israel")
    assert resolve.same_hotel("מלון דן", "מלון דן תל אביב", "תל אביב")


def test_trip_detail_link_keeps_affiliate_and_dates(monkeypatch):
    _affiliate(monkeypatch)
    url = links.trip_link(q(site_ids={"trip": {"hotel_id": "687592", "city_id": "4"}}))
    assert url.startswith("https://www.trip.com/hotels/detail/?hotelId=687592&cityId=4")
    for part in ("checkin=2026-12-03", "checkout=2026-12-06", "adult=2", "curr=ILS",
                 "Allianceid=11189452", "SID=334834760", "trip_sub1=telegram"):
        assert part in url


def test_trip_falls_back_to_list_without_id(monkeypatch):
    _affiliate(monkeypatch)
    url = links.trip_link(q())
    assert url.startswith("https://www.trip.com/hotels/list?") and "Allianceid=11189452" in url


def test_agoda_and_expedia_hotel_pages():
    qq = q(site_ids={"agoda": {"hotel_id": "297853"}, "expedia": {"hotel_id": "531293"}})
    a = links.agoda_link(qq)
    assert a.startswith("https://www.agoda.com/partners/partnersearch.aspx?hid=297853")
    assert "checkIn=2026-12-03" in a and "checkOut=2026-12-06" in a
    e = links.expedia_link(qq)
    assert e.startswith("https://www.expedia.com/h531293.Hotel-Information?") and "chkin=2026-12-03" in e
    assert links.agoda_link(q()).startswith("https://www.agoda.com/search?")


def test_ids_from_user_link():
    ids = resolve.ids_from_url("https://www.trip.com/hotels/eilat-hotel-detail-1234567/herods-palace/?checkin=2026/12/03")
    assert ids["trip"]["hotel_id"] == "1234567"
    ids = resolve.ids_from_url("https://www.expedia.com/Tel-Aviv-Hotels-Dan-Tel-Aviv.h531293.Hotel-Information")
    assert ids["expedia"]["hotel_id"] == "531293"


def _fake_fetch(monkeypatch, calls, fail=()):
    def make(site, data):
        async def f(client, term):
            calls.append(site)
            if site in fail:
                raise RuntimeError("blocked")
            return data
        return f
    monkeypatch.setattr(resolve, "FETCH", {"trip": make("trip", TRIP_JSON), "agoda": make("agoda", AGODA_JSON),
                                           "expedia": make("expedia", EXPEDIA_JSON)})


def test_enrich_resolves_and_caches(monkeypatch, tmp_path):
    calls = []
    _fake_fetch(monkeypatch, calls)
    store = Store(str(tmp_path / "t.db"))
    qq = asyncio.run(resolve.enrich(q(), store))
    assert qq.site_ids["trip"]["hotel_id"] == "687592" and len(calls) == 3
    q2 = asyncio.run(resolve.enrich(q(), store))
    assert {k: v["hotel_id"] for k, v in q2.site_ids.items()} == {"trip": "687592", "agoda": "297853", "expedia": "531293"}
    assert len(calls) == 3  # second time from bot.db


def test_enrich_error_falls_back_and_is_not_cached(monkeypatch, tmp_path):
    calls = []
    _fake_fetch(monkeypatch, calls, fail=("trip",))
    store = Store(str(tmp_path / "t.db"))
    qq = asyncio.run(resolve.enrich(q(), store))
    assert "trip" not in qq.site_ids and links.trip_link(qq).startswith("https://www.trip.com/hotels/list?")
    assert store.get_site_id("trip", resolve.cache_key(q())) is None
    assert store.get_site_id("agoda", resolve.cache_key(q())) == {"hotel_id": "297853", "city_id": None}


def test_miss_is_cached(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.set_site_id("trip", "k", {})
    assert store.get_site_id("trip", "k") == {}


def test_query_roundtrip_keeps_ids():
    qq = q(site_ids={"trip": {"hotel_id": "1", "city_id": None}})
    assert HotelQuery.from_dict(qq.to_dict()).site_ids == qq.site_ids
