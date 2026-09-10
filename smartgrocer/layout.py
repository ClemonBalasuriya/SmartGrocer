"""
SmartGrocer - four-layer store layout optimisation (proposal Objective 4).

Layers, in the order they constrain the final placement:
 1. High-lift co-location  - products from association.export_adjacency_pairs
    are greedily clustered (union-find over pairs, sorted by lift) so
    frequently co-purchased items end up physically near each other.
 2. Forced-path exposure   - staple items are pushed to the back wall, so a
    shopper has to walk past everything else to reach daily essentials
    (Rook 1987's impulse-purchase trigger via forced exposure).
 3. Decision-point placement - high-margin, non-staple items are pulled out
    to the checkout / decision-point zone regardless of their cluster.
 4. Shelf-height zoning    - within whatever zone a product lands in, it is
    assigned a shelf tier: child-targeted products at child eye-level,
    high-margin non-staples at adult eye-level, everything else at a normal
    or bulk/bottom tier (Kerfoot, Davies & Ward 2003).

The output is a plain assignment table (zone, shelf_tier per product) plus
a simple matplotlib planogram sketch - a real store obviously has its own
physical layout; treat the zone list below as a generic small-shop template
to be replaced with your own shop's actual aisle plan.
"""

from __future__ import annotations

import sqlite3

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from .association import export_adjacency_pairs

ZONES = [
    # name, x, y, w, h, is_decision_point, is_deep_store
    ("Entrance Display", 0.0, 3.0, 2.0, 1.5, False, False),
    ("Aisle 1", 0.0, 0.0, 2.0, 2.5, False, False),
    ("Aisle 2", 2.2, 0.0, 2.0, 2.5, False, False),
    ("Aisle 3", 4.4, 0.0, 2.0, 2.5, False, False),
    ("Back Wall (Staples)", 2.2, 3.0, 4.2, 1.5, False, True),
    ("Checkout Counter (Decision Point)", 6.6, 0.0, 1.8, 4.5, True, False),
]
GENERAL_ZONES = [z[0] for z in ZONES if not z[5] and not z[6]]  # the 3 aisles

HEIGHT_COLORS = {
    "Low (Child Eye-Level)": "#f6b26b",
    "Eye-Level (Impulse)": "#e06666",
    "Middle": "#93c47d",
    "Bottom/Bulk": "#6fa8dc",
}


class UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _cluster_products(product_ids: list[int], pairs: list[tuple[int, int, float]],
                       max_cluster_size: int = 4) -> dict[int, int]:
    """Greedy union-find clustering by descending lift, capped cluster size.
    Returns {product_id: cluster_id}."""
    uf = UnionFind(product_ids)
    cluster_size = {pid: 1 for pid in product_ids}

    def cluster_of(pid):
        return uf.find(pid)

    for a, b, _lift in sorted(pairs, key=lambda t: -t[2]):
        ra, rb = cluster_of(a), cluster_of(b)
        if ra == rb:
            continue
        if cluster_size[ra] + cluster_size[rb] <= max_cluster_size:
            uf.union(ra, rb)
            new_root = cluster_of(ra)
            total = cluster_size[ra] + cluster_size[rb]
            cluster_size[new_root] = total

    roots = {pid: cluster_of(pid) for pid in product_ids}
    unique_roots = {r: i for i, r in enumerate(sorted(set(roots.values())))}
    return {pid: unique_roots[r] for pid, r in roots.items()}


def compute_layout(conn: sqlite3.Connection, top_decision_point_n: int = 10) -> list[dict]:
    products = conn.execute(
        """SELECT p.*, COALESCE(SUM(ii.qty),0) total_sold
           FROM products p LEFT JOIN invoice_items ii ON ii.product_id = p.id
           WHERE p.active=1 GROUP BY p.id"""
    ).fetchall()
    product_ids = [p["id"] for p in products]
    by_id = {p["id"]: p for p in products}

    margins = {p["id"]: (p["cash_price"] - p["cost_price"]) / p["cash_price"] if p["cash_price"] else 0
               for p in products}
    margin_values = list(margins.values())
    margin_cutoff_hi = sorted(margin_values)[int(len(margin_values) * 0.66)] if margin_values else 0

    pairs = export_adjacency_pairs(conn)
    clusters = _cluster_products(product_ids, pairs)

    # Layer 3: decision-point items chosen first (highest margin, non-staple,
    # reasonably fast-moving) - they override whatever cluster/staple rule
    # would otherwise place them.
    candidates = sorted(
        (p for p in products if not p["is_staple"] and p["total_sold"] > 0),
        key=lambda p: -margins[p["id"]],
    )
    decision_point_ids = {p["id"] for p in candidates[:top_decision_point_n]}

    # cluster -> zone assignment (round-robin across the general aisles),
    # but a cluster made entirely of staples goes to the back wall instead.
    cluster_members: dict[int, list[int]] = {}
    for pid, cid in clusters.items():
        cluster_members.setdefault(cid, []).append(pid)

    zone_of_cluster: dict[int, str] = {}
    aisle_cursor = 0
    for cid, members in cluster_members.items():
        if all(by_id[m]["is_staple"] for m in members):
            zone_of_cluster[cid] = "Back Wall (Staples)"
        else:
            zone_of_cluster[cid] = GENERAL_ZONES[aisle_cursor % len(GENERAL_ZONES)]
            aisle_cursor += 1

    assignments = []
    for p in products:
        pid = p["id"]
        if pid in decision_point_ids:
            zone = "Checkout Counter (Decision Point)"
        else:
            zone = zone_of_cluster[clusters[pid]]

        if p["child_target"]:
            tier = "Low (Child Eye-Level)"
        elif margins[pid] >= margin_cutoff_hi and not p["is_staple"]:
            tier = "Eye-Level (Impulse)"
        elif p["is_staple"]:
            tier = "Bottom/Bulk"
        else:
            tier = "Middle"

        assignments.append({
            "product_id": pid, "code": p["code"], "name": p["name_en"], "category": p["category"],
            "zone": zone, "shelf_tier": tier, "margin": round(margins[pid], 3),
            "cluster_id": clusters[pid],
        })

    return assignments


def persist_layout(conn: sqlite3.Connection, assignments: list[dict]) -> None:
    from datetime import datetime
    conn.execute("DELETE FROM layout_assignments")
    now = datetime.now().isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO layout_assignments (product_id, zone, height_tier, computed_at) VALUES (?,?,?,?)",
        [(a["product_id"], a["zone"], a["shelf_tier"], now) for a in assignments],
    )
    conn.commit()


def render_planogram(assignments: list[dict], out_path: str, max_items_per_zone: int = 9) -> str:
    fig, ax = plt.subplots(figsize=(11, 7))
    by_zone: dict[str, list[dict]] = {}
    for a in assignments:
        by_zone.setdefault(a["zone"], []).append(a)

    for name, x, y, w, h, is_dp, is_deep in ZONES:
        edge = "#c00000" if is_dp else ("#38761d" if is_deep else "#444444")
        ax.add_patch(mpatches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02",
                                              linewidth=2, edgecolor=edge, facecolor="#ffffff"))
        ax.text(x + 0.05, y + h - 0.18, name, fontsize=9, fontweight="bold", color=edge)
        items = sorted(by_zone.get(name, []), key=lambda a: -a["margin"])
        text_y = y + h - 0.42
        for item in items[:max_items_per_zone]:
            color = HEIGHT_COLORS.get(item["shelf_tier"], "#000000")
            ax.text(x + 0.08, text_y, f"• {item['name']}", fontsize=7, color=color)
            text_y -= 0.16
            if text_y < y + 0.05:
                break
        remaining = len(items) - max_items_per_zone
        if remaining > 0:
            ax.text(x + 0.08, max(text_y, y + 0.05), f"+ {remaining} more", fontsize=7, style="italic")

    legend_handles = [mpatches.Patch(color=c, label=t) for t, c in HEIGHT_COLORS.items()]
    ax.legend(handles=legend_handles, loc="lower left", bbox_to_anchor=(0, -0.12), ncol=4, fontsize=8, frameon=False)

    ax.set_xlim(-0.3, 8.7)
    ax.set_ylim(-0.3, 4.9)
    ax.axis("off")
    ax.set_title("SmartGrocer - suggested store planogram", fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path
