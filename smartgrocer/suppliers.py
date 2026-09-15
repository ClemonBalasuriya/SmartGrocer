"""
SmartGrocer - supplier master records and accounts payable.

Matches the "Supplier" master-data screen in the sample POS system: a
simple named list that a Goods Received Note (GRN / stock receipt) can be
attributed to, so a shop owner can later ask "how much stock, and how much
did I spend, with each supplier" instead of GRNs floating with no source.

Also covers the shop's accounts-payable side, the mirror image of
customers.py's credit-account handling: a shop doesn't always pay a
supplier cash on the spot for stock - sometimes a cheque is handed over
(which still needs its date/amount remembered so the bank account has the
funds ready when it's cashed), and sometimes the stock is taken on credit
and only settled later, e.g. once it's been sold. suppliers.payable_balance
is what the shop currently owes a supplier (mirrors
customers.credit_balance - what a customer owes the shop); pay_supplier
mirrors customers.settle_credit; supplier_statement mirrors
customers.credit_statement. Stock that comes back to a supplier (damaged,
wrong delivery, etc) is handled here too, by return_to_supplier.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime


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


def record_purchase_payment(
    conn: sqlite3.Connection,
    *,
    supplier_id: int,
    batch_id: int,
    amount: float,
    method: str = "cash",
    cheque_number: str = "",
    cheque_date: str | None = None,
) -> int | None:
    """Called by pos.receive_stock right when stock comes in, based on how
    that particular delivery was paid for:

    - 'cash' (or any other on-the-spot payment) - nothing to record here at
      all, exactly matching how every restock behaved before this feature
      existed: paid in full, nothing owed, no reminder needed.
    - 'cheque' - the batch is already fully paid (no payable created) but a
      supplier_payments row is logged with the batch attached and
      status='pending', purely so it shows up in list_pending_cheques() as
      a reminder to have the cheque's amount ready in the bank by its date.
      Marking it cleared later (mark_cheque_cleared) never touches
      payable_balance - it wasn't part of it to begin with.
    - 'credit' ("not paid yet") - raises the supplier's payable_balance by
      the amount, to be settled later with pay_supplier - e.g. once this
      stock itself has actually been sold and there's cash in hand for it.

    Returns the new supplier_payments row id, or None when nothing needed
    recording (the plain 'cash' case)."""
    if method not in ("cash", "cheque", "credit"):
        raise ValueError(f"Unknown payment method: {method}")
    if method == "cheque":
        cur = conn.execute(
            """INSERT INTO supplier_payments
               (supplier_id, batch_id, amount, method, cheque_number, cheque_date, status, paid_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (supplier_id, batch_id, amount, "cheque", (cheque_number or "").strip() or None,
             cheque_date, "pending", datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid
    if method == "credit":
        conn.execute(
            "UPDATE suppliers SET payable_balance = payable_balance + ? WHERE id=?", (amount, supplier_id)
        )
        conn.commit()
        return None
    return None  # cash - already settled, nothing owed


def pay_supplier(
    conn: sqlite3.Connection, supplier_id: int, amount: float, method: str = "cash", note: str = ""
) -> int:
    """Record a payment against a supplier's outstanding payable balance -
    mirrors customers.settle_credit. Use this for a general settlement
    (e.g. "paying off what we owe this month"), not for a cheque handed
    over right at stock-receiving time (that's record_purchase_payment,
    called automatically from pos.receive_stock)."""
    if amount <= 0:
        raise ValueError("Payment amount must be positive.")
    cur = conn.execute(
        """INSERT INTO supplier_payments (supplier_id, batch_id, amount, method, status, paid_at, note)
           VALUES (?,NULL,?,?,?,?,?)""",
        (supplier_id, amount, method, "pending" if method == "cheque" else "cleared",
         datetime.now().isoformat(timespec="seconds"), note),
    )
    conn.execute(
        "UPDATE suppliers SET payable_balance = payable_balance - ? WHERE id=?", (amount, supplier_id)
    )
    conn.commit()
    return cur.lastrowid


def mark_cheque_cleared(conn: sqlite3.Connection, payment_id: int) -> None:
    """A pending cheque (either one handed over at receiving time, or one
    used for a general settlement) has actually gone through the bank -
    this is purely bookkeeping/reminder upkeep, it never changes
    payable_balance (a settlement cheque already reduced it the moment it
    was recorded; a receiving-time cheque never added to it)."""
    conn.execute("UPDATE supplier_payments SET status='cleared' WHERE id=? AND method='cheque'", (payment_id,))
    conn.commit()


def list_pending_cheques(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every cheque given to a supplier that hasn't been marked cleared yet,
    across all suppliers, earliest date due first - the actual "reminder"
    feature: at a glance, how much needs to be in the bank account and by
    when. Cheques with no date at all sort last, not first, so a
    forgotten/blank date doesn't wrongly jump to the top of the list."""
    return conn.execute(
        """SELECT sp.*, s.name AS supplier_name FROM supplier_payments sp
           JOIN suppliers s ON s.id = sp.supplier_id
           WHERE sp.method='cheque' AND sp.status='pending'
           ORDER BY sp.cheque_date IS NULL, sp.cheque_date, sp.paid_at"""
    ).fetchall()


def supplier_statement(conn: sqlite3.Connection, supplier_id: int) -> list[dict]:
    """Chronological ledger of what's owed to one supplier, each row
    carrying a running balance - mirrors customers.credit_statement.
    Charges are batches taken on credit ("not paid yet" at GRN time);
    payments are general settlements (pay_supplier, batch_id IS NULL) made
    later. A cheque logged right at receiving time is shown too, but
    purely informationally (charge=0, payment=0) since it never affected
    the payable balance to begin with - see record_purchase_payment."""
    charges = conn.execute(
        """SELECT received_date AS ts, 'Stock received (' || batch_no || ')' AS description,
                  qty_received * cost_price AS charge, 0 AS payment
           FROM stock_batches WHERE supplier_id=? AND payment_status='credit'""",
        (supplier_id,),
    ).fetchall()
    payments = conn.execute(
        """SELECT paid_at AS ts, 'Payment (' || method || ')' AS description, 0 AS charge, amount AS payment
           FROM supplier_payments WHERE supplier_id=? AND batch_id IS NULL""",
        (supplier_id,),
    ).fetchall()
    cheques_at_receipt = conn.execute(
        """SELECT paid_at AS ts,
                  'Cheque given at receiving (' || COALESCE(cheque_number,'no number') || ', due ' ||
                  COALESCE(cheque_date,'no date') || ')' AS description, 0 AS charge, 0 AS payment
           FROM supplier_payments WHERE supplier_id=? AND batch_id IS NOT NULL""",
        (supplier_id,),
    ).fetchall()
    rows = sorted(
        [dict(r) for r in charges] + [dict(r) for r in payments] + [dict(r) for r in cheques_at_receipt],
        key=lambda r: r["ts"],
    )
    balance = 0.0
    for r in rows:
        balance += r["charge"] - r["payment"]
        r["balance"] = round(balance, 2)
    return rows


def return_to_supplier(
    conn: sqlite3.Connection, batch_id: int, qty: float, *, credit_amount: float | None = None, reason: str = ""
) -> int:
    """Send stock back to a supplier - damaged goods, wrong delivery, etc.
    Reduces that batch's qty_remaining (can't return more than what's
    actually still on hand from it) and, unless it's a straight swap for
    replacement stock, credits the supplier's payable_balance so the shop
    owes that much less (credit_amount defaults to the batch's own cost
    price x qty, but can be overridden, e.g. if the supplier only agreed
    to a partial credit). Never pushes payable_balance below zero - a
    return on its own can't turn into the supplier owing the shop money."""
    batch = conn.execute("SELECT * FROM stock_batches WHERE id=?", (batch_id,)).fetchone()
    if batch is None:
        raise ValueError("Stock batch not found.")
    if batch["supplier_id"] is None:
        raise ValueError("This batch has no supplier on record to return it to.")
    if qty <= 0:
        raise ValueError("Return quantity must be greater than zero.")
    if qty > batch["qty_remaining"] + 1e-9:
        raise ValueError(f"Only {batch['qty_remaining']:g} of this batch is still in stock.")
    if credit_amount is None:
        credit_amount = qty * batch["cost_price"]
    if credit_amount < 0:
        raise ValueError("Credit amount can't be negative.")
    conn.execute("UPDATE stock_batches SET qty_remaining = qty_remaining - ? WHERE id=?", (qty, batch_id))
    cur = conn.execute(
        """INSERT INTO supplier_returns (supplier_id, product_id, batch_id, qty, credit_amount, returned_at, reason)
           VALUES (?,?,?,?,?,?,?)""",
        (batch["supplier_id"], batch["product_id"], batch_id, qty, credit_amount,
         datetime.now().isoformat(timespec="seconds"), reason),
    )
    if credit_amount > 0:
        conn.execute(
            "UPDATE suppliers SET payable_balance = MAX(0, payable_balance - ?) WHERE id=?",
            (credit_amount, batch["supplier_id"]),
        )
    conn.commit()
    return cur.lastrowid


def list_returns(conn: sqlite3.Connection, supplier_id: int) -> list[sqlite3.Row]:
    """History of items sent back to one supplier, most recent first."""
    return conn.execute(
        """SELECT r.*, p.name_en AS product_name FROM supplier_returns r
           JOIN products p ON p.id = r.product_id
           WHERE r.supplier_id=? ORDER BY r.returned_at DESC""",
        (supplier_id,),
    ).fetchall()
