"""
SmartGrocer - supplier master records.

Matches the "Supplier" master-data screen in the sample POS system: a
simple named list that a Goods Received Note (GRN / stock receipt) can be
attributed to, so a shop owner can later ask "how much stock, and how much
did I spend, with each supplier" instead of GRNs floating with no source.
"""

from __future__ import annotations

import sqlite3


def add_supplier(conn: sqlite3.Connection, name: str, phone: str = "", address: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO suppliers (name, phone, address) VALUES (?,?,?)",
        (name.strip(), phone.strip(), address.strip()),
    )
    conn.commit()
    return cur.lastrowid


def update_supplier(conn: sqlite3.Connection, supplier_id: int, name: str, phone: str, address: str) -> None:
    conn.execute(
        "UPDATE suppliers SET name=?, phone=?, address=? WHERE id=?",
        (name.strip(), phone.strip(), address.strip(), supplier_id),
    )
    conn.commit()


def list_suppliers(conn: sqlite3.Connection, active_only: bool = True) -> list[sqlite3.Row]:
    q = "SELECT * FROM suppliers"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY name"
    return conn.execute(q).fetchall()


def get_supplier(conn: sqlite3.Connection, supplier_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()


def supplier_summary(conn: sqlite3.Connection) -> list[dict]:
    """Batches received and total cost value received, per supplier - the
    kind of thing an owner asks when deciding who to keep ordering from."""
    rows = conn.execute(
        """SELECT s.name, COUNT(b.id) AS batches_received,
                  COALESCE(SUM(b.qty_received),0) AS qty_received,
                  COALESCE(SUM(b.qty_received * b.cost_price),0) AS value_received
           FROM suppliers s LEFT JOIN stock_batches b ON b.supplier_id = s.id
           WHERE s.active=1 GROUP BY s.id ORDER BY value_received DESC"""
    ).fetchall()
    return [dict(r) for r in rows]
