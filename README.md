# SmartGrocer

An automated analytics and decision-support system for independent grocery
retailers, built for the final-year research project *"SmartGrocer: An
Automated Analytics and Decision Support System for Independent Grocery
Retailers"* (B.D.C.A. Balasuriya, SC/2022/12933, Dept. of Mathematics,
University of Ruhuna, supervised by Dr N. Yapage).

Unlike the original proposal's framing (a standalone tool that imports a
PLU/REP export from a *separate* POS machine), this build is the POS itself:
a checkout module that scans/sells items and writes straight into the
database, with the four analytics modules running on that live data. This
also means SmartGrocer can generate its own historical data over time,
rather than depending on getting a real export file from a cooperating shop.
An importer for a real PLU/REP export can be added later (see *Extending*
below) once you have one.

## What's implemented, mapped to the proposal's objectives

| Objective | Module | Notes |
|---|---|---|
| 1. Per-shop forecasting (SARIMA/SARIMAX/Holt-Winters/Seasonal Naive, nRMSE model selection) | `smartgrocer/forecasting.py` | Holt-Winters & Seasonal Naive implemented from scratch (no dependency); SARIMA/SARIMAX use `statsmodels`. Rolling-origin CV picks the best model per product. 2-std-dev anomaly detector included. |
| 2. Promotion/expiry urgency engine (`U = 0.5 Se + 0.3 Sv + 0.2 Sm`) | `smartgrocer/promotions.py` | Tiered alerts (7-day / 3-day / overdue), discount suggestion with a margin floor, projected clearance date. |
| 3. Apriori bundle mining | `smartgrocer/association.py` | Classic Apriori implemented from scratch (no `mlxtend` dependency) at the proposal's 2% minimum support, LKR bundle pricing. |
| 4. Four-layer store layout | `smartgrocer/layout.py` | Adjacency clustering, forced-path staple placement, decision-point placement, shelf-height zoning; renders a planogram PNG. |
| 5. Evaluate vs. Seasonal Naive baseline | `forecasting.select_best_model` | nRMSE is reported per candidate model, Seasonal Naive included. |
| POS / checkout | `smartgrocer/pos.py` | Barcode/name search, cart, 3 price tiers, FIFO stock deduction by expiry, void. |
| Extras added beyond the proposal | see below | |

**Extra features added** (per your "add more if you think of them"):
- **Demand-based purchase list** (`forecasting.generate_purchase_list`) - compares forecast demand to on-hand stock and suggests reorder quantities. This was in your presentation's "Expected Outcomes" but had no implementation yet.
- **Historical waste tracking** (`waste_events` table, `reports.waste_summary`) - batches that expire unsold are now logged with their cost value, giving you a concrete "before" baseline to argue the promotions engine reduces, rather than only a live near-expiry queue.
- **Cashier daily statement**, **low-stock alerts**, **dashboard KPI cards**, **CSV export** on every report screen, and **one-click SQLite backup/restore** - the kind of things a real shop owner asks for on day one.
- **Receive Stock (GRN)** dialog in the Inventory screen, matching the GRN feature visible in the sample POS system you photographed.
- **Customer credit accounts** (`smartgrocer/customers.py`, Customers screen) - named customers with a credit limit and running balance, a credit sale is blocked once it would exceed their limit, and a "Settle Credit" / statement view records payments against the balance over time - matching the sample POS system's Customer / Credit Invoice / Credit Settlement screens.
- **Supplier records** (`smartgrocer/suppliers.py`, Suppliers screen) - the Receive Stock (GRN) dialog now attributes each stock batch to a supplier, and a Supplier Summary report shows quantity/value received per supplier.
- **Item return / refund** (`pos.return_items`, POS screen's "Return / Refund" button) - look up a past invoice by number, return part or all of a line, restock it, and refund it (or, for a credit sale, reduce the customer's balance instead of handing back cash).
- **Hold / resume invoice** (`pos.hold_cart`/`resume_held_invoice`, POS screen's "Hold Invoice" / "Resume Held" buttons) - park an in-progress cart and bring it back later, matching the sample POS system's F9 Hold Invoice.
- **Split / mixed payment** (POS screen's "Payment: split" option) - collect part cash, part card, part cheque and/or part credit on one sale, matching the sample POS system's Mix Payment screen.
- **Balasuriya Group branding** - the whole GUI (sidebar, buttons, tables) now uses a navy/blue theme sampled from the company logo, shown in the sidebar, instead of CustomTkinter's stock look.

## Project layout

```
smartgrocer/
  db.py              database schema + connection helpers
  catalog.py         static product catalogue + basket "affinity groups"
  calendar_sl.py      Sri Lankan festival calendar (SARIMAX exogenous variable)
  data_generator.py  synthetic ~9-month transaction history generator (demo data)
  pos.py             checkout: search, cart, invoice, FIFO stock deduction, void,
                     item return, hold/resume cart, split payment
  customers.py       customer credit accounts: limit, balance, settlement, statement
  suppliers.py       supplier master records + received-stock summary
  forecasting.py     SARIMA/SARIMAX/Holt-Winters/Seasonal Naive + model selection
  promotions.py      urgency scoring, tiered expiry alerts, discount suggestion
  association.py     Apriori from scratch, bundle recommendations
  layout.py          4-layer layout optimiser + planogram rendering
  reports.py         dashboard KPIs, reports, CSV export, backup/restore
  gui/
    app.py           main window, navigation
    theme.py         Balasuriya Group brand colors + ttk styling helpers
    screens.py       Dashboard / POS / Inventory / Customers / Suppliers /
                      Promotions / Forecasting / Bundles / Layout / Reports screens
tests/
  test_pipeline.py   end-to-end smoke tests (no GUI/display needed)
main.py              entry point
requirements.txt
```

## Setup (on your own Windows/Mac/Linux machine)

This needs a machine with internet access to install packages and (for the
GUI) a normal desktop Python - Windows' python.org installer includes
`tkinter` by default.

```bash
python -m venv venv
venv\Scripts\activate        # on Windows; use `source venv/bin/activate` on Mac/Linux
pip install -r requirements.txt
python main.py
```

On first run it will ask whether to build the synthetic ~9-month demo
dataset - say yes to see every screen populated immediately. Say no to start
from a clean database when you're ready to enter your own shop's data.

## Running the tests

```bash
python -m pytest tests/test_pipeline.py -v
```

or, without pytest: `python tests/test_pipeline.py`. These build a temporary
throwaway database and exercise every module (data generation reconciliation,
POS checkout/void/oversell-guard, forecasting + anomaly detection, Apriori
rule recovery, promotion scoring bounds, layout coverage, report KPIs) - all
9 currently pass.

## A development note worth knowing for your viva

This project was originally built inside a sandboxed environment with no
internet access (couldn't `pip install` anything) and no display/`tkinter`
(couldn't launch a GUI window). Two consequences to be aware of:

1. **Holt-Winters and Apriori were implemented from scratch** (not via
   `statsmodels`/`mlxtend`) specifically so they could be fully unit-tested
   without those libraries - this turned out to be a good thing for a thesis
   anyway, since you can walk through and defend your own implementation of
   both algorithms rather than pointing at a library call. SARIMA/SARIMAX
   still use `statsmodels` (the standard tool for the job); if it isn't
   installed, `forecasting.py` detects that and silently falls back to
   whichever of the other three models is available, so the pipeline never
   crashes for lack of it.
2. **The GUI (`smartgrocer/gui/`) could not be launched or screenshotted**
   during development - only carefully reviewed against the CustomTkinter
   API. Every other module was tested for real (see `tests/test_pipeline.py`
   and the counts/figures produced against the synthetic dataset). Please
   run `python main.py` early and tell me about anything that looks wrong
   layout-wise - that's the one part of this codebase that hasn't been
   executed yet.

## Packaging as a standalone Windows .exe

Do this on a Windows machine (PyInstaller builds for the OS it runs on).

```bash
pip install pyinstaller
pyinstaller --name SmartGrocer --onefile --windowed ^
    --collect-all customtkinter --collect-all statsmodels ^
    main.py
```

- `--windowed` stops a console window from popping up behind the GUI.
- `--collect-all customtkinter` is required - CustomTkinter ships theme
  JSON/asset files that PyInstaller won't find automatically otherwise.
- `--collect-all statsmodels` avoids "hidden import" errors PyInstaller
  sometimes has with statsmodels' compiled extensions.
- The resulting `dist/SmartGrocer.exe` is portable: `db.py` detects it's
  running frozen and keeps its `data/` and `exports/` folders next to the
  `.exe` itself, so double-click it from a USB stick or desktop shortcut and
  it keeps its own data there between runs.
- First run of the `.exe` will still offer to build the synthetic demo
  dataset if no `data/smartgrocer.db` exists yet.

## Known simplifications (worth a line in your methodology chapter)

- **Vesak date** in `calendar_sl.py` is a fixed approximation (it's a lunar
  calendar date that moves year to year) - replace with the correct poya
  date for whichever year(s) your real data covers.
- **SARIMA/SARIMAX orders** are fixed at `(1,1,1)(0,1,1,7)` rather than
  auto-selected by AIC/grid search (a `pmdarima`-style auto-order search is
  a natural extension, noted below).
- **Urgency score components** (Se, Sv, Sm) - the proposal gives the weights
  (0.5/0.3/0.2) but not exact definitions for each component; the docstring
  at the top of `promotions.py` states this implementation's operational
  definitions explicitly so you can cite/justify or tune them.
- The synthetic dataset's basket contents, seasonality and festival
  boosts are approximations meant to exercise every module realistically,
  not a substitute for your real shop's data.

## Extending

- **Importing a real PLU/REP export**: add a `import_pos_export.py` that
  parses the Sinhala-Unicode PLU/REP file format and calls the same
  `pos.create_invoice` / stock_batches inserts the live checkout uses, so
  every downstream module works unchanged.
- **Weekly retrain schedule**: `forecasting.select_best_model` is currently
  invoked on demand from the GUI; wire it to Windows Task Scheduler (or
  Python's `schedule` package) calling it for every product weekly, per the
  proposal's methodology.
- **RAG-based Sinhala/English assistant** (from your presentation's Expected
  Outcomes): deliberately left out of this build - it needs an LLM API key
  and is its own sub-project; a natural next phase once the core analytics
  are marking-ready.
- **`pmdarima`-style automatic SARIMA order search**, if you want to move
  beyond the fixed `(1,1,1)(0,1,1,7)` order.
