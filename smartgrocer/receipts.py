"""
SmartGrocer - printable sale receipts.

Builds a plain, 40-column receipt (the width a typical 3-inch thermal/POS
printer uses) and saves it under exports/receipts/. Actually spooling to a
specific physical printer varies too much by OS, driver and printer model
to automate reliably from a portable script, so `open_for_printing` opens
the saved receipt with the machine's own default handler (Notepad on
Windows, TextEdit on Mac) - the cashier can Ctrl+P/Cmd+P from there, which
works with whatever printer is already set up on that till.
"""

from __future__ import annotations

import os
import platform
import sqlite3
import subprocess
from pathlib import Path

from . import db

SHOP_NAME = "SmartGrocer"
SHOP_TAGLINE = "by Balasuriya Group"
RECEIPT_WIDTH = 40


def build_receipt_text(conn: sqlite3.Connection, invoice_id: int) -> str:
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if inv is None:
        raise ValueError(f"Unknown invoice_id {invoice_id}")
    items = conn.execute(
        """SELECT p.name_en, ii.qty, ii.unit_price, ii.line_total FROM invoice_items ii
           JOIN products p ON p.id = ii.product_id WHERE ii.invoice_id=?""",
        (invoice_id,),
    ).fetchall()
    staff = conn.execute("SELECT name FROM staff WHERE id=?", (inv["staff_id"],)).fetchone()
    payments = conn.execute(
        "SELECT method, amount FROM invoice_payments WHERE invoice_id=?", (invoice_id,)
    ).fetchall()

    w = RECEIPT_WIDTH
    lines = [
        SHOP_NAME.center(w),
        SHOP_TAGLINE.center(w),
        "=" * w,
        f"Invoice : {inv['invoice_no']}",
        f"Date    : {inv['datetime']}",
        f"Cashier : {staff['name'] if staff else '-'}",
        f"Customer: {inv['customer_name']}",
        "-" * w,
    ]
    for it in items:
        name = it["name_en"][:22]
        lines.append(f"{name:<22}{it['qty']:>5g} x{it['unit_price']:>7.2f}")
        lines.append(f"{'':<28}{it['line_total']:>10.2f}")
    lines.append("-" * w)
    lines.append(f"{'Subtotal':<28}{inv['subtotal']:>10.2f}")
    if inv["discount"]:
        lines.append(f"{'Discount':<28}{-inv['discount']:>10.2f}")
    lines.append(f"{'NET TOTAL':<28}{inv['net_total']:>10.2f}")
    lines.append("-" * w)
    for p in payments:
        lines.append(f"{p['method'].capitalize():<28}{p['amount']:>10.2f}")
    if inv["balance"] > 0.01:
        lines.append(f"{'Change':<28}{inv['balance']:>10.2f}")
    lines.append("=" * w)
    lines.append("Thank you for shopping with us!".center(w))
    return "\n".join(lines)


def save_receipt(conn: sqlite3.Connection, invoice_id: int) -> Path:
    text = build_receipt_text(conn, invoice_id)
    out_dir = db.ensure_output_dir() / "receipts"
    out_dir.mkdir(parents=True, exist_ok=True)
    inv = conn.execute("SELECT invoice_no FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    path = out_dir / f"{inv['invoice_no']}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def open_for_printing(path: Path) -> None:
    """Best-effort: open the saved receipt with the OS default text handler
    so the cashier can print it. Never raises - printing is a convenience on
    top of an already-saved, already-completed sale, not a precondition for
    finishing the checkout."""
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(str(path))  # Windows-only API; guarded by the platform check above
        elif system == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        pass
