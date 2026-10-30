"""Trip.com affiliate links + contract rules (no Trip.com next to competitor prices, no Trip.com in push)."""
import asyncio
from datetime import date

import config
import links
import messages as M
import pricing
from parsing import HotelQuery
from pricing import Offer, Verdict


def q():
    return HotelQuery(name="Dan Eilat", checkin=date(2026, 12, 3), checkout=date(2026, 12, 6), adults=2, site="Booking")


def _ids(monkeypatch):
    monkeypatch.setattr(config, "TRIP_ALLIANCE_ID", "11189452")
    monkeypatch.setattr(config, "TRIP_SID", "334834760")


def test_trip_link_has_affiliate_and_source(monkeypatch):
    _ids(monkeypatch)
    url = dict(links.comparison_links(q()))["Trip.com"]
    assert "Allianceid=11189452" in url and "SID=334834760" in url and "trip_sub1=telegram" in url
    assert "trip_sub1=ig" in links.trip_link(q(), sub="ig")


def test_trip_link_without_ids_is_plain(monkeypatch):
    monkeypatch.setattr(config, "TRIP_ALLIANCE_ID", None)
    monkeypatch.setattr(config, "TRIP_SID", None)
    assert "Allianceid" not in links.trip_link(q())


def test_trip_prices_never_compared(monkeypatch):
    class P:
        name = "fake"
        async def offers(self, _q):
            return [Offer("Trip.com", 900, "ILS", "https://trip"), Offer("Agoda", 1000, "ILS", "https://agoda")]
    monkeypatch.setattr(pricing, "PROVIDERS", [P()])
    offers = asyncio.run(pricing.fetch_offers(q()))
    assert [o.site for o in offers] == ["Agoda"]


def test_priced_result_has_no_trip(monkeypatch):
    _ids(monkeypatch)
    v = Verdict(kind="cheaper", best=Offer("Agoda", 1000, "ILS", "https://agoda"), saving=200, saving_pct=17,
                offers=[Offer("Agoda", 1000, "ILS", "https://agoda"), Offer("Trip.com", 900, "ILS", "https://trip")])
    text, kb = M.result(q(), 1200, "ILS", v, 1, True, True)
    blob = text + " ".join(b.text + (b.url or "") for row in kb.inline_keyboard for b in row)
    assert "Trip" not in blob and "trip.com" not in blob


def test_unpriced_result_keeps_trip(monkeypatch):
    _ids(monkeypatch)
    text, kb = M.result(q(), None, None, Verdict(kind="no_data"), 1, False, True)
    urls = [b.url for row in kb.inline_keyboard for b in row if b.url]
    assert any("trip.com" in u and "trip_sub1=telegram" in u for u in urls)
