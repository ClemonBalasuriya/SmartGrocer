"""
Import a real item-list export into the products table.

Why this exists (separate from smartgrocer/catalog.py + data_generator.py):
catalog.py's CATALOG is a small, hand-picked 66-item demo catalogue whose
job is to give the synthetic data generator, the forecasting/association/
layout demos, and the test suite a compact, fully-priced, fully-categorised
product list to work with - swapping a real ~9,000-row shop export in
there would make every demo and test slower and harder to reason about,
for no benefit, since none of that code needs real product data to prove
the underlying algorithms work.

This module is the other half: it loads a REAL shop's item list into the
`products` table of an actual running SmartGrocer database, for actual use
at the till. The shipped smartgrocer/data/real_catalog.csv was built from
Ranapriya Stores' own point-of-sale export (an "Item List for Stock Take"
covering ~9,100 real SKUs) cross-referenced against a month of real invoice
lines (an "Invoice Detail Report") for real selling prices - see
scripts/build_real_catalog.py for exactly how, and re-run it against a
newer export whenever the shop's own item list changes.

Two honest limitations of this real data, both handled explicitly rather
than silently:

1. The stocktake export is an item MASTER list (code + name only) - it
   carries no price list (and no quantities: it's literally a blank
   counting sheet for staff to walk the shelves with). Category and price
   therefore had to be reconstructed:
     - category: a keyword classifier over each item's name. It is
       necessarily imperfect on real, messy, mixed-language shop data -
       an unmatched item lands in "General Grocery" rather than being
       force-fitted into a wrong bucket. A wrong category is fixed the
       same way any catalogue mistake is fixed: Inventory -> Edit Item.
     - price: only ~7% of the 9,100 SKUs appeared in the one month of
       real invoices available, so most rows have no directly observed
       price. Those get the MEDIAN of real observed prices within the
       same category (a much closer guess than one flat number across a
       shop selling everything from rice to phone reload cards). The
       `price_verified` column marks exactly which rows are real
       (1 = seen in an actual sale) versus a placeholder (0) - the
       Inventory screen flags price_verified=0 rows with "CHECK PRICE"
       so nobody mistakes a placeholder for a real, checked price.

2. No opening stock quantities exist in the source data, so importing a
   product creates it with zero stock on hand (exactly like a brand-new
   item added by hand) rather than inventing a stock count - receiving
   real stock is a separate, expected step (Inventory -> Add/Scan Item's
   receive-stock flow) once the physical stocktake this export was
   printed for has actually happened.

Import is safe to re-run: matched by `code`, an existing product's name/
category/prices are refreshed (so re-importing after fixing a mistake in
the CSV updates rows instead of duplicating them), while anything about it
recorded from real use since - stock batches, its `active` flag, sales
history - is left completely untouched.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

DEFAULT_CSV_PATH = Path(__file__).parent / "data" / "real_catalog.csv"

REQUIRED_COLUMNS = {
    "code", "name_en", "category", "unit", "cost_price", "cash_price",
    "pack_size", "reorder_level", "is_perishable", "is_staple", "child_target",
}


def _bool_int(value) -> int:
    return 1 if str(value).strip().lower() in ("1", "true", "yes") else 0


def import_from_csv(conn: sqlite3.Connection, csv_path: str | Path = DEFAULT_CSV_PATH) -> dict:
    """Load a real item-list CSV into the products table.

    Returns {"inserted": n, "updated": n, "skipped": n,
    "needs_price_review": n, "errors": [(code, message), ...]}.

    Never raises for a single bad row - it's recorded in "errors" and the
    import carries on, since one malformed row in a several-thousand-row
    real export shouldn't block every other row from being imported.
    Raises ValueError only if the CSV itself is missing a required column
    (a structural problem, not a per-row one) or can't be found.
    """
    path = Path(csv_path)
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV is missing required column(s): {', '.join(sorted(missing))}")
        rows = list(reader)

    summary = {"inserted": 0, "updated": 0, "skipped": 0, "needs_price_review": 0, "errors": []}
    for row in rows:
        code = (row.get("code") or "").strip()
        name_en = (row.get("name_en") or "").strip()
        if not code or not name_en:
            summary["skipped"] += 1
            summary["errors"].append((code or "(blank)", "missing code or name"))
            continue
        try:
            category = (row.get("category") or "General Grocery").strip() or "General Grocery"
            unit = (row.get("unit") or "pcs").strip() or "pcs"
            cost_price = float(row.get("cost_price") or 0)
            cash_price = float(row.get("cash_price") or 0)
            if cash_price <= 0:
                raise ValueError("cash_price must be greater than zero")
            margin_pct = float(row.get("margin_pct") or 0.18)
            credit_price = round(cash_price * 1.03, 2)
            wholesale_price = round(cost_price * (1 + margin_pct * 0.55), 2) if cost_price > 0 \
                else round(cash_price * 0.85, 2)
            pack_size = int(float(row.get("pack_size") or 1)) or 1
            reorder_level = int(float(row.get("reorder_level") or 10))
            is_perishable = _bool_int(row.get("is_perishable", 0))
            is_staple = _bool_int(row.get("is_staple", 0))
            child_target = _bool_int(row.get("child_target", 0))
            # A row with no price_verified column at all (a hand-written CSV,
            # say) is assumed verified - the flag exists to mark KNOWN
            # placeholders, not to cast doubt on data that never said either way.
            price_verified = _bool_int(row["price_verified"]) if "price_verified" in row else 1
        except (TypeError, ValueError) as exc:
            summary["skipped"] += 1
            summary["errors"].append((code, str(exc)))
            continue

        existing = conn.execute("SELECT id FROM products WHERE code=?", (code,)).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO products
                   (code, name_en, name_si, category, unit, cost_price, cash_price,
                    credit_price, wholesale_price, pack_size, reorder_level,
                    is_perishable, is_staple, child_target, active, price_verified)
                   VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (code, name_en, category, unit, cost_price, cash_price, credit_price,
                 wholesale_price, pack_size, reorder_level, is_perishable, is_staple,
                 child_target, price_verified),
            )
            summary["inserted"] += 1
        else:
            conn.execute(
                """UPDATE products SET name_en=?, category=?, unit=?, cost_price=?,
                       cash_price=?, credit_price=?, wholesale_price=?, pack_size=?,
                       reorder_level=?, is_perishable=?, is_staple=?, child_target=?,
                       price_verified=?
                   WHERE code=?""",
                (name_en, category, unit, cost_price, cash_price, credit_price,
                 wholesale_price, pack_size, reorder_level, is_perishable, is_staple,
                 child_target, price_verified, code),
            )
            summary["updated"] += 1
        if not price_verified:
            summary["needs_price_review"] += 1

    conn.commit()
    return summary
