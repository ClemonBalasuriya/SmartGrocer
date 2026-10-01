"""
Build a standalone SQLite evaluation database for the real forecasting-model
comparison (Step 19 of pprs-25-step-progress-plan.md), from the three
Pack & Save dataset files Claude delivered earlier:
    Pack_and_Save_Invoices_and_Summary.xlsx
    Pack_and_Save_ItemLines_Part1_May-Oct2025.xlsx
    Pack_and_Save_ItemLines_Part2_Nov2025-Apr2026.xlsx

This uses the REAL smartgrocer/db.py schema (so forecasting.py's actual
functions run against it exactly as they would in the live app), but writes
to a brand-new file - it never touches the shop's live production database.

Run this with the project's own venv so pandas/openpyxl are available:
    venv\\Scripts\\python.exe scripts\\build_eval_db.py

Expects the three xlsx files to be in the same folder this is run from, or
pass that folder as the first argument, e.g.:
    venv\\Scripts\\python.exe scripts\\build_eval_db.py "C:\\Users\\avish\\OneDrive\\Documents\\Ruhuna Research\\Project\\Ruhuna_Data\\Pack & Save"

Output: pack_and_save_eval.db, written next to the input files.
"""
import sys
import statistics
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartgrocer import db  # noqa: E402

DATA_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
INVOICES_FILE = DATA_DIR / "Pack_and_Save_Invoices_and_Summary.xlsx"
ITEMS_FILE_1 = DATA_DIR / "Pack_and_Save_ItemLines_Part1_May-Oct2025.xlsx"
ITEMS_FILE_2 = DATA_DIR / "Pack_and_Save_ItemLines_Part2_Nov2025-Apr2026.xlsx"
DB_PATH = DATA_DIR / "pack_and_save_eval.db"


def parse_time_12h(t):
    """'10:18:59 am' -> (hh, mm, ss) in 24-hour form, or None."""
    if not isinstance(t, str):
        return None
    t = t.strip().lower()
    try:
        meridiem = t[-2:]
        clock = t[:-2].strip()
        parts = clock.split(":")
        hh = int(parts[0])
        mm = int(parts[1]) if len(parts) > 1 else 0
        ss = int(parts[2]) if len(parts) > 2 else 0
        if meridiem == "am":
            if hh == 12:
                hh = 0
        elif meridiem == "pm":
            if hh != 12:
                hh += 12
        else:
            return None
        return hh, mm, ss
    except Exception:
        return None


def to_iso_datetime(date_val, time_str):
    if date_val is None or pd.isna(date_val):
        return None
    hms = parse_time_12h(time_str)
    if hms is None:
        return None
    hh, mm, ss = hms
    d = date_val.date() if hasattr(date_val, "date") else date_val
    return f"{d.isoformat()} {hh:02d}:{mm:02d}:{ss:02d}"


def main():
    print("Reading Invoices sheet ...")
    inv_df = pd.read_excel(INVOICES_FILE, sheet_name="Invoices")
    print(f"  {len(inv_df)} invoices")

    print("Reading Item_Lines sheets (this can take a minute for 650K+ rows) ...")
    items_df = pd.concat([
        pd.read_excel(ITEMS_FILE_1, sheet_name="Item_Lines"),
        pd.read_excel(ITEMS_FILE_2, sheet_name="Item_Lines"),
    ], ignore_index=True)
    print(f"  {len(items_df)} item lines")

    DB_PATH.unlink(missing_ok=True)
    for ext in ("-wal", "-shm"):
        p = Path(str(DB_PATH) + ext)
        p.unlink(missing_ok=True)
    conn = db.get_conn(str(DB_PATH))
    db.init_db(conn)
    cur = conn.cursor()

    # --- products: one per distinct Item Code, priced from observed sales ---
    grouped = items_df.groupby("Item Code")
    product_id_by_code = {}
    for code, g in grouped:
        price = round(float(statistics.median([p for p in g["Unit Price"] if p and p > 0]) or 1.0), 2)
        cost = round(price * 0.82, 2)
        name = str(g["Item Name"].iloc[0])
        cur.execute(
            """INSERT INTO products (code, name_en, category, unit, cost_price, cash_price,
                   credit_price, wholesale_price, pack_size, reorder_level, is_perishable,
                   is_staple, child_target, active, price_verified)
               VALUES (?,?,?,?,?,?,?,?,1,10,0,0,0,1,1)""",
            (f"PS-{code}", name, "General Grocery", "pcs", cost, price, price, price),
        )
        product_id_by_code[code] = cur.lastrowid
    conn.commit()
    print(f"Inserted {len(product_id_by_code)} products")

    # --- staff: one per distinct cashier name ---
    staff_id_by_name = {}
    for name in sorted(set(items_df["Cashier"].dropna().astype(str))):
        cur.execute("INSERT INTO staff (name, role, pin, active) VALUES (?,?,?,1)",
                    (name, "cashier", "0000"))
        staff_id_by_name[name] = cur.lastrowid
    conn.commit()
    print(f"Inserted {len(staff_id_by_name)} staff (cashiers)")

    # --- invoices ---
    # IMPORTANT: the source report's own "Invoice No" column is NOT a
    # unique receipt number - it cycles through only ~40-50 distinct
    # values per month (confirmed by inspection of the raw PDF exports).
    # Using it directly as invoices.invoice_no (UNIQUE NOT NULL) would make
    # SQLite's "INSERT OR IGNORE" silently drop almost every row after the
    # first ~47 per month, with invoice_items then attaching to whatever
    # row cur.lastrowid happened to point at - corrupting the date
    # attribution of every subsequent item (this was caught and fixed
    # during Claude's own verification run - see eval_results.json's
    # sane nRMSE range as the sanity check if you ever touch this again).
    # (Month, Invoice No, Date, Time) together still uniquely identify
    # each real invoice for the item-join key below (a repeat of all four
    # is the ~0.07% genuine source duplicate noted in the dataset's
    # ReadMe), but the DB's own invoice_no column gets its own
    # guaranteed-unique synthetic value instead.
    invoice_pk_by_key = {}
    seen_keys = set()
    skipped_dupe = 0
    skipped_baddate = 0
    seq = 0
    for _, inv in inv_df.iterrows():
        key = (inv["Month"], inv["Invoice No"], inv["Date"], inv["Time"])
        if key in seen_keys:
            skipped_dupe += 1
            continue
        seen_keys.add(key)
        dt = to_iso_datetime(inv["Date"], inv["Time"])
        if dt is None:
            skipped_baddate += 1
            continue
        seq += 1
        invoice_no = f"{inv['Month']}-{seq:06d}"
        staff_id = staff_id_by_name.get(str(inv.get("Cashier")))
        net_total = float(inv["Net Amount"] or 0.0)
        discount = float(inv["Total Discount"] or 0.0)
        subtotal = net_total + discount
        cur.execute(
            """INSERT INTO invoices (invoice_no, datetime, staff_id, customer_name,
                   price_tier, payment_type, subtotal, discount, net_total, cash_paid, balance,
                   voided)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
            (invoice_no, dt, staff_id, "Walk-in", "cash", "cash", subtotal, discount,
             net_total, net_total, 0.0),
        )
        invoice_pk_by_key[key] = cur.lastrowid
    conn.commit()
    print(f"Inserted {len(invoice_pk_by_key)} invoices "
          f"(skipped {skipped_dupe} duplicate keys, {skipped_baddate} bad dates)")

    # --- invoice_items ---
    item_rows = []
    skipped_no_invoice = 0
    for _, it in items_df.iterrows():
        key = (it["Month"], it["Invoice No"], it["Date"], it["Time"])
        invoice_id = invoice_pk_by_key.get(key)
        if invoice_id is None:
            skipped_no_invoice += 1
            continue
        product_id = product_id_by_code[it["Item Code"]]
        item_rows.append((invoice_id, product_id, float(it["Qty"]), float(it["Unit Price"]),
                           float(it["Unit Discount"] or 0.0), float(it["Net Amount"])))
    cur.executemany(
        """INSERT INTO invoice_items (invoice_id, product_id, qty, unit_price, discount, line_total)
           VALUES (?,?,?,?,?,?)""",
        item_rows,
    )
    conn.commit()
    print(f"Inserted {len(item_rows)} invoice_items (skipped {skipped_no_invoice})")

    conn.close()
    print(f"\nSaved eval database: {DB_PATH}")


if __name__ == "__main__":
    main()
