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
   - A follow-up attempt at the mousewheel/trackpad fix tried binding the
     scroll handler directly to every individual widget in the app,
     re-walking the entire widget tree on a repeating timer to catch
     newly-created widgets too. That made the whole window visibly
     stutter ("every screen going and coming"), and a next attempt to fix
     *that* by narrowing the walk still didn't resolve things reliably.
     Both were reverted - back to the simpler single global `bind_all` +
     ancestor-walk from the point above, re-applied on a repeating timer
     (just re-claiming one binding, nothing recursive) as the known-stable
     version. The **Scan with Phone** button next to POS's search box
     (see "Barcode-scanner scan-to-cart" above) was also reverted from
     sitting beside the "Scan barcode or search item" label back to
     sitting next to the Search button - that repositioning shipped in
     the same round as the mousewheel regression, so it went back too.
     Touchpad scrolling being intermittently unreliable in some spots is
     a known open issue, not fully solved as of this note. (It's since
     been solved for real - see the entry below explaining what was
     actually wrong, which turned out to make the whole premise of this
     paragraph's `_on_global_mousewheel` fix incorrect too.)
   - **Touchpad/wheel scrolling over the sidebar nav list and the POS
     screen's body reported as still not working at all**, even after two
     rounds of hardening `_on_global_mousewheel` itself (trying every
     known private attribute name for the scrollable frame's internal
     canvas instead of just one, and reading the widget under the pointer
     from `event.widget` instead of re-deriving it from raw screen
     coordinates - both real, defensible hardening, neither one wrong,
     and neither one the actual bug). Checking CustomTkinter's own current
     source directly (`ctk_scrollable_frame.py` on its GitHub repo, rather
     than continuing to guess) turned up the real cause: **this app's own
     "fix" was the bug**.
     CustomTkinter's `CTkScrollableFrame` already registers its own
     `self.bind_all("<MouseWheel>", self._mouse_wheel_all, add=True)`
     inside its own `__init__` - one per instance - and `_mouse_wheel_all`
     already walks up from whatever widget the event landed on to work out
     whether IT should react (stopping at a CTkScrollbar/CTkSlider/
     CTkTextbox, which scroll themselves), which already covers scrolling
     over a button or any other child widget, not just bare background.
     `customtkinter>=5.2` in `requirements.txt` means this native fix is
     almost certainly what's actually installed. The assumption an earlier
     version of this app was built on - that CTkScrollableFrame flatly
     *can't* scroll over its own children - was true of some past
     CustomTkinter release, but isn't true of the one actually running
     here, and nothing had re-checked that assumption since.
     Working from that (wrong, but reasonable at the time) assumption, an
     earlier fix added this app's OWN app-wide mousewheel handler,
     re-applied on a repeating 500ms timer as a defensive measure against
     "anything else touching the same global slot." That re-apply called
     `self.bind_all("<MouseWheel>", ...)` with no `add=True` - and
     Tkinter's `bind_all` without `add` *replaces* the entire list of
     scripts bound to that event on the `"all"` bindtag, rather than
     adding to it. Every 500ms, for as long as the app ran, that silently
     deleted every `CTkScrollableFrame` instance's own already-correct
     native registration - the sidebar's, the POS screen's, every
     dialog's - and put only this app's own (redundant, cruder) copy back
     in its place. The wheel not working wasn't CustomTkinter falling
     short of what this app needed; it was this app quietly breaking
     CustomTkinter's own working mechanism, over and over, faster than
     anything could rely on it - which is also exactly why the two earlier
     hardening attempts, however sound in isolation, could never have
     fixed it: neither touched the timer doing the actual damage.
     Fixed by removing this app's own `_on_global_mousewheel` and its
     repeating-timer re-apply entirely (`_reassert_scroll_binding`,
     `smartgrocer/gui/app.py`) rather than patching it to coexist (e.g.
     adding `add=True` to it too, which would have left both handlers
     firing for the same scroll, moving the list twice as fast as
     intended) - once this app stops interfering, CustomTkinter's own
     native per-instance handling runs unimpeded, which is a more precise
     mechanism than what this app was reimplementing anyway. The sidebar's
     ▲/▼ click-to-scroll arrow buttons (a fallback that doesn't depend on
     wheel events at all) are untouched by this and still work the same
     way, via the `_find_scroll_canvas()` helper added in the round before
     this one - kept for that, even though the wheel-handling code it was
     originally hardening is now gone.
   - **The real cause of "every screen going and coming" plus crashes**
     (`No more menus can be allocated`, then
     `sqlite3.ProgrammingError: Cannot operate on a closed database`):
     turned out to have nothing to do with scrolling at all, despite the
     symptom looking identical to the stutter above. When the periodic
     `_reassert_scroll_binding` method was first added to
     `smartgrocer/gui/app.py`, the code that used to continue
     `SmartGrocerApp.__init__` right after that point - building
     `self.container`, creating all 13 screens, and calling
     `show_frame("dashboard")` - was accidentally left *inside*
     `_reassert_scroll_binding`'s own body instead of staying part of
     `__init__` (Python has no marker for "this new method ends here"
     other than indentation, and the leftover `__init__` code sat at the
     same indent with no `def` between them, so it silently became part
     of the new method). Since `_reassert_scroll_binding` re-runs itself
     every fraction of a second forever via `self.after(...)`, the
     *entire app* - every screen, every widget - was being torn down and
     rebuilt from scratch that often, which is what looked like the
     window flickering/disappearing, exhausted Tcl's menu-handle limit
     (each rebuild made new option-menu widgets without the old ones ever
     being freed), and eventually tried to use the database connection
     after it had been closed. Fixed by moving `__init__`'s tail end back
     into `__init__` where it belongs and leaving
     `_reassert_scroll_binding` as its own small, separate method -
     verified with `ast.parse`/`ast.walk` (checking method boundaries and
     that no method name is duplicated), `py_compile`, and the full test
     suite (39/39). If the app ever seems to rebuild itself repeatedly
     again, check for this same class of mistake first: a `def` inserted
     into the middle of another method without confirming the *rest* of
     the original method is still attached to the right one.
   - **POS search box moved to the bottom of the screen**, next to its
     own results list, instead of sitting above the Price
     tier/Payment/Customer/Cashier row - it's used far more often than
     those settings, so it now sits right where its results and the Qty
     row already are. Two search improvements came with the move:
     - **Category filter**: a "Category:" dropdown next to the search box
       (`pos.list_categories`, reading distinct values straight out of
       `products.category` - there's no separate categories table) narrows
       results to one category, in addition to the existing
       code/English-name/Sinhala-name matching.
     - **Live search-as-you-type**: results now update after every
       keystroke (`POSScreen._on_search_changed`, wired via
       `self.search_var.trace_add("write", ...)`), not only after
       pressing Enter or clicking "Search" - typing "ric" starts showing
       matching items immediately, and the results box already scrolls
       (it's a `Treeview` from `make_treeview`) once more than 5 rows
       match. Scanning a full barcode (or pressing Enter/clicking
       "Search" with one typed in) still works exactly as before: an
       exact code match skips the list entirely and goes straight into
       the cart.
   - **Phone-camera barcode misreads**: scanning the same physical barcode
     with "Scan with Phone" could occasionally add a *different* product
     than the one just scanned, because a single camera frame can
     misdecode (motion blur, glare, the barcode half out of frame) into a
     different but still valid-looking code. Fixed in
     `smartgrocer/mobile_scan.py`: the **live camera** mode (continuous
     frames while the phone's camera is pointed at items) now requires
     the same decoded code on two consecutive frames, about a quarter
     second apart, before accepting it - a genuine scan reads the same
     code twice in a row essentially always, while a misread on one frame
     almost never repeats immediately on the next. The one-photo-per-item
     fallback (used when the browser can't do live camera, e.g. some
     iPhone/Safari cases) is unaffected and still accepts its single
     photo immediately, since there's no second frame to compare it
     against there.
   - **Long dropdown lists couldn't scroll**: CustomTkinter's own
     `CTkOptionMenu` dropdown has no scrollbar - past a certain number of
     values it just becomes a stack of buttons taller than the screen,
     with entries at the bottom unreachable. Fine for a short, fixed list
     (price tier, payment method), but every dropdown that grows with your
     data - the new Category filter, POS's Customer selector, the
     Product/Supplier pickers in Receive Stock, the Staff picker in
     Login/Forgot PIN, and the Staff filter on Reports - would eventually
     hit this as the shop's catalog/customer/staff lists grow. Replaced
     all of those (only those - the short fixed ones are untouched) with
     a new `SearchableDropdown` (`smartgrocer/gui/screens.py`): a small
     popup with its own scrollable list AND a search box that filters it
     as you type, which is also just faster than scanning a long list by
     eye once you have more than a handful of categories/customers/staff.
   - **Clicking a `SearchableDropdown` list item didn't select it, then a
     follow-up fix made the list not show up at all**: the popup closed
     itself as soon as its search box lost keyboard focus - which also
     happens the instant you click one of its OWN list buttons, so the
     popup (and the button just clicked) could be destroyed as a side
     effect of that very click, before the click's own selection had a
     chance to register. The first fix for this replaced the popup
     window with a plain frame placed on top of the main window instead,
     which turned out to render behind/under the rest of the screen in
     practice - invisible instead of unselectable. Settled on: keep the
     popup as its own small window (draws on top of everything reliably,
     regardless of the app's own widget stacking), but decide when to
     close it by checking what was actually clicked (walking up from the
     clicked widget to see whether it's part of the popup) instead of by
     watching focus - a click on the popup's own buttons is now correctly
     left alone, only a click genuinely outside it closes it. Also forces
     real OS-level focus onto the popup the moment it opens
     (`popup.focus_force()`), since a brand-new window's first click can
     otherwise just activate the window instead of reaching the widget
     under the cursor.
   - **Mousewheel/trackpad scrolling didn't work inside a
     `SearchableDropdown`'s list**: the app-wide mousewheel handler
     (`SmartGrocerApp._on_global_mousewheel`) finds whatever's under the
     cursor with `winfo_containing()`, which doesn't reliably see into an
     override-redirect popup window like this one's - so it never found
     the list to scroll it. A first attempt bound the wheel directly on
     the popup itself instead, which still didn't scroll (CustomTkinter's
     `CTkScrollableFrame`, used for the list, reimplements scrolling on
     its own canvas in a way that turned out not to respond reliably
     inside a standalone popup window either). Settled on replacing that
     scrollable frame with a plain `ttk.Treeview` instead - a native Tk
     widget with mousewheel scrolling built in by Tk itself, no custom
     event wiring needed, which is exactly what every other scrollable
     list in this app (`make_treeview`) already relies on.
     Even that didn't scroll by wheel - only by dragging the scrollbar
     itself, which pointed at the popup window ITSELF rather than
     anything inside it: it used `overrideredirect(True)` for a clean,
     borderless "dropdown" look, and on Windows a window shown that way
     never gets real window-manager focus/activation. Clicking still
     worked regardless (Windows routes clicks by cursor position), but
     mouse-wheel input is routed by which window currently has focus -
     which this kind of window can never actually get, so the wheel
     event never reached anything inside it no matter what was bound
     where. Replaced `overrideredirect(True)` with `-toolwindow`
     (Windows-only; ignored elsewhere) - a real, window-manager-owned
     window with just a slim title bar and no taskbar entry, which
     should let it receive focus, and with it, wheel input, normally.
   - **Once wheel scrolling worked, it felt too slow**: Tk's own default
     wheel handling for a `ttk.Treeview` moves it only one row per wheel
     notch, noticeably slower than normal scrolling elsewhere in Windows
     (most apps move 3+ lines per notch, matching the Windows mouse
     setting for it). A handler bound directly on the list widget itself
     now scrolls 3 rows per notch instead (scaling up further for a fast
     flick of a high-resolution wheel) and suppresses Tk's own slower
     default so the two don't both fire and scroll twice.
   - **A `SearchableDropdown` popup didn't say what it was, and looked
     different from the rest of the app's popups**: it now takes a
     `label` (each call site names its own - "Category", "Customer",
     "Product", "Supplier", "Staff") shown both as the popup's own window
     title and as a small header inside it (the title bar alone is easy
     to miss, being slim by design - see the `-toolwindow` note above).
     Its background also now matches the same light background the
     app's other popup dialogs (Login, Receive Stock, ...) use directly
     on the dialog window itself, rather than a separate white bordered
     "card" that looked visually different from them.
   - **That fix then showed the same name twice** (the OS title bar text
     plus the bold header inside the popup, right underneath it): the
     title bar text is now blank (`popup.title("")`), leaving the bold
     in-popup header as the one and only name shown. (A plain "x" was
     also added next to that header at this point, as a second way to
     close the popup - removed again a few fixes later; see the last
     entry below.)
   - **A few different attempts at making click-outside-to-close fully
     reliable made things worse instead of better, and are worth recording
     so nobody re-tries the same dead ends**: the click-based detection
     below (walking up from whatever was actually clicked) was, at one
     point, swapped out for closing the popup whenever it lost Windows
     window focus - first via Tk's `<Deactivate>` event, then also by
     polling `focus_get()` every 200ms as a fallback for switching to a
     different application entirely, on the theory that Windows can
     swallow a click for window-activation without ever handing it to Tk
     as a real click event. In practice, on this machine, that approach
     made the popup read its OWN brand-new-window setup as an instant
     loss of focus, closing itself a moment after every single open - the
     box would "pop up and go" before it could be used at all, a far
     worse bug than occasionally needing a second click to close it. A
     separate attempt at the same time to also strip Windows' native
     close button via a direct Win32 API call turned out to risk
     corrupting the popup's own on-screen rendering, since CustomTkinter
     already does similar low-level window-style work of its own for
     that same window. Both of those have been fully reverted - no
     `<Deactivate>` binding, no focus polling, no raw Win32 calls
     anywhere in `SearchableDropdown` - back to the simpler mechanism
     below, which has none of these failure modes because it only ever
     reacts to an actual click event, never to window-focus timing.
   - **Two close buttons showing at once**: the plain in-app "x" added a
     few fixes back (next to the bold header) and Windows' own native
     title-bar close button both did the same thing, side by side. Since
     removing the native one natively risked breaking the popup's own
     rendering (the entry above), the in-app one was the one dropped
     instead - the popup now has exactly one close button (Windows' own,
     in its title bar), on top of clicking away and Escape.
   - **That native close button didn't actually go through this app's own
     closing logic**: clicking it just let Tk's default window-close
     handling destroy the popup directly, never calling `_close_popup()`
     - unlike clicking away, pressing Escape, or re-clicking the dropdown
     button, all of which do. Harmless for the popup itself (later checks
     already treat a destroyed popup as "gone"), but it meant the
     click-outside `<Button-1>` binding that `_close_popup()` is
     responsible for removing never got removed that way, staying
     attached to the whole app after the popup was already gone. Fixed by
     wiring the window's close button, via `popup.protocol("WM_DELETE_WINDOW", self._close_popup)`,
     to the exact same `_close_popup()` every other way of closing it
     already uses - one cleanup path for all of them, instead of one path
     with an exception.

## Loyalty customers, membership scanning & offers

Requested directly by the shop: tell a "loyal" (registered) customer apart
from an anonymous walk-in at checkout, identify them by phone number or a
scannable card, show when an item's price includes a discount, and let
staff set up offers that are either for everyone or for loyalty members
only - without needing a second physical scanner for customers versus
products.

- **What makes someone a "loyalty member"**: registering them at all
  (`customers.add_customer`, from the Customers screen or on the spot at
  checkout) - there's no separate opt-in step. The one exception is the
  seeded "Walk-in" customer used for anonymous cash sales, which is not a
  real registration and never counts (`customers.is_loyalty_member`).
- **One scan channel for both products and customers, not two**: a
  customer's membership QR encodes `"SGCUST:" + their member_code`
  (`customers.member_qr_payload`), so it's told apart from a plain product
  barcode by that prefix alone (`customers.member_code_from_scan`) -
  wherever scanned text can land (the POS search box, the always-on Phone
  Scanner, the dedicated "Scan Card" button), it's checked for that prefix
  first and routed to identifying a customer instead of searching the
  catalog. This is exactly why a QR (not a plain barcode) was used for
  membership: a plain 1D barcode has no room for a distinguishing prefix
  and would be genuinely ambiguous with a product code read by the same
  1D scanner, whereas a QR's payload is arbitrary text either way. A
  standard 1D laser barcode scanner (most shops' existing hardware) cannot
  read a QR code at all - it needs a 2D imager scanner or, as built here,
  a phone camera (`mobile_scan.py`'s existing phone-as-scanner feature,
  reused for this rather than building a second one).
- **Offers** (`offers.py`, `offers` table, Offers screen) are a manual,
  staff-created list - deliberately separate from `promotions.py`'s
  automatic near-expiry clearance suggestions, which only ever *suggest* a
  discount and never apply one to a sale. An offer targets one product or
  a whole category, is scoped to "everyone" or "loyalty members only", and
  can have an optional start/end date. Applied automatically the moment an
  item is added to the cart (and re-checked whenever the selected customer
  changes, since a loyalty-only offer switches on/off with them) via
  `offers.line_discount_amount`, which is stored as `pos.CartLine.discount`
  - already a first-class, flat-currency-per-line field `create_invoice`
  understood before any of this, just never populated by the GUI until
  now. Where two offers could both apply, the single biggest discount
  wins, never the smaller one.
- **Membership codes are also just typeable**: every registered customer
  gets a short human-readable code (`"SG-XXXXXX"`) as well as the QR, so a
  cashier can identify them by typing it in (or the phone number) if a
  scan fails or nothing's at hand.
- **Sending the membership code to a customer's phone**: SMS/WhatsApp
  (via the Twilio setup `notifications.py` already supports for staff
  alerts) can text the plain membership *code*, but deliberately does NOT
  attempt to send the scannable QR *image* - Twilio (or any SMS/MMS/
  WhatsApp provider) needs to fetch an image from a public HTTPS URL, and
  this PC, sitting on the shop's own LAN, isn't reachable from the public
  internet without extra networking work (port-forwarding, a public
  hostname, etc.) well outside the scope of a POS till. The reliable way
  to get a customer the actual scannable QR is the on-screen display right
  after registering, or the printable card built from it - both work with
  nothing extra to set up.
- **Existing databases upgrade in place**: `db._migrate` adds the new
  `member_code` column and backfills a code for every real customer
  already on file (never "Walk-in"), the same pattern used for every
  earlier schema change in this project - a shop's existing customer/sale
  history is never at risk from this update.

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
