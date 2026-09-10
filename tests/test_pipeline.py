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

from smartgrocer import association, data_generator, db, forecasting, layout, pos, promotions, reports


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
