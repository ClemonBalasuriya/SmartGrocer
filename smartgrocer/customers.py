"""
SmartGrocer - customer accounts and credit sales.

The sample POS system's invoice screen shows a running "Points/Avl. Credit"
balance per customer and separate Credit Invoice / Credit Settlement flows.
This module is the SmartGrocer equivalent: a customer can have a credit
limit, sales on credit raise their outstanding balance (checked against the
limit before the sale is allowed), and settlements record a payment against
that balance later.

A "Walk-in" customer (id 1, credit_limit 0) always exists so cash sales
never need a real customer record - customer accounts are only needed for
shoppers who actually buy on credit.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime


class CreditLimitExceededError(Exception):
    def __init__(self, customer_name: str, limit: float, balance: float, amount: float):
        self.customer_name = customer_name
        self.limit = limit
        self.balance = balance
        self.amount = amount
        super().__init__(
            f"'{customer_name}' credit limit is {limit:.2f}, already owes {balance:.2f}; "
            f"this sale of {amount:.2f} would exceed it."
        )


def is_loyalty_member(customer_row: sqlite3.Row | None) -> bool:
    """Per the shop's own rule: registering a customer at all is what
    makes them a loyalty member - there's no separate opt-in step. Only
    the seeded "Walk-in" customer (used for anonymous cash sales, never
    actually registered by anyone) is not a member."""
    return customer_row is not None and customer_row["name"] != "Walk-in"


def _check_phone_not_taken(conn: sqlite3.Connection, phone: str, *, exclude_id: int | None = None) -> None:
    """A customer's phone number IS their loyalty ID - it's what gets
    printed as a barcode and scanned at checkout (see POSScreen in the
    GUI) - so two active customers sharing one phone number would make a
    scan ambiguous about who it means. Blank/no phone is fine (that
    customer just isn't scannable/identifiable by phone - they can still
    be picked by name like before this feature existed); a real phone
    number must be unique among active, real (non-"Walk-in") customers."""
    phone = (phone or "").strip()
    if not phone:
        return
    q = "SELECT id FROM customers WHERE active=1 AND name != 'Walk-in' AND phone=?"
    params: list = [phone]
    if exclude_id is not None:
        q += " AND id != ?"
        params.append(exclude_id)
    clash = conn.execute(q, params).fetchone()
    if clash is not None:
        raise ValueError(f"Phone number '{phone}' is already registered to another customer.")


def add_customer(
    conn: sqlite3.Connection, name: str, phone: str = "", address: str = "", credit_limit: float = 0.0
) -> int:
    _check_phone_not_taken(conn, phone)
    cur = conn.execute(
        "INSERT INTO customers (name, phone, address, credit_limit) VALUES (?,?,?,?)",
        (name.strip(), phone.strip(), address.strip(), credit_limit),
    )
    conn.commit()
    return cur.lastrowid


def find_customer_by_phone(conn: sqlite3.Connection, phone: str) -> sqlite3.Row | None:
    """The primary way a loyalty customer is identified at checkout -
    exact match only (phone numbers get typed or scanned under time
    pressure, and a "close enough" fuzzy match risks quietly attaching a
    sale, and any loyalty-only discount, to the wrong person's account)."""
    phone = (phone or "").strip()
    if not phone:
        return None
    return conn.execute(
        "SELECT * FROM customers WHERE active=1 AND name != 'Walk-in' AND phone=?", (phone,)
    ).fetchone()


def update_customer(
    conn: sqlite3.Connection, customer_id: int, name: str, phone: str, address: str, credit_limit: float
) -> None:
    _check_phone_not_taken(conn, phone, exclude_id=customer_id)
    conn.execute(
        "UPDATE customers SET name=?, phone=?, address=?, credit_limit=? WHERE id=?",
        (name.strip(), phone.strip(), address.strip(), credit_limit, customer_id),
    )
    conn.commit()


def list_customers(conn: sqlite3.Connection, active_only: bool = True) -> list[sqlite3.Row]:
    q = "SELECT * FROM customers"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY name"
    return conn.execute(q).fetchall()


def get_customer(conn: sqlite3.Connection, customer_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()


def available_credit(conn: sqlite3.Connection, customer_id: int) -> float:
    c = get_customer(conn, customer_id)
    if c is None:
        return 0.0
    return round(c["credit_limit"] - c["credit_balance"], 2)


def check_credit_limit(conn: sqlite3.Connection, customer_id: int, amount: float) -> None:
    """Raise CreditLimitExceededError if this sale would push the customer over
    their limit. Call before create_invoice for a credit-tier/credit-payment sale."""
    c = get_customer(conn, customer_id)
    if c is None:
        return
    if c["credit_balance"] + amount > c["credit_limit"] + 1e-6:
        raise CreditLimitExceededError(c["name"], c["credit_limit"], c["credit_balance"], amount)


def record_credit_sale(conn: sqlite3.Connection, customer_id: int, amount: float) -> None:
    conn.execute(
        "UPDATE customers SET credit_balance = credit_balance + ? WHERE id=?", (amount, customer_id)
    )
    conn.commit()


def settle_credit(
    conn: sqlite3.Connection, customer_id: int, amount: float, method: str = "cash", note: str = ""
) -> int:
    """Record a payment against a customer's credit balance (a 'Credit Settlement')."""
    if amount <= 0:
        raise ValueError("Settlement amount must be positive.")
    cur = conn.execute(
        "INSERT INTO credit_settlements (customer_id, amount, method, settled_at, note) VALUES (?,?,?,?,?)",
        (customer_id, amount, method, datetime.now().isoformat(timespec="seconds"), note),
    )
    conn.execute(
        "UPDATE customers SET credit_balance = credit_balance - ? WHERE id=?", (amount, customer_id)
    )
    conn.commit()
    return cur.lastrowid


def credit_statement(conn: sqlite3.Connection, customer_id: int) -> list[dict]:
    """Chronological ledger of credit invoices (charges) and settlements
    (payments) for one customer, each row carrying a running balance -
    the 'Credit Customer Statement' report from the sample POS system."""
    invoices = conn.execute(
        """SELECT datetime AS ts, 'Sale ' || invoice_no AS description, net_total AS charge, 0 AS payment
           FROM invoices WHERE customer_id=? AND payment_type='credit' AND voided=0""",
        (customer_id,),
    ).fetchall()
    settlements = conn.execute(
        """SELECT settled_at AS ts, 'Settlement (' || method || ')' AS description, 0 AS charge, amount AS payment
           FROM credit_settlements WHERE customer_id=?""",
        (customer_id,),
    ).fetchall()
    rows = sorted([dict(r) for r in invoices] + [dict(r) for r in settlements], key=lambda r: r["ts"])
    balance = 0.0
    for r in rows:
        balance += r["charge"] - r["payment"]
        r["balance"] = round(balance, 2)
    return rows


def customers_over_limit(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Customers currently at/over their credit limit - useful as a dashboard alert."""
    return conn.execute(
        "SELECT * FROM customers WHERE active=1 AND credit_balance >= credit_limit AND credit_limit > 0"
    ).fetchall()
