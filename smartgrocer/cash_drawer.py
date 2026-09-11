"""
SmartGrocer - cash drawer / day open-close.

A single-till shop's daily ritual: start the day by counting an opening
cash float, sell through the day, then close by counting the actual cash in
the drawer and comparing it to what the system expects - matching the
sample POS system's Cash Drawer control and giving the owner a concrete
over/short figure every single day rather than only at month-end.

Expected cash = opening float
              + cash portion of every sale during the session (invoice_payments)
              + cash credit-settlements collected during the session
              - cash refunds paid out during the session
(card/cheque/credit money never touches the physical drawer, so those are
excluded; a return against a credit/mixed sale is assumed to have been
absorbed as a credit-note reduction rather than paid out in cash - see
pos.return_items, which does exactly that.)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime


def get_open_session(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM cash_sessions WHERE closed_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()


def open_session(
    conn: sqlite3.Connection, staff_id: int, opening_float: float, note: str = ""
) -> int:
    if get_open_session(conn) is not None:
        raise ValueError("A cash drawer session is already open - close it before opening a new one.")
    cur = conn.execute(
        "INSERT INTO cash_sessions (staff_id, opened_at, opening_float, note) VALUES (?,?,?,?)",
        (staff_id, datetime.now().isoformat(timespec="seconds"), opening_float, note),
    )
    conn.commit()
    return cur.lastrowid


def expected_cash(conn: sqlite3.Connection, session: sqlite3.Row) -> float:
    since = session["opened_at"]
    cash_sales = conn.execute(
        """SELECT COALESCE(SUM(ip.amount),0) v FROM invoice_payments ip
           JOIN invoices i ON i.id = ip.invoice_id
           WHERE ip.method='cash' AND i.voided=0 AND i.datetime >= ?""",
        (since,),
    ).fetchone()["v"]
    cash_settlements = conn.execute(
        "SELECT COALESCE(SUM(amount),0) v FROM credit_settlements WHERE method='cash' AND settled_at >= ?",
        (since,),
    ).fetchone()["v"]
    cash_refunds = conn.execute(
        """SELECT COALESCE(SUM(r.refund_amount),0) v FROM returns r
           JOIN invoices i ON i.id = r.invoice_id
           WHERE r.returned_at >= ? AND (i.customer_id IS NULL OR i.payment_type NOT IN ('credit','mixed'))""",
        (since,),
    ).fetchone()["v"]
    return round(session["opening_float"] + cash_sales + cash_settlements - cash_refunds, 2)


def close_session(conn: sqlite3.Connection, session_id: int, counted_cash: float, note: str = "") -> dict:
    session = conn.execute("SELECT * FROM cash_sessions WHERE id=?", (session_id,)).fetchone()
    if session is None or session["closed_at"] is not None:
        raise ValueError("No such open cash drawer session.")
    expected = expected_cash(conn, session)
    variance = round(counted_cash - expected, 2)
    conn.execute(
        """UPDATE cash_sessions SET closed_at=?, counted_cash=?, expected_cash=?, variance=?, note=?
           WHERE id=?""",
        (datetime.now().isoformat(timespec="seconds"), counted_cash, expected, variance, note, session_id),
    )
    conn.commit()
    return {"expected_cash": expected, "counted_cash": round(counted_cash, 2), "variance": variance}


def session_history(conn: sqlite3.Connection, limit: int = 30) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT cs.*, s.name AS staff_name FROM cash_sessions cs LEFT JOIN staff s ON s.id = cs.staff_id
           ORDER BY cs.id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
