"""
Static reference data: the product catalogue for a typical small Sri Lankan
grocery shop, and the "affinity groups" used by the synthetic data generator
to build realistic baskets (so the association-rule module has real patterns
to find, e.g. bread+jam+margarine, tea+sugar+milk powder).

Fields per item (in order):
  code, name_en, name_si, category, unit, cost_price, margin_pct, pack_size,
  is_perishable, is_staple, child_target, shelf_life_days (None if not perishable)

Sinhala names are the common shop-floor terms; treat them as a starting point
- swap in exact wording from your own POS export once you have it.
"""

# code,    name_en,                        name_si,              category,          unit, cost, margin, pack, perish, staple, child, shelf_life
CATALOG = [
    ("10001", "Samba Rice 5kg",             "සම්බා සහල් 5kg",      "Rice & Grains",   "pcs", 950.0, 0.12, 1, 0, 1, 0, None),
    ("10002", "Nadu Rice 5kg",              "නාඩු සහල් 5kg",       "Rice & Grains",   "pcs", 850.0, 0.12, 1, 0, 1, 0, None),
    ("10003", "Kekulu Rice 5kg",            "කැකුළු සහල් 5kg",     "Rice & Grains",   "pcs", 820.0, 0.12, 1, 0, 1, 0, None),
    ("10004", "Red Raw Rice 5kg",           "රතු කැකුළු සහල් 5kg", "Rice & Grains",   "pcs", 900.0, 0.12, 1, 0, 1, 0, None),
    ("10005", "Wheat Flour 1kg",            "තිරිඟු පිටි 1kg",     "Rice & Grains",   "pcs", 220.0, 0.15, 1, 0, 1, 0, None),
    ("10006", "Red Lentils (Parippu) 1kg",  "පරිප්පු 1kg",         "Rice & Grains",   "pcs", 380.0, 0.14, 1, 0, 1, 0, None),
    ("10007", "Green Gram 500g",            "මුං ඇට 500g",        "Rice & Grains",   "pcs", 260.0, 0.15, 1, 0, 0, 0, None),
    ("10008", "White Sugar 1kg",            "සීනි 1kg",            "Rice & Grains",   "pcs", 240.0, 0.10, 1, 0, 1, 0, None),
    ("20001", "Sunlight Bread Loaf",        "පාන් ලොෆ්",           "Bread & Bakery",  "pcs", 130.0, 0.20, 1, 1, 1, 0, 3),
    ("20002", "Kimbula Bun (pack 4)",       "කිඹුල බනිස්",         "Bread & Bakery",  "pcs", 150.0, 0.22, 1, 1, 0, 0, 2),
    ("20003", "Butter Cake Slice",          "බටර් කේක්",           "Bread & Bakery",  "pcs", 90.0, 0.30, 1, 1, 0, 1, 2),
    ("30001", "Full Cream Milk Powder 400g","කිරි පිටි 400g",      "Dairy",           "pcs", 780.0, 0.10, 1, 0, 1, 0, 240),
    ("30002", "Fresh Milk 1L",              "නැවුම් කිරි 1L",      "Dairy",           "pcs", 300.0, 0.14, 1, 1, 1, 0, 5),
    ("30003", "Yoghurt Cup",                "යෝගට් කප්",           "Dairy",           "pcs", 65.0, 0.25, 1, 1, 0, 1, 7),
    ("30004", "Butter 200g",                "බටර් 200g",           "Dairy",           "pcs", 480.0, 0.15, 1, 1, 0, 0, 60),
    ("30005", "Cheese Slices (pack)",       "චීස් ස්ලයිස්",        "Dairy",           "pcs", 420.0, 0.18, 1, 1, 0, 1, 30),
    ("30006", "Eggs (tray of 10)",          "බිත්තර 10",           "Dairy",           "pcs", 380.0, 0.12, 1, 1, 1, 0, 21),
    ("40001", "Ceylon Tea 100 Bags",        "තේ කොළ 100",          "Beverages",       "pcs", 420.0, 0.18, 1, 0, 1, 0, None),
    ("40002", "Instant Coffee 100g",        "කෝපි 100g",           "Beverages",       "pcs", 480.0, 0.20, 1, 0, 0, 0, None),
    ("40003", "Ginger Beer 1.5L",           "ජින්ජර් බියර්",       "Beverages",       "pcs", 210.0, 0.22, 1, 1, 0, 0, 180),
    ("40004", "Orange Cordial 750ml",       "ඔරේන්ජ් කෝඩියල්",     "Beverages",       "pcs", 380.0, 0.20, 1, 0, 0, 1, None),
    ("40005", "Mineral Water 1.5L",         "පිරිසිදු වතුර 1.5L",  "Beverages",       "pcs", 90.0, 0.25, 1, 0, 1, 0, None),
    ("40006", "Milo 400g",                  "මයිලෝ 400g",          "Beverages",       "pcs", 680.0, 0.16, 1, 0, 0, 1, None),
    ("50001", "Coconut Oil 750ml",          "පොල් තෙල් 750ml",     "Spices & Condiments","pcs", 480.0, 0.15, 1, 0, 1, 0, None),
    ("50002", "Chilli Powder 250g",         "මිරිස් කුඩු 250g",    "Spices & Condiments","pcs", 260.0, 0.20, 1, 0, 1, 0, None),
    ("50003", "Curry Powder 250g",          "කරි කුඩු 250g",       "Spices & Condiments","pcs", 240.0, 0.20, 1, 0, 1, 0, None),
    ("50004", "Turmeric Powder 100g",       "කහ කුඩු 100g",        "Spices & Condiments","pcs", 120.0, 0.22, 1, 0, 0, 0, None),
    ("50005", "Table Salt 400g",            "ලුණු 400g",            "Spices & Condiments","pcs", 45.0, 0.30, 1, 0, 1, 0, None),
    ("50006", "Tomato Sauce 400g",          "තක්කාලි සෝස් 400g",   "Spices & Condiments","pcs", 260.0, 0.20, 1, 0, 0, 0, None),
    ("50007", "Chilli Sauce 400g",          "මිරිස් සෝස් 400g",    "Spices & Condiments","pcs", 260.0, 0.20, 1, 0, 0, 0, None),
    ("50008", "Strawberry Jam 500g",        "ජෑම් 500g",           "Spices & Condiments","pcs", 420.0, 0.20, 1, 0, 0, 1, None),
    ("50009", "Margarine 500g",             "මාගරින් 500g",        "Spices & Condiments","pcs", 320.0, 0.18, 1, 1, 0, 0, 90),
    ("60001", "Canned Fish (Mackerel) 425g","ටින් මාළු 425g",      "Canned & Packaged","pcs", 380.0, 0.18, 1, 0, 1, 0, None),
    ("60002", "Canned Sardines 155g",       "සාඩින් ටින්",         "Canned & Packaged","pcs", 220.0, 0.20, 1, 0, 0, 0, None),
    ("60003", "Instant Noodles (pack)",     "නූඩ්ල්ස්",             "Canned & Packaged","pcs", 90.0, 0.25, 1, 0, 0, 1, None),
    ("60004", "Baked Beans 425g",           "බේක්ඩ් බීන්ස්",       "Canned & Packaged","pcs", 260.0, 0.20, 1, 0, 0, 0, None),
    ("70001", "Potato Chips (large)",       "අර්තාපල් චිප්ස්",     "Snacks & Confectionery","pcs", 160.0, 0.28, 1, 0, 0, 1, 120),
    ("70002", "Chocolate Bar",              "චොකලට් බාර්",         "Snacks & Confectionery","pcs", 130.0, 0.30, 1, 0, 0, 1, 180),
    ("70003", "Biscuits (Marie) 200g",      "බිස්කට් 200g",        "Snacks & Confectionery","pcs", 150.0, 0.25, 1, 0, 0, 1, 150),
    ("70004", "Toffee Pack",                "ටොෆි",                "Snacks & Confectionery","pcs", 90.0, 0.30, 1, 0, 0, 1, 150),
    ("70005", "Kavum / Kokis (festive)",    "කැවුම් / කොකිස්",     "Snacks & Confectionery","pcs", 180.0, 0.28, 1, 1, 0, 1, 10),
    ("80001", "Dish Wash Liquid 500ml",     "පිඟන් සෝදන ද්‍රව්‍ය", "Household",       "pcs", 260.0, 0.20, 1, 0, 1, 0, None),
    ("80002", "Laundry Powder 1kg",         "රෙදි සෝදන පව්ඩර්",    "Household",       "pcs", 420.0, 0.18, 1, 0, 1, 0, None),
    ("80003", "Toilet Soap",                "සබන්",                "Household",       "pcs", 90.0, 0.22, 1, 0, 1, 0, None),
    ("80004", "Mosquito Coil (pack)",       "මදුරු කොයිල්",        "Household",       "pcs", 140.0, 0.25, 1, 0, 0, 0, None),
    ("80005", "Garbage Bags (roll)",        "කසළ බෑග්",            "Household",       "pcs", 160.0, 0.25, 1, 0, 0, 0, None),
    ("80006", "Matches (box)",              "පොරය",                "Household",       "pcs", 15.0, 0.30, 1, 0, 1, 0, None),
    ("80007", "Candles (pack)",             "ඉටිපන්දම්",           "Household",       "pcs", 60.0, 0.25, 1, 0, 0, 0, None),
    ("90001", "Shampoo Sachet",             "ෂැම්පු සැචට්",        "Personal Care",   "pcs", 20.0, 0.35, 1, 0, 0, 0, None),
    ("90002", "Toothpaste 100g",            "දන්ත බේට්ටර්",        "Personal Care",   "pcs", 180.0, 0.20, 1, 0, 1, 0, None),
    ("90003", "Toothbrush",                 "දත් බුරුසුව",         "Personal Care",   "pcs", 60.0, 0.30, 1, 0, 0, 0, None),
    ("90004", "Talcum Powder",              "තැල්කම් පව්ඩර්",      "Personal Care",   "pcs", 210.0, 0.22, 1, 0, 0, 0, None),
    ("90005", "Sanitary Pads (pack)",       "සනීපාරක්ෂක තුවා",     "Personal Care",   "pcs", 220.0, 0.22, 1, 0, 1, 0, None),
    ("90006", "Baby Diapers (pack of 10)",  "බේබි ඩයපර්",          "Baby Products",   "pcs", 850.0, 0.18, 1, 0, 0, 1, None),
    ("90007", "Baby Milk Powder 400g",      "බේබි කිරි පිටි",      "Baby Products",   "pcs", 1450.0, 0.12, 1, 0, 0, 1, None),
    ("90008", "Baby Wipes",                 "බේබි වයිප්ස්",        "Baby Products",   "pcs", 320.0, 0.20, 1, 0, 0, 1, None),
    ("90009", "Baby Biscuits",              "බේබි බිස්කට්",        "Baby Products",   "pcs", 260.0, 0.25, 1, 0, 0, 1, 200),
    ("A0001", "Tomatoes 1kg",               "තක්කාලි 1kg",         "Vegetables",      "kg", 220.0, 0.20, 1, 1, 1, 0, 5),
    ("A0002", "Onions (Big) 1kg",           "ලූනු 1kg",            "Vegetables",      "kg", 260.0, 0.18, 1, 1, 1, 0, 20),
    ("A0003", "Potatoes 1kg",               "අර්තාපල් 1kg",        "Vegetables",      "kg", 240.0, 0.18, 1, 1, 1, 0, 20),
    ("A0004", "Green Chillies 250g",        "මිරිස් 250g",         "Vegetables",      "kg", 100.0, 0.25, 1, 1, 0, 0, 6),
    ("A0005", "Carrots 1kg",                "කැරට් 1kg",           "Vegetables",      "kg", 200.0, 0.20, 1, 1, 0, 0, 10),
    ("A0006", "Bananas (dozen)",            "කෙසෙල් (දුසිම)",      "Vegetables",      "pcs", 180.0, 0.22, 1, 1, 1, 0, 6),
    ("B0001", "Frozen Sausages 500g",       "සොසේජස් 500g",        "Frozen",          "pcs", 420.0, 0.18, 1, 1, 0, 1, 60),
    ("B0002", "Frozen Chicken 1kg",         "කුකුල් මස් 1kg",      "Frozen",          "kg", 780.0, 0.15, 1, 1, 1, 0, 45),
    ("B0003", "Ice Cream Cup",              "අයිස්ක්‍රීම් කප්",    "Frozen",          "pcs", 120.0, 0.28, 1, 1, 0, 1, 90),
]

COLUMNS = [
    "code", "name_en", "name_si", "category", "unit", "cost_price", "margin_pct",
    "pack_size", "is_perishable", "is_staple", "child_target", "shelf_life_days",
]

# Groups of item codes that tend to be bought together - used only by the
# synthetic data generator to bias basket composition so the Apriori module
# has genuine co-purchase patterns to discover, mirroring literature on
# planned bundle purchases (bread/spread, tea-time, cooking staples).
AFFINITY_GROUPS = [
    ["20001", "50008", "50009"],            # bread + jam + margarine
    ["40001", "10008", "30001"],            # tea + sugar + milk powder
    ["10006", "50002", "50003", "50001"],   # dhal + chilli powder + curry powder + coconut oil
    ["A0001", "A0002", "50002"],            # tomatoes + onions + chilli powder (curry base)
    ["30002", "40002"],                     # milk + coffee
    ["60003", "50006"],                     # noodles + tomato sauce
    ["70001", "70002", "70003"],            # snack run
    ["90006", "90007", "90008"],            # baby diapers + milk powder + wipes
    ["B0001", "20001", "50006"],            # sausages + bread + sauce (quick breakfast)
    ["A0006", "30003"],                     # bananas + yoghurt
]
