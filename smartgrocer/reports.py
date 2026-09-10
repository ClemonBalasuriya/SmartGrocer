"""
SmartGrocer - reports, dashboard KPIs, and the extra value-add features
that round the system out into something a shop owner would actually use
day to day (low-stock alerts, cashier reconciliation, CSV export, backup).
"""

from __future__ import annotations

import csv
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path


def dashboard_kpis(conn: sqlite3.Connection, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    today = as_of.isoformat()

    today_sales = conn.execute(
        "SELECT COALESCE(SUM(net_total),0) v, COUNT(*) n FROM invoices WHERE date(datetime)=? AND voided=0",
        (today,),
    ).fetchone()

    margin_row = conn.execute(
        """SELECT COALESCE(SUM(ii.line_total),0) rev,
                  COALESCE(SUM(ii.qty * COALESCE(b.cost_price, p.cost_price)),0) cost
           FROM invoice_items ii JOIN invoices i ON i.id = ii.invoice_id
           JOIN products p ON p.id = ii.product_id
           LEFT JOIN stock_batches b ON b.id = ii.batch_id
           WHERE date(i.datetime)=? AND i.voided=0""",
        (today,),
    ).fetchone()
    gross_margin_pct = (
        round(100 * (margin_row["rev"] - margin_row["cost"]) / margin_row["rev"], 1)
        if margin_row["rev"] else 0.0
    )

    low_stock = conn.execute(
        """SELECT COUNT(*) c FROM (
             SELECT p.id, p.reorder_level, COALESCE(SUM(b.qty_remaining),0) stock
             FROM products p LEFT JOIN stock_batches b ON b.product_id = p.id
             WHERE p.active=1 GROUP BY p.id HAVING stock < p.reorder_level)"""
    ).fetchone()["c"]

    expiring_7d = conn.execute(
        """SELECT COUNT(*) c FROM stock_batches
           WHERE qty_remaining>0 AND expiry_date IS NOT NULL
             AND date(expiry_date) <= date(?, '+7 day')""",
        (today,),
    ).fetchone()["c"]

    top_seller = conn.execute(
        """SELECT p.name_en, SUM(ii.qty) q FROM invoice_items ii
           JOIN invoices i ON i.id = ii.invoice_id JOIN products p ON p.id = ii.product_id
           WHERE i.voided=0 AND date(i.datetime) >= date(?, '-30 day')
           GROUP BY p.id ORDER BY q DESC LIMIT 1""",
        (today,),
    ).fetchone()

    total_waste_value = conn.execute("SELECT COALESCE(SUM(value_lost),0) v FROM waste_events").fetchone()["v"]

    return {
        "date": today,
        "today_sales_total": round(today_sales["v"], 2),
        "today_invoice_count": today_sales["n"],
        "today_gross_margin_pct": gross_margin_pct,
        "low_stock_count": low_stock,
        "expiring_within_7_days": expiring_7d,
        "top_seller_30d": top_seller["name_en"] if top_seller else None,
        "historical_waste_value_lkr": round(total_waste_value, 2),
    }


def daily_sales_summary(conn: sqlite3.Connection, day: date) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT p.name_en, SUM(ii.qty) qty, SUM(ii.line_total) revenue
           FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id JOIN products p ON p.id=ii.product_id
           WHERE date(i.datetime)=? AND i.voided=0 GROUP BY p.id ORDER BY revenue DESC""",
        (day.isoformat(),),
    ).fetchall()


def best_sellers(conn: sqlite3.Connection, days: int = 30, top_n: int = 15) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT p.name_en, p.category, SUM(ii.qty) qty, SUM(ii.line_total) revenue
           FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id JOIN products p ON p.id=ii.product_id
           WHERE i.voided=0 AND date(i.datetime) >= date('now', ?)
           GROUP BY p.id ORDER BY revenue DESC LIMIT ?""",
        (f"-{days} day", top_n),
    ).fetchall()


def stock_summary(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT p.id, p.code, p.name_en, p.category, p.reorder_level,
                  COALESCE(SUM(b.qty_remaining),0) on_hand
           FROM products p LEFT JOIN stock_batches b ON b.product_id = p.id
           WHERE p.active=1 GROUP BY p.id ORDER BY p.category, p.name_en"""
    ).fetchall()
    return [dict(r) | {"below_reorder": r["on_hand"] < r["reorder_level"]} for r in rows]


def low_stock_alert(conn: sqlite3.Connection) -> list[dict]:
    return [r for r in stock_summary(conn) if r["below_reorder"]]


def cashier_daily_statement(conn: sqlite3.Connection, staff_id: int, day: date) -> dict:
    rows = conn.execute(
        """SELECT payment_type, COUNT(*) n, COALESCE(SUM(net_total),0) total
           FROM invoices WHERE staff_id=? AND date(datetime)=? AND voided=0
           GROUP BY payment_type""",
        (staff_id, day.isoformat()),
    ).fetchall()
    staff = conn.execute("SELECT name FROM staff WHERE id=?", (staff_id,)).fetchone()
    breakdown = {r["payment_type"]: {"count": r["n"], "total": r["total"]} for r in rows}
    return {
        "staff_name": staff["name"] if staff else "Unknown",
        "date": day.isoformat(),
        "by_payment_type": breakdown,
        "grand_total": round(sum(v["total"] for v in breakdown.values()), 2),
        "invoice_count": sum(v["count"] for v in breakdown.values()),
    }


def credit_outstanding_summary(conn: sqlite3.Connection) -> list[dict]:
    """One row per customer who currently owes money - the 'Credit Customer
    Statement' / 'Avl. Credit Limit' style report from the sample POS system,
    at a glance rather than per-customer."""
    rows = conn.execute(
        """SELECT name, phone, credit_limit, credit_balance,
                  ROUND(credit_limit - credit_balance, 2) AS available_credit
           FROM customers WHERE active=1 AND credit_balance > 0
           ORDER BY credit_balance DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def returns_summary(conn: sqlite3.Connection, days: int = 30) -> list[dict]:
    rows = conn.execute(
        """SELECT p.name_en, p.category, COUNT(*) events, SUM(r.qty) qty, SUM(r.refund_amount) refunded
           FROM returns r JOIN products p ON p.id = r.product_id
           WHERE date(r.returned_at) >= date('now', ?)
           GROUP BY p.id ORDER BY refunded DESC""",
        (f"-{days} day",),
    ).fetchall()
    return [dict(r) for r in rows]


def waste_summary(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT p.name_en, p.category, COUNT(*) events, SUM(w.qty_wasted) qty,
                  SUM(w.value_lost) value_lost
           FROM waste_events w JOIN products p ON p.id = w.product_id
           GROUP BY p.id ORDER BY value_lost DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def export_csv(rows: list[dict], out_path: str | Path) -> str:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        out_path.write_text("")
        return str(out_path)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig so Excel shows Sinhala correctly
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return str(out_path)


def backup_database(db_path: str | Path, backup_dir: str | Path) -> str:
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"smartgrocer_backup_{stamp}.db"
    shutil.copy2(db_path, dest)
    return str(dest)


def restore_database(backup_file: str | Path, db_path: str | Path) -> str:
    shutil.copy2(Path(backup_file), Path(db_path))
    return str(db_path)
