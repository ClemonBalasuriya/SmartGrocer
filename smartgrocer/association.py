"""
SmartGrocer - association rule mining for bundle recommendations
(proposal Objective 3, citing Agrawal & Srikant 1994).

Note: this module originally also fed a planogram/layout-optimisation
feature (Objective 4). That feature was descoped from the research and
removed from the app on 22 Oct 2026; this module itself is unaffected and
continues to power the live Bundles screen.

Implements the classic level-wise Apriori algorithm from scratch (candidate
generation -> subset pruning -> support counting), rather than depending on
a third-party library - this also means the thesis can show and defend the
exact algorithm used. The proposal specifies a conservative 2% minimum
support threshold to accommodate a small shop's sparse transaction data
(Kolassa 2016); that is this module's default.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from itertools import combinations


def build_baskets(conn: sqlite3.Connection) -> list[frozenset]:
    rows = conn.execute(
        """SELECT ii.invoice_id, ii.product_id FROM invoice_items ii
           JOIN invoices i ON i.id = ii.invoice_id WHERE i.voided=0"""
    ).fetchall()
    baskets: dict[int, set] = {}
    for r in rows:
        baskets.setdefault(r["invoice_id"], set()).add(r["product_id"])
    # single-item baskets can never form a rule; drop them to shrink the scan
    return [frozenset(items) for items in baskets.values() if len(items) >= 2]


def apriori(baskets: list[frozenset], min_support: float = 0.02, max_len: int = 3) -> dict[frozenset, float]:
    """Returns {itemset: support} for all frequent itemsets up to size max_len."""
    n = len(baskets)
    if n == 0:
        return {}

    item_counts = Counter()
    for b in baskets:
        for item in b:
            item_counts[item] += 1
    current_level = {frozenset([item]): c / n for item, c in item_counts.items() if c / n >= min_support}
    all_frequent = dict(current_level)

    k = 2
    while current_level and k <= max_len:
        prev_itemsets = list(current_level.keys())
        candidates = set()
        for i in range(len(prev_itemsets)):
            for j in range(i + 1, len(prev_itemsets)):
                union = prev_itemsets[i] | prev_itemsets[j]
                if len(union) == k:
                    candidates.add(union)

        # classic Apriori prune: every (k-1)-subset of a candidate must already be frequent
        pruned = {
            c for c in candidates
            if all(frozenset(s) in current_level for s in combinations(c, k - 1))
        }

        counts = Counter()
        for b in baskets:
            for c in pruned:
                if c.issubset(b):
                    counts[c] += 1

        current_level = {c: counts[c] / n for c in pruned if counts[c] / n >= min_support}
        all_frequent.update(current_level)
        k += 1

    return all_frequent


def generate_rules(frequent: dict[frozenset, float], min_confidence: float = 0.3,
                    min_lift: float = 1.1) -> list[dict]:
    """A -> B rules from itemsets of size >= 2, with support/confidence/lift."""
    rules = []
    for itemset, support in frequent.items():
        if len(itemset) < 2:
            continue
        for r in range(1, len(itemset)):
            for antecedent in combinations(itemset, r):
                antecedent = frozenset(antecedent)
                consequent = itemset - antecedent
                ant_support = frequent.get(antecedent)
                cons_support = frequent.get(consequent)
                if not ant_support or not cons_support:
                    continue
                confidence = support / ant_support
                lift = confidence / cons_support
                if confidence >= min_confidence and lift >= min_lift:
                    rules.append({
                        "antecedent": antecedent, "consequent": consequent,
                        "support": support, "confidence": confidence, "lift": lift,
                    })
    rules.sort(key=lambda r: -r["lift"])
    return rules


def _product_lookup(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    return {r["id"]: r for r in conn.execute("SELECT * FROM products").fetchall()}


def bundle_price_suggestion(conn: sqlite3.Connection, item_ids: frozenset, lift: float,
                             products: dict[int, sqlite3.Row] | None = None) -> dict:
    products = products or _product_lookup(conn)
    items = [products[i] for i in item_ids]
    sum_price = sum(p["cash_price"] for p in items)
    sum_cost = sum(p["cost_price"] for p in items)
    discount = max(0.0, min(0.15, (lift - 1) * 0.05))
    bundle_price = round(sum_price * (1 - discount), 2)
    floor = sum_cost * 1.02
    if bundle_price < floor:
        bundle_price = round(floor, 2)
        discount = 1 - bundle_price / sum_price if sum_price else 0
    return {
        "items": [p["name_en"] for p in items],
        "item_codes": [p["code"] for p in items],
        "sum_price_lkr": round(sum_price, 2),
        "bundle_price_lkr": bundle_price,
        "savings_lkr": round(sum_price - bundle_price, 2),
        "discount_pct": round(discount * 100, 1),
    }


def bundle_profit_potential(conn: sqlite3.Connection, item_ids: frozenset, support: float,
                             n_baskets: int, lift: float,
                             products: dict[int, sqlite3.Row] | None = None) -> dict:
    """Real, historical profit opportunity behind one candidate bundle - this
    is what turns "a statistically real pairing" (lift) into "a pairing
    worth actually recommending" (profit). Lift only says two items sell
    together more than chance would predict; it says nothing about whether
    those items are cheap, low-margin staples or genuinely worth the
    shop's shelf space and a staff member's time to push. A small shop's
    sparse data regularly produces high-lift pairs on inexpensive items
    (e.g. two spice packets) that move far less real money than a
    moderate-lift pair of higher-margin products bought often.

    co_occurrences: how many real past (non-voided) invoices contained
    every item in this bundle together - support expressed as a raw count
    rather than a ratio, since a ratio alone hides whether a pairing is
    rare-but-real or common.

    margin_per_bundle_lkr: the net margin the proposed bundle price (see
    bundle_price_suggestion - already floored at 2% over cost, so this is
    never negative) would earn per sale, after the pairing's own discount.

    realized_profit_lkr: co_occurrences * margin_per_bundle_lkr - the real
    gross profit this exact pairing has already generated in basket
    history, had every one of those real co-purchases been rung up as this
    priced bundle. 100% real data (no projected uplift, no assumption) -
    this is what get_bundle_recommendations() ranks candidates by."""
    products = products or _product_lookup(conn)
    pricing = bundle_price_suggestion(conn, item_ids, lift, products)
    sum_cost = sum(products[i]["cost_price"] for i in item_ids)
    co_occurrences = round(support * n_baskets)
    margin_per_bundle = round(pricing["bundle_price_lkr"] - sum_cost, 2)
    realized_profit = round(margin_per_bundle * co_occurrences, 2)
    return {
        **pricing,
        "co_occurrences": co_occurrences,
        "margin_per_bundle_lkr": margin_per_bundle,
        "realized_profit_lkr": realized_profit,
    }


def get_bundle_recommendations(conn: sqlite3.Connection, min_support: float = 0.02,
                                min_confidence: float = 0.3, min_lift: float = 1.1,
                                top_n: int = 20) -> list[dict]:
    """Bundle candidates, gated by statistical significance (the
    min_support/min_confidence/min_lift thresholds below decide which
    pairings are REAL - the same Apriori mining as before, unchanged) but
    RANKED by real profit potential, not by lift. Lift and profit answer
    different questions: lift asks "is this pairing genuine, not noise?";
    profit asks "is this pairing worth recommending?" A shop should see its
    most valuable real bundles first, not merely its most statistically
    surprising ones."""
    baskets = build_baskets(conn)
    n_baskets = len(baskets)
    frequent = apriori(baskets, min_support=min_support)
    rules = generate_rules(frequent, min_confidence=min_confidence, min_lift=min_lift)
    products = _product_lookup(conn)

    seen_itemsets = set()
    candidates = []
    for r in rules:
        itemset = r["antecedent"] | r["consequent"]
        if itemset in seen_itemsets:
            continue
        seen_itemsets.add(itemset)
        profit = bundle_profit_potential(conn, itemset, r["support"], n_baskets, r["lift"], products)
        candidates.append({
            "antecedent": [products[i]["name_en"] for i in r["antecedent"]],
            "consequent": [products[i]["name_en"] for i in r["consequent"]],
            "support": round(r["support"], 4), "confidence": round(r["confidence"], 3),
            "lift": round(r["lift"], 3), **profit,
        })

    candidates.sort(key=lambda c: -c["realized_profit_lkr"])
    return candidates[:top_n]


def export_adjacency_pairs(conn: sqlite3.Connection, min_support: float = 0.02,
                            min_lift: float = 1.05) -> list[tuple[int, int, float]]:
    """(product_id_a, product_id_b, lift) for pair-wise rules.

    Originally fed layout.py's high-lift co-location layer; that feature
    (Objective 4, store layout optimisation) was descoped and layout.py was
    removed on 22 Oct 2026, so this function currently has no caller in the
    app. Left in place in case a future co-location feature wants it -
    association.py itself is otherwise unaffected and still live."""
    baskets = build_baskets(conn)
    frequent = apriori(baskets, min_support=min_support, max_len=2)
    pairs = []
    for itemset, support in frequent.items():
        if len(itemset) != 2:
            continue
        a, b = tuple(itemset)
        sa, sb = frequent.get(frozenset([a])), frequent.get(frozenset([b]))
        if not sa or not sb:
            continue
        lift = support / (sa * sb)
        if lift >= min_lift:
            pairs.append((a, b, lift))
    pairs.sort(key=lambda t: -t[2])
    return pairs
