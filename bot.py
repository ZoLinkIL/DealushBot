"""Telegram bot: send a hotel (screenshot / link from any booking site / name + dates) and get where it's cheaper."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import config
import messages as M
import pricing
import resolve
import vision
from links import is_trip, link_for_site
from parsing import HotelQuery, extract_url, parse_dates, parse_free_text, parse_price
from urlread import read_link
from pricing import Conditions
from storage import Store

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")
store = Store(config.DB_PATH)
HTML = ParseMode.HTML


# ---------- helpers ----------

def is_private(update: Update) -> bool:
    return update.effective_chat.type == ChatType.PRIVATE


def clear_pending(ud: dict) -> None:
    for k in ("pending", "pending_dates", "awaiting_feedback"):
        ud.pop(k, None)


async def send(context, chat_id: int, text: str, kb=None) -> None:
    await context.bot.send_message(chat_id, text, parse_mode=HTML, reply_markup=kb,
                                   disable_web_page_preview=True)


async def show_results(context, chat_id: int, chat_type: str, q: HotelQuery, source: str,
                       user_price: float | None = None, currency: str | None = None,
                       cond: Conditions | None = None) -> None:
    qid = store.log_query(q.to_dict(), source, chat_type, user_price, asdict(cond) if cond else None)
    offers = await pricing.fetch_offers(q, cond)
    comparable = user_price if (currency or "ILS") == "ILS" else None
    v = pricing.make_verdict(comparable, offers)
    store.set_verdict(qid, v.kind, v.best.site if v.best else None, v.best.total if v.best else None)
    await resolve.enrich(q, store)  # buttons open the hotel's own page where we can find it
    text, kb = M.result(q, user_price, currency, v, qid, pricing.enabled(), chat_type == ChatType.PRIVATE)
    await send(context, chat_id, text, kb)


async def start_query(update: Update, context: ContextTypes.DEFAULT_TYPE, q: HotelQuery, source: str,
                      user_price: float | None = None, currency: str | None = None,
                      cond: Conditions | None = None) -> None:
    """Entry for every recognized hotel. Asks for the user's price when we don't have it."""
    chat = update.effective_chat
    if user_price is None and is_private(update) and q.has_dates:
        context.user_data["pending"] = {"q": q.to_dict(), "source": source,
                                        "cond": asdict(cond) if cond else None}
        text, kb = M.ask_price(q)
        await send(context, chat.id, text, kb)
        return
    await show_results(context, chat.id, chat.type, q, source, user_price, currency, cond)


# ---------- commands ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_pending(context.user_data)
    await update.message.reply_text(M.WELCOME, parse_mode=HTML)


async def cmd_watches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, kb = M.watches_list(store.chat_watches(update.effective_chat.id))
    await update.message.reply_text(text, parse_mode=HTML, reply_markup=kb)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in config.ADMIN_IDS:
        return
    s = store.stats()
    acc = f"{s['feedback_match']}/{s['feedback']}" if s["feedback"] else "אין עדיין"
    await update.message.reply_text(
        f"📊 בדיקות: {s['queries']} (7 ימים: {s['queries_7d']})\n"
        f"צילומי מסך: {s['screenshots']} · לינקים: {s['links']} · עם מחיר משתמש: {s['with_price']}\n"
        f"נמצא זול יותר: {s['cheaper_found']}\nהתראות פעילות: {s['active_watches']}\n"
        f"דיוק לפי דיווחי משתמשים (±2%): {acc}")


# ---------- messages ----------

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    ud = context.user_data
    chat = update.effective_chat

    # Groups: react only to hotel links, never chat back on ordinary messages.
    if not is_private(update):
        url = extract_url(text)
        if url:
            res = await read_link(url)
            if res.query:
                await show_results(context, chat.id, chat.type, res.query, "link_group")
        return

    # 1. Answer to "what price did you see?"
    if "awaiting_feedback" in ud:
        qid = ud.pop("awaiting_feedback")
        seen = parse_price(text)
        if seen:
            row = store.get_query(qid)
            store.add_feedback(qid, row["best_price"] if row else None, seen)
            await update.message.reply_text(M.FEEDBACK_THANKS)
            return

    # 2. Answer to "what dates?" after a screenshot without dates
    if "pending_dates" in ud:
        ci, co = parse_dates(text)
        if ci and co:
            p = ud.pop("pending_dates")
            q = HotelQuery.from_dict(p["q"])
            q.checkin, q.checkout = ci, co
            cond = Conditions(**p["cond"]) if p.get("cond") else None
            await start_query(update, context, q, p["source"], p.get("price"), p.get("currency"), cond)
            return
        ud.pop("pending_dates")

    # 3. Answer to "how much does it cost you?"
    if "pending" in ud:
        price = parse_price(text)
        if price:
            p = ud.pop("pending")
            cond = Conditions(**p["cond"]) if p.get("cond") else None
            await show_results(context, chat.id, chat.type, HotelQuery.from_dict(p["q"]), p["source"],
                               price, "ILS", cond)
            return
        if not extract_url(text) and not parse_dates(text)[0]:
            await update.message.reply_text(M.ASK_PRICE_HINT)
            return
        ud.pop("pending")  # looks like a new hotel - fall through

    # 4. A link
    url = extract_url(text)
    if url:
        res = await read_link(url)
        if res.kind == "airbnb":
            await update.message.reply_text(M.AIRBNB)
        elif res.query:
            # text next to the link may carry dates the link lacks ("12/11-15/11 https://...")
            if not res.query.has_dates:
                ci, co = parse_dates(text.replace(url, " "))
                if ci and co:
                    res.query.checkin, res.query.checkout = ci, co
            await start_query(update, context, res.query, "link")
        else:
            await update.message.reply_text(M.LINK_UNREADABLE)
        return

    # 5. Free text
    q = parse_free_text(text)
    if q:
        await start_query(update, context, q, "text")
    else:
        await update.message.reply_text(M.NOT_FOUND, parse_mode=HTML)


async def on_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not is_private(update):
        return
    clear_pending(context.user_data)
    if not vision.available():
        await msg.reply_text(M.NO_VISION)
        return

    status = await msg.reply_text(M.READING)
    if msg.photo:
        f, mime = await msg.photo[-1].get_file(), "image/jpeg"
    else:
        f, mime = await msg.document.get_file(), msg.document.mime_type or "image/jpeg"
    data = bytes(await f.download_as_bytearray())
    res = await vision.read_screenshot(data, mime)
    try:
        await status.delete()
    except Exception:
        pass

    if not res or not res.query:
        await msg.reply_text(M.SCREENSHOT_FAIL)
        return
    q = res.query

    if res.kind == "booking_confirmation":
        await handle_confirmation(update, context, res)
        return

    if not q.has_dates:
        context.user_data["pending_dates"] = {"q": q.to_dict(), "source": "screenshot", "price": res.price,
                                              "currency": res.currency, "cond": asdict(res.conditions)}
        await msg.reply_text(M.ASK_DATES, parse_mode=HTML)
        return
    await start_query(update, context, q, "screenshot", res.price, res.currency, res.conditions)


async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, res) -> None:
    q, chat = res.query, update.effective_chat
    store.log_query(q.to_dict(), "confirmation", chat.type, res.price, asdict(res.conditions))
    usable = pricing.enabled() and q.has_dates and q.checkin > date.today() and (res.currency or "ILS") == "ILS"
    if not usable:
        await update.message.reply_text(M.confirmation_not_ready(q), parse_mode=HTML)
        return
    store.add_watch(chat.id, "post", q.to_dict(), res.price, asdict(res.conditions), res.cancel_deadline)
    await update.message.reply_text(M.confirmation_watch(q, res.price, res.cancel_deadline), parse_mode=HTML)


# ---------- buttons ----------

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cq = update.callback_query
    await cq.answer()
    data, chat = cq.data or "", update.effective_chat

    if data == "skip":
        p = context.user_data.pop("pending", None)
        if p:
            cond = Conditions(**p["cond"]) if p.get("cond") else None
            await show_results(context, chat.id, chat.type, HotelQuery.from_dict(p["q"]), p["source"], cond=cond)
        return

    if data.startswith("fb:"):
        context.user_data["awaiting_feedback"] = int(data[3:])
        await send(context, chat.id, M.ASK_FEEDBACK)
        return

    if data.startswith("watch:"):
        row = store.get_query(int(data[6:]))
        if not row:
            return
        q = HotelQuery.from_dict(json.loads(row["query_json"]))
        prices = [p for p in (row["user_price"], row["best_price"]) if p]
        store.add_watch(chat.id, "pre", q.to_dict(), min(prices) if prices else None,
                        json.loads(row["conditions_json"] or "{}"))
        await send(context, chat.id, M.watch_added(q))
        return

    if data.startswith("stop:"):
        if store.stop_watch(int(data[5:]), chat.id):
            await send(context, chat.id, M.WATCH_STOPPED)


# ---------- background price watch ----------

async def check_watches(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not pricing.enabled():
        return
    today = date.today().isoformat()
    for w in store.active_watches():
        q = HotelQuery.from_dict(json.loads(w["query_json"]))
        if not q.has_dates or q.checkin.isoformat() <= today or (w["cancel_deadline"] and w["cancel_deadline"] < today):
            store.update_watch(w["id"], active=0)
            continue
        cond = Conditions(**json.loads(w["conditions_json"] or "{}"))
        # Trip.com contract: no push in instant messages -> alerts never mention or link Trip.com
        offers = [o for o in await pricing.fetch_offers(q, cond) if not is_trip(o.site)]
        if not offers:
            store.update_watch(w["id"])
            continue
        best, ref = offers[0], (w["last_alert_price"] or w["baseline"])
        if ref is None:  # no baseline yet - the first check sets it
            store.update_watch(w["id"], baseline=best.total)
            continue
        if pricing.price_drop(ref, best.total):
            if not best.url:
                await resolve.enrich(q, store)
            url = best.url or link_for_site(best.site, q)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🔎 {best.site} · {M.ils(best.total)}", url=url)]]) if url else None
            try:
                await send(context, w["chat_id"], M.price_alert(q, w["kind"], best.site, best.total, ref,
                                                                w["cancel_deadline"]), kb)
                store.update_watch(w["id"], last_alert_price=best.total)
            except Exception as e:  # user blocked the bot etc.
                log.warning("alert failed for watch %s: %s", w["id"], e)
                store.update_watch(w["id"], active=0)
        else:
            store.update_watch(w["id"])


def main() -> None:
    if not config.TELEGRAM_TOKEN:
        raise SystemExit("חסר TELEGRAM_TOKEN בקובץ .env")
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("watches", cmd_watches))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_image))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    if app.job_queue:
        app.job_queue.run_repeating(check_watches, interval=config.WATCH_INTERVAL_HOURS * 3600, first=60)
    else:
        log.warning("job queue missing - install python-telegram-bot[job-queue] for price alerts")
    log.info("bot running · screenshots: %s · live prices: %s", vision.available(), pricing.enabled())
    app.run_polling()


if __name__ == "__main__":
    main()
