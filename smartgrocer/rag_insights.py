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
Group J): it can explain "why" using facts this system already has to
hand, in a fixed handful of sentence shapes, but it can't answer an
open-ended question typed in free text the way a full LLM-backed RAG
system would - and it needs no API key, billing account, or internet
connection to work at all, which keeps it consistent with Objective 6's
other half (the POS staying usable offline). README "Extending" notes
exactly where a real LLM call could be substituted for just the
generation step later (the retrieval step - the actual "what's relevant
right now" logic - would not need to change).
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import promotions as promotions_module
from . import reports as reports_module
from . import weather_sl
from .calendar_sl import FESTIVALS

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
        names = ", ".join(r["name_en"] for r in context["low_stock"][:5])
        more = f" and {len(context['low_stock']) - 5} more" if len(context["low_stock"]) > 5 else ""
        cards.append({
            "kind": "stock",
            "text": f"{len(context['low_stock'])} product(s) are below their reorder level: {names}{more}.",
        })

    if not cards:
        cards.append({"kind": "ok", "text": "No festivals, weather effects, expiring stock or low-stock "
                                             "alerts need attention right now."})
    return cards


def dashboard_insights(conn: sqlite3.Connection, as_of: date | None = None) -> list[dict]:
    """The one call the Dashboard screen needs: retrieve, then generate."""
    return generate(retrieve(conn, as_of))
