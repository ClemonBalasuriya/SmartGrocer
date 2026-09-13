"""
SmartGrocer - staff (owner/admin/cashier) management.

Staff rows are never deleted, only deactivated - invoices.staff_id references
them, so removing a row would either break that reference or silently orphan
historical sales figures against a name nobody can see anymore. Deactivating
just hides someone from the login list once they've left, while every past
invoice/cash session they rang up still shows their name in reports.

Three roles, each a superset of the one before: 'cashier' can ring up
sales; 'admin' can additionally manage staff (add, reset PINs, activate/
deactivate); 'owner' can do everything admin can, plus see the Activity
Log (audit.py) of every sensitive action any admin has taken. Who is
allowed to CREATE an 'owner' account is a policy decision (only an
existing owner should be able to promote a new one, except when
bootstrapping a shop that has none yet) - that's enforced in the GUI
(gui/screens.py's StaffScreen), not here, matching how "only admin/owner
can manage staff at all" is also a GUI-level check rather than a database
constraint.
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
    if role not in ("owner", "admin", "cashier"):
        raise ValueError("role must be 'owner', 'admin', or 'cashier'")
    if not name or not name.strip():
        raise ValueError("Name cannot be blank")
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
