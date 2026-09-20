"""
Weather-category exogenous regressor for forecasting.py (Objective 6 -
"event/weather-aware forecasting"). Sri Lanka's monsoon cycle drives a
coarse but genuinely useful demand signal for a grocery shop - e.g. cold
beverages / bottled water sell more on hot, dry days; other categories
shift on wet days - so this supplies a simple rain/dry/hot category per
date, in two layers:

 1. `weather_log` table (see db.py) - a shop can log the ACTUAL observed
    category for a given date (a one-tap choice on the Forecasting screen,
    or a future weather-API integration - see README "Extending"). This is
    always preferred when present.
 2. `climatology_category(d)` - the fallback used for any date with no
    logged entry (most of history, and every future forecast date): a
    fixed monthly Sri Lankan monsoon climatology. This is intentionally
    coarse (like calendar_sl.py's fixed Vesak date - replace with your
    shop's real local pattern/weather-API feed once available), but it's
    enough to test whether a weather signal helps at all, which is the
    point of Objective 6's with-vs-without-regressor evaluation.

Categories are one of "rain", "dry", "hot" - deliberately just three, wide
regional categories (not temperature/rainfall figures) so a shop owner with
no weather-station access can log them from memory ("it rained most of that
week") and so a small amount of real data is quickly enough to matter.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import numpy as np
import pandas as pd

CATEGORIES = ("rain", "dry", "hot")

# Coarse Sri Lankan monsoon climatology, by month (1=Jan): Yala monsoon
# (~May-Sep) and the Maha-monsoon-adjacent Oct-Nov inter-monsoon bring the
# wettest spells island-wide; Dec-Jan (post-Maha) and Feb-Apr (the hot,
# dry inter-monsoon before Yala) are drier. This is a national-average
# approximation, not specific to any one shop's exact location - a shop in
# the wet zone vs. dry zone should override individual dates via
# `log_weather` rather than relying on this for real evaluation.
_CLIMATOLOGY_BY_MONTH = {
    1: "dry", 2: "hot", 3: "hot", 4: "hot",
    5: "rain", 6: "rain", 7: "rain", 8: "rain", 9: "rain",
    10: "rain", 11: "rain", 12: "dry",
}

# Which of the catalogue's grocery categories plausibly respond to which
# weather category - used only by data_generator.py to give the synthetic
# demo data a genuine (if modest) weather signal to discover, the same way
# FESTIVE_CATEGORIES gives it a festival signal to discover.
WEATHER_SENSITIVE_CATEGORIES = {
    "hot": {"Beverages": 1.3},
    "dry": {"Beverages": 1.1},
    "rain": {"Household": 1.1},
}


def climatology_category(d: date) -> str:
    return _CLIMATOLOGY_BY_MONTH[d.month]


def log_weather(conn: sqlite3.Connection, log_date: date, category: str, source: str = "manual") -> None:
    """Record the actual observed weather category for a date - overrides
    the climatology fallback for that date in every forecast run from now
    on. Safe to call again for the same date (replaces the earlier entry)."""
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}, got {category!r}")
    from datetime import datetime
    conn.execute(
        "INSERT INTO weather_log (log_date, category, source, logged_at) VALUES (?,?,?,?) "
        "ON CONFLICT(log_date) DO UPDATE SET category=excluded.category, source=excluded.source, "
        "logged_at=excluded.logged_at",
        (log_date.isoformat(), category, source, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def logged_categories(conn: sqlite3.Connection) -> dict[str, str]:
    """{'YYYY-MM-DD': category} for every date with a logged (non-climatology)
    entry - used to build the exogenous regressor without one query per date."""
    rows = conn.execute("SELECT log_date, category FROM weather_log").fetchall()
    return {r["log_date"]: r["category"] for r in rows}


def weather_category(conn: sqlite3.Connection, d: date) -> str:
    """Logged entry if one exists for this date, else the climatology
    fallback. Prefer `build_weather_exog` for a whole date range - it looks
    up the logged entries once instead of per-day."""
    logged = logged_categories(conn)
    return logged.get(d.isoformat(), climatology_category(d))


def build_weather_exog(conn: sqlite3.Connection, date_index: pd.DatetimeIndex) -> np.ndarray:
    """One column per category (is_rain, is_dry, is_hot), in CATEGORIES
    order - matches how build_festival_exog in forecasting.py returns a
    plain 0/1 SARIMAX exogenous column."""
    logged = logged_categories(conn)
    rows = []
    for ts in date_index:
        d = ts.date()
        cat = logged.get(d.isoformat(), climatology_category(d))
        rows.append([1.0 if cat == c else 0.0 for c in CATEGORIES])
    return np.array(rows, dtype=float)
