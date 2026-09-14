"""
SmartGrocer - manual promotional offers.

This is deliberately separate from promotions.py, which only ever
*computes suggested* discounts for stock at risk of expiring and never
touches a sale - see that module's own docstring. This one is the other
half of "some items give offers for loyalty customers only, sometimes all
customers": a staff member picks a product (or a whole category), a
discount percentage, who it's for, and optionally a date range, and the
POS screen applies it automatically at the till - no per-sale staff
action needed.

"Loyalty customers only" means exactly what customers.is_loyalty_member
means: any customer with their own registered account (the seeded
"Walk-in" customer used for anonymous cash sales is the only one that
never counts). See customers.py for that decision.

Where more than one offer could apply to the same line (e.g. a
product-specific offer and a category-wide one both matching, or two
overlapping category offers), the single biggest discount wins - a
customer should never end up paying more just because of how the offers
happened to be set up, and a cashier shouldn't have to work out by hand
which one "should" apply.
"""

from __future__ import annotations

import sqlite3
from datetime import date

SCOPE_ALL = "all"
SCOPE_LOYALTY = "loyalty"


def add_offer(
    conn: sqlite3.Connection,
    name: str,
    discount_pct: float,
    *,
    product_id: int | None = None,
    category: str | None = None,
    scope: str = SCOPE_ALL,
    start_date: str | None = None,
    end_date: str | None = None,
) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("Offer needs a name.")
    if product_id is None and not category:
        raise ValueError("Offer needs either a product or a category.")
    if product_id is not None and category:
        raise ValueError("Offer needs a product OR a category, not both.")
    if not (0 < discount_pct <= 100):
        raise ValueError("Discount % must be greater than 0 and at most 100.")
    if scope not in (SCOPE_ALL, SCOPE_LOYALTY):
        raise ValueError(f"Unknown scope: {scope}")
    cur = conn.execute(
        """INSERT INTO offers (name, product_id, category, discount_pct, scope, start_date, end_date, active)
           VALUES (?,?,?,?,?,?,?,1)""",
        (name, product_id, category, discount_pct, scope, start_date or None, end_date or None),
    )
    conn.commit()
    return cur.lastrowid


def set_active(conn: sqlite3.Connection, offer_id: int, active: bool) -> None:
    conn.execute("UPDATE offers SET active=? WHERE id=?", (1 if active else 0, offer_id))
    conn.commit()


def delete_offer(conn: sqlite3.Connection, offer_id: int) -> None:
    conn.execute("DELETE FROM offers WHERE id=?", (offer_id,))
    conn.commit()


def list_offers(conn: sqlite3.Connection, include_inactive: bool = True) -> list[sqlite3.Row]:
    q = """SELECT o.*, p.name_en AS product_name FROM offers o
           LEFT JOIN products p ON p.id = o.product_id"""
    if not include_inactive:
        q += " WHERE o.active=1"
    q += " ORDER BY o.id DESC"
    return conn.execute(q).fetchall()


def _in_window(row: sqlite3.Row, as_of: date) -> bool:
    today = as_of.isoformat()
    if row["start_date"] and today < row["start_date"]:
        return False
    if row["end_date"] and today > row["end_date"]:
        return False
    return True


def best_discount_pct(
    conn: sqlite3.Connection, product_id: int, category: str, is_loyalty: bool, as_of: date | None = None,
) -> tuple[float, str | None]:
    """The single largest currently-active discount % that applies to this
    product for this customer right now, and the offer name it came from
    (None, None if nothing applies). Re-checked at add-to-cart time AND
    again by the caller at checkout, so an offer that was switched off or
    ran past its end date in between simply stops applying rather than
    causing an error."""
    as_of = as_of or date.today()
    rows = conn.execute(
        "SELECT * FROM offers WHERE active=1 AND (product_id=? OR (category IS NOT NULL AND category=?))",
        (product_id, category),
    ).fetchall()
    best_pct, best_name = 0.0, None
    for r in rows:
        if r["scope"] == SCOPE_LOYALTY and not is_loyalty:
            continue
        if not _in_window(r, as_of):
            continue
        if r["discount_pct"] > best_pct:
            best_pct, best_name = r["discount_pct"], r["name"]
    return best_pct, best_name


def line_discount_amount(
    conn: sqlite3.Connection, product_id: int, category: str, qty: float, unit_price: float,
    is_loyalty: bool, as_of: date | None = None,
) -> tuple[float, str | None]:
    """Flat currency amount to take off a cart line of `qty` units at
    `unit_price` each, plus the offer name it came from (0.0, None if no
    offer applies) - the shape POSScreen stores per cart line and passes
    straight through as pos.CartLine.discount."""
    pct, name = best_discount_pct(conn, product_id, category, is_loyalty, as_of=as_of)
    if pct <= 0:
        return 0.0, None
    return round(qty * unit_price * pct / 100.0, 2), name
