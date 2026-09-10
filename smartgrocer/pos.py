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

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

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
    cash_paid: float | None = None,
    allow_backorder: bool = False,
) -> InvoiceResult:
    """Post a sale. Raises InsufficientStockError unless allow_backorder=True
    (backorder still sells, just without a batch reference / expiry deduction -
    useful for a shop still entering its opening stock)."""
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
    paid = cash_paid if cash_paid is not None else (net_total if payment_type != "credit" else 0.0)
    balance = round(paid - net_total, 2)
    invoice_no = _next_invoice_no(conn)

    cur = conn.execute(
        """INSERT INTO invoices (invoice_no,datetime,staff_id,customer_name,price_tier,
           payment_type,subtotal,discount,net_total,cash_paid,balance)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (invoice_no, datetime.now().isoformat(timespec="seconds"), staff_id, customer_name,
         price_tier, payment_type, round(subtotal, 2), round(line_discount_total, 2), net_total,
         paid, balance),
    )
    invoice_id = cur.lastrowid
    conn.executemany(
        """INSERT INTO invoice_items (invoice_id,product_id,batch_id,qty,unit_price,discount,line_total)
           VALUES (?,?,?,?,?,?,?)""",
        [(invoice_id, *row) for row in item_rows],
    )
    conn.commit()

    return InvoiceResult(
        invoice_id=invoice_id, invoice_no=invoice_no, subtotal=round(subtotal, 2),
        discount=round(line_discount_total, 2), net_total=net_total, cash_paid=paid,
        balance=balance, lines=item_rows,
    )


def void_invoice(conn: sqlite3.Connection, invoice_id: int) -> None:
    """Reverse an invoice: restore stock to the batches it was drawn from and mark voided."""
    items = conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (invoice_id,)).fetchall()
    for it in items:
        if it["batch_id"] is not None:
            conn.execute(
                "UPDATE stock_batches SET qty_remaining = qty_remaining + ? WHERE id=?",
                (it["qty"], it["batch_id"]),
            )
    conn.execute("UPDATE invoices SET voided=1 WHERE id=?", (invoice_id,))
    conn.commit()
