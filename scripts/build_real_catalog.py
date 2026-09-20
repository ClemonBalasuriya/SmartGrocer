"""
Build smartgrocer/data/real_catalog.csv from a shop's own POS exports.

This is a one-off / re-run-when-you-get-new-exports tool, not something the
app calls at runtime - the runtime side is smartgrocer/import_products.py,
which just reads whatever CSV this script produces.

Why this exists: SmartGrocer's demo catalogue (smartgrocer/catalog.py) is a
small, hand-picked, fully-priced 66-item list used to keep the forecasting/
association/layout demos and the test suite fast and easy to reason about.
For actually running the till at a real shop, that's nowhere near enough -
Balasuriya Group's own stores carry thousands of real SKUs. This script
turns two real exports most small-shop POS software can produce into the
CSV smartgrocer/import_products.py loads into the live `products` table:

  1. An "Item List for Stock Take" export (code + item name per SKU, no
     price, no quantity - it's a blank counting sheet). Run it through
     `pdftotext -layout <file>.pdf <file>.txt` first.
  2. An "Invoice Detail Report" export covering some period of real sales
     (item name + code + qty + unit PRICE per line actually sold). Also
     via `pdftotext -layout`. This is optional - without it every row
     gets a placeholder price - but it's what lets real prices flow in
     for whichever items actually sold during that period.

Usage:
    python3 scripts/build_real_catalog.py \\
        --stocktake path/to/item_list_stocktake.txt \\
        --invoice path/to/invoice_detail_report.txt \\
        --out smartgrocer/data/real_catalog.csv

Both input formats are specific to the "Item List for Stock Take" / "Invoice
Detail Report" layouts this was built against (seen in Ranapriya Stores'
2026 exports) - a different POS system's export will need its own small
regex tweak below, but the CSV shape it produces (and therefore everything
downstream in import_products.py) stays the same.
"""
from __future__ import annotations

import argparse
import csv
import re
import statistics
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Category rules: (category, [substrings to match in the UPPERCASED item
# name]). Checked in order, first match wins - a heuristic keyword
# classifier over real, messy shop data (mixed English/Sinhala/typos), so it
# WILL misclassify some items. That's a known, documented limitation, not a
# bug to chase down: unmatched/misclassified items land in "General
# Grocery" rather than a confidently-wrong bucket, and the existing
# Inventory -> Edit Item dialog is exactly where a shop owner fixes one.
# ---------------------------------------------------------------------------
CATEGORY_RULES = [
    ("Baby Products", ["DIAPER", "PAMPERS", "BABY", "HUGGIES"]),
    ("Seafood & Dried Fish", ["KARAWALA", "HALMASSO", "KOONISSO", "JADI", "JAADI", "DRIED FISH",
                              "ISSO", "KATTA ", "SPRATS", "KELLAWALLA", "BALA "]),
    ("Tobacco & Betel", ["CIGAR", "BEEDI", "DUMMALU", "BETEL", "BULATH"]),
    ("Telecom & Reload", ["RELOAD", "DIALOG", "MOBITEL", "AIRTEL", "HUTCH", "ETISALAT",
                           "SCRATCH CARD", "TOP UP", "TOPUP"]),
    ("Medical & Pharmacy", ["BANDAGE", "PANADOL", "TABLET", "SYRUP", "OINTMENT", "COTTON WOOL",
                             "DETTOL", "SAVLON", "ORS ", "PARACETAMOL"]),
    ("Stationery", ["PENCIL", " PEN ", "PEN,", "EXERCISE BOOK", "NOTE BOOK", "NOTEBOOK", "ERASER",
                     "STAPLER", "GLUE", "CRAYON", "GEOMETRY", "SKETCH", "PAPER", "FILE COVER",
                     "MATHS SET"]),
    ("Hardware & Electrical", ["BULB", "ADAPTER", "TORCH", "WIRE", "SWITCH", "BATTARY", "BATTERY",
                                "PLUG", "TAPE ELECTRIC"]),
    ("Frozen", ["ICE CREEM", "ICE CREAM", "FROZEN", "SOSEJ", "SAUSAGE"]),
    ("Dairy", ["MILK", "KIRI ", "YOGURT", "YOGHURT", "CURD", "CHEESE", "BUTTER", "MARGARINE",
               "MAGARINE", "GHEE"]),
    ("Bread & Bakery", ["BREAD", "BUN", " PAN ", "CAKE", "BAKERY"]),
    ("Beverages", ["JUICE", "DRINK", "COLA", "SODA", "WATER BOTTLE", "MINERAL WATER", " TEA ",
                    "KIRI TEA", "COFFEE", "KAWPI", "MILO", "NESCAFE", "CORDIAL", "ALOVERA",
                    "ENERGY DRINK", "GINGER BEER"]),
    ("Snacks & Confectionery", ["CHOCOLATE", "CHOCO", "BISCUIT", "TOFFEE", "CANDY", "KANDOS",
                                 "GUM", "WAFER", "CRACKER", "CRAKER", "JELLY", "SWEET"]),
    ("Canned & Packaged", ["TIN ", " TIN", "CANNED", "NOODLES", "NOOLDES", "INSTANT NOODLE"]),
    ("Personal Care", ["SHAMPOO", "TOOTHPASTE", "TOOTHBRUSH", "DEO ", "DEO BODY", "LOTION",
                        "SOAP", "SANITARY", "RAZOR", "TALCUM", "COLOGNE", "FAIR & LOVELY",
                        "FACE CREAM", "PADS"]),
    ("Household", ["DISH WASH", "DETERGENT", "WASHING POWDER", "SODEN", "MOSQUITO", "GARBAGE BAG",
                    "CANDLE", "MATCHES", "BAG 1", "BAG 2", "BAG 3", "BAG 5", "BAGS ", "TISSUE",
                    "TOILET ROLL", "AIR FRESHNER", "BAYGONE", "MOP"]),
    ("Spices & Condiments", ["MIRIS", "CHILLI", "KAHA", "TURMERIC", "CURRY POWDER", "LUNU",
                              "LOONU", "SALT", "SUGAR", "SINI", "JAM ", "SAUCE", "VINEGAR",
                              "COCONUT OIL", "POL TEL", "AJI NO MOTO", "SPICE", "THUNAPAHA"]),
    ("Rice & Grains", ["RICE", "SAHAL", "NADU", "SAMBA", "KEKULU", "PARIPPU", "DHAL", "LENTIL",
                        "FLOUR", "PITI", "PITTU", "WHEAT", "GRAM ", "KURAKKAN", "OAT MEAL",
                        "OATMEAL", "CORN FLAKES", "CEREAL", "BAKING POWDER"]),
    ("Vegetables", ["TOMATO", "TAKKALI", "ONION", "POTATO", "ARTAPAL", "CARROT", "BANANA",
                     "KESEL", "ALA ", "CASSAVA", "MANIOC"]),
]

# Second pass, only checked if nothing above matched - broader/ambiguous
# terms that would misfire if checked first (e.g. "POWDER" alone).
CATEGORY_RULES_FALLBACK = [
    ("Personal Care", ["FACE WASH", "HAIR OIL", "BODY SPRAY", "TOOTH BRUSH", "CONDITIONER",
                        " CON ", "PERFUME", "SKIN CARE", "LIPSTICK", "NAIL"]),
    ("Household", ["SHOE POLISH", "REFILL", "INSECT", "REPELLENT"]),
    ("Seafood & Dried Fish", ["CUTTLEFISH", "PRAWN", "FISH ", " FISH", "TUNA", "MACKEREL",
                               "SARDINE", "DEVEL"]),
    ("Snacks & Confectionery", ["SAMOSA", "CUTLET", "PATTIES", "SHORTEATS"]),
    ("Canned & Packaged", ["CURRY PASTE", "CHICKEN FINGER", "NUGGET"]),
    ("Stationery", ["ROOL", "EXERCISE", "CHALK", " KALI"]),
]

MARGIN_BY_CATEGORY = {
    "Rice & Grains": 0.12, "Bread & Bakery": 0.20, "Dairy": 0.12, "Beverages": 0.18,
    "Spices & Condiments": 0.20, "Canned & Packaged": 0.20, "Snacks & Confectionery": 0.28,
    "Household": 0.20, "Personal Care": 0.25, "Baby Products": 0.15, "Vegetables": 0.20,
    "Frozen": 0.20, "Seafood & Dried Fish": 0.18, "Stationery": 0.25, "Tobacco & Betel": 0.10,
    "Telecom & Reload": 0.05, "Hardware & Electrical": 0.20, "Medical & Pharmacy": 0.20,
    "General Grocery": 0.18,
}
PERISHABLE_CATEGORIES = {"Dairy", "Bread & Bakery", "Frozen", "Vegetables", "Seafood & Dried Fish"}
STAPLE_CATEGORIES = {"Rice & Grains", "Spices & Condiments"}
DEFAULT_MARGIN = 0.18

# Matches one stocktake row, e.g.:
#   " AARYA CLASSIC 10 PADS                       00035          ........"
_STOCKTAKE_ITEM_RE = re.compile(r'^\s*(.+?)\s{2,}(\d{4,8})\s+\.{5,}')
_STOCKTAKE_GROUP_RE = re.compile(r'^\s*Item Group Name\s+(.+?)\s*$')

# Matches one invoice-detail line item, e.g.:
#   "lanka FISH JACK MACKEREL 425 G            19208    1.00  480.00  0.00  0.00  480.00"
_INVOICE_ROW_RE = re.compile(
    r'^(.{1,45}?)\s{2,}(\d{3,8})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+'
    r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$'
)


def parse_stocktake(path: Path) -> dict[str, dict]:
    """code (normalized, no leading zeros) -> {code, name, group}."""
    items: dict[str, dict] = {}
    current_group = None
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        gm = _STOCKTAKE_GROUP_RE.match(line)
        if gm:
            g = gm.group(1).strip()
            current_group = None if g == "- None -" else g
            continue
        im = _STOCKTAKE_ITEM_RE.match(line)
        if im:
            name, code = im.group(1).strip(), im.group(2).strip()
            items[code.lstrip("0") or "0"] = {"code": code, "name": name, "group": current_group}
    return items


def parse_invoice_prices(path: Path) -> dict[str, list[float]]:
    """code (normalized) -> list of real observed unit selling prices."""
    prices: dict[str, list[float]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        m = _INVOICE_ROW_RE.match(line)
        if not m:
            continue
        _name, code, _qty, price, _dis, _tax, _net = m.groups()
        code_n = code.lstrip("0") or "0"
        prices.setdefault(code_n, []).append(float(price.replace(",", "")))
    return prices


def categorize(name: str, group: str | None) -> str:
    upper = f" {name.upper()} "
    for category, needles in CATEGORY_RULES:
        if any(needle in upper for needle in needles):
            return category
    for category, needles in CATEGORY_RULES_FALLBACK:
        if any(needle in upper for needle in needles):
            return category
    if group:
        g = group.upper()
        if "TOBAC" in g:
            return "Tobacco & Betel"
        if any(t in g for t in ("ETISALAT", "MOBITEL", "DIALOG")):
            return "Telecom & Reload"
        if "PARIPPU" in g:
            return "Rice & Grains"
    return "General Grocery"


def build_rows(items: dict[str, dict], prices: dict[str, list[float]]) -> list[dict]:
    classified = {code: categorize(rec["name"], rec["group"]) for code, rec in items.items()}

    cat_observed: dict[str, list[float]] = {}
    for code, plist in prices.items():
        if code in classified:
            cat_observed.setdefault(classified[code], []).extend(plist)
    cat_placeholder = {cat: round(statistics.median(pl), 2) for cat, pl in cat_observed.items()}
    all_observed = [p for pl in prices.values() for p in pl]
    global_placeholder = round(statistics.median(all_observed), 2) if all_observed else 100.0

    rows = []
    for code, rec in sorted(items.items(), key=lambda kv: kv[1]["name"]):
        cat = classified[code]
        margin = MARGIN_BY_CATEGORY.get(cat, DEFAULT_MARGIN)
        if code in prices:
            cash_price = round(statistics.median(prices[code]), 2)
            verified = 1
        else:
            cash_price = cat_placeholder.get(cat, global_placeholder)
            verified = 0
        cash_price = max(cash_price, 1.0)
        cost_price = round(cash_price / (1 + margin), 2)
        rows.append({
            "code": rec["code"], "name_en": rec["name"].strip(), "category": cat, "unit": "pcs",
            "cost_price": cost_price, "cash_price": cash_price, "margin_pct": margin,
            "pack_size": 1, "reorder_level": 10,
            "is_perishable": 1 if cat in PERISHABLE_CATEGORIES else 0,
            "is_staple": 1 if cat in STAPLE_CATEGORIES else 0, "child_target": 0,
            "price_verified": verified, "source_group": rec["group"] or "",
        })
    return rows


FIELDNAMES = ["code", "name_en", "category", "unit", "cost_price", "cash_price", "margin_pct",
              "pack_size", "reorder_level", "is_perishable", "is_staple", "child_target",
              "price_verified", "source_group"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stocktake", required=True, type=Path,
                         help="pdftotext -layout output of the Item List for Stock Take export")
    parser.add_argument("--invoice", type=Path, default=None,
                         help="pdftotext -layout output of an Invoice Detail Report (optional)")
    parser.add_argument("--out", required=True, type=Path,
                         help="Where to write the resulting CSV (e.g. smartgrocer/data/real_catalog.csv)")
    args = parser.parse_args()

    items = parse_stocktake(args.stocktake)
    prices = parse_invoice_prices(args.invoice) if args.invoice else {}
    rows = build_rows(items, prices)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    verified_n = sum(r["price_verified"] for r in rows)
    cat_counts = Counter(r["category"] for r in rows)
    print(f"Wrote {len(rows)} items to {args.out}")
    print(f"Real observed prices: {verified_n} ({verified_n / len(rows):.0%}) "
          f"- the rest use a same-category placeholder (price_verified=0)")
    print("By category:")
    for cat, n in cat_counts.most_common():
        print(f"  {cat:28s} {n:5d}")


if __name__ == "__main__":
    main()
