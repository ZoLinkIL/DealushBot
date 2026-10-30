"""Tests for the v2 engine: prices, verdicts, storage, screenshot parsing, messages, price watch."""
import asyncio
import os
import tempfile
from datetime import date, timedelta

import pricing
from parsing import HotelQuery, parse_price, parse_dates
from pricing import Conditions, Offer, make_verdict, fetch_offers, price_drop
from storage import Store
from vision import to_result, _json_from
import messages as M

FUT = date.today() + timedelta(days=30)


def q():
    return HotelQuery(name="Dan Tel Aviv", checkin=FUT, checkout=FUT + timedelta(days=3), city="Tel Aviv")


def test_parse_price():
    assert parse_price("950") == 950
    assert parse_price("₪1,250") == 1250
    assert parse_price('זה 820 ש"ח') == 820
    assert parse_price("1250.50") == 1250
    assert parse_price("12/11 - 15/11") is None
    assert parse_price("https://www.booking.com/x 900") is None
    assert parse_price("היי") is None
    assert parse_price("5") is None


def test_parse_dates():
    ci, co = parse_dates("12/11 - 15/11", today=date(2026, 10, 28))
    assert ci == date(2026, 11, 12) and co == date(2026, 11, 15)


def test_roundtrip():
    a = q()
    b = HotelQuery.from_dict(a.to_dict())
    assert b == a


def test_verdicts():
    offers = [Offer("Agoda", 820), Offer("Trip.com", 900)]
    v = make_verdict(950, offers)
    assert v.kind == "cheaper" and v.best.site == "Agoda" and v.saving == 130 and v.saving_pct == 14
    assert make_verdict(840, offers).kind == "good"          # 2.4% gap - below threshold
    assert make_verdict(None, offers).kind == "no_anchor"
    assert make_verdict(950, []).kind == "no_data"
    assert price_drop(1000, 940) and not price_drop(1000, 970)


class FakeProvider(pricing.PriceProvider):
    name = "fake"

    def __init__(self, offers):
        self._o = offers

    async def offers(self, q):
        return self._o


class Broken(pricing.PriceProvider):
    name = "broken"

    async def offers(self, q):
        raise RuntimeError("down")


def test_fetch_offers_rules():
    pricing.PROVIDERS[:] = [FakeProvider([
        Offer("Agoda", 700, free_cancellation=False), Offer("Agoda", 800, free_cancellation=True),
        Offer("Expedia", 760), Offer("Trip.com", 50, currency="USD")]), Broken()]
    try:
        all_ = asyncio.run(fetch_offers(q()))
        assert [(o.site, o.total) for o in all_] == [("Agoda", 700), ("Expedia", 760)]  # USD dropped, cheapest per site
        flex = asyncio.run(fetch_offers(q(), Conditions(free_cancellation=True)))
        assert [(o.site, o.total) for o in flex] == [("Expedia", 760), ("Agoda", 800)]  # non-refundable excluded
    finally:
        pricing.PROVIDERS.clear()
    assert asyncio.run(fetch_offers(q())) == []


def test_storage():
    with tempfile.TemporaryDirectory() as d:
        s = Store(os.path.join(d, "t.db"))
        qid = s.log_query(q().to_dict(), "screenshot", "private", 950, {"free_cancellation": True})
        s.set_verdict(qid, "cheaper", "Agoda", 820)
        assert s.get_query(qid)["best_site"] == "Agoda"
        wid = s.add_watch(42, "post", q().to_dict(), 950, {}, "2026-12-01")
        assert len(s.chat_watches(42)) == 1 and not s.stop_watch(wid, 99) and s.stop_watch(wid, 42)
        assert s.active_watches() == []
        s.add_feedback(qid, 820, 815)
        st = s.stats()
        assert st["queries"] == 1 and st["feedback_match"] == 1 and st["screenshots"] == 1


def test_vision_parse():
    raw = 'Sure: {"type":"hotel_page","site":"Booking.com","hotel_name":"dan eilat","city":"Eilat",' \
          '"checkin":"2026-12-03","checkout":"2026-12-06","adults":2,"price":400,"price_is_per_night":true,' \
          '"currency":"ILS","free_cancellation":true,"breakfast_included":null}'
    r = to_result(_json_from(raw))
    assert r.kind == "hotel_page" and r.query.name == "Dan Eilat" and r.query.nights == 3
    assert r.price == 1200 and r.conditions.free_cancellation is True
    conf = to_result({"type": "booking_confirmation", "hotel_name": "מלון ים", "price": "2,000",
                      "cancellation_deadline": "bad"})
    assert conf.kind == "booking_confirmation" and conf.price is None and conf.cancel_deadline is None
    assert to_result({"type": "other"}).query is None


def test_messages_render():
    v = make_verdict(950, [Offer("Agoda", 820), Offer("Expedia", 900)])
    text, kb = M.result(q(), 950, "ILS", v, 7, True)
    assert "נמצא זול יותר ב-Agoda" in text and "₪130" in text
    labels = [r[0].text for r in kb.inline_keyboard]
    assert labels[0].startswith("🔎 Agoda · ₪820")
    assert any("Booking" in l for l in labels)  # link-only fallbacks
    assert not any("Trip.com" in l for l in labels)  # Trip.com contract: never next to competitor prices
    assert labels[-2].startswith("🔔") and labels[-1].startswith("📝")
    text, kb = M.result(q(), None, None, make_verdict(None, []), 8, False)
    assert "בדקו את אותו מלון" in text and not any(r[0].text.startswith("🔔") for r in kb.inline_keyboard)
    assert "ביטול" in M.price_alert(q(), "post", "Agoda", 800, 950, "2026-12-01")


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
