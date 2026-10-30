"""Price engine: providers return offers, the verdict decides what we tell the user.

Today PROVIDERS is empty (no affiliate/API access yet), so the bot shows links only.
When an API is approved, add a provider class here and append it to PROVIDERS -
everything else (verdicts, alerts, post-booking watch) switches on automatically.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import config
from parsing import HotelQuery
from links import is_trip

log = logging.getLogger(__name__)


@dataclass
class Conditions:
    """What the user is looking at - we only compare like with like."""
    free_cancellation: bool | None = None
    breakfast: bool | None = None
    room_type: str | None = None


@dataclass
class Offer:
    site: str
    total: float                 # full stay, incl. taxes and fees
    currency: str = "ILS"
    url: str | None = None
    free_cancellation: bool | None = None
    breakfast: bool | None = None
    room_type: str | None = None


class PriceProvider:
    """Base class. Implement offers() with a live (not cached) call, ILS, Israel point of sale."""
    name = "base"

    async def offers(self, q: HotelQuery) -> list[Offer]:
        raise NotImplementedError


PROVIDERS: list[PriceProvider] = []   # e.g. AgodaProvider(), ExpediaProvider() - see ROADMAP.md
PROVIDER_TIMEOUT = 12


def enabled() -> bool:
    return bool(PROVIDERS)


def matches(offer: Offer, cond: Conditions | None) -> bool:
    """Drop offers that are worse on terms the user has (e.g. user has free cancellation)."""
    if not cond:
        return True
    if cond.free_cancellation and offer.free_cancellation is False:
        return False
    if cond.breakfast and offer.breakfast is False:
        return False
    return True


async def fetch_offers(q: HotelQuery, cond: Conditions | None = None) -> list[Offer]:
    if not PROVIDERS or not q.has_dates:
        return []

    async def one(p: PriceProvider):
        try:
            return await asyncio.wait_for(p.offers(q), PROVIDER_TIMEOUT)
        except Exception as e:  # one failing site must never break the answer
            log.warning("provider %s failed: %s", p.name, e)
            return []

    results = await asyncio.gather(*(one(p) for p in PROVIDERS))
    # Trip.com contract: its prices may not be shown next to competitors' prices -> never compared
    offers = [o for r in results for o in r if o.total > 0 and o.currency == "ILS" and matches(o, cond)
              and not is_trip(o.site)]
    # keep the cheapest offer per site
    best: dict[str, Offer] = {}
    for o in offers:
        if o.site not in best or o.total < best[o.site].total:
            best[o.site] = o
    return sorted(best.values(), key=lambda o: o.total)


@dataclass
class Verdict:
    kind: str                      # cheaper | good | no_anchor | no_data
    best: Offer | None = None
    saving: float = 0.0
    saving_pct: float = 0.0
    offers: list[Offer] = field(default_factory=list)


def make_verdict(user_price: float | None, offers: list[Offer],
                 threshold: float = config.CHEAPER_THRESHOLD) -> Verdict:
    if not offers:
        return Verdict("no_data")
    best = offers[0]
    if not user_price:
        return Verdict("no_anchor", best, offers=offers)
    saving = user_price - best.total
    pct = saving / user_price
    kind = "cheaper" if pct >= threshold else "good"
    return Verdict(kind, best, round(saving), round(pct * 100), offers)


def price_drop(baseline: float, current: float, threshold: float = config.CHEAPER_THRESHOLD) -> bool:
    return baseline > 0 and (baseline - current) / baseline >= threshold
