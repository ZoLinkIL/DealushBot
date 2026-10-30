"""Read a screenshot (hotel page or booking confirmation) into structured data.

Uses Claude (ANTHROPIC_API_KEY) or Gemini (GEMINI_API_KEY) - whichever is set.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import date

import httpx

import config
from parsing import HotelQuery, _iso, _pretty
from pricing import Conditions

log = logging.getLogger(__name__)

PROMPT = """You read screenshots that users send to a hotel price-comparison bot.
Return ONLY a JSON object, no prose, with these keys (null when not visible):
{
 "type": "hotel_page" | "booking_confirmation" | "other",
 "site": e.g. "Booking.com", "Agoda", "Expedia", "Hotels.com", "Trip.com", "Airbnb",
 "hotel_name": exact name as shown,
 "city": city or area,
 "checkin": "YYYY-MM-DD", "checkout": "YYYY-MM-DD",
 "adults": int, "children": int, "rooms": int,
 "price": number - the price the user sees for the WHOLE stay including taxes if shown,
 "price_is_per_night": true/false,
 "currency": ISO code, e.g. "ILS", "USD", "EUR",
 "room_type": short text,
 "free_cancellation": true/false,
 "cancellation_deadline": "YYYY-MM-DD",
 "breakfast_included": true/false
}
Dates on Israeli sites are usually day/month. If the year is missing, assume the next upcoming date."""


def available() -> bool:
    return bool(config.ANTHROPIC_API_KEY or config.GEMINI_API_KEY)


@dataclass
class ScreenshotResult:
    kind: str                       # hotel_page | booking_confirmation | other
    query: HotelQuery | None
    price: float | None             # total stay price as the user sees it
    currency: str | None
    conditions: Conditions
    site: str | None = None
    cancel_deadline: str | None = None


def _json_from(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    return json.loads(m.group(0)) if m else {}


def to_result(d: dict) -> ScreenshotResult:
    kind = d.get("type") if d.get("type") in ("hotel_page", "booking_confirmation") else "other"
    q = None
    name = (d.get("hotel_name") or "").strip()
    if kind != "other" and name:
        q = HotelQuery(
            name=_pretty(name), city=d.get("city"),
            checkin=_iso(d.get("checkin")), checkout=_iso(d.get("checkout")),
            adults=int(d.get("adults") or 2), children=int(d.get("children") or 0), rooms=int(d.get("rooms") or 1),
        )
    price = d.get("price")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    if price and d.get("price_is_per_night") and q and q.nights:
        price = price * q.nights
    cond = Conditions(free_cancellation=d.get("free_cancellation"),
                      breakfast=d.get("breakfast_included"), room_type=d.get("room_type"))
    deadline = d.get("cancellation_deadline")
    return ScreenshotResult(kind, q, price, (d.get("currency") or None), cond, d.get("site"),
                            deadline if _iso(deadline) else None)


async def _claude(img: bytes, mime: str) -> str:
    body = {
        "model": config.ANTHROPIC_MODEL, "max_tokens": 700,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime,
                                         "data": base64.b64encode(img).decode()}},
            {"type": "text", "text": PROMPT + f"\nToday is {date.today().isoformat()}."},
        ]}],
    }
    async with httpx.AsyncClient(timeout=40) as c:
        r = await c.post("https://api.anthropic.com/v1/messages", json=body, headers={
            "x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []))


async def _gemini(img: bytes, mime: str) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent")
    body = {"contents": [{"parts": [
        {"inline_data": {"mime_type": mime, "data": base64.b64encode(img).decode()}},
        {"text": PROMPT + f"\nToday is {date.today().isoformat()}."},
    ]}], "generationConfig": {"responseMimeType": "application/json"}}
    async with httpx.AsyncClient(timeout=40) as c:
        r = await c.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY})
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)


async def read_screenshot(img: bytes, mime: str = "image/jpeg") -> ScreenshotResult | None:
    if not available():
        return None
    try:
        raw = await (_claude(img, mime) if config.ANTHROPIC_API_KEY else _gemini(img, mime))
        return to_result(_json_from(raw))
    except Exception as e:
        log.warning("screenshot read failed: %s", e)
        return None
