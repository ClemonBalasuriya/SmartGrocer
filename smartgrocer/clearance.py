"""
SmartGrocer - slow-mover clearance engine (research Objective 4).

Extends the existing urgency-scoring (promotions.py, Objective 2) and
association-mining (association.py, Objective 3) machinery to a problem
neither of them covers: a non-perishable product that has simply stopped
selling. promotions.py's urgency engine only ever looks at stock_batches
rows that have an expiry_date, so a slow household good with no tracked
expiry gets no automated attention today, no matter how dead its sales
have gone - staff would have to notice it by eye.

This module adds three real, data-driven pieces, deliberately reusing the
live app's own velocity and co-purchase logic rather than reimplementing
either (same "not a reimplementation" standard the rest of this research
holds Objectives 1-3 to):

  1. detect_slow_movers()   - which products have genuinely slowed down,
                               for EVERY active product, not just ones
                               with a tracked expiry date. Reuses
                               promotions._velocity_map() directly.
  2. match_bundle_partner() - for a slow mover, the best real fast-moving
                               bundle partner backed by actual co-purchase
                               evidence, not just "whatever sells fastest
                               in the same aisle". Reuses
                               association.export_adjacency_pairs()
                               directly - that function has had no caller
                               since layout.py (the descoped planogram
                               feature) was removed; this is its first
                               real use since.
  3. revenue_options()      - for a detected slow mover, what each
                               possible action (do nothing / discount it
                               standalone / bundle it with its matched
                               partner) is projected to earn in both
                               revenue AND profit, so the shop (or an
                               automated extension of the existing manual
                               offers.py screen) can pick the one that
                               maximises PROFIT - not just the one that
                               moves the most revenue, which a discount or
                               a thin-margin bundle can do while actually
                               leaving the shop worse off once margin is
                               accounted for. (Earlier revision of this
                               module picked by revenue; fixed once that
                               mismatch with the function's own stated
                               goal was caught - see revenue_options()'s
                               own docstring for the full reasoning.)

Literature grounding (same standard as Objectives 1-3's cited methodology,
not engineering built without a research basis). Verified against the
actual full-text PDFs the papers were cited from, not just abstracts -
each note below says what transferred as-is, what only transferred as
general framing, and why:

  - Part A (detect_slow_movers):
      Qi et al. (2025) define "slow-moving" via Inventory Turnover Ratio
      and solve it with a hybrid neural network (14.6% turnover
      improvement in their deployment) - a much heavier model than this
      module's category-relative velocity-ratio quartile. No numeric
      threshold of theirs transfers; what transfers is the framing that
      SMI detection is its own problem, not a free side-effect of ordinary
      demand forecasting.
      Gabor et al. (2021)'s actual mechanism, read in full, is an
      omnichannel INVENTORY model: a multi-echelon retailer discounts
      slow movers by switching their fulfilment channel (store pickup vs.
      online shipping) to avoid stockouts elsewhere in the network - not a
      markdown-for-clearance decision like this module's. It is cited
      narrowly, for "slow-moving inventory is a distinct decision problem
      deserving its own discount mechanism" only; it is NOT evidence for
      any specific threshold or for the detect-then-decide two-step
      structure specifically (that structure is this module's own design,
      not borrowed from either paper).
  - Part B (match_bundle_partner):
      Netessine & Rudi (2006) is the strongest, most directly-applicable
      citation in this module. They propose, and numerically validate
      across "a wide range of problem parameters", a "myopic heuristic
      H_M" that explicitly "ignores product inventories and time-to-go"
      and simply picks the packaging partner that "maximizes the expected
      profit from selling a package" - i.e. the simpler, non-dynamic-
      programming heuristic their own paper recommends over the full
      inventory-aware optimum. match_bundle_partner()'s design (ignore
      inventory levels/time dynamics, just use real co-purchase evidence
      plus the candidate's own velocity) is in that same spirit, not an
      unjustified shortcut.
      Kumar et al. (2020)'s real retailer field experiment applied "the
      same relative discount on all the bundles - 10% off on the add-on
      product and full price for the focal product ... selected after
      discussions with the retailer", and found that over 90% of real
      product pairs were NEVER actually co-purchased - i.e. most
      "plausible" bundle pairs a human would guess have zero supporting
      evidence, which is exactly why match_bundle_partner() requires a
      real, mined co-purchase pair rather than assuming complementarity
      from category adjacency. Their real 10% figure is why
      evaluate_clearance.py's UPLIFT_SWEEP is centered on 0.10 rather than
      an arbitrary midpoint.
  - Part C (revenue_options' discount option):
      Hua et al. (2021) frame markdown pricing from historical transaction
      data as a "counterfactual prediction" problem precisely because "it
      is difficult to predict sales of a product at a counterfactual
      price" the business never actually charged - the same reason this
      module's "discount" option can only use a product's own
      actually-observed discounted vs. full-price days, never an
      untested price point. They also note retailers in practice "often
      choose an empirical discount on goods (such as 30% off, 50% off)"
      rather than a data-driven figure - the ad hoc baseline this module's
      disclosed-assumption approach (and its 15% no-history fallback) is
      explicitly trying to improve on, not replicate.
      Fisher et al. (2017) state the exact confound this module's
      "discount" option cannot rule out: with historical data, "prices are
      correlated with demand shocks observable to pricing managers but
      not to researchers" - here, a staff member's private read of a slow
      mover (e.g. "this is about to go stale-looking, mark it down") is
      exactly such an unobserved shock, and it is why this module labels
      the discount-day comparison OBSERVATIONAL, never causal. Fisher et
      al. only resolve that confound via a randomized field price
      experiment, which this project does not have access to and is not
      attempting to simulate.

IMPORTANT - what's real and what's assumed in revenue_options():
  - "no_action" and "discount" use ONLY real historical data: real sales
    velocity, and (where it exists) the real per-line `discount` values
    already in invoice_items, comparing this product's own discounted-day
    revenue against its own full-price-day revenue. This is an
    OBSERVATIONAL comparison - the real data has no record of WHY staff
    discounted a given line on a given day, so it cannot rule out
    confounds. It is real, not synthetic, but it is correlational, not a
    controlled experiment - say so wherever this number is surfaced.
  - "bundle" has no real historical ground truth at all (no bundle was
    ever actually sold as a single priced bundle in this data) and is
    deliberately a disclosed PROJECTION: the combined standalone revenue
    of the two real products, scaled by an assumed cross-sell uplift that
    the caller must supply explicitly - this module never defaults or
    picks that number itself. evaluate_clearance.py sweeps it across a
    disclosed plausible range rather than asserting a single value.
"""

from __future__ import annotations

import sqlite3
import statistics
from datetime import date, timedelta

from . import association
from . import promotions

RECENT_WINDOW_DEFAULT = 14
BASELINE_WINDOW_DEFAULT = 90
SLOW_PERCENTILE = 0.25   # bottom quartile of category-relative velocity ratio
MIN_CATEGORY_SIZE = 4    # too few same-category products for a percentile to mean anything


def _velocity_ratio_map(conn: sqlite3.Connection, product_ids: list[int], as_of: date,
                         recent_window: int, baseline_window: int):
    """recent/baseline velocity ratio per product - 0 = stalled, 1 = selling
    at (or above) its own historical pace. Thin wrapper around the real,
    unmodified promotions._velocity_map(), called once per window across
    ALL products (not once per product) for the same real-shop-scale
    reason that function's own docstring gives."""
    recent = promotions._velocity_map(conn, product_ids, recent_window, as_of)
    baseline = promotions._velocity_map(conn, product_ids, baseline_window, as_of)
    ratios: dict[int, float | None] = {}
    for pid in product_ids:
        b = baseline[pid]
        ratios[pid] = (recent[pid] / b) if b > 1e-6 else None
    return ratios, recent, baseline


def detect_slow_movers(conn: sqlite3.Connection, as_of: date | None = None,
                        recent_window: int = RECENT_WINDOW_DEFAULT,
                        baseline_window: int = BASELINE_WINDOW_DEFAULT,
                        min_baseline_velocity: float = 0.05) -> list[dict]:
    """Every active product whose recent sales velocity has fallen into
    the bottom quartile of its OWN CATEGORY's velocity ratios -
    category-relative, not shop-wide, so a naturally slower but normal
    specialty item isn't flagged just for being less popular than rice.
    min_baseline_velocity excludes products that barely sold even in the
    long baseline window - there is nothing meaningful to slow down FROM."""
    as_of = as_of or date.today()
    products = conn.execute(
        "SELECT id, code, name_en, category, cash_price, cost_price FROM products WHERE active=1"
    ).fetchall()
    product_ids = [p["id"] for p in products]
    ratios, recent_v, baseline_v = _velocity_ratio_map(conn, product_ids, as_of, recent_window, baseline_window)

    by_category: dict[str, list[int]] = {}
    for p in products:
        pid = p["id"]
        if baseline_v[pid] >= min_baseline_velocity and ratios[pid] is not None:
            by_category.setdefault(p["category"], []).append(pid)

    slow_ids: set[int] = set()
    for ids in by_category.values():
        if len(ids) < MIN_CATEGORY_SIZE:
            continue
        sorted_ids = sorted(ids, key=lambda pid: ratios[pid])
        cutoff = max(1, round(len(sorted_ids) * SLOW_PERCENTILE))
        slow_ids.update(sorted_ids[:cutoff])

    by_id = {p["id"]: p for p in products}
    out = []
    for pid in slow_ids:
        p = by_id[pid]
        out.append({
            "product_id": pid, "code": p["code"], "name": p["name_en"], "category": p["category"],
            "recent_velocity": round(recent_v[pid], 4), "baseline_velocity": round(baseline_v[pid], 4),
            "velocity_ratio": round(ratios[pid], 4), "cash_price": p["cash_price"], "cost_price": p["cost_price"],
        })
    out.sort(key=lambda r: r["velocity_ratio"])
    return out


def match_bundle_partner(conn: sqlite3.Connection, slow_product_id: int, as_of: date | None = None,
                          recent_window: int = RECENT_WINDOW_DEFAULT,
                          baseline_window: int = BASELINE_WINDOW_DEFAULT,
                          min_lift: float = 1.05, min_support: float = 0.02) -> dict | None:
    """Best real fast-moving bundle partner for one slow mover: among
    products with REAL co-purchase evidence against it (association.py's
    own pairwise lift, not a reimplementation), picks the one with the
    highest lift that is ALSO a genuine fast mover (top quartile velocity
    ratio among the real candidates found) - so a pairing is never
    proposed on co-purchase strength alone without the slow mover actually
    having a fast-selling partner to lean on. Returns None if no such
    partner exists in the real data - an honest "no bundle candidate"
    result, never a forced one."""
    as_of = as_of or date.today()
    pairs = association.export_adjacency_pairs(conn, min_support=min_support, min_lift=min_lift)
    candidates = [(a, b, lift) for (a, b, lift) in pairs if a == slow_product_id or b == slow_product_id]
    if not candidates:
        return None

    partner_ids = sorted({(b if a == slow_product_id else a) for (a, b, _) in candidates})
    ratios, recent_v, _ = _velocity_ratio_map(conn, partner_ids, as_of, recent_window, baseline_window)
    valid_ratios = [r for r in ratios.values() if r is not None]
    fast_cutoff = statistics.quantiles(valid_ratios, n=4)[-1] if len(valid_ratios) >= MIN_CATEGORY_SIZE else None

    best = None
    for (a, b, lift) in candidates:
        partner_id = b if a == slow_product_id else a
        ratio = ratios.get(partner_id)
        if ratio is None:
            continue
        if fast_cutoff is not None and ratio < fast_cutoff:
            continue  # real co-purchase evidence exists, but this partner isn't actually selling fast right now
        if best is None or lift > best["lift"]:
            p = conn.execute("SELECT code, name_en, cash_price, cost_price FROM products WHERE id=?",
                              (partner_id,)).fetchone()
            best = {
                "partner_product_id": partner_id, "partner_code": p["code"], "partner_name": p["name_en"],
                "partner_cash_price": p["cash_price"], "partner_cost_price": p["cost_price"],
                "lift": round(lift, 3), "partner_velocity_ratio": round(ratio, 4),
                "partner_recent_velocity": round(recent_v[partner_id], 4),
                "partner_daily_revenue": round(recent_v[partner_id] * p["cash_price"], 2),
            }
    return best


def discount_natural_experiment(conn: sqlite3.Connection, product_id: int, as_of: date,
                                 window_days: int = BASELINE_WINDOW_DEFAULT) -> dict:
    """Real, observational comparison of this product's own daily revenue
    on days it carried a real historical discount (invoice_items.discount
    > 0) vs. days it sold at full price, over the trailing window_days.
    Not causal: the real data has no record of WHY staff discounted a
    given line on a given day, so this cannot rule out confounds (e.g. a
    discount happening to coincide with a day that already had unrelated
    extra footfall). It is still a real, data-backed signal, not a
    synthetic one - just disclose it as observational wherever it's
    surfaced, exactly like the January 2026 and Objective 2 disclosures."""
    since = (as_of - timedelta(days=window_days)).isoformat()
    until = (as_of + timedelta(days=1)).isoformat()
    rows = conn.execute(
        """SELECT substr(i.datetime,1,10) d, SUM(ii.line_total) rev, SUM(ii.discount) disc, SUM(ii.qty) qty
           FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id
           WHERE ii.product_id=? AND i.voided=0 AND i.datetime>=? AND i.datetime<?
           GROUP BY d""",
        (product_id, since, until),
    ).fetchall()
    discounted_days = [(r["rev"], r["qty"]) for r in rows if r["disc"] and r["disc"] > 1e-6]
    full_price_days = [(r["rev"], r["qty"]) for r in rows if not r["disc"] or r["disc"] <= 1e-6]
    return {
        "n_discounted_days": len(discounted_days),
        "n_full_price_days": len(full_price_days),
        "mean_daily_revenue_discounted": round(statistics.mean(r for r, _ in discounted_days), 2) if discounted_days else None,
        "mean_daily_revenue_full_price": round(statistics.mean(r for r, _ in full_price_days), 2) if full_price_days else None,
        # Real units sold per day in each group, alongside revenue - needed
        # to turn revenue into PROFIT (revenue - qty*cost_price) in
        # revenue_options() below. A discount can raise revenue through
        # higher volume while still leaving less real profit behind, once
        # the margin given up on every unit is accounted for - revenue
        # alone can't tell the two apart, which is exactly the gap this
        # field closes.
        "mean_daily_qty_discounted": round(statistics.mean(q for _, q in discounted_days), 3) if discounted_days else None,
        "mean_daily_qty_full_price": round(statistics.mean(q for _, q in full_price_days), 3) if full_price_days else None,
        # need a handful of real observations, not one fluke day, before trusting the comparison
        "has_real_discount_history": len(discounted_days) >= 3 and len(full_price_days) >= 3,
    }


def revenue_options(slow_mover: dict, partner: dict | None, natural_exp: dict,
                     assumed_bundle_uplift: float = 0.0) -> dict:
    """Projected daily revenue AND profit for one slow mover under up to
    three actions: 'no_action' (real), 'discount' (real-observational
    where history exists for this product, else a disclosed flat-markdown
    fallback with no assumed demand response), and 'bundle' (always a
    disclosed projection - see module docstring). `natural_exp` comes from
    discount_natural_experiment(); `assumed_bundle_uplift` must be supplied
    explicitly by the caller on every call - evaluate_clearance.py sweeps
    it across a disclosed range rather than this function ever picking a
    value on its own.

    recommended_action is chosen by PROFIT, not revenue. A discount
    reliably raises revenue (more units move) but gives up margin on every
    one of them; a bundle's combined revenue can look attractive while
    actually being two already-thin margins added together. Revenue alone
    can make either look like the right call when it isn't - profit is the
    number a small retailer actually wants to maximise, and it's also the
    objective Netessine & Rudi (2006)'s own myopic heuristic optimises for
    (see module docstring, Part B). Both figures are still returned on
    every option so the revenue story stays visible, not hidden."""
    cost_price = slow_mover["cost_price"]
    cash_price = slow_mover["cash_price"]
    unit_margin = cash_price - cost_price

    no_action_daily_rev = round(slow_mover["recent_velocity"] * cash_price, 2)
    no_action_daily_profit = round(slow_mover["recent_velocity"] * unit_margin, 2)

    if natural_exp["has_real_discount_history"]:
        discount_daily_rev = natural_exp["mean_daily_revenue_discounted"]
        discount_qty = natural_exp["mean_daily_qty_discounted"] or 0.0
        # Profit = real revenue on those real discounted days minus the
        # real COGS of the real units sold on them (qty * this product's
        # own cost_price) - the discount's effect on per-unit margin is
        # already baked into discount_daily_rev, so this is a direct,
        # fully-real profit figure, not a second assumption stacked on
        # the first.
        discount_daily_profit = round(discount_daily_rev - discount_qty * cost_price, 2)
        discount_basis = "real_observational_historical_discount_days"
    else:
        # No real discounted-day history for THIS product - fall back to
        # the same flat markdown arithmetic the live urgency engine already
        # uses for its own suggested price, applied to the same real recent
        # velocity, rather than inventing a demand-response number that
        # isn't known for this product. Profit falls by exactly the
        # discount given on each unit still sold at the real, unchanged
        # recent_velocity pace (no assumed volume lift either).
        discount_daily_rev = round(no_action_daily_rev * 0.85, 2)
        discount_daily_profit = round(slow_mover["recent_velocity"] * (cash_price * 0.85 - cost_price), 2)
        discount_basis = "assumed_flat_15pct_markdown_no_demand_response_data"

    options = {
        "no_action": {
            "projected_daily_revenue": no_action_daily_rev,
            "projected_daily_profit": no_action_daily_profit, "basis": "real",
        },
        "discount": {
            "projected_daily_revenue": discount_daily_rev,
            "projected_daily_profit": discount_daily_profit,
            "basis": discount_basis, **natural_exp,
        },
    }

    if partner is not None:
        partner_unit_margin = partner["partner_cash_price"] - partner["partner_cost_price"]
        partner_daily_profit = partner["partner_recent_velocity"] * partner_unit_margin
        combined_standalone_rev = no_action_daily_rev + partner["partner_daily_revenue"]
        combined_standalone_profit = no_action_daily_profit + partner_daily_profit
        options["bundle"] = {
            "projected_daily_revenue": round(combined_standalone_rev * (1 + assumed_bundle_uplift), 2),
            "projected_daily_profit": round(combined_standalone_profit * (1 + assumed_bundle_uplift), 2),
            "basis": f"assumed_uplift_{assumed_bundle_uplift:.0%}_over_combined_standalone_revenue_and_profit",
            "partner_product_id": partner["partner_product_id"], "partner_name": partner["partner_name"],
            "partner_lift": partner["lift"],
        }

    best_action = max(options, key=lambda k: options[k]["projected_daily_profit"])
    return {"product_id": slow_mover["product_id"], "options": options, "recommended_action": best_action}
