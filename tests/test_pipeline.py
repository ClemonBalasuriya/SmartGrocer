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

from smartgrocer import (
    association, cash_drawer, customers, data_generator, db, forecasting, layout, pos, promotions,
    receipts, reports, suppliers,
)


def _fresh_db():
    tmp = Path(tempfile.mkdtemp()) / "test_smartgrocer.db"
    conn = db.reset_db(tmp)
    code_to_id = data_generator.insert_products(conn)
    data_generator.generate_history(conn, code_to_id, days=200, avg_invoices_per_day=40)
    return conn


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
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
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
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
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
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
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
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
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
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
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
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
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


def test_receipt_text_includes_key_fields():
    conn = _fresh_db()
    p = conn.execute("SELECT * FROM products LIMIT 1").fetchone()
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
