"""
SmartGrocer - association rule mining for bundle & layout recommendations
(proposal Objective 3, citing Agrawal & Srikant 1994).

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


def get_bundle_recommendations(conn: sqlite3.Connection, min_support: float = 0.02,
                                min_confidence: float = 0.3, min_lift: float = 1.1,
                                top_n: int = 20) -> list[dict]:
    baskets = build_baskets(conn)
    frequent = apriori(baskets, min_support=min_support)
    rules = generate_rules(frequent, min_confidence=min_confidence, min_lift=min_lift)
    products = _product_lookup(conn)

    seen_itemsets = set()
    out = []
    for r in rules:
        itemset = r["antecedent"] | r["consequent"]
        if itemset in seen_itemsets:
            continue
        seen_itemsets.add(itemset)
        pricing = bundle_price_suggestion(conn, itemset, r["lift"], products)
        out.append({
            "antecedent": [products[i]["name_en"] for i in r["antecedent"]],
            "consequent": [products[i]["name_en"] for i in r["consequent"]],
            "support": round(r["support"], 4), "confidence": round(r["confidence"], 3),
            "lift": round(r["lift"], 3), **pricing,
        })
        if len(out) >= top_n:
            break
    return out


def export_adjacency_pairs(conn: sqlite3.Connection, min_support: float = 0.02,
                            min_lift: float = 1.05) -> list[tuple[int, int, float]]:
    """(product_id_a, product_id_b, lift) for pair-wise rules - fed into layout.py's
    high-lift co-location layer."""
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
