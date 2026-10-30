"""All user-facing Hebrew texts and keyboards. Pure functions - easy to test and to edit copy."""
from __future__ import annotations

import json

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
from links import comparison_links, is_trip, link_for_site
from parsing import HotelQuery
from pricing import Verdict

DISCLOSURE = "ℹ️ חלק מהקישורים הם קישורי שותפים – ייתכן שנקבל עמלה, בלי עלות נוספת עבורכם."

WELCOME = (
    f"היי! 👋 אני <b>{config.BOT_NAME}</b>, הסוכן האישי שלך לדילים.\n"
    "מצאתם מלון? רגע לפני שמזמינים – שלחו לי אותו ואבדוק איפה הוא זול יותר.\n\n"
    "<b>3 דרכים:</b>\n"
    "📸 צילום מסך של דף המלון, מכל אתר (הכי מדויק – אני רואה את המחיר שלכם)\n"
    "🔗 לינק מכל אתר הזמנות: Booking, Agoda, Trip.com, Expedia (כפתור <b>שיתוף</b>)\n"
    "✍️ שם מלון + תאריכים, למשל:\n<code>דן תל אביב 12/11 - 15/11 2 מבוגרים</code>\n\n"
    "/watches – ההתראות שלי"
)
NOT_FOUND = (
    "לא הצלחתי לזהות מלון 🤔\n"
    "שלחו צילום מסך של דף המלון, לינק מאתר ההזמנות, או שם מלון + תאריכים, למשל:\n"
    "<code>הילטון אילת 3/12 - 6/12</code>"
)
LINK_UNREADABLE = ("לא הצלחתי לקרוא את הלינק הזה 🤔\n"
                   "שלחו צילום מסך של דף המלון, או כתבו שם מלון + תאריכים, ואבדוק.")
AIRBNB = ("דירות Airbnb הן בדרך כלל נכס פרטי שמופיע רק שם, אז אין עם מה להשוות 🏠\n"
          "מלון? שלחו לי אותו ואבדוק איפה הוא זול יותר.")
READING_LINK = "🔗 קורא את הלינק..."
NO_VISION = "כרגע אני עוד לא קורא צילומי מסך 🙈\nשלחו לינק מאתר ההזמנות, או שם מלון + תאריכים."
READING = "🔍 קורא את הצילום..."
SCREENSHOT_FAIL = ("לא הצלחתי לזהות מלון בצילום 🤔\n"
                   "נסו צילום של דף המלון שרואים בו את השם, התאריכים והמחיר.")
ASK_DATES = "לא ראיתי תאריכים בצילום. באיזה תאריכים? למשל <code>12/11 - 15/11</code>"
ASK_PRICE_HINT = "כתבו רק מספר, למשל 950 – או לחצו דלג."
ASK_FEEDBACK = "איזה מחיר ראיתם באתר? כתבו רק את המספר (סה\"כ לשהות) 🙏"
FEEDBACK_THANKS = "תודה! 🙏 זה עוזר לי לדייק את ההשוואות."
NO_WATCHES = "אין לכם התראות פעילות. אחרי בדיקת מלון אפשר ללחוץ 🔔 כדי לקבל עדכון אם המחיר יורד."
WATCH_STOPPED = "ההתראה בוטלה ✅"


def dmy(iso: str | None) -> str | None:
    """'2026-11-25' -> '25.11'"""
    if not iso or len(iso) < 10:
        return iso
    return f"{iso[8:10]}.{iso[5:7]}"


def ils(v: float | None) -> str:
    return f"₪{v:,.0f}" if v is not None else "—"


def header(q: HotelQuery) -> str:
    lines = [f"🏨 <b>{q.name}</b>" + (f" · מ-{q.site}" if q.site else "")]
    if q.has_dates:
        lines.append(f"📅 {q.checkin:%d.%m} – {q.checkout:%d.%m} ({q.nights} לילות) · {q.adults} מבוגרים")
    else:
        lines.append("📅 בלי תאריכים – כדאי לשלוח עם תאריכים כדי לקבל השוואה מדויקת")
    return "\n".join(lines)


def _btn(label: str, q: HotelQuery) -> str:
    src = (q.site or "").lower().replace("hotels.com", "expedia")
    mine = bool(src) and (label.lower() in src or src in label.lower())
    return f"🔎 {label}" + (" (האתר שלכם)" if mine else "")


def ask_price(q: HotelQuery) -> tuple[str, InlineKeyboardMarkup]:
    text = header(q) + "\n\n💬 כמה זה עולה לכם שם? (סה\"כ לכל השהות)\n" + ASK_PRICE_HINT
    return text, InlineKeyboardMarkup([[InlineKeyboardButton("דלג ⏭", callback_data="skip")]])


def result(q: HotelQuery, user_price: float | None, user_currency: str | None, v: Verdict,
           qid: int, alerts_enabled: bool, private: bool = True) -> tuple[str, InlineKeyboardMarkup]:
    lines = [header(q)]
    if user_price:
        shown = ils(user_price) if (user_currency or "ILS") == "ILS" else f"{user_price:,.0f} {user_currency}"
        lines.append(f"💰 המחיר שלכם: {shown}")
    lines.append("")

    if v.kind == "cheaper":
        lines.append(f"🟢 <b>נמצא זול יותר ב-{v.best.site}: {ils(v.best.total)}</b>")
        lines.append(f"חיסכון של {ils(v.saving)} ({v.saving_pct}%)")
    elif v.kind == "good":
        lines.append("🟡 המחיר שלכם טוב – לא מצאנו חיסכון משמעותי (מעל 5%).")
    elif v.kind == "no_anchor":
        lines.append(f"💡 הכי זול שמצאנו: {v.best.site} – {ils(v.best.total)}")
    else:
        lines.append("בדקו את אותו מלון באתרים האחרים 👇")

    if v.offers:
        lines.append("\nהמחירים נבדקו עכשיו מישראל, כולל מיסים. מחוברים לחשבון באתר? ייתכן שתראו אפילו פחות.")
    lines += ["", DISCLOSURE]

    rows = []
    if v.offers:
        seen = set()
        for o in v.offers:
            if is_trip(o.site):
                continue
            url = o.url or link_for_site(o.site, q)
            if url:
                rows.append([InlineKeyboardButton(f"🔎 {o.site} · {ils(o.total)}", url=url)])
                seen.add(o.site.lower())
        for label, url in comparison_links(q, include_trip=False):
            if label.lower() not in seen:
                rows.append([InlineKeyboardButton(_btn(label, q), url=url)])
    else:
        rows += [[InlineKeyboardButton(_btn(label, q), url=url)] for label, url in comparison_links(q)]

    if private:  # alerts and feedback are personal - not offered inside group chats
        if alerts_enabled and q.has_dates:
            rows.append([InlineKeyboardButton("🔔 עדכנו אותי אם המחיר יורד", callback_data=f"watch:{qid}")])
        rows.append([InlineKeyboardButton("📝 דווחו מה המחיר שראיתם", callback_data=f"fb:{qid}")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def watch_added(q: HotelQuery) -> str:
    return (f"🔔 מעולה! אבדוק את המחיר של <b>{q.name}</b> מדי כמה שעות ואעדכן אם הוא יורד.\n"
            "/watches – לרשימת ההתראות")


def confirmation_watch(q: HotelQuery, price: float | None, deadline: str | None) -> str:
    until = f"עד {dmy(deadline)}" if deadline else "עד הצ'ק-אין"
    return (f"✅ קיבלתי את ההזמנה ב-<b>{q.name}</b>"
            + (f", ששילמתם {ils(price)}" if price else "") + ".\n"
            f"אבדוק את המחיר מדי כמה שעות {until}. אם הוא יורד ביותר מ-5% – אכתוב לכם, "
            "כדי שתוכלו להזמין מחדש בזול ולבטל את הישנה. 🔔")


def confirmation_not_ready(q: HotelQuery) -> str:
    return (f"✅ זיהיתי אישור הזמנה ב-<b>{q.name}</b>.\n"
            "מעקב מחיר אחרי הזמנה עוד לא פעיל – כרגע אפשר לשלוח לי מלון לפני שמזמינים ואבדוק איפה זול יותר.")


def price_alert(q: HotelQuery, kind: str, site: str, now: float, was: float,
                deadline: str | None) -> str:
    base = (f"📉 <b>המחיר ירד!</b>\n{header(q)}\n\n"
            f"עכשיו {ils(now)} ב-{site} (היה {ils(was)}) – חיסכון של {ils(was - now)}")
    if kind == "post":
        base += ("\n\nאם ההזמנה שלכם עם ביטול חינם" + (f" (עד {dmy(deadline)})" if deadline else "") +
                 ", אפשר להזמין מחדש בזול.\n⚠️ קודם מזמינים את החדשה, ורק אחרי שהיא מאושרת – מבטלים את הישנה.")
    return base


def watches_list(rows) -> tuple[str, InlineKeyboardMarkup | None]:
    if not rows:
        return NO_WATCHES, None
    lines, kb = ["🔔 <b>ההתראות שלכם:</b>"], []
    for i, w in enumerate(rows, 1):
        q = HotelQuery.from_dict(json.loads(w["query_json"]))
        tag = "אחרי הזמנה" if w["kind"] == "post" else "לפני הזמנה"
        dates = f"{q.checkin:%d.%m}–{q.checkout:%d.%m}" if q.has_dates else ""
        lines.append(f"{i}. {q.name} {dates} · {tag} · בסיס {ils(w['baseline'])}")
        kb.append([InlineKeyboardButton(f"❌ בטל {i}", callback_data=f"stop:{w['id']}")])
    return "\n".join(lines), InlineKeyboardMarkup(kb)
