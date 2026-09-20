"""
SmartGrocer - lightweight RAG (Retrieval-Augmented Generation) insights for
the Dashboard screen (Objective 6's third piece - the PPRS plan's scoping
decision: "a lightweight RAG layer for the dashboard, not an autonomous
internet-monitoring agent").

What "RAG" means here, deliberately: RETRIEVAL against a small, curated,
entirely local knowledge base assembled fresh on every call -
 - the festival calendar (calendar_sl.py) - is a festival coming up soon?
 - the weather category, logged or climatology-estimated (weather_sl.py) -
   what's today's likely condition, and which product categories respond
   to it?
 - the shop's own current state - low stock (reports.low_stock_alert),
   near-expiry batches (promotions.compute_promotions, already sorted by
   urgency) - both of which already exist and are cheap to query, so this
   adds no new forecasting/database cost of its own.
followed by GENERATION as plain template-filling over whatever was
retrieved - NOT a call to an external large language model. This is a
genuine, citable scope trade-off (see Brown/Roman/Devereux's RAG survey,
Group J): it needs no API key, billing account, or internet connection to
work at all, which keeps it consistent with Objective 6's other half (the
POS staying usable offline). README "Extending" notes exactly where a
real LLM call could be substituted for just the generation step later
(the retrieval step - the actual "what's relevant right now" logic -
would not need to change).

answer_question() extends the same retrieval-then-template approach to a
typed, free-text question, for one specific reason: the shop owner this
was built for is not comfortable with technical software, so the Dashboard
now has a plain "ask a question" box rather than expecting them to learn
where "Best Sellers" or "Promotions & Expiry" live in the menu. It still
does NOT understand arbitrary English - it matches the question against a
short list of plain-language KEYWORDS for the handful of things an owner
actually asks (what to buy, what's selling fast, what to put on offer),
retrieves the same cheap, already-existing data those screens use, and
fills in one of a few sentence templates written in plain words on
purpose - no "urgency score", "SARIMA", "forecast", or any other term
from the rest of this codebase, because the person reading the answer
never needs to know any of that exists. A question that matches nothing
gets today's dashboard insights instead of a dead-end "I don't
understand", plus a reminder of what it does know how to answer.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import promotions as promotions_module
from . import reports as reports_module
from . import weather_sl
from .calendar_sl import FESTIVALS

# Order matters: checked top to bottom, first matching intent wins (so a
# question mentioning both "buy" and "promotion" still gets one clear
# answer rather than a merged, confusing one). Every phrase is something a
# shop owner might plausibly type or say out loud, not a technical term.
_INTENT_KEYWORDS = [
    ("promotion", ["promotion", "promo", "discount", "offer", "on sale", "clearance",
                    "mark down", "markdown", "reduce price", "reduce the price"]),
    ("fast_moving", ["fast moving", "fast-moving", "moving fast", "best seller", "bestseller",
                       "best selling", "selling well", "selling fast", "popular", "top selling",
                       "top seller", "what sells", "what's selling", "whats selling"]),
    ("purchase", ["what to buy", "what should i buy", "need to buy", "need to purchase",
                    "what to purchase", "what to order", "need to order", "restock",
                    "stock up", "running out", "running low", "low stock", "what to get",
                    "shopping list", "what do i need"]),
]

FESTIVAL_LOOKAHEAD_DAYS = 21  # how far ahead a festival counts as "coming up" for the insight card


def _next_festival_occurrences(as_of: date) -> list[dict]:
    """Every festival's NEXT occurrence from as_of (today or later, never a
    date that already passed this year), soonest first."""
    out = []
    for month, day, _half_width, _peak, name in FESTIVALS:
        for year in (as_of.year, as_of.year + 1):
            try:
                occurrence = date(year, month, day)
            except ValueError:
                continue
            if occurrence >= as_of:
                out.append({"name": name, "date": occurrence, "days_until": (occurrence - as_of).days})
                break
    out.sort(key=lambda f: f["days_until"])
    return out


def retrieve(conn: sqlite3.Connection, as_of: date | None = None) -> dict:
    """The RETRIEVAL half: gathers whatever's actually relevant right now
    into one plain dict, so it's easy to unit-test independently of the
    sentence-generation step below."""
    as_of = as_of or date.today()
    upcoming_festivals = [
        f for f in _next_festival_occurrences(as_of) if f["days_until"] <= FESTIVAL_LOOKAHEAD_DAYS
    ]
    weather_today = weather_sl.weather_category(conn, as_of)
    weather_sensitive = weather_sl.WEATHER_SENSITIVE_CATEGORIES.get(weather_today, {})
    low_stock = reports_module.low_stock_alert(conn)
    urgent_batches = [b for b in promotions_module.compute_promotions(conn, as_of=as_of)
                       if b["alert_tier"] is not None]
    return {
        "as_of": as_of,
        "upcoming_festivals": upcoming_festivals,
        "weather_today": weather_today,
        "weather_sensitive_categories": weather_sensitive,
        "low_stock": low_stock,
        "urgent_batches": urgent_batches,
    }


def generate(context: dict) -> list[dict]:
    """The GENERATION half: fixed sentence templates filled in from
    `retrieve()`'s output. Returns a list of {"kind", "text"} cards, most
    actionable first - kind lets the GUI pick an icon/color, text is the
    plain-language explanation."""
    cards: list[dict] = []

    for f in context["upcoming_festivals"]:
        when = "today" if f["days_until"] == 0 else (
            "tomorrow" if f["days_until"] == 1 else f"in {f['days_until']} days"
        )
        cards.append({
            "kind": "festival",
            "text": (f"{f['name']} is {when} ({f['date'].strftime('%d %b')}) - demand typically rises "
                     "beforehand, especially for Snacks & Confectionery, Beverages, Bread & Bakery and "
                     "Dairy. Consider building stock ahead of it."),
        })

    if context["weather_sensitive_categories"]:
        cats = ", ".join(context["weather_sensitive_categories"])
        cards.append({
            "kind": "weather",
            "text": (f"Today's weather is {context['weather_today']} - {cats} demand tends to move with "
                     f"this. Log the actual weather on the Forecasting screen if this estimate looks wrong; "
                     "logged days make the weather-aware forecast more accurate than the climatology guess."),
        })

    if context["urgent_batches"]:
        top = context["urgent_batches"][:3]
        names = ", ".join(f"{b['name']} ({b['days_to_expiry']}d)" for b in top)
        cards.append({
            "kind": "expiry",
            "text": (f"{len(context['urgent_batches'])} batch(es) need attention soon, most urgently: "
                     f"{names}. See the Promotions & Expiry screen for suggested discounts."),
        })

    if context["low_stock"]:
        n = len(context["low_stock"])
        if n > 200:
            # Almost certainly a freshly-imported real item list (see
            # import_products.py) with no stock received yet, not 200+
            # items that have genuinely sold out - naming them all would
            # bury the other cards for no benefit.
            text = (f"Stock levels haven't been recorded for most of your {n} items yet - this "
                     "will become accurate as you receive stock for each one.")
        else:
            names = ", ".join(r["name_en"] for r in context["low_stock"][:5])
            more = f" and {n - 5} more" if n > 5 else ""
            text = f"{n} product(s) are below their reorder level: {names}{more}."
        cards.append({"kind": "stock", "text": text})

    if not cards:
        cards.append({"kind": "ok", "text": "No festivals, weather effects, expiring stock or low-stock "
                                             "alerts need attention right now."})
    return cards


def dashboard_insights(conn: sqlite3.Connection, as_of: date | None = None) -> list[dict]:
    """The one call the Dashboard screen needs: retrieve, then generate."""
    return generate(retrieve(conn, as_of))


def _classify_question(question: str) -> str | None:
    """Match a free-text question against the plain-language phrases in
    _INTENT_KEYWORDS. Returns None (not a "don't know" error, just "no
    match") when nothing fits - answer_question() has a friendly fallback
    for exactly that case."""
    q = (question or "").lower()
    for intent, phrases in _INTENT_KEYWORDS:
        if any(phrase in q for phrase in phrases):
            return intent
    return None


def _answer_purchase(conn: sqlite3.Connection) -> str:
    """'What do I need to buy?' - reuses the same fast, plain SQL query the
    Inventory screen's low-stock flag already runs (reports.low_stock_alert),
    NOT the forecasting-based purchase list (Forecasting screen's "Generate
    Purchase List" button) - that one fits a product with a real, expensive
    forecast per item, deliberately run in the background because it can
    take a while (see gui/screens.py's run_purchase_list). A typed question
    should answer back in an instant, so this sticks to what's already
    cheap to compute; the heavier, more precise report is still there for
    when someone wants it."""
    low = reports_module.low_stock_alert(conn)
    if not low:
        return ("Nothing is low right now - you don't need to buy anything urgently today. "
                 "Check again after a busy day of selling.")
    # A shop that just imported its real item list (import_products.py) has
    # zero recorded stock for almost everything until items are actually
    # received - "everything is low" is technically true but useless to
    # read as a list, so this says the more helpful true thing instead.
    if len(low) > 200:
        return (
            f"Stock levels haven't been recorded for most of your {len(low)} items yet, so I "
            "can't tell you what's really running low. As you receive stock for an item "
            "(Inventory -> Add / Scan Item), its stock level here becomes accurate - ask me "
            "again once you've received a few deliveries."
        )
    low_sorted = sorted(low, key=lambda r: r["on_hand"] - r["reorder_level"])
    shown = low_sorted[:8]
    lines = [f"- {r['name_en']}: only {r['on_hand']:.0f} left (usually keep at least "
             f"{r['reorder_level']:.0f})" for r in shown]
    more = f"\n...and {len(low) - 8} more item(s) are also low." if len(low) > 8 else ""
    return "These items are running low - it's a good time to buy more of them:\n" + "\n".join(lines) + more


def _answer_fast_moving(conn: sqlite3.Connection) -> str:
    """'What's fast moving?' - the last 30 days of real sales, by quantity sold."""
    top = reports_module.best_sellers(conn, days=30, top_n=10)
    if not top:
        return ("There isn't enough recent selling history yet to say what's moving fast. "
                 "Check again after a few more days of sales.")
    lines = [f"{i}. {r['name_en']} - {r['qty']:.0f} sold in the last 30 days"
             for i, r in enumerate(top[:8], start=1)]
    return "Your fastest-moving items in the last 30 days:\n" + "\n".join(lines)


def _answer_promotion(conn: sqlite3.Connection, as_of: date) -> str:
    """'What promotion should I give?' - the same urgency ranking the
    Promotions & Expiry screen shows, in plain words instead of scores."""
    urgent = [b for b in promotions_module.compute_promotions(conn, as_of=as_of) if b["alert_tier"] is not None]
    if not urgent:
        return ("Nothing needs a promotion right now - nothing is close to its expiry date "
                 "or selling slowly enough to need a discount.")
    lines = []
    for b in urgent[:5]:
        if b["days_to_expiry"] < 0:
            when = "has already passed its expiry date"
        elif b["days_to_expiry"] == 0:
            when = "expires today"
        else:
            when = f"expires in {b['days_to_expiry']} day(s)"
        lines.append(
            f"- {b['name']}: {when} and isn't selling quickly. Drop the price to about "
            f"LKR {b['suggested_price']:,.2f} (around {b['suggested_discount_pct']:.0f}% off) to clear it."
        )
    return "The best item(s) to put on promotion right now:\n" + "\n".join(lines)


def answer_question(conn: sqlite3.Connection, question: str, as_of: date | None = None) -> dict:
    """Answer a free-text question typed (or read aloud and typed by staff)
    by the shop owner, in plain, simple language - see this module's
    docstring for why this exists and what it deliberately does and
    doesn't understand.

    Returns {"intent", "text"}: intent is "purchase" / "fast_moving" /
    "promotion" when the question was recognised, or None when it wasn't
    (in which case `text` falls back to today's dashboard insights plus a
    reminder of the questions this can actually answer)."""
    as_of = as_of or date.today()
    intent = _classify_question(question)
    if intent == "purchase":
        text = _answer_purchase(conn)
    elif intent == "fast_moving":
        text = _answer_fast_moving(conn)
    elif intent == "promotion":
        text = _answer_promotion(conn, as_of)
    else:
        cards = generate(retrieve(conn, as_of))
        text = (
            "I'm not sure exactly what you're asking, so here's what needs attention today instead:\n\n"
            + "\n\n".join(c["text"] for c in cards)
            + "\n\nYou can also ask things like: 'what do I need to buy?', 'what's fast moving?', "
              "or 'what promotion should I give?'"
        )
    return {"intent": intent, "text": text}
