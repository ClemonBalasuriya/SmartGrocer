"""
SmartGrocer - database layer.

A single SQLite file is the whole "backend". Schema covers:
 - products: the item master (barcode/PLU code, prices at 4 tiers, flags used
   by the analytics modules: is_perishable, is_staple, child_target).
 - stock_batches: each goods-received batch, so expiry dates and FIFO stock
   deduction are tracked at the batch level (not just a single stock number).
 - staff / invoices / invoice_items: the POS/checkout side.
 - layout_assignments: last computed planogram, persisted so the GUI can show
   "current layout" without recomputing every time.

Every other module in this package only talks to the database through the
helpers here (get_conn / init_db) plus plain SQL - there is no ORM, which
keeps the code easy to read and defend in a viva.
"""

from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

if getattr(sys, "frozen", False):
    # Running as a PyInstaller-built .exe: __file__ would resolve inside the
    # temporary _MEIPASS extraction folder, which is deleted after every run -
    # that would silently reset the database on every launch. Use the folder
    # the .exe itself lives in instead, so data/ and exports/ persist.
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "smartgrocer.db"
OUTPUT_DIR = PROJECT_ROOT / "exports"  # CSV exports, planogram images, DB backups land here


def ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT UNIQUE NOT NULL,      -- barcode / PLU code
    name_en         TEXT NOT NULL,
    name_si         TEXT,                      -- Sinhala name, for receipts/UI
    category        TEXT NOT NULL,
    unit            TEXT NOT NULL DEFAULT 'pcs',
    cost_price      REAL NOT NULL,
    cash_price      REAL NOT NULL,
    credit_price    REAL NOT NULL,
    wholesale_price REAL NOT NULL,
    pack_size       INTEGER NOT NULL DEFAULT 1,
    reorder_level   INTEGER NOT NULL DEFAULT 10,
    is_perishable   INTEGER NOT NULL DEFAULT 0, -- 1/0
    is_staple       INTEGER NOT NULL DEFAULT 0, -- 1/0, used by layout module
    child_target    INTEGER NOT NULL DEFAULT 0, -- 1/0, used by layout module
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS stock_batches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    batch_no        TEXT,
    qty_received    REAL NOT NULL,
    qty_remaining   REAL NOT NULL,
    received_date   TEXT NOT NULL,              -- ISO date
    expiry_date     TEXT,                        -- ISO date, NULL = non-perishable
    cost_price      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS staff (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    role    TEXT NOT NULL DEFAULT 'cashier',    -- 'admin' | 'cashier'
    pin     TEXT NOT NULL DEFAULT '0000'
);

CREATE TABLE IF NOT EXISTS invoices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no     TEXT UNIQUE NOT NULL,
    datetime       TEXT NOT NULL,               -- ISO datetime
    staff_id       INTEGER REFERENCES staff(id),
    customer_name  TEXT DEFAULT 'Walk-in',
    price_tier     TEXT NOT NULL DEFAULT 'cash', -- cash|credit|wholesale
    payment_type   TEXT NOT NULL DEFAULT 'cash',
    subtotal       REAL NOT NULL,
    discount       REAL NOT NULL DEFAULT 0,
    net_total      REAL NOT NULL,
    cash_paid      REAL NOT NULL DEFAULT 0,
    balance        REAL NOT NULL DEFAULT 0,
    voided         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invoice_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id   INTEGER NOT NULL REFERENCES invoices(id),
    product_id   INTEGER NOT NULL REFERENCES products(id),
    batch_id     INTEGER REFERENCES stock_batches(id),
    qty          REAL NOT NULL,
    unit_price   REAL NOT NULL,
    discount     REAL NOT NULL DEFAULT 0,
    line_total   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS waste_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   INTEGER NOT NULL REFERENCES products(id),
    batch_id     INTEGER NOT NULL REFERENCES stock_batches(id),
    qty_wasted   REAL NOT NULL,
    value_lost   REAL NOT NULL,       -- at cost price
    waste_date   TEXT NOT NULL,       -- ISO date the batch expired unsold
    reason       TEXT NOT NULL DEFAULT 'expired_unsold'
);

CREATE TABLE IF NOT EXISTS layout_assignments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   INTEGER NOT NULL REFERENCES products(id),
    zone         TEXT NOT NULL,
    height_tier  TEXT NOT NULL,
    computed_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_invitems_product ON invoice_items(product_id);
CREATE INDEX IF NOT EXISTS idx_invoices_datetime ON invoices(datetime);
CREATE INDEX IF NOT EXISTS idx_batches_product ON stock_batches(product_id);
CREATE INDEX IF NOT EXISTS idx_batches_expiry ON stock_batches(expiry_date);
"""


def get_conn(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) c FROM staff").fetchone()["c"] == 0:
        conn.execute(
            "INSERT INTO staff (name, role, pin) VALUES (?,?,?)", ("Admin", "admin", "1234")
        )
        conn.execute(
            "INSERT INTO staff (name, role, pin) VALUES (?,?,?)", ("Cashier", "cashier", "0000")
        )
    conn.commit()


def reset_db(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Delete and recreate the database file. Used by the synthetic data generator."""
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    conn = get_conn(db_path)
    init_db(conn)
    return conn
