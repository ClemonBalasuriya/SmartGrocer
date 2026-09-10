"""
SmartGrocer - live POS / checkout module.

This is what makes SmartGrocer an actual point-of-sale system rather than
just an analytics layer: it looks up items (by barcode/PLU code or name),
builds a cart, and posts a real invoice that deducts stock FIFO by expiry
date - the same stock_batches table every analytics module reads from.

A cart line can legitimately draw from more than one batch (e.g. 5 loaves
of bread where the oldest batch only has 2 left) - each batch drawn from
becomes its own invoice_items row, so batch-level cost/expiry stays exact
for margin and waste reporting.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from . import customers as customers_module

PRICE_FIELD = {"cash": "cash_price", "credit": "credit_price", "wholesale": "wholesale_price"}


class InsufficientStockError(Exception):
    def __init__(self, product_name: str, requested: float, available: float):
        self.product_name = product_name
        self.requested = requested
        self.available = available
        super().__init__(
            f"Not enough stock for '{product_name}': requested {requested}, available {available}"
        )


@dataclass
class CartLine:
    product_id: int
    qty: float
    discount: float = 0.0  # flat currency amount off this line


@dataclass
class InvoiceResult:
    invoice_id: int
    invoice_no: str
    subtotal: float
    discount: float
    net_total: float
    cash_paid: float
    balance: float
    lines: list = field(default_factory=list)


def search_products(conn: sqlite3.Connection, query: str, limit: int = 25) -> list[sqlite3.Row]:
    like = f"%{query}%"
    return conn.execute(
        """SELECT * FROM products WHERE active=1 AND
           (code LIKE ? OR name_en LIKE ? OR name_si LIKE ?)
           ORDER BY name_en LIMIT ?""",
        (like, like, like, limit),
    ).fetchall()


def get_product_by_code(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM products WHERE code=? AND active=1", (code,)).fetchone()


def get_stock_on_hand(conn: sqlite3.Connection, product_id: int) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(qty_remaining),0) q FROM stock_batches WHERE product_id=?",
        (product_id,),
    ).fetchone()
    return row["q"]


def _next_invoice_no(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT invoice_no FROM invoices ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "INV0000001"
    last_n = int(row["invoice_no"].replace("INV", ""))
    return f"INV{last_n + 1:07d}"


def _fifo_batches(conn: sqlite3.Connection, product_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM stock_batches WHERE product_id=? AND qty_remaining>0
           ORDER BY (expiry_date IS NULL), expiry_date, received_date""",
        (product_id,),
    ).fetchall()


def create_invoice(
    conn: sqlite3.Connection,
    staff_id: int,
    cart: list[CartLine],
    price_tier: str = "cash",
    payment_type: str = "cash",
    customer_name: str = "Walk-in",
    customer_id: int | None = None,
    cash_paid: float | None = None,
    allow_backorder: bool = False,
    payments: list[tuple[str, float]] | None = None,
) -> InvoiceResult:
    """Post a sale. Raises InsufficientStockError unless allow_backorder=True
    (backorder still sells, just without a batch reference / expiry deduction -
    useful for a shop still entering its opening stock).

    Two ways to pay:
    - Simple (default): payment_type is one of cash/card/cheque/credit and
      cash_paid is how much was actually handed over now (defaults to the
      full net_total, or 0 for a pure credit sale).
    - Split/mixed: pass `payments` as a list of (method, amount) tuples, e.g.
      [("cash", 500.0), ("card", 1200.0)] or [("cash", 300.0), ("credit", 900.0)]
      for part-cash/part-credit. Each row is recorded in invoice_payments.

    A sale with any "credit" portion requires customer_id, and raises
    customers.CreditLimitExceededError if it would push that customer's
    outstanding balance over their credit limit."""
    if price_tier not in PRICE_FIELD:
        raise ValueError(f"Unknown price_tier: {price_tier}")
    price_field = PRICE_FIELD[price_tier]

    product_rows = {}
    for line in cart:
        p = conn.execute("SELECT * FROM products WHERE id=?", (line.product_id,)).fetchone()
        if p is None:
            raise ValueError(f"Unknown product_id {line.product_id}")
        product_rows[line.product_id] = p
        available = get_stock_on_hand(conn, line.product_id)
        if available < line.qty and not allow_backorder:
            raise InsufficientStockError(p["name_en"], line.qty, available)

    subtotal = 0.0
    line_discount_total = 0.0
    item_rows = []  # (product_id, batch_id, qty, unit_price, discount, line_total)

    for line in cart:
        p = product_rows[line.product_id]
        unit_price = p[price_field]
        left = line.qty
        batches = _fifo_batches(conn, line.product_id)
        drawn_any = False
        for b in batches:
            if left <= 0:
                break
            take = min(b["qty_remaining"], left)
            if take <= 0:
                continue
            new_remaining = b["qty_remaining"] - take
            conn.execute("UPDATE stock_batches SET qty_remaining=? WHERE id=?", (new_remaining, b["id"]))
            share_discount = line.discount * (take / line.qty) if line.qty else 0
            line_total = round(unit_price * take - share_discount, 2)
            item_rows.append((line.product_id, b["id"], take, unit_price, share_discount, line_total))
            subtotal += unit_price * take
            line_discount_total += share_discount
            left -= take
            drawn_any = True
        if left > 0:  # backorder remainder with no batch reference
            line_total = round(unit_price * left - (line.discount if not drawn_any else 0), 2)
            item_rows.append((line.product_id, None, left, unit_price, 0.0, line_total))
            subtotal += unit_price * left

    net_total = round(subtotal - line_discount_total, 2)

    credit_amount = 0.0
    if payments:
        total_paid = round(sum(a for _, a in payments), 2)
        credit_amount = round(sum(a for m, a in payments if m == "credit"), 2)
        paid = round(total_paid - credit_amount, 2)
        balance = round(total_paid - net_total, 2)
        methods = {m for m, _ in payments}
        payment_type = "mixed" if len(methods) > 1 else next(iter(methods))
    else:
        paid = cash_paid if cash_paid is not None else (net_total if payment_type != "credit" else 0.0)
        balance = round(paid - net_total, 2)
        if payment_type == "credit":
            credit_amount = round(net_total - paid, 2)

    if credit_amount > 1e-6:
        if customer_id is None:
            raise ValueError("customer_id is required when part of a sale is on credit.")
        customers_module.check_credit_limit(conn, customer_id, credit_amount)

    invoice_no = _next_invoice_no(conn)

    cur = conn.execute(
        """INSERT INTO invoices (invoice_no,datetime,staff_id,customer_name,customer_id,price_tier,
           payment_type,subtotal,discount,net_total,cash_paid,balance)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (invoice_no, datetime.now().isoformat(timespec="seconds"), staff_id, customer_name, customer_id,
         price_tier, payment_type, round(subtotal, 2), round(line_discount_total, 2), net_total,
         paid, balance),
    )
    invoice_id = cur.lastrowid
    conn.executemany(
        """INSERT INTO invoice_items (invoice_id,product_id,batch_id,qty,unit_price,discount,line_total)
           VALUES (?,?,?,?,?,?,?)""",
        [(invoice_id, *row) for row in item_rows],
    )
    if payments:
        conn.executemany(
            "INSERT INTO invoice_payments (invoice_id, method, amount) VALUES (?,?,?)",
            [(invoice_id, m, a) for m, a in payments],
        )
    if credit_amount > 1e-6:
        customers_module.record_credit_sale(conn, customer_id, credit_amount)
    conn.commit()

    return InvoiceResult(
        invoice_id=invoice_id, invoice_no=invoice_no, subtotal=round(subtotal, 2),
        discount=round(line_discount_total, 2), net_total=net_total, cash_paid=paid,
        balance=balance, lines=item_rows,
    )


def void_invoice(conn: sqlite3.Connection, invoice_id: int) -> None:
    """Reverse an invoice: restore stock to the batches it was drawn from,
    reverse any credit-account charge it created, and mark it voided."""
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if inv is None or inv["voided"]:
        return
    items = conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (invoice_id,)).fetchall()
    for it in items:
        if it["batch_id"] is not None:
            conn.execute(
                "UPDATE stock_batches SET qty_remaining = qty_remaining + ? WHERE id=?",
                (it["qty"], it["batch_id"]),
            )
    credit_charged = round(inv["net_total"] - inv["cash_paid"], 2)
    if inv["customer_id"] is not None and credit_charged > 1e-6:
        customers_module.record_credit_sale(conn, inv["customer_id"], -credit_charged)
    conn.execute("UPDATE invoices SET voided=1 WHERE id=?", (invoice_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Item return / refund - a dedicated flow against a past invoice, matching
# the "Item Return" button on the sample POS system's invoice screen. Unlike
# a full void, a return can cover just some of the lines/quantity on an
# invoice (a customer keeping 3 of the 5 items they bought).
# ---------------------------------------------------------------------------

def returnable_items(conn: sqlite3.Connection, invoice_id: int) -> list[dict]:
    """Invoice lines with how much of each is still eligible to be returned
    (original qty minus whatever has already been returned against it)."""
    items = conn.execute(
        """SELECT ii.id AS invoice_item_id, ii.product_id, ii.batch_id, ii.qty, ii.unit_price,
                  p.name_en
           FROM invoice_items ii JOIN products p ON p.id = ii.product_id
           WHERE ii.invoice_id=?""",
        (invoice_id,),
    ).fetchall()
    already = conn.execute(
        """SELECT ii.id AS invoice_item_id, COALESCE(SUM(r.qty),0) AS returned
           FROM invoice_items ii LEFT JOIN returns r
             ON r.invoice_id=ii.invoice_id AND r.product_id=ii.product_id AND
                (r.batch_id IS ii.batch_id)
           WHERE ii.invoice_id=? GROUP BY ii.id""",
        (invoice_id,),
    ).fetchall()
    returned_by_item = {r["invoice_item_id"]: r["returned"] for r in already}
    result = []
    for it in items:
        returned = returned_by_item.get(it["invoice_item_id"], 0.0)
        remaining = round(it["qty"] - returned, 4)
        if remaining > 1e-6:
            result.append(
                {
                    "invoice_item_id": it["invoice_item_id"],
                    "product_id": it["product_id"],
                    "batch_id": it["batch_id"],
                    "name_en": it["name_en"],
                    "unit_price": it["unit_price"],
                    "returnable_qty": remaining,
                }
            )
    return result


def return_items(
    conn: sqlite3.Connection, invoice_id: int, lines: list[tuple[int, int | None, float]], reason: str = ""
) -> float:
    """Return some quantity of one or more lines from an invoice: restocks
    the batch it came from (if any) and logs the refund value. `lines` is a
    list of (product_id, batch_id, qty) tuples - batch_id may be None for a
    backorder line. Returns the total refund amount."""
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if inv is None:
        raise ValueError(f"Unknown invoice_id {invoice_id}")
    total_refund = 0.0
    now = datetime.now().isoformat(timespec="seconds")
    for product_id, batch_id, qty in lines:
        if qty <= 0:
            continue
        item = conn.execute(
            """SELECT unit_price FROM invoice_items WHERE invoice_id=? AND product_id=? AND
               (batch_id IS ?) LIMIT 1""",
            (invoice_id, product_id, batch_id),
        ).fetchone()
        unit_price = item["unit_price"] if item else 0.0
        refund = round(unit_price * qty, 2)
        total_refund += refund
        if batch_id is not None:
            conn.execute(
                "UPDATE stock_batches SET qty_remaining = qty_remaining + ? WHERE id=?", (qty, batch_id)
            )
        conn.execute(
            """INSERT INTO returns (invoice_id, product_id, batch_id, qty, refund_amount, returned_at, reason)
               VALUES (?,?,?,?,?,?,?)""",
            (invoice_id, product_id, batch_id, qty, refund, now, reason),
        )
    total_refund = round(total_refund, 2)
    # If the original sale had a credit portion, reduce the customer's
    # outstanding balance by the refunded amount rather than handing back cash.
    if inv["customer_id"] is not None and inv["payment_type"] in ("credit", "mixed"):
        credit_charged = round(inv["net_total"] - inv["cash_paid"], 2)
        if credit_charged > 1e-6:
            customers_module.record_credit_sale(conn, inv["customer_id"], -min(total_refund, credit_charged))
    conn.commit()
    return total_refund


# ---------------------------------------------------------------------------
# Hold / resume invoice - "park" an in-progress cart (e.g. a customer forgot
# their wallet, or the queue needs to move) and bring it back later, matching
# the "F9 Hold Invoice" button on the sample POS system.
# ---------------------------------------------------------------------------

def hold_cart(
    conn: sqlite3.Connection,
    staff_id: int,
    cart: list[CartLine],
    price_tier: str = "cash",
    customer_id: int | None = None,
    note: str = "",
) -> int:
    cart_json = json.dumps([{"product_id": c.product_id, "qty": c.qty, "discount": c.discount} for c in cart])
    cur = conn.execute(
        """INSERT INTO held_invoices (held_at, staff_id, customer_id, price_tier, cart_json, note)
           VALUES (?,?,?,?,?,?)""",
        (datetime.now().isoformat(timespec="seconds"), staff_id, customer_id, price_tier, cart_json, note),
    )
    conn.commit()
    return cur.lastrowid


def list_held_invoices(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT h.*, COALESCE(c.name,'Walk-in') AS customer_name
           FROM held_invoices h LEFT JOIN customers c ON c.id = h.customer_id
           ORDER BY h.held_at"""
    ).fetchall()


def resume_held_invoice(conn: sqlite3.Connection, held_id: int) -> tuple[list[CartLine], str, int | None]:
    """Return (cart, price_tier, customer_id) for a held invoice without
    deleting it yet - the caller deletes it once the sale is actually posted
    or explicitly discarded, so a crash mid-checkout doesn't lose the cart."""
    row = conn.execute("SELECT * FROM held_invoices WHERE id=?", (held_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown held invoice {held_id}")
    raw = json.loads(row["cart_json"])
    cart = [CartLine(product_id=r["product_id"], qty=r["qty"], discount=r.get("discount", 0.0)) for r in raw]
    return cart, row["price_tier"], row["customer_id"]


def delete_held_invoice(conn: sqlite3.Connection, held_id: int) -> None:
    conn.execute("DELETE FROM held_invoices WHERE id=?", (held_id,))
    conn.commit()
