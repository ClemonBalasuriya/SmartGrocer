"""
SmartGrocer - audit trail for sensitive staff/account actions.

The point of this module is a straight, tamper-evident-by-design answer to
"who did that": every time an Admin or Owner adds a staff member, resets a
PIN, or activates/deactivates someone, a row is written here recording who
performed the action, who it was done to, and when. Rows are never
updated or deleted through this module (there's no "edit_entry" function)
- the log is meant to be read, not curated after the fact.

Names/roles are snapshotted onto the row at write time rather than only
storing staff ids, so the log still reads sensibly even if that staff
member is later renamed, promoted, or deactivated.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime


def record(
    conn: sqlite3.Connection,
    *,
    actor_staff_id: int,
    action: str,
    target_staff_id: int | None = None,
    details: str = "",
) -> int:
    actor = conn.execute("SELECT name, role FROM staff WHERE id=?", (actor_staff_id,)).fetchone()
    actor_name = actor["name"] if actor else f"staff#{actor_staff_id}"
    actor_role = actor["role"] if actor else "unknown"

    target_name = None
    if target_staff_id is not None:
        target = conn.execute("SELECT name FROM staff WHERE id=?", (target_staff_id,)).fetchone()
        target_name = target["name"] if target else f"staff#{target_staff_id}"

    cur = conn.execute(
        """INSERT INTO audit_log (at, actor_staff_id, actor_name, actor_role, action,
           target_staff_id, target_name, details) VALUES (?,?,?,?,?,?,?,?)""",
        (datetime.now().isoformat(timespec="seconds"), actor_staff_id, actor_name, actor_role,
         action, target_staff_id, target_name, details),
    )
    conn.commit()
    return cur.lastrowid


def list_log(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
