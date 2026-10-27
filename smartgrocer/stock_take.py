"""
SmartGrocer - stock take (physical count) and the shrinkage/variance report
that comes out of it.

The gap this closes: every other way stock leaves the system is already
logged somewhere (a sale, a return, a waste write-off, a supplier return),
so the system's own qty_remaining is only ever wrong because of something
that happened OFF the books - theft, breakage nobody logged, a miscount at
GRN time. The only way to catch that is to actually count the shelf and
compare, the same "Stock Take"/"Stock Verification" report every commercial
POS (including the sample system this was checked against) ships with.
This mirrors cash_drawer.py's day open/close shape - count, compare to what
the system expects, record the variance - one short session per category
rather than the whole shop at once, since that's how counting is actually
done in a small store.

A session is scoped to one category at a time (see start_session) so a
cashier can count "just Dairy today" without having to walk the whole shop
in one sitting. finish_session can optionally write the counted numbers
back to stock_batches straight away (apply_adjustments=True) - a shortfall
comes off the same batches a sale would draw from first (oldest/soonest to
expire), so a stock-take shortfall is treated exactly like an unlogged sale
or an unlogged waste event would have been; a surplus (counted MORE than
the system expected - a miscount somewhere earlier, most often) is added
back as one new batch via pos.receive_stock, same as a GRN would. Every
apply is written to the Activity Log (audit.py), same as every other
stock-affecting action in Inventory.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from . import audit
from . import pos as pos_module


def start_session(conn: sqlite3.Connection, staff_id: int, category: str, note: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO stock_takes (staff_id, category, started_at, note) VALUES (?,?,?,?)",
        (staff_id, category, datetime.now().isoformat(timespec="seconds"), note),
    )
    conn.commit()
    return cur.lastrowid


def products_for_count(conn: sqlite3.Connection, category: str) -> list[dict]:
    """Active products in this category with their current system on-hand -
    what the Stock Take dialog pre-fills each row with before the staff
    member overtypes the number they actually counted."""
    rows = conn.execute(
        """SELECT p.id AS product_id, p.code, p.name_en, p.cost_price,
                  COALESCE(SUM(b.qty_remaining),0) system_qty
           FROM products p LEFT JOIN stock_batches b ON b.product_id = p.id
           WHERE p.active=1 AND p.category=?
           GROUP BY p.id ORDER BY p.name_en""",
        (category,),
    ).fetchall()
    return [dict(r) for r in rows]


def save_counts(
    conn: sqlite3.Connection, stock_take_id: int, counts: list[tuple[int, float, float, float]],
) -> None:
    """counts: list of (product_id, system_qty, counted_qty, unit_cost) - the
    system_qty/unit_cost are passed in rather than re-queried, so the line
    records exactly what the staff member saw on screen at count time, even
    if a sale changes the live stock a moment later. Replaces any existing
    lines for this session (so re-saving the same dialog before Finish
    doesn't duplicate rows)."""
    conn.execute("DELETE FROM stock_take_lines WHERE stock_take_id=?", (stock_take_id,))
    for product_id, system_qty, counted_qty, unit_cost in counts:
        variance = round(counted_qty - system_qty, 3)
        conn.execute(
            """INSERT INTO stock_take_lines
               (stock_take_id, product_id, system_qty, counted_qty, variance, unit_cost, value_variance)
               VALUES (?,?,?,?,?,?,?)""",
            (stock_take_id, product_id, system_qty, counted_qty, variance, unit_cost,
             round(variance * unit_cost, 2)),
        )
    conn.commit()


def finish_session(
    conn: sqlite3.Connection, stock_take_id: int, *, apply_adjustments: bool,
    actor_staff_id: int, actor_name: str,
) -> dict:
    """Closes the session. When apply_adjustments is True, every line with a
    non-zero variance is written back to stock_batches (see module
    docstring for how); when False, the count is kept purely as a report -
    nothing about actual stock changes, for a shop that wants to review
    before committing to an adjustment."""
    lines = conn.execute(
        "SELECT * FROM stock_take_lines WHERE stock_take_id=?", (stock_take_id,)
    ).fetchall()

    applied_count = 0
    if apply_adjustments:
        for line in lines:
            variance = line["variance"]
            if variance == 0:
                continue
            if variance < 0:
                _deduct_shrinkage(conn, line["product_id"], -variance)
            else:
                pos_module.receive_stock(
                    conn, product_id=line["product_id"], qty=variance,
                    cost_price=line["unit_cost"],
                    batch_no=f"STOCKTAKE-ADJ-{datetime.now().date().isoformat()}",
                )
            applied_count += 1

    conn.execute(
        "UPDATE stock_takes SET finished_at=?, applied=? WHERE id=?",
        (datetime.now().isoformat(timespec="seconds"), 1 if apply_adjustments else 0, stock_take_id),
    )
    conn.commit()

    total_variance_value = round(sum(l["value_variance"] for l in lines), 2)
    shortfall_value = round(sum(l["value_variance"] for l in lines if l["value_variance"] < 0), 2)
    audit.record(
        conn, actor_staff_id=actor_staff_id, action="stock_take.finish",
        details=(
            f"stock_take#{stock_take_id}: {len(lines)} item(s) counted, "
            f"net variance LKR {total_variance_value:,.2f} (shortfall LKR {shortfall_value:,.2f}), "
            + ("adjustments applied to stock." if apply_adjustments else "recorded only, stock not adjusted.")
        ),
    )
    return {
        "lines_counted": len(lines),
        "lines_with_variance": sum(1 for l in lines if l["variance"] != 0),
        "total_variance_value": total_variance_value,
        "shortfall_value": shortfall_value,
        "adjustments_applied": applied_count,
    }


def _deduct_shrinkage(conn: sqlite3.Connection, product_id: int, shortfall_qty: float) -> None:
    """Removes shortfall_qty units of this product from stock_batches, same
    oldest/soonest-to-expire-first order pos.create_invoice draws a sale
    from - a stock-take shortfall is treated exactly like an unlogged sale
    or waste event would have been. Never takes a batch below zero; if the
    count found more missing than the system has batch records for at all
    (shouldn't normally happen), the unexplained remainder is simply left
    undeducted rather than going negative."""
    left = shortfall_qty
    batches = conn.execute(
        """SELECT id, qty_remaining FROM stock_batches WHERE product_id=? AND qty_remaining>0
           ORDER BY (expiry_date IS NULL), expiry_date, received_date""",
        (product_id,),
    ).fetchall()
    for b in batches:
        if left <= 0:
            break
        take = min(b["qty_remaining"], left)
        if take <= 0:
            continue
        conn.execute(
            "UPDATE stock_batches SET qty_remaining=qty_remaining-? WHERE id=?", (take, b["id"])
        )
        left -= take


def session_history(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        """SELECT st.id, st.category, st.started_at, st.finished_at, st.applied, s.name AS staff_name,
                  COUNT(l.id) AS lines_counted,
                  COALESCE(SUM(CASE WHEN l.variance != 0 THEN 1 ELSE 0 END),0) AS lines_with_variance,
                  COALESCE(SUM(l.value_variance),0) AS total_variance_value,
                  COALESCE(SUM(CASE WHEN l.value_variance < 0 THEN l.value_variance ELSE 0 END),0) AS shortfall_value
           FROM stock_takes st
           LEFT JOIN staff s ON s.id = st.staff_id
           LEFT JOIN stock_take_lines l ON l.stock_take_id = st.id
           GROUP BY st.id ORDER BY st.id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) | {
        "status": "Applied to stock" if r["applied"] else ("Recorded only" if r["finished_at"] else "In progress"),
    } for r in rows]


def session_lines(conn: sqlite3.Connection, stock_take_id: int) -> list[dict]:
    """Per-product detail for one session - the drill-down behind a
    session_history row."""
    rows = conn.execute(
        """SELECT p.name_en, l.system_qty, l.counted_qty, l.variance, l.unit_cost, l.value_variance
           FROM stock_take_lines l JOIN products p ON p.id = l.product_id
           WHERE l.stock_take_id=? ORDER BY ABS(l.value_variance) DESC""",
        (stock_take_id,),
    ).fetchall()
    return [dict(r) for r in rows]
