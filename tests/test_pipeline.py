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
    association, cash_drawer, customers, data_generator, db, forecasting, layout, mobile_scan, netclient,
    netserver, offers, pos, promotions, receipts, reports, staff, suppliers,
)


def _fresh_db():
    tmp = Path(tempfile.mkdtemp()) / "test_smartgrocer.db"
    conn = db.reset_db(tmp)
    code_to_id = data_generator.insert_products(conn)
    data_generator.generate_history(conn, code_to_id, days=200, avg_invoices_per_day=40)
    return conn


def _fresh_db_with_path():
    """Like _fresh_db, but also hands back the file path - needed for
    multi-till tests, where a second, independent connection (the local
    verification connection, or another simulated till) has to open the
    exact same file rather than share the in-memory conn object."""
    tmp = Path(tempfile.mkdtemp()) / "test_smartgrocer.db"
    conn = db.reset_db(tmp)
    code_to_id = data_generator.insert_products(conn)
    data_generator.generate_history(conn, code_to_id, days=200, avg_invoices_per_day=40)
    return conn, tmp


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

    try:
        staff.add_staff(conn, "", "cashier", "1111")
        assert False, "should have rejected a blank name"
    except ValueError:
        pass

    # Optional email/phone, used by notifications.py to message this
    # person directly (e.g. their new PIN) rather than only the Owner.
    with_contact_id = staff.add_staff(conn, "Nimal", "cashier", "2222",
                                       email="nimal@example.com", phone="+94771234567")
    contact_row = conn.execute("SELECT * FROM staff WHERE id=?", (with_contact_id,)).fetchone()
    assert contact_row["email"] == "nimal@example.com"
    assert contact_row["phone"] == "+94771234567"

    # Blank contact fields stay NULL, not empty strings.
    no_contact_id = staff.add_staff(conn, "Saman", "cashier", "3333")
    no_contact_row = conn.execute("SELECT * FROM staff WHERE id=?", (no_contact_id,)).fetchone()
    assert no_contact_row["email"] is None and no_contact_row["phone"] is None

    try:
        staff.add_staff(conn, "Bad Email", "cashier", "4444", email="not-an-email")
        assert False, "should have rejected a malformed email"
    except ValueError:
        pass


def test_exactly_one_owner_seeded_by_default_never_two_never_deactivatable():
    # A fresh database must come with exactly one Owner account already in
    # it (PIN 1234, changeable via "Change My PIN") - the shop should never
    # be in a state with zero Owners, and Admin/Cashier must keep their
    # familiar ids (Admin=1) since a few places (this suite included) treat
    # id 1 as "the default account" before anyone has logged in.
    conn = _fresh_db()
    admin_row = conn.execute("SELECT * FROM staff WHERE id=1").fetchone()
    assert admin_row["name"] == "Admin" and admin_row["role"] == "admin"
    owners = conn.execute("SELECT * FROM staff WHERE role='owner'").fetchall()
    assert len(owners) == 1
    assert owners[0]["pin"] == "1234"
    owner_id = owners[0]["id"]

    # Can't create a second Owner, even though 'owner' is otherwise a
    # perfectly valid role value.
    try:
        staff.add_staff(conn, "Second Owner", "owner", "9999")
        assert False, "should have rejected creating a second Owner"
    except ValueError as e:
        assert "already exists" in str(e)

    # Can't deactivate the Owner account, unlike any other role.
    try:
        staff.set_active(conn, owner_id, False)
        assert False, "should have rejected deactivating the Owner"
    except ValueError:
        pass
    assert conn.execute("SELECT active FROM staff WHERE id=?", (owner_id,)).fetchone()["active"] == 1

    # An ordinary admin/cashier can still be deactivated as normal.
    cashier_id = staff.add_staff(conn, "Ordinary Cashier", "cashier", "5555")
    staff.set_active(conn, cashier_id, False)
    assert conn.execute("SELECT active FROM staff WHERE id=?", (cashier_id,)).fetchone()["active"] == 0


def test_update_contact_sets_and_clears_email_phone_without_touching_pin():
    conn = _fresh_db()
    sid = staff.add_staff(conn, "Clemon", "cashier", "12345")
    before_pin = conn.execute("SELECT pin FROM staff WHERE id=?", (sid,)).fetchone()["pin"]

    staff.update_contact(conn, sid, "clemon@example.com", "+94771111111")
    row = conn.execute("SELECT * FROM staff WHERE id=?", (sid,)).fetchone()
    assert row["email"] == "clemon@example.com"
    assert row["phone"] == "+94771111111"
    assert row["pin"] == before_pin  # never touched by this

    # Blanking both fields clears them to NULL, not empty strings.
    staff.update_contact(conn, sid, "", "")
    cleared = conn.execute("SELECT * FROM staff WHERE id=?", (sid,)).fetchone()
    assert cleared["email"] is None and cleared["phone"] is None

    try:
        staff.update_contact(conn, sid, "not-an-email", "")
        assert False, "expected ValueError for malformed email"
    except ValueError:
        pass


def test_notifications_debug_mode_reports_why_nothing_was_sent():
    from smartgrocer import notifications

    # Nothing configured at all and no recipient given -> no errors either,
    # since there was nothing to even attempt (matches the plain, non-debug
    # behavior of returning an empty list quietly).
    notifications.save(dict(notifications._DEFAULT))
    sent, errors = notifications.send("Test", "hello", debug=True)
    assert sent == [] and errors == []

    # A recipient IS given (as gui/app.py's Forgot PIN does) but email is
    # disabled - must explain why, not just fail silently.
    sent, errors = notifications.send("Test", "hello", to_email="cashier@example.com", debug=True)
    assert sent == []
    assert any(ch == "email" for ch, _ in errors)

    # Enabled but pointed at nothing listening - the real exception text
    # should come back, not just "email" with no explanation.
    cfg = dict(notifications._DEFAULT)
    cfg.update(email_enabled=True, smtp_host="127.0.0.1", smtp_port=1,
               smtp_user="shop@example.com", smtp_app_password="x", owner_email="owner@example.com")
    notifications.save(cfg)
    sent, errors = notifications.send("Test", "hello", debug=True)
    assert sent == []
    assert len(errors) == 1 and errors[0][0] == "email" and errors[0][1]  # non-empty reason string

    notifications.save(dict(notifications._DEFAULT))


def test_generate_temp_pin_is_numeric_and_varies():
    # Used by gui/app.py's "Forgot PIN?" self-service reset - a random PIN
    # emailed to the account's saved address, never shown on screen.
    pins = {staff.generate_temp_pin() for _ in range(20)}
    assert len(pins) > 1, "should not generate the same PIN every time"
    for pin in pins:
        assert len(pin) == 6
        assert pin.isdigit()
    assert len(staff.generate_temp_pin(length=4)) == 4


def test_audit_log_records_who_did_what_to_whom():
    from smartgrocer import audit
    conn = _fresh_db()
    cashier_id = staff.add_staff(conn, "Clemon", "cashier", "12345")

    audit.record(conn, actor_staff_id=1, action="staff.add", target_staff_id=cashier_id,
                 details="added as cashier")
    audit.record(conn, actor_staff_id=1, action="staff.pin_reset", target_staff_id=cashier_id)

    log = audit.list_log(conn)
    assert len(log) == 2
    newest = log[0]  # list_log orders newest first
    assert newest["action"] == "staff.pin_reset"
    assert newest["target_staff_id"] == cashier_id
    assert newest["target_name"] == "Clemon"
    assert newest["actor_name"] == "Admin" and newest["actor_role"] == "admin"

    # The snapshot survives the actor/target changing later.
    staff.set_active(conn, cashier_id, False)
    log_after = audit.list_log(conn)
    assert log_after[0]["target_name"] == "Clemon"  # unchanged even though Clemon is now inactive


def test_notifications_config_round_trips_and_send_is_a_safe_noop_when_unconfigured():
    from smartgrocer import notifications
    # Nothing configured yet (fresh default) - send() must not raise and
    # must not claim any channel succeeded.
    notifications.save(dict(notifications._DEFAULT))
    assert notifications.send("Test", "hello") == []

    cfg = notifications.load()
    cfg["email_enabled"] = True
    cfg["smtp_host"] = "127.0.0.1"  # nothing listening here - fails fast (connection refused) rather
    cfg["smtp_port"] = 1            # than spending the full network timeout against a real SMTP host
    cfg["smtp_user"] = "shop@example.com"
    cfg["smtp_app_password"] = "not-a-real-password"
    cfg["owner_email"] = "owner@example.com"
    notifications.save(cfg)
    reloaded = notifications.load()
    assert reloaded["email_enabled"] is True
    assert reloaded["owner_email"] == "owner@example.com"

    # An unreachable/failing SMTP server must fail quietly, not raise.
    assert notifications.send("Test", "hello") == []

    notifications.save(dict(notifications._DEFAULT))  # leave shared test state clean


def test_notifications_send_targets_the_right_recipient():
    from smartgrocer import notifications

    cfg = dict(notifications._DEFAULT)
    cfg.update(email_enabled=True, smtp_user="shop@example.com", smtp_app_password="x",
               owner_email="owner@example.com")
    notifications.save(cfg)

    sent_to = []
    original = notifications._send_email
    notifications._send_email = lambda config, subject, message, to_addr: sent_to.append(to_addr)
    try:
        # Not passing to_email at all -> defaults to the Owner's address.
        assert notifications.send("Subject", "msg") == ["email"]
        assert sent_to == ["owner@example.com"]

        # Passing a real address -> that person, not the Owner.
        sent_to.clear()
        assert notifications.send("Subject", "msg", to_email="cashier@example.com") == ["email"]
        assert sent_to == ["cashier@example.com"]

        # Passing None explicitly (this staff member has no email on file)
        # -> skip the email channel entirely, do NOT fall back to Owner.
        sent_to.clear()
        assert notifications.send("Subject", "msg", to_email=None) == []
        assert sent_to == []
    finally:
        notifications._send_email = original
        notifications.save(dict(notifications._DEFAULT))


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


def test_netserver_lets_a_remote_till_share_the_same_database():
    # This is the multi-cashier-till feature: a "Cashier Till" runs the
    # ordinary app but with a netclient.RemoteConnection standing in for
    # self.app.conn instead of a local sqlite3 connection. Every existing
    # module (pos.py here) must work completely unchanged against it, and
    # what it writes must actually land in the shared database file - not
    # some private copy - which is verified via a second, independent
    # local connection to the same file.
    conn, db_path = _fresh_db_with_path()
    server = netserver.NetDBServer(db_path)
    server.start(port=0)  # random free port - avoids clashing with a real Main Till or another test
    try:
        remote = netclient.RemoteConnection("127.0.0.1", server.port, server.pairing_code)
        try:
            assert remote.ping() is True

            product_id = pos.add_product(
                remote, code="REMOTE-TILL-001", name_en="Remote Till Item", category="Snacks",
                cost_price=10, cash_price=25, initial_qty=40,
            )
            # Visible from a completely separate, local connection to the same file.
            local_row = conn.execute("SELECT * FROM products WHERE code=?", ("REMOTE-TILL-001",)).fetchone()
            assert local_row is not None and local_row["id"] == product_id

            # A full checkout (multi-statement, uses executemany internally)
            # works the same over the network as it does locally.
            result = pos.create_invoice(remote, staff_id=1, cart=[pos.CartLine(product_id=product_id, qty=5)])
            assert result.net_total == 125.0
            assert pos.get_stock_on_hand(conn, product_id) == 35

            # staff.py (a different module, unmodified for this feature)
            # also works unchanged over the same remote connection.
            new_staff_id = staff.add_staff(remote, "Remote Cashier", "cashier", "9999")
            assert any(s["id"] == new_staff_id for s in staff.list_staff(conn))
        finally:
            remote.close()
    finally:
        server.stop()


def test_netserver_rejects_wrong_pairing_code_and_stale_session():
    conn, db_path = _fresh_db_with_path()
    server = netserver.NetDBServer(db_path)
    server.start(port=0)  # random free port - avoids clashing with a real Main Till or another test
    try:
        try:
            netclient.RemoteConnection("127.0.0.1", server.port, "0000")
            assert False, "expected RemoteError for a wrong pairing code"
        except netclient.RemoteError:
            pass

        remote = netclient.RemoteConnection("127.0.0.1", server.port, server.pairing_code)
        remote.close()  # server now forgets this session
        try:
            remote.execute("SELECT 1")
            assert False, "expected RemoteError for a disconnected session"
        except netclient.RemoteError:
            pass
    finally:
        server.stop()


def test_netserver_reports_duplicate_barcode_as_a_value_error():
    # A raw constraint violation from the remote database (bypassing
    # add_product's own pre-check, to exercise the actual network error
    # path a genuine two-till race could hit) must surface as a ValueError
    # so it lands in the same error dialogs as a local duplicate does,
    # not as an unhandled network exception.
    conn, db_path = _fresh_db_with_path()
    server = netserver.NetDBServer(db_path)
    server.start(port=0)  # random free port - avoids clashing with a real Main Till or another test
    try:
        remote = netclient.RemoteConnection("127.0.0.1", server.port, server.pairing_code)
        try:
            existing = conn.execute("SELECT code FROM products LIMIT 1").fetchone()
            try:
                remote.execute(
                    "INSERT INTO products (code,name_en,category,cost_price,cash_price,credit_price,"
                    "wholesale_price) VALUES (?,?,?,?,?,?,?)",
                    (existing["code"], "Dup", "Snacks", 1, 2, 2, 1),
                )
                assert False, "expected a duplicate-code error"
            except ValueError as e:
                assert isinstance(e, netclient.RemoteIntegrityError)
        finally:
            remote.close()
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


def test_barcode_matches_upc_a_and_ean_13_forms_of_the_same_code():
    # The phone-scanner camera decode doesn't always agree on whether a
    # barcode is UPC-A (12 digits) or its EAN-13-equivalent (13 digits,
    # same number with a leading zero) - a product saved from one reading
    # must still be found when a later scan reports the other form,
    # otherwise "add it once, scan it again at checkout" can wrongly say
    # the item isn't in the catalog even though it plainly is.
    conn = _fresh_db()
    pos.add_product(conn, code="012345678905", name_en="UPC Test Item", category="Snacks",
                     cost_price=10, cash_price=20)
    # Saved as 12-digit UPC-A; must also be found by its 13-digit EAN-13 form.
    found = pos.get_product_by_code(conn, "0012345678905")
    assert found is not None and found["name_en"] == "UPC Test Item"

    pos.add_product(conn, code="0098765432109", name_en="EAN Test Item", category="Snacks",
                     cost_price=10, cash_price=20)
    # Saved as 13-digit EAN-13; must also be found by its 12-digit UPC-A form.
    found2 = pos.get_product_by_code(conn, "098765432109")
    assert found2 is not None and found2["name_en"] == "EAN Test Item"

    # Short PLU-style codes are untouched by this - no false matches.
    pos.add_product(conn, code="4821", name_en="Loose Bananas", category="Produce",
                     cost_price=1, cash_price=2)
    assert pos.get_product_by_code(conn, "04821") is None
    assert pos.get_product_by_code(conn, "482") is None

    # Adding the same barcode again under its other numeral form is
    # rejected as a duplicate, not created as a second product record.
    try:
        pos.add_product(conn, code="0012345678905", name_en="Dup", category="Snacks",
                         cost_price=1, cash_price=2)
        assert False, "expected ValueError for a duplicate barcode in its other numeral form"
    except ValueError as e:
        assert "already exists" in str(e)


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


def test_update_product_edits_details_without_touching_stock():
    conn = _fresh_db()
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
    before_stock = pos.get_stock_on_hand(conn, p["id"])

    pos.update_product(
        conn, p["id"], name_en="Renamed Item", category="New Category", unit="pcs",
        cost_price=p["cost_price"], cash_price=99.5, pack_size=2, reorder_level=7,
        is_perishable=True,
    )
    updated = conn.execute("SELECT * FROM products WHERE id=?", (p["id"],)).fetchone()
    assert updated["name_en"] == "Renamed Item"
    assert updated["category"] == "New Category"
    assert updated["cash_price"] == 99.5
    assert updated["pack_size"] == 2
    assert updated["reorder_level"] == 7
    assert updated["is_perishable"] == 1
    assert updated["code"] == p["code"]  # barcode is not editable through update_product
    assert pos.get_stock_on_hand(conn, p["id"]) == before_stock  # untouched

    # Bad input is rejected the same way add_product rejects it.
    try:
        pos.update_product(conn, p["id"], name_en="", category="X", cost_price=1, cash_price=2)
        assert False, "expected ValueError for blank name"
    except ValueError:
        pass
    try:
        pos.update_product(conn, 999999, name_en="X", category="Y", cost_price=1, cash_price=2)
        assert False, "expected ValueError for unknown product"
    except ValueError:
        pass


def test_deactivate_and_reactivate_product_zeroes_and_restores_visibility():
    # Mirrors staff.py's "deactivate, never delete" pattern: removing an
    # item must not delete its row (stock_batches/invoice_items reference
    # it) and, per how this shop wants it, should zero its stock rather
    # than leaving stale phantom quantity behind.
    conn = _fresh_db()
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
    assert pos.get_stock_on_hand(conn, p["id"]) > 0  # sanity check on the seeded demo data

    pos.deactivate_product(conn, p["id"])
    row = conn.execute("SELECT * FROM products WHERE id=?", (p["id"],)).fetchone()
    assert row["active"] == 0
    assert pos.get_stock_on_hand(conn, p["id"]) == 0
    # It disappears from the active catalog lookup used at checkout...
    assert pos.get_product_by_code(conn, p["code"]) is None
    # ...but the row itself, and its stock batch history, still exist.
    assert pos.get_product_by_code_any(conn, p["code"]) is not None
    batches_still_present = conn.execute(
        "SELECT COUNT(*) c FROM stock_batches WHERE product_id=?", (p["id"],)
    ).fetchone()["c"]
    assert batches_still_present > 0

    pos.reactivate_product(conn, p["id"])
    row_after = conn.execute("SELECT * FROM products WHERE id=?", (p["id"],)).fetchone()
    assert row_after["active"] == 1
    assert pos.get_product_by_code(conn, p["code"]) is not None
    assert pos.get_stock_on_hand(conn, p["id"]) == 0  # stays zero until restocked, doesn't come back on its own

    try:
        pos.deactivate_product(conn, 999999)
        assert False, "expected ValueError for unknown product"
    except ValueError:
        pass


def test_till_config_round_trips_and_defaults_to_standalone():
    from smartgrocer import till_config
    # A fresh install (no config file written yet) must behave exactly like
    # today's single-till app - "standalone" is the only mode that changes
    # nothing about existing behavior.
    default = till_config.load()
    assert default["mode"] == "standalone"

    till_config.save("server", pairing_code="1234")
    reloaded = till_config.load()
    assert reloaded["mode"] == "server"
    assert reloaded["pairing_code"] == "1234"

    till_config.save("client", server_host="192.168.1.5", server_port=8765, pairing_code="4321")
    reloaded2 = till_config.load()
    assert reloaded2 == {
        "mode": "client", "server_host": "192.168.1.5", "server_port": 8765, "pairing_code": "4321",
    }

    try:
        till_config.save("not-a-real-mode")
        assert False, "expected ValueError for an invalid mode"
    except ValueError:
        pass

    till_config.save("standalone")  # leave shared test state clean for anything that runs after this


def test_receipt_text_includes_key_fields():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=1)
    result = pos.create_invoice(conn, staff_id=1, cart=[pos.CartLine(product_id=p["id"], qty=1)])
    text = receipts.build_receipt_text(conn, result.invoice_id)
    assert result.invoice_no in text
    assert p["name_en"][:22] in text
    assert f"{result.net_total:.2f}" in text


def test_walk_in_is_not_a_loyalty_member_but_a_registered_customer_is():
    conn = _fresh_db()
    walk_in = conn.execute("SELECT * FROM customers WHERE name='Walk-in'").fetchone()
    assert not customers.is_loyalty_member(walk_in)
    assert not customers.is_loyalty_member(None)

    cid = customers.add_customer(conn, "Loyalty Person", phone="0771234567")
    member = customers.get_customer(conn, cid)
    assert customers.is_loyalty_member(member)


def test_phone_number_is_the_loyalty_id_and_must_be_unique():
    """A customer's phone number IS their loyalty ID (see customers.py) -
    typed in at checkout to find them by name, so two active customers
    sharing one number would make a lookup ambiguous."""
    conn = _fresh_db()
    cid1 = customers.add_customer(conn, "Person One", phone="0711111111")
    customers.add_customer(conn, "Person Two", phone="0722222222")

    assert customers.find_customer_by_phone(conn, "0711111111")["id"] == cid1
    assert customers.find_customer_by_phone(conn, "0000000000") is None
    # Walk-in itself must never be findable by "phone" - it has none, and
    # even if it did, it isn't a real loyalty account.
    assert customers.find_customer_by_phone(conn, "") is None

    # Registering a second customer with an already-taken number is rejected...
    try:
        customers.add_customer(conn, "Person Three", phone="0711111111")
        assert False, "should have rejected a duplicate phone number"
    except ValueError:
        pass
    # ...as is editing someone else's number to collide with it...
    cid3 = customers.add_customer(conn, "Person Three", phone="0733333333")
    try:
        customers.update_customer(conn, cid3, "Person Three", "0711111111", "", 0)
        assert False, "should have rejected a duplicate phone number"
    except ValueError:
        pass
    # ...but a customer keeping their own number when saving other edits is fine.
    customers.update_customer(conn, cid1, "Person One Renamed", "0711111111", "", 0)
    assert customers.get_customer(conn, cid1)["name"] == "Person One Renamed"


def test_offer_scoped_to_loyalty_only_applies_to_registered_customers():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=5)
    offers.add_offer(conn, "Members-only Milk Deal", 20, product_id=p["id"], scope=offers.SCOPE_LOYALTY)

    pct_loyalty, name = offers.best_discount_pct(conn, p["id"], p["category"], is_loyalty=True)
    assert pct_loyalty == 20
    assert name == "Members-only Milk Deal"

    pct_walkin, name_walkin = offers.best_discount_pct(conn, p["id"], p["category"], is_loyalty=False)
    assert pct_walkin == 0
    assert name_walkin is None


def test_offer_scoped_to_everyone_applies_regardless_of_loyalty():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=5)
    offers.add_offer(conn, "Everyone Discount", 10, product_id=p["id"], scope=offers.SCOPE_ALL)
    for is_loyalty in (True, False):
        pct, name = offers.best_discount_pct(conn, p["id"], p["category"], is_loyalty=is_loyalty)
        assert pct == 10
        assert name == "Everyone Discount"


def test_category_offer_applies_to_every_product_in_it_and_biggest_discount_wins():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=5)
    offers.add_offer(conn, "Category Wide", 5, category=p["category"], scope=offers.SCOPE_ALL)
    offers.add_offer(conn, "Bigger Product Deal", 15, product_id=p["id"], scope=offers.SCOPE_ALL)
    pct, name = offers.best_discount_pct(conn, p["id"], p["category"], is_loyalty=False)
    assert pct == 15  # the bigger of the two applicable offers wins, never the smaller
    assert name == "Bigger Product Deal"


def test_offer_outside_its_date_window_does_not_apply():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=5)
    from datetime import date as _date, timedelta as _timedelta
    yesterday = (_date.today() - _timedelta(days=1)).isoformat()
    last_week = (_date.today() - _timedelta(days=7)).isoformat()
    offers.add_offer(conn, "Expired Deal", 30, product_id=p["id"], start_date=last_week, end_date=yesterday)
    pct, name = offers.best_discount_pct(conn, p["id"], p["category"], is_loyalty=True)
    assert pct == 0
    assert name is None


def test_deactivated_offer_does_not_apply():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=5)
    offer_id = offers.add_offer(conn, "Turned Off", 25, product_id=p["id"])
    offers.set_active(conn, offer_id, False)
    pct, name = offers.best_discount_pct(conn, p["id"], p["category"], is_loyalty=True)
    assert pct == 0
    assert name is None


def test_offer_needs_exactly_one_of_product_or_category():
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=5)
    try:
        offers.add_offer(conn, "Neither", 10)
        assert False, "expected ValueError with no product/category"
    except ValueError:
        pass
    try:
        offers.add_offer(conn, "Both", 10, product_id=p["id"], category=p["category"])
        assert False, "expected ValueError with both product AND category"
    except ValueError:
        pass


def test_line_discount_amount_flows_through_create_invoice_as_a_flat_amount():
    """The GUI computes offers.line_discount_amount and stores it on
    pos.CartLine.discount before calling create_invoice - this checks that
    round trip end to end, the way POSScreen.checkout actually does it,
    rather than just testing offers.py in isolation."""
    conn = _fresh_db()
    p = _product_with_stock(conn, min_qty=4)
    offers.add_offer(conn, "Test Discount", 10, product_id=p["id"], scope=offers.SCOPE_ALL)

    unit_price = p["cash_price"]
    qty = 4.0
    discount, offer_name = offers.line_discount_amount(conn, p["id"], p["category"], qty, unit_price, False)
    assert offer_name == "Test Discount"
    assert abs(discount - round(qty * unit_price * 0.10, 2)) < 0.01

    result = pos.create_invoice(
        conn, staff_id=1, cart=[pos.CartLine(product_id=p["id"], qty=qty, discount=discount)],
    )
    expected_net = round(qty * unit_price - discount, 2)
    assert abs(result.net_total - expected_net) < 0.01
    assert abs(result.discount - discount) < 0.01


def test_customer_top_items_reflects_only_that_customers_purchases():
    conn = _fresh_db()
    p1 = _product_with_stock(conn, min_qty=5)
    other = conn.execute(
        "SELECT * FROM products WHERE active=1 AND id != ? ORDER BY id LIMIT 1", (p1["id"],)
    ).fetchone()
    cid = customers.add_customer(conn, "Regular Shopper")
    pos.create_invoice(conn, staff_id=1, cart=[pos.CartLine(product_id=p1["id"], qty=3)],
                        customer_id=cid, customer_name="Regular Shopper")
    top = reports.customer_top_items(conn, cid)
    assert any(r["product_id"] == p1["id"] for r in top)
    if other is not None:
        assert not any(r["product_id"] == other["id"] for r in top)  # never bought this one


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
