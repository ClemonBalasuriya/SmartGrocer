"""
Synthetic POS transaction history generator.

Why this exists: SmartGrocer's analytics (forecasting, promotions, bundles,
layout) all need months of real transaction history to be meaningful, but a
brand-new install - or this demo - has none. This module plays the role of
"a shop that has been trading for ~9 months", so every module can be built,
demonstrated and marked against realistic numbers. Swap it out once real
POS history (via pos.py, or an imported PLU/REP export) accumulates.

Design choices (documented so they can be cited/justified in the thesis):
 - Day-of-week and three Sri Lankan festival windows (Sinhala/Tamil New Year,
   Vesak, Christmas) scale daily footfall - this is also what the SARIMAX
   festival-calendar exogenous regressor in forecasting.py is trained against.
 - "Affinity groups" (catalog.AFFINITY_GROUPS) bias basket composition so the
   Apriori module has genuine co-purchase signal to discover, rather than
   pure noise.
 - A handful of products get an artificial slow-down + a large near-expiry
   batch near the end of the window, and one day gets an artificial demand
   shock - both exist purely so the promotion/expiry engine and the anomaly
   detector have something concrete to demonstrate against.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, datetime, timedelta

import numpy as np

from . import db
from .calendar_sl import festival_multiplier
from .catalog import AFFINITY_GROUPS, CATALOG, COLUMNS

RNG_SEED = 42


def insert_products(conn: sqlite3.Connection) -> dict[str, int]:
    """Insert the static catalogue. Returns {code: product_id}."""
    code_to_id: dict[str, int] = {}
    for row in CATALOG:
        rec = dict(zip(COLUMNS, row))
        cost = rec["cost_price"]
        cash_price = round(cost * (1 + rec["margin_pct"]), 2)
        credit_price = round(cash_price * 1.03, 2)
        wholesale_price = round(cost * (1 + rec["margin_pct"] * 0.55), 2)
        reorder_level = 25 if rec["is_staple"] else 12
        cur = conn.execute(
            """INSERT INTO products
               (code,name_en,name_si,category,unit,cost_price,cash_price,credit_price,
                wholesale_price,pack_size,reorder_level,is_perishable,is_staple,child_target)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec["code"], rec["name_en"], rec["name_si"], rec["category"], rec["unit"],
                cost, cash_price, credit_price, wholesale_price, rec["pack_size"],
                reorder_level, rec["is_perishable"], rec["is_staple"], rec["child_target"],
            ),
        )
        code_to_id[rec["code"]] = cur.lastrowid
    conn.commit()
    return code_to_id


DOW_MULT = {0: 1.0, 1: 0.95, 2: 0.95, 3: 1.0, 4: 1.15, 5: 1.35, 6: 1.2}  # Mon..Sun

FESTIVE_CATEGORIES = {"Snacks & Confectionery", "Beverages", "Bread & Bakery", "Dairy"}


def generate_history(
    conn: sqlite3.Connection,
    code_to_id: dict[str, int],
    days: int = 270,
    avg_invoices_per_day: float = 55,
    seed: int = RNG_SEED,
) -> None:
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    products = {rec["code"]: rec for rec in (dict(zip(COLUMNS, r)) for r in CATALOG)}
    for code, pid in code_to_id.items():
        p = products[code]
        p["id"] = pid
        p["reorder_level"] = 25 if p["is_staple"] else 12
        p["cash_price"] = round(p["cost_price"] * (1 + p["margin_pct"]), 2)
        p["credit_price"] = round(p["cash_price"] * 1.03, 2)
        p["wholesale_price"] = round(p["cost_price"] * (1 + p["margin_pct"] * 0.55), 2)

    start_date = date.today() - timedelta(days=days)

    # base popularity weight per code: staples and low-cost items sell more often
    base_weight = {}
    for code, p in products.items():
        w = 3.0 if p["is_staple"] else 1.0
        w *= 1.3 if p["cost_price"] < 200 else 1.0
        base_weight[code] = w

    # a few products get an artificial demand slow-down in the final month,
    # so their sales velocity visibly drops vs. their own history (Sv signal)
    slow_movers = rng.sample([c for c, p in products.items() if p["is_perishable"]], 5)

    # in-memory stock state: code -> list of batch dicts (oldest expiry first)
    stock_state: dict[str, list[dict]] = {code: [] for code in products}

    def restock(code: str, as_of: date, qty: float) -> None:
        p = products[code]
        expiry = (as_of + timedelta(days=p["shelf_life_days"])).isoformat() if p["is_perishable"] else None
        cur = conn.execute(
            """INSERT INTO stock_batches (product_id, batch_no, qty_received, qty_remaining,
               received_date, expiry_date, cost_price) VALUES (?,?,?,?,?,?,?)""",
            (p["id"], f"GRN-{code}-{as_of.isoformat()}", qty, qty, as_of.isoformat(), expiry, p["cost_price"]),
        )
        stock_state[code].append({"batch_id": cur.lastrowid, "remaining": qty, "expiry": expiry})
        stock_state[code].sort(key=lambda b: (b["expiry"] is None, b["expiry"]))

    def remaining_stock(code: str) -> float:
        return sum(b["remaining"] for b in stock_state[code])

    dirty_batches: dict[int, float] = {}  # batch_id -> latest qty_remaining, flushed once per day

    def sell(code: str, qty: float, as_of: date) -> int | None:
        """Deduct qty FIFO by expiry; auto-replenish if short. Returns the batch_id
        the (first, largest) deduction came from, for invoice_items.batch_id.
        Mutates the in-memory FIFO state AND records the new remaining qty in
        dirty_batches so it gets persisted to stock_batches (see flush below) -
        without this, the database would never reflect what was actually sold."""
        if remaining_stock(code) < qty:
            p = products[code]
            top_up = max(p["reorder_level"] * (2 if p["is_perishable"] else 4), qty * 2)
            restock(code, as_of, top_up)
        first_batch = None
        left = qty
        for b in stock_state[code]:
            if left <= 0:
                break
            if b["remaining"] <= 0:
                continue
            take = min(b["remaining"], left)
            b["remaining"] -= take
            dirty_batches[b["batch_id"]] = b["remaining"]
            left -= take
            if first_batch is None:
                first_batch = b["batch_id"]
        return first_batch

    # initial stock for every product so day 1 has something to sell
    for code, p in products.items():
        opening = p["reorder_level"] * (2 if p["is_perishable"] else 6)
        restock(code, start_date - timedelta(days=1), opening)

    invoice_no_seq = 1
    all_codes = list(products.keys())

    for day_offset in range(days):
        d = start_date + timedelta(days=day_offset)
        fmult = festival_multiplier(d)
        dmult = DOW_MULT[d.weekday()]
        lam = avg_invoices_per_day * fmult * dmult
        # one artificial demand-shock day for the anomaly detector to catch
        if day_offset == days - 40:
            lam *= 2.6
        n_invoices = np_rng.poisson(lam)

        day_invoice_rows = []
        day_item_rows = []

        for _ in range(n_invoices):
            basket_size = rng.choices([1, 2, 3, 4, 5, 6], weights=[15, 25, 25, 15, 12, 8])[0]
            chosen: list[str] = []

            if rng.random() < 0.55:
                group = rng.choice(AFFINITY_GROUPS)
                chosen.extend(rng.sample(group, k=min(len(group), max(2, basket_size // 2))))

            weights = []
            for code in all_codes:
                w = base_weight[code]
                if fmult > 1.05 and products[code]["category"] in FESTIVE_CATEGORIES:
                    w *= fmult
                if code in slow_movers and day_offset > days - 30:
                    w *= 0.25
                weights.append(w)

            while len(chosen) < basket_size:
                pick = rng.choices(all_codes, weights=weights, k=1)[0]
                if pick not in chosen:
                    chosen.append(pick)

            tier_roll = rng.random()
            price_tier = "wholesale" if tier_roll > 0.97 else ("credit" if tier_roll > 0.90 else "cash")
            payment_type = "credit" if price_tier == "credit" else rng.choice(["cash", "cash", "card"])

            subtotal = 0.0
            item_lines = []
            for code in chosen:
                p = products[code]
                qty = float(rng.choice([1, 1, 1, 2, 2, 3]) if not p["is_staple"] else rng.choice([1, 2, 2, 3, 5]))
                if price_tier == "wholesale":
                    qty *= rng.choice([3, 5, 8])
                unit_price = {"cash": p["cash_price"], "credit": p["credit_price"],
                              "wholesale": p["wholesale_price"]}[price_tier]
                batch_id = sell(code, qty, d)
                line_total = round(unit_price * qty, 2)
                subtotal += line_total
                item_lines.append((p["id"], batch_id, qty, unit_price, 0.0, line_total))

            discount = round(subtotal * 0.03, 2) if rng.random() < 0.05 else 0.0
            net_total = round(subtotal - discount, 2)
            cash_paid = net_total if payment_type != "credit" else 0.0
            invoice_no = f"INV{invoice_no_seq:07d}"
            invoice_no_seq += 1
            invoice_dt = datetime.combine(d, datetime.min.time()) + timedelta(
                hours=int(np_rng.integers(8, 21)), minutes=int(np_rng.integers(0, 60))
            )
            day_invoice_rows.append(
                (invoice_no, invoice_dt.isoformat(timespec="minutes"), 1 if rng.random() < 0.5 else 2,
                 "Walk-in", price_tier, payment_type, round(subtotal, 2), discount, net_total,
                 cash_paid, round(cash_paid - net_total, 2))
            )
            day_item_rows.append((invoice_no, item_lines))

        # flush the day: insert invoices, map invoice_no -> id, insert items
        for inv_row in day_invoice_rows:
            conn.execute(
                """INSERT INTO invoices (invoice_no,datetime,staff_id,customer_name,price_tier,
                   payment_type,subtotal,discount,net_total,cash_paid,balance)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                inv_row,
            )
        conn.commit()

        if day_item_rows:
            invoice_ids = {
                r["invoice_no"]: r["id"]
                for r in conn.execute(
                    "SELECT id, invoice_no FROM invoices WHERE invoice_no IN (%s)"
                    % ",".join("?" * len(day_item_rows)),
                    [no for no, _ in day_item_rows],
                )
            }
            flat_items = []
            for invoice_no, lines in day_item_rows:
                inv_id = invoice_ids[invoice_no]
                for (product_id, batch_id, qty, unit_price, disc, line_total) in lines:
                    flat_items.append((inv_id, product_id, batch_id, qty, unit_price, disc, line_total))
            conn.executemany(
                """INSERT INTO invoice_items (invoice_id,product_id,batch_id,qty,unit_price,discount,line_total)
                   VALUES (?,?,?,?,?,?,?)""",
                flat_items,
            )
            conn.commit()

        if dirty_batches:
            conn.executemany(
                "UPDATE stock_batches SET qty_remaining=? WHERE id=?",
                [(v, k) for k, v in dirty_batches.items()],
            )
            conn.commit()
            dirty_batches.clear()

    end_date = start_date + timedelta(days=days - 1)

    # Any batch whose expiry fell before "today" but still shows remaining
    # stock represents real-world shrinkage the shop already absorbed - write
    # it off rather than leaving ghost inventory, and log it so the system
    # can report historical waste (baseline evidence for the expiry-reduction
    # objective in the proposal).
    stale = conn.execute(
        """SELECT id, product_id, qty_remaining, expiry_date, cost_price FROM stock_batches
           WHERE expiry_date IS NOT NULL AND date(expiry_date) < date(?) AND qty_remaining > 0""",
        (end_date.isoformat(),),
    ).fetchall()
    for b in stale:
        conn.execute(
            "INSERT INTO waste_events (product_id,batch_id,qty_wasted,value_lost,waste_date,reason) VALUES (?,?,?,?,?,?)",
            (b["product_id"], b["id"], b["qty_remaining"], round(b["qty_remaining"] * b["cost_price"], 2),
             b["expiry_date"], "expired_unsold"),
        )
        conn.execute("UPDATE stock_batches SET qty_remaining=0 WHERE id=?", (b["id"],))
    conn.commit()

    # Guarantee a few concrete near-expiry, slow-moving batches "as of today"
    # so the promotions/expiry screen has something real to show immediately.
    for i, code in enumerate(slow_movers):
        restock(code, end_date - timedelta(days=1), qty=18 + i * 3)
        stock_state[code][-1]["expiry"] = (end_date + timedelta(days=2 + i)).isoformat()
        conn.execute(
            "UPDATE stock_batches SET expiry_date=? WHERE id=?",
            (stock_state[code][-1]["expiry"], stock_state[code][-1]["batch_id"]),
        )
    conn.commit()


DEMO_CUSTOMERS = [
    ("Nimal Perera", "0771234567", 20000.0),
    ("Kamala Silva", "0779876543", 12000.0),
    ("Sunil Fernando Stores", "0712223344", 35000.0),
    ("Ruwani Jayasuriya", "0765554433", 10000.0),
]

DEMO_SUPPLIERS = [
    ("Ceylon Grocery Distributors", "0112345678", "Colombo"),
    ("Southern Wholesale Traders", "0912233445", "Matara"),
    ("Ruhuna Fresh Produce", "0413344556", "Galle"),
]


def _seed_demo_customers_and_suppliers(conn: sqlite3.Connection, rng: random.Random) -> None:
    """Attach a handful of realistic customer/supplier records to the
    synthetic history, so the Customers/Suppliers screens and the credit
    settlement report have something meaningful to show out of the box."""
    customer_ids = []
    for name, phone, limit in DEMO_CUSTOMERS:
        cur = conn.execute(
            "INSERT INTO customers (name, phone, credit_limit) VALUES (?,?,?)", (name, phone, limit)
        )
        customer_ids.append(cur.lastrowid)

    supplier_ids = []
    for name, phone, address in DEMO_SUPPLIERS:
        cur = conn.execute(
            "INSERT INTO suppliers (name, phone, address) VALUES (?,?,?)", (name, phone, address)
        )
        supplier_ids.append(cur.lastrowid)
    conn.commit()

    # Spread the existing supplier-less stock batches across the demo suppliers.
    batch_ids = [r["id"] for r in conn.execute("SELECT id FROM stock_batches").fetchall()]
    conn.executemany(
        "UPDATE stock_batches SET supplier_id=? WHERE id=?",
        [(supplier_ids[i % len(supplier_ids)], bid) for i, bid in enumerate(batch_ids)],
    )

    # Attach only a recent, bounded slice of the synthetic credit-tier
    # invoices to demo customers (round-robin) - attaching all ~9 months of
    # credit sales to just 4 accounts would run their balances into the
    # hundreds of thousands against 5k-25k credit limits, which looks broken
    # rather than like a real, currently-in-use credit account. Older credit
    # invoices stay unlinked (customer_id NULL) - still real historical sales,
    # just not attributed to a live account balance.
    max_credit_invoices_per_customer = 4
    credit_invoice_ids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM invoices WHERE payment_type='credit' ORDER BY datetime DESC "
            "LIMIT ?", (max_credit_invoices_per_customer * len(customer_ids),)
        ).fetchall()
    ]
    conn.executemany(
        "UPDATE invoices SET customer_id=? WHERE id=?",
        [(customer_ids[i % len(customer_ids)], iid) for i, iid in enumerate(credit_invoice_ids)],
    )
    conn.commit()

    for cid in customer_ids:
        total = conn.execute(
            "SELECT COALESCE(SUM(net_total),0) t FROM invoices WHERE customer_id=? AND payment_type='credit' AND voided=0",
            (cid,),
        ).fetchone()["t"]
        conn.execute("UPDATE customers SET credit_balance=? WHERE id=?", (round(total, 2), cid))
    conn.commit()

    for cid in customer_ids[:2]:
        balance = conn.execute("SELECT credit_balance FROM customers WHERE id=?", (cid,)).fetchone()[
            "credit_balance"
        ]
        if balance > 0:
            pay = round(balance * rng.uniform(0.3, 0.7), 2)
            conn.execute(
                "INSERT INTO credit_settlements (customer_id, amount, method, settled_at, note) "
                "VALUES (?,?,?,?,?)",
                (cid, pay, "cash", datetime.now().isoformat(timespec="seconds"), "Demo part-payment"),
            )
            conn.execute("UPDATE customers SET credit_balance = credit_balance - ? WHERE id=?", (pay, cid))
    conn.commit()


def build_demo_database(db_path=None) -> None:
    path = db_path or db.DEFAULT_DB_PATH
    conn = db.reset_db(path)
    code_to_id = insert_products(conn)
    generate_history(conn, code_to_id)
    _seed_demo_customers_and_suppliers(conn, random.Random(42))
    conn.close()


if __name__ == "__main__":
    build_demo_database()
    print("Demo database built at", db.DEFAULT_DB_PATH)
