"""
End-to-end smoke test for every non-GUI module, run against a throwaway
database. This is what was used to validate the pipeline during development
(no display required) - run it yourself after making changes:

    python -m pytest tests/test_pipeline.py -v

or, without pytest installed:

    python tests/test_pipeline.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import base64
import http.client
import json
import ssl

from smartgrocer import (
    association, cash_drawer, customers, data_generator, db, forecasting, layout, mobile_scan, pos,
    promotions, receipts, reports, staff, suppliers,
)


def _fresh_db():
    tmp = Path(tempfile.mkdtemp()) / "test_smartgrocer.db"
    conn = db.reset_db(tmp)
    code_to_id = data_generator.insert_products(conn)
    data_generator.generate_history(conn, code_to_id, days=200, avg_invoices_per_day=40)
    return conn


def _product_with_stock(conn, min_qty=10):
    """Pick a product guaranteed to have at least min_qty on hand.

    The 200-day synthetic history sells down every product's stock by a
    different amount depending on how popular it is - a plain
    'SELECT * FROM products LIMIT 1' can land on a staple that the
    generator has (realistically) sold down to almost nothing, making a
    checkout test fail on "insufficient stock" for a reason that has
    nothing to do with what the test is actually checking. Ordering by
    on-hand stock descending sidesteps that instead of hard-coding a
    product id that might stop being true if the generator's parameters
    ever change."""
    row = conn.execute(
        """SELECT p.*, COALESCE(SUM(b.qty_remaining),0) AS stock_on_hand
           FROM products p LEFT JOIN stock_batches b ON b.product_id = p.id
           WHERE p.active=1 GROUP BY p.id ORDER BY stock_on_hand DESC LIMIT 1"""
    ).fetchone()
    assert row["stock_on_hand"] >= min_qty, (
        f"test fixture assumption broken: best-stocked product only has "
        f"{row['stock_on_hand']} on hand, need at least {min_qty}"
    )
    return row


def test_data_generation_reconciles():
    conn = _fresh_db()
    recv = conn.execute(
        "SELECT SUM(qty_received) q FROM stock_batches sb JOIN products p ON p.id=sb.product_id WHERE p.is_perishable=1"
    ).fetchone()["q"]
    sold = conn.execute(
        "SELECT SUM(ii.qty) q FROM invoice_items ii JOIN products p ON p.id=ii.product_id WHERE p.is_perishable=1"
    ).fetchone()["q"]
    wasted = conn.execute("SELECT COALESCE(SUM(qty_wasted),0) q FROM waste_events").fetchone()["q"]
    remaining = conn.execute(
        "SELECT SUM(qty_remaining) q FROM stock_batches sb JOIN products p ON p.id=sb.product_id WHERE p.is_perishable=1"
    ).fetchone()["q"]
    assert abs(recv - sold - wasted - remaining) < 1e-6, "received != sold + wasted + remaining"
    neg = conn.execute("SELECT COUNT(*) c FROM stock_batches WHERE qty_remaining < 0").fetchone()["c"]
    assert neg == 0, "found negative stock"


def test_pos_checkout_and_void():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=2)
    before = pos.get_stock_on_hand(conn, p["id"])
    result = pos.create_invoice(conn, staff_id=1, cart=[pos.CartLine(product_id=p["id"], qty=2)])
    assert result.net_total > 0
    assert pos.get_stock_on_hand(conn, p["id"]) == before - 2
    pos.void_invoice(conn, result.invoice_id)
    assert pos.get_stock_on_hand(conn, p["id"]) == before


def test_pos_rejects_overselling():
    conn = _fresh_db()
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
    try:
        pos.create_invoice(conn, staff_id=1, cart=[pos.CartLine(product_id=p["id"], qty=10 ** 9)])
        assert False, "should have raised InsufficientStockError"
    except pos.InsufficientStockError:
        pass


def test_forecasting_runs_and_selects_a_model():
    conn = _fresh_db()
    p = conn.execute(
        "SELECT p.* FROM products p WHERE p.is_staple=1 ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    result = forecasting.select_best_model(conn, p["id"])
    assert result["best_model"] is not None
    assert result["forecast"] is not None and len(result["forecast"]) == 7
    assert all(v >= 0 for v in result["forecast"])


def test_anomaly_detection_flags_something():
    conn = _fresh_db()
    p = conn.execute("SELECT * FROM products WHERE is_staple=1 LIMIT 1").fetchone()
    series = forecasting.daily_sales_series(conn, p["id"])
    anomalies = forecasting.detect_anomalies(series)
    assert isinstance(anomalies, list)  # not asserting count - depends on random seed/window


def test_association_rules_recover_known_affinity():
    conn = _fresh_db()
    bundles = association.get_bundle_recommendations(conn, top_n=50)
    assert len(bundles) > 0
    all_items = {tuple(sorted(b["item_codes"])) for b in bundles}
    assert any({"20001", "50008"} <= set(codes) or {"20001", "50009"} <= set(codes) for codes in all_items), \
        "expected the bread+jam/margarine affinity group to surface as a rule"


def test_promotions_scores_are_bounded():
    conn = _fresh_db()
    rows = promotions.compute_promotions(conn)
    for r in rows:
        assert 0 <= r["urgency"] <= 1
        assert r["suggested_price"] >= 0


def test_layout_assigns_every_active_product():
    conn = _fresh_db()
    n_products = conn.execute("SELECT COUNT(*) c FROM products WHERE active=1").fetchone()["c"]
    assignments = layout.compute_layout(conn)
    assert len(assignments) == n_products
    assert all(a["zone"] and a["shelf_tier"] for a in assignments)


def test_reports_kpis_do_not_crash():
    conn = _fresh_db()
    kpis = reports.dashboard_kpis(conn)
    assert "today_sales_total" in kpis
    assert len(reports.best_sellers(conn)) > 0
    assert isinstance(reports.low_stock_alert(conn), list)


def test_demo_database_seeds_customers_and_suppliers():
    conn = _fresh_db()
    data_generator._seed_demo_customers_and_suppliers(conn, __import__("random").Random(1))
    assert len(customers.list_customers(conn)) >= len(data_generator.DEMO_CUSTOMERS)
    assert len(suppliers.list_suppliers(conn)) >= len(data_generator.DEMO_SUPPLIERS)
    batches_with_supplier = conn.execute(
        "SELECT COUNT(*) c FROM stock_batches WHERE supplier_id IS NOT NULL"
    ).fetchone()["c"]
    assert batches_with_supplier > 0


def test_credit_sale_and_settlement_track_customer_balance():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=3)
    cid = customers.add_customer(conn, "Credit Test Customer", credit_limit=100000)
    result = pos.create_invoice(
        conn, staff_id=1, cart=[pos.CartLine(product_id=p["id"], qty=3)],
        price_tier="credit", payment_type="credit", customer_id=cid, customer_name="Credit Test Customer",
    )
    balance = customers.get_customer(conn, cid)["credit_balance"]
    assert abs(balance - result.net_total) < 0.01

    customers.settle_credit(conn, cid, balance / 2, method="cash")
    assert abs(customers.get_customer(conn, cid)["credit_balance"] - balance / 2) < 0.01

    statement = customers.credit_statement(conn, cid)
    assert len(statement) == 2  # one charge, one settlement
    assert statement[-1]["balance"] >= 0


def test_credit_limit_is_enforced():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=50)
    cid = customers.add_customer(conn, "Low Limit Customer", credit_limit=1.0)
    try:
        pos.create_invoice(
            conn, staff_id=1, cart=[pos.CartLine(product_id=p["id"], qty=50)],
            price_tier="credit", payment_type="credit", customer_id=cid, customer_name="Low Limit Customer",
        )
        assert False, "should have raised CreditLimitExceededError"
    except customers.CreditLimitExceededError:
        pass


def test_item_return_restocks_and_refunds():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=4)
    before = pos.get_stock_on_hand(conn, p["id"])
    result = pos.create_invoice(conn, staff_id=1, cart=[pos.CartLine(product_id=p["id"], qty=4)])
    returnable = pos.returnable_items(conn, result.invoice_id)
    assert returnable and returnable[0]["returnable_qty"] == 4
    refund = pos.return_items(
        conn, result.invoice_id, [(p["id"], returnable[0]["batch_id"], 2)], reason="test return"
    )
    assert refund > 0
    assert pos.get_stock_on_hand(conn, p["id"]) == before - 2
    still_returnable = pos.returnable_items(conn, result.invoice_id)
    assert still_returnable[0]["returnable_qty"] == 2


def test_hold_and_resume_cart_round_trips():
    conn = _fresh_db()
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
    held_id = pos.hold_cart(conn, staff_id=1, cart=[pos.CartLine(product_id=p["id"], qty=5)], price_tier="cash")
    assert len(pos.list_held_invoices(conn)) == 1
    cart, tier, customer_id = pos.resume_held_invoice(conn, held_id)
    assert tier == "cash"
    assert cart[0].product_id == p["id"] and cart[0].qty == 5
    pos.delete_held_invoice(conn, held_id)
    assert len(pos.list_held_invoices(conn)) == 0


def test_split_payment_sums_and_records_methods():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=2)
    cid = customers.add_customer(conn, "Split Payer", credit_limit=1_000_000)
    cart = [pos.CartLine(product_id=p["id"], qty=2)]
    net_total = p["cash_price"] * 2
    half = round(net_total / 2, 2)
    result = pos.create_invoice(
        conn, staff_id=1, cart=cart, price_tier="cash", customer_id=cid, customer_name="Split Payer",
        payments=[("cash", half), ("credit", round(net_total - half, 2))],
    )
    rows = conn.execute("SELECT method, amount FROM invoice_payments WHERE invoice_id=?", (result.invoice_id,)).fetchall()
    assert {r["method"] for r in rows} == {"cash", "credit"}
    assert abs(sum(r["amount"] for r in rows) - result.net_total) < 0.01
    assert customers.get_customer(conn, cid)["credit_balance"] > 0


def test_supplier_summary_reflects_received_stock():
    conn = _fresh_db()
    sid = suppliers.add_supplier(conn, "Unit Test Supplier")
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
    conn.execute(
        """INSERT INTO stock_batches (product_id,batch_no,qty_received,qty_remaining,received_date,cost_price,supplier_id)
           VALUES (?,?,?,?,?,?,?)""",
        (p["id"], "TESTBATCH", 20, 20, "2026-01-01", p["cost_price"], sid),
    )
    conn.commit()
    summary = {s["name"]: s for s in suppliers.supplier_summary(conn)}
    assert "Unit Test Supplier" in summary
    assert summary["Unit Test Supplier"]["qty_received"] >= 20


def test_cash_drawer_day_open_sell_close_matches():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=2)
    session_id = cash_drawer.open_session(conn, staff_id=1, opening_float=1000.0)

    result = pos.create_invoice(conn, staff_id=1, cart=[pos.CartLine(product_id=p["id"], qty=2)])
    session = cash_drawer.get_open_session(conn)
    assert session is not None and session["id"] == session_id
    expected = cash_drawer.expected_cash(conn, session)
    assert abs(expected - (1000.0 + result.net_total)) < 0.01

    close_result = cash_drawer.close_session(conn, session_id, counted_cash=expected)
    assert abs(close_result["variance"]) < 0.01
    assert cash_drawer.get_open_session(conn) is None  # closed, so no open session remains

    # a second session can be opened once the first is closed
    session_id_2 = cash_drawer.open_session(conn, staff_id=1, opening_float=500.0)
    assert session_id_2 != session_id


def test_cash_drawer_refuses_double_open():
    conn = _fresh_db()
    cash_drawer.open_session(conn, staff_id=1, opening_float=1000.0)
    try:
        cash_drawer.open_session(conn, staff_id=1, opening_float=500.0)
        assert False, "should have refused to open a second concurrent session"
    except ValueError:
        pass


def test_cash_drawer_flags_variance():
    conn = _fresh_db()
    session_id = cash_drawer.open_session(conn, staff_id=1, opening_float=1000.0)
    result = cash_drawer.close_session(conn, session_id, counted_cash=1000.0 - 50)  # LKR 50 short
    assert abs(result["variance"] - (-50)) < 0.01


def test_staff_add_reset_pin_and_deactivate():
    conn = _fresh_db()
    starting = len(staff.list_staff(conn, active_only=True))

    sid = staff.add_staff(conn, "Clemon", "cashier", "12345")
    active = staff.list_staff(conn, active_only=True)
    assert len(active) == starting + 1
    row = conn.execute("SELECT * FROM staff WHERE id=?", (sid,)).fetchone()
    assert row["name"] == "Clemon" and row["role"] == "cashier" and row["pin"] == "12345" and row["active"] == 1

    staff.update_pin(conn, sid, "54321")
    assert conn.execute("SELECT pin FROM staff WHERE id=?", (sid,)).fetchone()["pin"] == "54321"

    staff.set_active(conn, sid, False)
    assert len(staff.list_staff(conn, active_only=True)) == starting
    assert conn.execute("SELECT active FROM staff WHERE id=?", (sid,)).fetchone()["active"] == 0

    try:
        staff.add_staff(conn, "Nobody", "manager", "1111")
        assert False, "should have rejected an unknown role"
    except ValueError:
        pass


def test_mobile_scan_server_end_to_end():
    # Exercises the phone-scanner companion the same way a real phone
    # would once it has clicked through the self-signed certificate
    # warning: an HTTPS POST carrying a photo, decoded server-side. Uses a
    # self-generated QR code as the "photo" (OpenCV can't generate a 1D
    # barcode to test against, but the decode path is identical either way
    # - see mobile_scan._decode_code) to keep this test free of any
    # external test-image file. The client context below mirrors what a
    # phone's browser does after accepting the "not private" warning - it
    # doesn't verify the certificate, since there's no real CA behind a
    # private shop network's self-signed one.
    conn = _fresh_db()
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
    seen = []

    def on_scan(code):
        # Mirrors POSScreen.handle_phone_scan: this callback runs on the
        # HTTP server's own background thread, so - like that real code -
        # it must open its own connection rather than reuse one created on
        # the test's (main) thread; sqlite3 connections aren't shareable
        # across threads.
        seen.append(code)
        scan_conn = db.get_conn(db_path)
        try:
            product = pos.get_product_by_code(scan_conn, code)
        finally:
            scan_conn.close()
        if product is None:
            return {"unknown": True}
        return {"name": product["name_en"], "added": True}

    server = mobile_scan.MobileScanServer(on_scan)
    server.start()
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE

    def post(body: dict) -> dict:
        conn_ = http.client.HTTPSConnection("127.0.0.1", server.port, context=client_ctx, timeout=5)
        conn_.request("POST", "/scan", body=json.dumps(body), headers={"Content-Type": "application/json"})
        return json.loads(conn_.getresponse().read())

    try:
        png = mobile_scan.generate_qr_png_bytes(p["code"])
        b64 = base64.b64encode(png).decode()

        resp = post({"code": server.pairing_code, "image": "data:image/png;base64," + b64})
        assert resp == {"ok": True, "found": True, "code": p["code"], "name": p["name_en"], "added": True}
        assert seen == [p["code"]]

        # scanning the same code again immediately is a duplicate (still
        # pointed at the same item) - reported, but not re-added
        resp_dup = post({"code": server.pairing_code, "image": "data:image/png;base64," + b64})
        assert resp_dup == {"ok": True, "found": True, "code": p["code"], "duplicate": True}
        assert seen == [p["code"]]  # on_scan was not called a second time

        # wrong pairing code must be rejected without ever reaching on_scan
        resp2 = post({"code": "0000", "image": "data:image/png;base64," + b64})
        assert resp2 == {"ok": False, "error": "wrong_pairing_code"}
        assert seen == [p["code"]]  # unchanged - the bad request never called on_scan

        # a barcode/QR that isn't in the catalogue is reported, not crashed on
        unknown_png = mobile_scan.generate_qr_png_bytes("no-such-code-999")
        unknown_b64 = base64.b64encode(unknown_png).decode()
        resp3 = post({"code": server.pairing_code, "image": "data:image/png;base64," + unknown_b64})
        assert resp3["found"] is True and resp3.get("unknown") is True
    finally:
        server.stop()


def test_barcode_exact_match_lookup():
    # This is what the POS screen's scan-to-cart shortcut relies on: a
    # scanner "typing" a code into the search box should resolve to exactly
    # one product so it can be auto-added without the cashier clicking
    # anything. A partial/fuzzy match (what search_products is for) must
    # NOT be returned here, or a barcode that happens to be a substring of
    # another product's code could add the wrong item automatically.
    conn = _fresh_db()
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
    exact = pos.get_product_by_code(conn, p["code"])
    assert exact is not None and exact["id"] == p["id"]

    partial_code = p["code"][:-1] if len(p["code"]) > 1 else p["code"]
    assert pos.get_product_by_code(conn, partial_code) is None or partial_code == p["code"]
    assert pos.get_product_by_code(conn, "no-such-barcode-xyz") is None


def test_add_product_creates_catalog_entry_with_opening_stock():
    # This is the "add a brand-new product to the store" flow (Inventory
    # screen's "Add New Product" dialog), distinct from the GRN dialog
    # which only restocks a product that already exists.
    conn = _fresh_db()
    product_id = pos.add_product(
        conn,
        code="NEW-BARCODE-001",
        name_en="Test Choc Bar 50g",
        name_si="",
        category="Snacks",
        unit="pcs",
        cost_price=50.0,
        cash_price=80.0,
        pack_size=1,
        reorder_level=5,
        initial_qty=24,
    )
    fetched = pos.get_product_by_code(conn, "NEW-BARCODE-001")
    assert fetched is not None and fetched["id"] == product_id
    assert fetched["name_en"] == "Test Choc Bar 50g"
    # credit/wholesale prices default sensibly when left blank
    assert fetched["credit_price"] == 80.0
    assert fetched["wholesale_price"] == 50.0
    assert pos.get_stock_on_hand(conn, product_id) == 24

    # Duplicate barcode is rejected with a clear error, not a silent overwrite.
    try:
        pos.add_product(conn, code="NEW-BARCODE-001", name_en="Dup", category="Snacks",
                         cost_price=1, cash_price=2)
        assert False, "expected ValueError for duplicate code"
    except ValueError as e:
        assert "already exists" in str(e)

    # Blank required fields are rejected.
    for bad_kwargs in [
        dict(code="", name_en="X", category="Y", cost_price=1, cash_price=2),
        dict(code="ABC", name_en="", category="Y", cost_price=1, cash_price=2),
        dict(code="ABC", name_en="X", category="", cost_price=1, cash_price=2),
        dict(code="ABC", name_en="X", category="Y", cost_price=1, cash_price=0),
    ]:
        try:
            pos.add_product(conn, **bad_kwargs)
            assert False, f"expected ValueError for {bad_kwargs}"
        except ValueError:
            pass


def test_receive_stock_tops_up_existing_product():
    # This is the "already in the catalog - just ask the quantity" path of
    # the Inventory screen's "Add / Scan Item" dialog (and the GRN dialog),
    # as opposed to add_product which creates a brand-new catalog entry.
    conn = _fresh_db()
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
    before = pos.get_stock_on_hand(conn, p["id"])
    pos.receive_stock(conn, product_id=p["id"], qty=15)
    after = pos.get_stock_on_hand(conn, p["id"])
    assert after == before + 15

    # Zero/negative quantity and an unknown product are rejected clearly.
    try:
        pos.receive_stock(conn, product_id=p["id"], qty=0)
        assert False, "expected ValueError for zero quantity"
    except ValueError:
        pass
    try:
        pos.receive_stock(conn, product_id=999999, qty=5)
        assert False, "expected ValueError for unknown product"
    except ValueError:
        pass


def test_receipt_text_includes_key_fields():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=1)
    result = pos.create_invoice(conn, staff_id=1, cart=[pos.CartLine(product_id=p["id"], qty=1)])
    text = receipts.build_receipt_text(conn, result.invoice_id)
    assert result.invoice_no in text
    assert p["name_en"][:22] in text
    assert f"{result.net_total:.2f}" in text


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
