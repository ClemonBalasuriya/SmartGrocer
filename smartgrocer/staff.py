"""
SmartGrocer - staff (owner/admin/cashier) management.

Staff rows are never deleted, only deactivated - invoices.staff_id references
them, so removing a row would either break that reference or silently orphan
historical sales figures against a name nobody can see anymore. Deactivating
just hides someone from the login list once they've left, while every past
invoice/cash session they rang up still shows their name in reports.

Three roles, each a superset of the one before: 'cashier' can ring up
sales; 'admin' can additionally add staff, add/edit catalog items, and
activate/deactivate staff; 'owner' can do everything admin can, plus reset
anyone's PIN, remove a catalog item, and view the Activity Log (audit.py)
of every sensitive action any admin has taken. Who is allowed to CREATE an
'owner' account is a policy decision (only an existing owner should be
able to promote a new one, except when bootstrapping a shop that has none
yet) - that's enforced in the GUI (gui/screens.py's StaffScreen), not
here, matching how all of the above role checks are GUI-level rather than
database constraints.

Every account - cashier, admin, or owner alike - can also change their OWN
PIN at any time (knowing their current one), and reset it themselves via
email if they forget it (see generate_temp_pin below and gui/app.py's
"Change My PIN" / "Forgot PIN?" dialogs) - that isn't role-gated at all,
since it only ever touches the logged-in person's own account.
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


def set_active(conn: sqlite3.Connection, staff_id: int, active: bool) -> None:
    conn.execute("UPDATE staff SET active=? WHERE id=?", (1 if active else 0, staff_id))
    conn.commit()


def generate_temp_pin(length: int = 6) -> str:
    """A random numeric PIN for the "forgot PIN" email-reset flow - long
    enough (6 digits, drawn with secrets rather than random so it isn't
    guessable) that it's impractical to guess before the real owner reads
    their email, without being awkward to type in at a till."""
    return "".join(secrets.choice("0123456789") for _ in range(length))
