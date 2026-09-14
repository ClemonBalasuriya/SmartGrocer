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
- **Add / Scan Item** dialog in the Inventory screen (`pos.add_product` / `pos.receive_stock`) - one entry point for "put an item into the store" that figures out which you mean from the barcode itself: type or scan a code, and if it's not in the catalog yet, the full new-product form appears (name, category, unit, cost/cash/credit/wholesale prices, pack size, reorder level, perishable/staple/child-target flags, opening stock); if that code already belongs to a product, it just shows the product's name/category/current stock and asks how many units came in, then tops up stock the same way the GRN dialog does (both now share `pos.receive_stock`). Previously the only way to register a brand-new product at all was editing the database by hand. The barcode field can be filled by typing, by a USB/Bluetooth scanner (it types into the focused field, same as a keyboard), or by tapping "Scan with Phone" to read it with a phone's camera via the phone-scanner feature described below. Rejects a duplicate barcode with a clear error instead of silently overwriting the existing product.
- **Customer credit accounts** (`smartgrocer/customers.py`, Customers screen) - named customers with a credit limit and running balance, a credit sale is blocked once it would exceed their limit, and a "Settle Credit" / statement view records payments against the balance over time - matching the sample POS system's Customer / Credit Invoice / Credit Settlement screens.
- **Supplier records** (`smartgrocer/suppliers.py`, Suppliers screen) - the Receive Stock (GRN) dialog now attributes each stock batch to a supplier, and a Supplier Summary report shows quantity/value received per supplier.
- **Item return / refund** (`pos.return_items`, POS screen's "Return / Refund" button) - look up a past invoice by number, return part or all of a line, restock it, and refund it (or, for a credit sale, reduce the customer's balance instead of handing back cash).
- **Hold / resume invoice** (`pos.hold_cart`/`resume_held_invoice`, POS screen's "Hold Invoice" / "Resume Held" buttons) - park an in-progress cart and bring it back later, matching the sample POS system's F9 Hold Invoice.
- **Split / mixed payment** (POS screen's "Payment: split" option) - collect part cash, part card, part cheque and/or part credit on one sale, matching the sample POS system's Mix Payment screen.
- **Balasuriya Group branding** - the whole GUI (sidebar, buttons, tables) now uses a navy/blue theme sampled from the company logo, shown in the sidebar, instead of CustomTkinter's stock look. Sidebar labels now wrap instead of overflowing past the sidebar's edge (was clipping the last letter or two of "by Balasuriya Group").
- **Cash drawer / day open-close** (`smartgrocer/cash_drawer.py`, sidebar "Login / Open Day" / "Close Day") - start the day with a counted opening float, and every cashier logs in with their staff PIN (ties every sale to who actually rang it up, not just whoever was picked from a list); closing the day counts the actual cash and shows the variance against what the system expects.
- **Printed receipts** (`smartgrocer/receipts.py`) - every completed sale saves a printable receipt to `exports/receipts/` and opens it with your default text viewer so you can Ctrl+P it.
- **Quick-item buttons** on the POS screen - your top ~12 best-selling items appear as one-tap buttons so the cashier doesn't have to search/type every single sale.
- **Cash Sessions** report - the full history of day-open/day-close cycles with expected vs counted cash and variance.
- **Barcode-scanner scan-to-cart** - the POS screen's search box auto-focuses the moment you open it, and typing/scanning an *exact* product code (any USB or Bluetooth barcode scanner just types the code + Enter into whatever field has focus - no driver/integration needed) adds it straight to the cart at one click's fewer effort than before. A phone running a free scanner app such as "Barcode to PC" (connected over the shop Wi-Fi) works the same way, including for QR codes, with zero code changes needed here. The search box also has its own **"Scan with Phone"** button now, right next to Search (the same one the Add/Scan Item dialog in Inventory already had) - previously the box's own label ("Scan barcode or search item") promised a scan option that wasn't actually there on screen unless you already had a physical scanner. One tap opens the phone-camera scanner from `mobile_scan.py`; the single code it reads goes straight into the box and searches, same as typing/scanning it - an exact match still skips right to the cart. The full continuous "Phone Scanner" session below (which adds every scan straight to the cart with no per-item tap) is unchanged and still the better choice for ringing up a whole basket hands-free; this is for grabbing just one item without needing a hardware scanner.
- **Performance fixes** - two real, measured causes of the app feeling slow as transaction history grows, both fixed this round: (1) several report/dashboard queries wrapped the `datetime`/`expiry_date` columns in `date(...)` in their `WHERE` clause, which stops SQLite from using the existing indexes and forces a full table scan - rewritten to compare against the raw ISO8601 strings directly (see `reports.py`'s and `promotions.py`'s module notes); (2) the Forecasting, Bundle Recommendations, and Store Layout screens ran their SARIMA/Apriori/clustering computation directly on the GUI thread, freezing the whole window - they now run on a background thread (`gui/screens.py`'s `run_in_background` helper) with their own database connection, so the window stays responsive while a spinner/status label shows progress.
- **Staff management screen** (`smartgrocer/staff.py`, Staff screen) - add cashier/admin accounts, edit someone's saved contact info, or deactivate them, from inside the app instead of needing a developer to edit the database by hand. Adding staff and editing contact info needs Admin or Owner; **removing (deactivating) a staff member is Owner-only**, same split as removing an item in Inventory - Admin can onboard people and keep their contact info current, but taking someone off the roster is reserved for the Owner. Deactivating (not deleting) keeps that person's name on their past invoices and cash sessions. Nobody manages anyone else's PIN from here, not even the Owner - see the Owner/PIN bullets below for why, and for what "Edit Contact Info" is actually for.
- **Phone-as-barcode-scanner companion** (`smartgrocer/mobile_scan.py`, POS screen's "Phone Scanner" button) - turns any phone on the shop's Wi-Fi into a hands-free second scanner with nothing to install: the phone opens a plain web page (scan a QR code shown on the till, or type the address) and its **live camera feed** auto-detects barcodes/QR codes roughly twice a second - point it at items and they land in the till's cart on their own, no per-item tap needed (a one-photo-per-item fallback kicks in automatically if the phone's browser can't do a live camera feed). Works identically on Android and iPhone since it's a browser page, not a native app. Needs HTTPS to get camera access at all, so the server generates and caches its own self-signed certificate - the phone's browser shows a one-time "connection not private" warning per phone, which the POS screen's dialog explains how to click through (it's expected for a private local-network device, not a sign of a problem). A short pairing code (shown on the till) stops anyone else on the shop Wi-Fi from adding to a stranger's cart, and a brief per-code cooldown stops one item being added over and over while the phone is still pointed at it. See the module's docstring for the full reasoning, including why this still uses plain request/response per frame rather than a persistent WebSocket connection. Needs `opencv-contrib-python` and `cryptography` (added to `requirements.txt`) - a `pip install -r requirements.txt` is needed after pulling this update.
- **UPC-A / EAN-13 barcode matching** (`pos.get_product_by_code`) - a UPC-A barcode (12 digits) and its EAN-13 form (the same number with a leading zero, 13 digits) are the same physical barcode, but the phone camera's decoder doesn't always report the same one from scan to scan. Without this, a product saved from one reading could come back "not in the catalog" the next time it's scanned, purely because the decoder returned the other, equally valid, form of the same code. Product lookup (search box, Phone Scanner, and the Add/Scan Item dialog's catalog check) now treats both forms as the same product; short PLU/manual codes are untouched. The POS search box also now says clearly when nothing matches, instead of just showing an empty results list.
- **Multi-till networking** (`smartgrocer/netserver.py`, `netclient.py`, `till_config.py`, Network screen) - lets more than one cashier till (wired or Wi-Fi, doesn't matter which) share the exact same live product/stock/sales data instead of each till having its own separate, disconnected database file. One PC is the "Main Till" (keeps the real database, shares it); every other PC is a "Cashier Till" that connects to it instead of opening a database file of its own. Every existing screen and module works completely unchanged either way - `pos.py`, `staff.py`, every raw query in `screens.py` - because `netclient.RemoteConnection` makes a network connection to the Main Till look exactly like a normal database connection everywhere else in the code (see `netclient.py`'s docstring for why that's both simpler and safer than adding a network endpoint per feature one at a time). Protected by a pairing code and the same kind of self-signed-HTTPS setup as the Phone Scanner; each connected till gets its own database session so one till's in-progress sale doesn't leak into another's queries (backed by SQLite's WAL mode, also newly turned on in `db.py`). See "Setting up more than one till" below for how to actually connect a second PC.

- **Owner role, Activity Log, and admin-action notifications** (`smartgrocer/audit.py`, `notifications.py`, `staff.py`, Activity Log screen) - a security layer built from the shop owner's own requests over a few rounds. Roles: **Owner** sits above **Admin** sits above **Cashier**. There is exactly **one** Owner account, always - `db.py` seeds it automatically (name "Owner", PIN **1234**, same as the default Admin) the first time the app runs against a database that doesn't have one yet, so it's there from the very first launch with nothing to set up; `staff.add_staff` refuses to ever create a second Owner, and `staff.set_active` refuses to deactivate this one - both enforced at the data layer, not just hidden buttons, so it can't be created, duplicated, or removed by mistake or by a bug in the GUI. The **Activity Log** (who added/removed a cashier, who edited their contact info, who edited/removed a catalog item, and when - names/roles are snapshotted at write time so it still reads correctly after someone is renamed or deactivated) is viewable by **both Admin and Owner**; the **Notification Settings** inside it (SMTP/Twilio credentials) stay Owner-only, since that's configuration, not just reading a log. Every sensitive action sends the Owner a best-effort "who did what" notification. **Email** sends immediately once you fill in a Gmail address + "app password" in Notification Settings; **SMS/WhatsApp** need your own paid Twilio account (there's no free or keyless way to send either from any provider) - the Twilio code is complete and real but untested against a live account since none exists here. A real send failure never blocks the action that triggered it - except see the PIN-recovery bullet below, where the ordering is deliberately the other way round. If a notification isn't arriving, Notification Settings' **"Save & Send Test"** button now shows the *actual* reason (wrong Gmail app password, Twilio credentials blank, etc.) instead of a bare "nothing sent" - every other place in the app still hides that detail on purpose, since a notification is never allowed to block real work, but on this settings screen surfacing it is the whole point. A cloud-based system for checking shop details remotely is a natural next step here but deliberately not built yet - noted for later.
- **Add / Edit / Remove Item, with matching permissions** (`pos.add_product` / `update_product` / `deactivate_product` / `reactivate_product`, Inventory screen) - **Admin or Owner** can add a new item, receive stock (GRN), and edit an existing item's details (name, category, unit, all four prices, pack size, reorder level, perishable/staple/child-target flags) - a cashier can view stock levels but not change the catalog, closing what was previously an open door (Add/Scan Item and Receive Stock had no login check at all). The barcode itself isn't editable via "Edit Item" (it's what every scan/receipt/stock batch identifies the item by - add a fresh item instead if a code was genuinely wrong). Removing an item is **Owner-only** and, exactly like staff, never actually deletes anything - stock_batches and invoice_items reference a product by id, so a real delete would either fail or orphan real sales/stock history. Instead it's marked inactive (disappears from the active catalog and checkout lookup, same as a deactivated cashier) **and its stock on hand is zeroed** rather than left as stale phantom quantity - the same button restores it later (stock stays at zero until restocked; past batch/sale history for it was never touched). All of these actions are written to the Activity Log.
- **Self-service-only PIN changes, with email recovery** (`staff.generate_temp_pin`/`update_contact`, sidebar's "Change My PIN", Login dialog's "Forgot PIN?", Staff screen's "Edit Contact Info") - **nobody manages anyone else's PIN anymore, not even the Owner.** An earlier version let an Admin/Owner force-reset someone else's PIN directly; it was removed on the shop owner's own instinct that it was a real weak point (an Admin/Owner account being able to silently hand itself access to any other account is exactly the kind of privileged lever worth not having at all). What's left: (1) **"Change My PIN"**, next to Login/Close Day in the sidebar - anyone logged in changes their own PIN, knowing their current one, any role; (2) **"Forgot PIN?"** on the Login screen - if someone's locked out entirely, picking their name and clicking it emails (or texts, if phone + Twilio are set up) a fresh random PIN straight to the address saved on their account, never shown on screen; **the PIN is only changed once delivery actually succeeds** - if sending fails, nothing about the account changes and the person is told plainly why (most often the shop hasn't finished Notification Settings yet) rather than being left with a new PIN nobody, including them, knows. If someone has no email/phone on file yet, an Admin/Owner can add one via Staff screen's new **"Edit Contact Info"** - which only ever touches contact details, never the PIN itself. Every one of these actions notifies **two** people, not just one: the Owner always gets a "who did what" copy, and the **individual whose own account changed** - PIN changed (self-service or forgotten), contact info edited, activated/deactivated - gets their own copy too, addressed to whatever email/phone is on file for THEM specifically, so a change to someone's account is never something only the Owner hears about.

These were added because this system is meant for the shop's actual day-to-day operation, not only as a decision-support/analytics layer - the four analytics objectives from the proposal are still there underneath, but the POS itself now behaves like a till a cashier would use every day.

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
  cash_drawer.py     day open/close: opening float, expected vs counted cash, variance
  receipts.py        printable per-sale receipt generation
  staff.py           owner/admin/cashier accounts: add, reset PIN, deactivate
  audit.py           activity log: who did what to whom, and when (staff actions)
  notifications.py   email (SMTP) / SMS / WhatsApp (Twilio) alerts for sensitive staff actions
  mobile_scan.py     phone-as-barcode-scanner companion (local HTTP server + OpenCV decode)
  netserver.py       multi-till server: exposes this PC's database to other cashier tills over the network
  netclient.py       multi-till client: makes a remote till's database look like a local one everywhere else
  till_config.py     this till's saved networking role (standalone / server / client)
  forecasting.py     SARIMA/SARIMAX/Holt-Winters/Seasonal Naive + model selection
  promotions.py      urgency scoring, tiered expiry alerts, discount suggestion
  association.py     Apriori from scratch, bundle recommendations
  layout.py          4-layer layout optimiser + planogram rendering
  reports.py         dashboard KPIs, reports, CSV export, backup/restore
  gui/
    app.py           main window, navigation
    theme.py         Balasuriya Group brand colors + ttk styling helpers
    screens.py       Dashboard / POS / Inventory / Customers / Suppliers /
                      Promotions / Forecasting / Bundles / Layout / Reports /
                      Staff / Network / Activity Log screens
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

## Setting up more than one till

By default every till is "Standalone" - exactly today's behavior, nothing
changes unless you touch the Network screen. To add a second (or third...)
cashier till so it shares the same live stock/sales data:

1. **Pick one PC as the Main Till** - whichever one is least likely to be
   switched off during the day (it keeps the real database). It needs
   nothing extra - it's already running SmartGrocer normally.
2. On the Main Till, open the **Network** screen (sidebar) and click
   **"Share This Till's Data"**. It shows an address (like
   `https://192.168.1.5:8790/`) and a 4-digit pairing code - leave this
   screen open, or just remember the two values; they stay the same across
   restarts.
3. **Set up each additional PC exactly like the first one** - copy the
   SmartGrocer folder to it, install Python + `pip install -r
   requirements.txt` there too (see Setup above), but **don't** copy the
   `data/` folder over - a Cashier Till doesn't keep its own database.
4. On that PC, run `python main.py`, open the **Network** screen, click
   **"Connect to Another Till"**, and enter the Main Till's address, port,
   and pairing code from step 2. That till now shows and updates the exact
   same products, stock, and sales as the Main Till, in real time.
5. Both PCs need to be on the **same network** - the same shop Wi-Fi, or
   plugged into the same router/switch by cable. It does not need to be
   the internet; the two PCs just need to be able to reach each other.

Notes:
- The very first time a Cashier Till connects, Windows may ask to allow
  the connection through the Firewall - allow it (it's the same private
  network, not the internet).
- If the Main Till is closed or loses network for a moment, a Cashier Till
  falls back to a temporary local copy so the cashier isn't locked out -
  but anything rung up during that time stays on that till only until it's
  reconnected (there's no automatic merge of that fallback data yet, so
  reconnect as soon as possible and treat that gap as something to
  reconcile by hand, e.g. from that till's own receipts).
- "Stop Sharing" (Main Till) or "Disconnect" (Cashier Till) on the Network
  screen reverts to Standalone at any time.

## Running the tests

```bash
python -m pytest tests/test_pipeline.py -v
```

or, without pytest: `python tests/test_pipeline.py`. These build a temporary
throwaway database and exercise every module (data generation reconciliation,
POS checkout/void/oversell-guard, forecasting + anomaly detection, Apriori
rule recovery, promotion scoring bounds, layout coverage, report KPIs,
customer credit/settlement/limit enforcement, item returns, hold/resume
cart, split payment, supplier summary, cash drawer open/close/variance,
receipt text, exact-barcode lookup, staff account management, and the
phone-scanner's HTTP server end-to-end) - all 23 currently pass.

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
   - One real bug this caused, found across a few rounds of testing on a
     real Windows machine: every popup dialog had a fixed pixel size
     chosen to fit its content comfortably, with no regard for (a) how
     tall the actual screen is - a common 1366x768 laptop screen, minus
     the taskbar and title bar, is shorter than some of the taller
     dialogs - or (b) Windows **display scaling** (125%/150%/175%, common
     on real, especially high-DPI, laptop screens) - CustomTkinter renders
     every label/entry/button that much bigger to match it, so a dialog
     sized to fit its content at 100% scaling can genuinely no longer fit
     the same pixel box once Windows is scaling everything up. Either way
     the symptom was the same: the Save button, almost always the last
     thing packed into the dialog, ended up rendered past the bottom of
     the window - needing the whole thing maximized just to reach it.
     Fixed two ways, together: a shared `fit_dialog()` helper
     (`smartgrocer/gui/screens.py`) caps every dialog's size to the actual
     screen and centers it (handles (a)); and every dialog's fields are
     now packed into a `CTkScrollableFrame` body, with its Save/action
     button(s) packed directly onto the dialog window itself, OUTSIDE that
     scrollable area (handles (b), and (a) again as a backstop) - Tkinter's
     layout rules always give a plain, non-scrolling widget its full
     requested size first, and only let the scrollable body grow into
     whatever space is left over, so the button can never lose its slot no
     matter how much taller the fields above it render. A shorter window
     just means more of the form scrolls to reach the fields; the button
     itself is unaffected either way.
   - Two smaller GUI bugs found the same way: (1) `CTkScrollableFrame`
     only wires up mouse-wheel/trackpad scrolling for its own bare
     background, not for widgets packed inside it - a widely-reported
     CustomTkinter limitation. That made scrolling work only when the
     cursor happened to be over empty space, which is most of the sidebar
     nav list and the POS screen's body (both mostly buttons/entries with
     little bare background). Fixed with one global mouse-wheel handler
     (`SmartGrocerApp._on_global_mousewheel` in `smartgrocer/gui/app.py`)
     that finds whichever `CTkScrollableFrame` the pointer is actually
     over and scrolls that one directly - covers every scrollable area in
     the app, not just the sidebar it was first written for. (2) Popup
     dialogs (Login, Add Staff, etc.) only submitted when you clicked
     their button - pressing Enter after typing did nothing. Fixed by
     binding Enter, on the dialog itself, to the same action as its
     primary button, for every simple single-purpose dialog in the app
     (deliberately NOT done for Close Day, since accidentally confirming
     a cash-drawer close with a stray Enter press is worse than typing
     one extra click; and not for Add/Scan Item's barcode field, which
     already uses Enter for something else - checking the scanned code -
     so adding a second meaning would make one Enter press both check the
     code and submit the whole form before there's anything to review).
   - One more: the Dashboard and Network screens packed all their content
     straight into the screen itself with no scrollable frame and no
     treeview or other expanding widget to absorb extra space - unlike
     every other screen, which has one or the other. On a smaller screen
     or with Windows display scaling above 100%, their content could
     overflow with genuinely no way to reach whatever got cut off (not
     even a scrollbar to try). Fixed the same way as POS/every dialog
     above: their content now packs into a `CTkScrollableFrame`.
   - And a fix to the mousewheel/trackpad fix above: it turns out every
     CTkScrollableFrame quietly does its own version of the same trick
     internally (bind_all when the mouse enters its bare background,
     unbind_all when it leaves) - and bind_all is one shared app-wide slot,
     not additive, so crossing any scrollable area's bare background
     anywhere in the app could silently steal or wipe out our own global
     binding, breaking wheel/trackpad scrolling everywhere until something
     rebound it. Fixed by re-claiming the binding on a repeating timer
     (every 200ms) instead of once at startup, so it can never stay lost.

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
