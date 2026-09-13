"""
SmartGrocer - staff (cashier/admin) management.

Staff rows are never deleted, only deactivated - invoices.staff_id references
them, so removing a row would either break that reference or silently orphan
historical sales figures against a name nobody can see anymore. Deactivating
just hides someone from the login list once they've left, while every past
invoice/cash session they rang up still shows their name in reports.
"""

from __future__ import annotations

import sqlite3


def list_staff(conn: sqlite3.Connection, active_only: bool = False) -> list[sqlite3.Row]:
    query = "SELECT * FROM staff"
    if active_only:
        query += " WHERE active=1"
    query += " ORDER BY active DESC, name"
    return conn.execute(query).fetchall()


def add_staff(conn: sqlite3.Connection, name: str, role: str, pin: str) -> int:
    if role not in ("admin", "cashier"):
        raise ValueError("role must be 'admin' or 'cashier'")
    if not pin or not pin.strip():
        raise ValueError("PIN cannot be blank")
    cur = conn.execute(
        "INSERT INTO staff (name, role, pin, active) VALUES (?,?,?,1)",
        (name.strip(), role, pin.strip()),
    )
    conn.commit()
    return cur.lastrowid


def update_pin(conn: sqlite3.Connection, staff_id: int, new_pin: str) -> None:
    if not new_pin or not new_pin.strip():
        raise ValueError("PIN cannot be blank")
    conn.execute("UPDATE staff SET pin=? WHERE id=?", (new_pin.strip(), staff_id))
    conn.commit()


def set_active(conn: sqlite3.Connection, staff_id: int, active: bool) -> None:
    conn.execute("UPDATE staff SET active=? WHERE id=?", (1 if active else 0, staff_id))
    conn.commit()
