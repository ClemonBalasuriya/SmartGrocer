"""
SmartGrocer - promotion & expiry-waste-reduction engine (proposal Objective 2).

Ranks at-risk stock batches by a composite "urgency score"

    U = 0.5 * Se + 0.3 * Sv + 0.2 * Sm

as given in the proposal's methodology section. The proposal states the
weights but not the exact definition of each component score; the
definitions below are this implementation's operational choice - document
them (and justify or tune them) in the methodology chapter:

  Se (expiry score)   - how close the batch is to its expiry date, relative
                         to that product's own typical shelf life. 0 = just
                         received, 1 = at/after expiry.
  Sv (velocity score)  - how far *recent* sales velocity has fallen below the
                         product's *historical* average velocity. 0 = selling
                         at or above its usual pace, 1 = stalled completely.
  Sm (margin score)    - the product's profit margin, min-max normalised
                         against the rest of the active catalogue. Higher
                         margin = more room to discount deeply while staying
                         profitable, so it is weighted in (not against)
                         urgency to promote.

Every score is scaled to [0, 1] before weighting, so U itself is in [0, 1].
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta


def _margin_scores(conn: sqlite3.Connection) -> dict[int, float]:
    rows = conn.execute("SELECT id, cost_price, cash_price FROM products WHERE active=1").fetchall()
    margins = {r["id"]: (r["cash_price"] - r["cost_price"]) / r["cash_price"] if r["cash_price"] else 0
               for r in rows}
    if not margins:
        return {}
    lo, hi = min(margins.values()), max(margins.values())
    span = hi - lo
    return {pid: ((m - lo) / span if span > 1e-9 else 0.5) for pid, m in margins.items()}


def _velocity(conn: sqlite3.Connection, product_id: int, days: int, as_of: date) -> float:
    since = (as_of - timedelta(days=days)).isoformat()
    row = conn.execute(
        """SELECT COALESCE(SUM(ii.qty),0) q FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id
           WHERE ii.product_id=? AND i.voided=0 AND date(i.datetime) >= ? AND date(i.datetime) <= ?""",
        (product_id, since, as_of.isoformat()),
    ).fetchone()
    return row["q"] / days


def _typical_shelf_life(conn: sqlite3.Connection, product_id: int) -> float:
    row = conn.execute(
        """SELECT AVG(julianday(expiry_date) - julianday(received_date)) d FROM stock_batches
           WHERE product_id=? AND expiry_date IS NOT NULL""",
        (product_id,),
    ).fetchone()
    return row["d"] if row["d"] else 14.0


def compute_promotions(conn: sqlite3.Connection, as_of: date | None = None,
                        recent_window: int = 7, historical_window: int = 90) -> list[dict]:
    as_of = as_of or date.today()
    margin_scores = _margin_scores(conn)

    batches = conn.execute(
        """SELECT b.id batch_id, b.product_id, b.qty_remaining, b.expiry_date, b.cost_price,
                  p.code, p.name_en, p.name_si, p.cash_price
           FROM stock_batches b JOIN products p ON p.id = b.product_id
           WHERE b.qty_remaining > 0 AND b.expiry_date IS NOT NULL AND p.active=1"""
    ).fetchall()

    out = []
    for b in batches:
        expiry = date.fromisoformat(b["expiry_date"])
        days_to_expiry = (expiry - as_of).days
        shelf_life = _typical_shelf_life(conn, b["product_id"])
        se = max(0.0, min(1.0, 1 - max(days_to_expiry, 0) / shelf_life)) if shelf_life > 0 else 1.0
        if days_to_expiry <= 0:
            se = 1.0

        recent_v = _velocity(conn, b["product_id"], recent_window, as_of)
        hist_v = _velocity(conn, b["product_id"], historical_window, as_of)
        if hist_v <= 1e-6:
            sv = 1.0 if recent_v <= 1e-6 else 0.0
        else:
            sv = max(0.0, min(1.0, 1 - recent_v / hist_v))

        sm = margin_scores.get(b["product_id"], 0.5)
        urgency = 0.5 * se + 0.3 * sv + 0.2 * sm

        discount_pct = min(0.40, urgency * 0.45)
        floor_price = b["cost_price"] * 1.02
        proposed_price = b["cash_price"] * (1 - discount_pct)
        if proposed_price < floor_price and b["cash_price"] > 0:
            discount_pct = max(0.0, 1 - floor_price / b["cash_price"])
            proposed_price = b["cash_price"] * (1 - discount_pct)

        velocity_for_clearance = recent_v if recent_v > 1e-6 else hist_v
        clearance_date = (
            as_of + timedelta(days=int(-(-b["qty_remaining"] // max(velocity_for_clearance, 1e-6))))
            if velocity_for_clearance > 1e-6 else None
        )

        if days_to_expiry < 0:
            tier = "overdue"
        elif days_to_expiry <= 3:
            tier = "critical_3day"
        elif days_to_expiry <= 7:
            tier = "warning_7day"
        else:
            tier = None

        out.append({
            "batch_id": b["batch_id"], "product_id": b["product_id"], "code": b["code"],
            "name": b["name_en"], "name_si": b["name_si"], "qty_remaining": b["qty_remaining"],
            "expiry_date": b["expiry_date"], "days_to_expiry": days_to_expiry,
            "se": round(se, 3), "sv": round(sv, 3), "sm": round(sm, 3), "urgency": round(urgency, 3),
            "cash_price": b["cash_price"], "suggested_discount_pct": round(discount_pct * 100, 1),
            "suggested_price": round(proposed_price, 2),
            "projected_clearance_date": clearance_date.isoformat() if clearance_date else None,
            "alert_tier": tier,
        })

    out.sort(key=lambda r: -r["urgency"])
    return out


def tiered_alerts(conn: sqlite3.Connection, as_of: date | None = None) -> dict[str, list[dict]]:
    rows = compute_promotions(conn, as_of=as_of)
    buckets: dict[str, list[dict]] = {"overdue": [], "critical_3day": [], "warning_7day": []}
    for r in rows:
        if r["alert_tier"]:
            buckets[r["alert_tier"]].append(r)
    return buckets
