"""
SmartGrocer - staff (owner/admin/cashier) management.

Staff rows are never deleted, only deactivated - invoices.staff_id references
them, so removing a row would either break that reference or silently orphan
historical sales figures against a name nobody can see anymore. Deactivating
just hides someone from the login list once they've left, while every past
invoice/cash session they rang up still shows their name in reports.

Three roles, each a superset of the one before: 'cashier' can ring up
sales; 'admin' can additionally add staff, add/edit catalog items, and
activate/deactivate staff; 'owner' can do everything admin can, plus
remove a catalog item and view the Activity Log (audit.py) of every
sensitive action any admin has taken.

There is deliberately exactly ONE 'owner' account, ever, for the whole
shop - db.py seeds it automatically (PIN 1234, changeable via "Change My
PIN") so it always exists from the very first run, add_staff below refuses
to create a second one (enforced here, not just left to the GUI, since
this is the one role invariant worth protecting at the data layer too),
and set_active refuses to deactivate it - there is never a moment with no
Owner at all, and never ambiguity about which account IS the Owner.

Nobody - not even the Owner - can set or see another account's PIN
directly anymore (an earlier version let an Admin/Owner force-reset
someone else's PIN, which is exactly the kind of thing a compromised
Admin/Owner account could abuse, so it was removed). Every account -
cashier, admin, or owner alike - can only change their OWN PIN, either
directly (knowing their current one) or, if they've forgotten it, via a
random PIN emailed/texted to the contact info on file (see
generate_temp_pin below and gui/app.py's "Change My PIN" / "Forgot PIN?"
dialogs) - neither of those is role-gated, since both only ever touch the
logged-in (or PIN-recovering) person's own account. An Admin/Owner can
still update_contact() someone else's saved email/phone (e.g. to give a
person who wasn't set up with one a way to recover their own PIN later) -
that's the one indirect lever left, and it still never reveals or sets
their PIN itself.
"""

from __future__ import annotations

import secrets
import sqlite3


def list_staff(conn: sqlite3.Connection, active_only: bool = False) -> list[sqlite3.Row]:
    query = "SELECT * FROM staff"
    if active_only:
        query += " WHERE active=1"
    query += " ORDER BY active DESC, name"
    return conn.execute(query).fetchall()


def add_staff(
    conn: sqlite3.Connection,
    name: str,
    role: str,
    pin: str,
    email: str | None = None,
    phone: str | None = None,
) -> int:
    """email/phone are optional and only used so notifications.py can message
    this person directly (e.g. "your PIN was changed") rather than only the
    shop Owner - leave either blank if this person doesn't need that."""
    if role not in ("owner", "admin", "cashier"):
        raise ValueError("role must be 'owner', 'admin', or 'cashier'")
    if role == "owner" and conn.execute("SELECT 1 FROM staff WHERE role='owner' LIMIT 1").fetchone():
        raise ValueError("An Owner account already exists - only one Owner is allowed")
    if not name or not name.strip():
        raise ValueError("Name cannot be blank")
    if not pin or not pin.strip():
        raise ValueError("PIN cannot be blank")
    email = (email or "").strip() or None
    phone = (phone or "").strip() or None
    if email is not None and "@" not in email:
        raise ValueError("Email address doesn't look valid")
    cur = conn.execute(
        "INSERT INTO staff (name, role, pin, active, email, phone) VALUES (?,?,?,1,?,?)",
        (name.strip(), role, pin.strip(), email, phone),
    )
    conn.commit()
    return cur.lastrowid


def update_pin(conn: sqlite3.Connection, staff_id: int, new_pin: str) -> None:
    if not new_pin or not new_pin.strip():
        raise ValueError("PIN cannot be blank")
    conn.execute("UPDATE staff SET pin=? WHERE id=?", (new_pin.strip(), staff_id))
    conn.commit()


def update_contact(conn: sqlite3.Connection, staff_id: int, email: str | None, phone: str | None) -> None:
    """Update the email/phone saved for a staff member - lets an Admin/Owner
    give someone a way to recover their own PIN later (via "Forgot PIN?")
    if they weren't set up with contact info when added. Never touches the
    PIN itself - nobody sets or sees another account's PIN, by design (see
    the module docstring)."""
    email = (email or "").strip() or None
    phone = (phone or "").strip() or None
    if email is not None and "@" not in email:
        raise ValueError("Email address doesn't look valid")
    conn.execute("UPDATE staff SET email=?, phone=? WHERE id=?", (email, phone, staff_id))
    conn.commit()


def set_active(conn: sqlite3.Connection, staff_id: int, active: bool) -> None:
    if not active:
        row = conn.execute("SELECT role FROM staff WHERE id=?", (staff_id,)).fetchone()
        if row and row["role"] == "owner":
            raise ValueError("The Owner account cannot be deactivated")
    conn.execute("UPDATE staff SET active=? WHERE id=?", (1 if active else 0, staff_id))
    conn.commit()


def generate_temp_pin(length: int = 6) -> str:
    """A random numeric PIN for the "forgot PIN" email-reset flow - long
    enough (6 digits, drawn with secrets rather than random so it isn't
    guessable) that it's impractical to guess before the real owner reads
    their email, without being awkward to type in at a till."""
    return "".join(secrets.choice("0123456789") for _ in range(length))
