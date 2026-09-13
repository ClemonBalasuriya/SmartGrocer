"""
SmartGrocer - screen (page) implementations. See app.py's module docstring
for the testing caveat on this GUI layer.
"""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk

import customtkinter as ctk

from .. import association, forecasting, layout, pos, promotions, reports
from .. import cash_drawer as cash_drawer_module
from .. import customers as customers_module
from .. import receipts as receipts_module
from .. import suppliers as suppliers_module
from .. import staff as staff_module
from .. import mobile_scan
from .. import netclient
from .. import netserver
from .. import audit
from .. import notifications
from .. import db as db_module
from . import theme
from .theme import tree_clear, tree_insert


def make_treeview(parent, columns: list[str], widths: dict[str, int] | None = None, rows: int = 16) -> ttk.Treeview:
    widths = widths or {}
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=rows)
    for col in columns:
        tree.heading(col, text=col)
        tree.column(col, width=widths.get(col, 120), anchor="w")
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    frame.grid_rowconfigure(0, weight=1)
    frame.grid_columnconfigure(0, weight=1)
    tree.master_frame = frame  # convenience handle so callers can .pack()/.grid() `frame`
    theme.configure_stripes(tree)
    tree._row_counter = 0
    return tree


PRIMARY_BTN = dict(fg_color=theme.ACCENT_BLUE, hover_color=theme.ACCENT_BLUE_HOVER,
                    text_color="#FFFFFF", corner_radius=8)
SUCCESS_BTN = dict(fg_color=theme.SUCCESS_GREEN, hover_color=theme.SUCCESS_GREEN_HOVER,
                    text_color="#FFFFFF", corner_radius=8)
DANGER_BTN = dict(fg_color=theme.DANGER_RED, hover_color=theme.DANGER_RED_HOVER,
                   text_color="#FFFFFF", corner_radius=8)
SECONDARY_BTN = dict(fg_color=theme.CARD_BG, hover_color=theme.CARD_BG_ALT, text_color=theme.NAVY_DARK,
                      border_width=1, border_color=theme.BORDER, corner_radius=8)


def styled_button(parent, text, command, kind="primary", **kwargs):
    style = {"primary": PRIMARY_BTN, "success": SUCCESS_BTN, "danger": DANGER_BTN,
             "secondary": SECONDARY_BTN}[kind]
    return ctk.CTkButton(parent, text=text, command=command, **{**style, **kwargs})


def run_in_background(widget: ctk.CTkBaseClass, work_fn, on_done, on_error=None):
    """Run a slow, DB-heavy computation (forecasting, association-rule
    mining, layout optimisation - anything that can take seconds over a
    real transaction history) off the Tkinter main thread, so the window
    stays responsive instead of freezing mid-click.

    `work_fn` takes no arguments and must open its OWN sqlite connection
    (sqlite3 connections aren't safe to share across threads) - screens
    pass a closure that does `db_module.get_conn(app.db_path)` and closes
    it before returning. `on_done`/`on_error` run back on the main thread
    via `widget.after(0, ...)`, so they can touch widgets safely."""

    def worker():
        try:
            result = work_fn()
        except Exception as e:  # noqa: BLE001 - surface any failure to the cashier, don't crash the thread silently
            widget.after(0, lambda: (on_error or (lambda err: messagebox.showerror("Error", str(err))))(e))
            return
        widget.after(0, lambda: on_done(result))

    threading.Thread(target=worker, daemon=True).start()


class BaseScreen(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG_LIGHT)
        self.app = app

    @property
    def conn(self):
        return self.app.conn

    def on_show(self):
        pass

    def header(self, text: str):
        lbl = ctk.CTkLabel(self, text=text, font=ctk.CTkFont(size=22, weight="bold"),
                            text_color=theme.NAVY_DARK)
        lbl.pack(anchor="w", padx=24, pady=(20, 12))


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #

class DashboardScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Dashboard")

        self.cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.cards_frame.pack(fill="x", padx=24, pady=10)
        self.card_labels: dict[str, ctk.CTkLabel] = {}
        specs = [
            ("today_sales_total", "Today's Sales (LKR)"),
            ("today_invoice_count", "Invoices Today"),
            ("today_gross_margin_pct", "Gross Margin % (Today)"),
            ("low_stock_count", "Products Below Reorder Level"),
            ("expiring_within_7_days", "Batches Expiring in 7 Days"),
            ("historical_waste_value_lkr", "Historical Waste (LKR)"),
        ]
        for i, (key, label) in enumerate(specs):
            card = ctk.CTkFrame(self.cards_frame, corner_radius=10, fg_color=theme.CARD_BG,
                                 border_width=1, border_color=theme.BORDER)
            card.grid(row=i // 3, column=i % 3, padx=10, pady=10, sticky="nsew")
            self.cards_frame.grid_columnconfigure(i % 3, weight=1)
            ctk.CTkLabel(card, text=label, font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED).pack(
                anchor="w", padx=16, pady=(14, 0))
            value_lbl = ctk.CTkLabel(card, text="-", font=ctk.CTkFont(size=26, weight="bold"),
                                      text_color=theme.NAVY_DARK)
            value_lbl.pack(anchor="w", padx=16, pady=(0, 14))
            self.card_labels[key] = value_lbl

        top_seller_frame = ctk.CTkFrame(self, corner_radius=10, fg_color=theme.CARD_BG,
                                         border_width=1, border_color=theme.BORDER)
        top_seller_frame.pack(fill="x", padx=24, pady=(0, 10))
        self.top_seller_label = ctk.CTkLabel(top_seller_frame, text="Top seller (30d): -",
                                              font=ctk.CTkFont(size=14), text_color=theme.TEXT_DARK)
        self.top_seller_label.pack(anchor="w", padx=16, pady=12)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=10)
        styled_button(btn_row, "Refresh", self.refresh, kind="secondary").pack(side="left", padx=(0, 10))
        styled_button(btn_row, "Rebuild synthetic demo data", self.rebuild_demo_data,
                      kind="danger").pack(side="left")

    def on_show(self):
        self.refresh()

    def refresh(self):
        kpis = reports.dashboard_kpis(self.conn)
        self.card_labels["today_sales_total"].configure(text=f"{kpis['today_sales_total']:,.0f}")
        self.card_labels["today_invoice_count"].configure(text=str(kpis["today_invoice_count"]))
        self.card_labels["today_gross_margin_pct"].configure(text=f"{kpis['today_gross_margin_pct']}%")
        self.card_labels["low_stock_count"].configure(text=str(kpis["low_stock_count"]))
        self.card_labels["expiring_within_7_days"].configure(text=str(kpis["expiring_within_7_days"]))
        self.card_labels["historical_waste_value_lkr"].configure(text=f"{kpis['historical_waste_value_lkr']:,.0f}")
        self.top_seller_label.configure(text=f"Top seller (30d): {kpis['top_seller_30d'] or '-'}")

    def rebuild_demo_data(self):
        if not messagebox.askyesno(
            "Rebuild demo data",
            "This deletes all current data (sales, stock, invoices) and regenerates the "
            "~9-month synthetic dataset. Use this to reset the demo, not on a shop's real data. Continue?",
        ):
            return
        from .. import data_generator
        self.app.conn.close()
        data_generator.build_demo_database()
        self.app.conn = db_module.get_conn()
        messagebox.showinfo("Done", "Synthetic demo data rebuilt.")
        self.refresh()


# --------------------------------------------------------------------------- #
# POS / Checkout
# --------------------------------------------------------------------------- #

def prompt_split_payment(parent, total: float) -> list[tuple[str, float]] | None:
    """Modal dialog for a mixed-tender sale (e.g. part cash, part card, part
    credit) - matches the sample POS system's "Mix Payment" button. Returns
    a list of (method, amount) tuples that sum to `total`, or None if the
    cashier cancelled."""
    result: dict = {"payments": None}
    dialog = ctk.CTkToplevel(parent)
    dialog.title("Split / Mixed Payment")
    dialog.geometry("360x340")
    dialog.configure(fg_color=theme.BG_LIGHT)
    dialog.grab_set()

    ctk.CTkLabel(dialog, text=f"Total to collect: LKR {total:,.2f}",
                 font=ctk.CTkFont(size=14, weight="bold"), text_color=theme.NAVY_DARK).pack(pady=(16, 10))

    method_vars: dict[str, tk.StringVar] = {}
    for method in ["cash", "card", "cheque", "credit"]:
        row = ctk.CTkFrame(dialog, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(row, text=method.capitalize() + ":", width=70, anchor="w",
                     text_color=theme.TEXT_DARK).pack(side="left")
        v = tk.StringVar(value="0")
        ctk.CTkEntry(row, textvariable=v, width=140).pack(side="left")
        method_vars[method] = v

    def submit():
        try:
            payments = [(m, float(v.get())) for m, v in method_vars.items() if float(v.get() or 0) > 0]
        except ValueError:
            messagebox.showerror("Invalid amount", "Enter numbers only.")
            return
        if not payments:
            messagebox.showerror("Nothing entered", "Enter at least one payment amount.")
            return
        paid_sum = round(sum(a for _, a in payments), 2)
        if abs(paid_sum - total) > 0.01:
            messagebox.showerror("Amount mismatch",
                                  f"Payments total LKR {paid_sum:,.2f}, expected LKR {total:,.2f}.")
            return
        result["payments"] = payments
        dialog.destroy()

    btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_row.pack(pady=20)
    styled_button(btn_row, "Confirm", submit, kind="success").pack(side="left", padx=6)
    styled_button(btn_row, "Cancel", dialog.destroy, kind="secondary").pack(side="left", padx=6)

    dialog.wait_window()
    return result["payments"]


class POSScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("POS / Checkout")  # stays fixed at the top, outside the scrollable area below
        self.cart: list[dict] = []

        # Everything below goes in a scrollable frame, not straight into
        # `self` - this screen has a lot stacked vertically (search/filter
        # row, results, quick items, the cart, and two rows of action
        # buttons), and on a smaller screen, a laptop with taskbar/title bar
        # eating into the usable height, or Windows display scaling above
        # 100%, that stack is taller than the window. Without a scrollbar
        # the bottom of it (the Checkout button, Hold/Resume/Return/Phone
        # Scanner) was simply cut off with no way to reach it at all -
        # CTkScrollableFrame fixes that regardless of how small the window
        # or screen ends up being.
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)

        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x", padx=24)

        ctk.CTkLabel(top, text="Scan barcode or search item:", text_color=theme.TEXT_DARK).grid(
            row=0, column=0, sticky="w")
        self.search_var = tk.StringVar()
        self.search_entry = ctk.CTkEntry(top, textvariable=self.search_var, width=260,
                                          placeholder_text="barcode / name (English or Sinhala)")
        self.search_entry.grid(row=1, column=0, padx=(0, 10), pady=6)
        self.search_entry.bind("<Return>", lambda e: self.do_search())
        styled_button(top, "Search", self.do_search, width=90).grid(row=1, column=1)

        ctk.CTkLabel(top, text="Price tier:", text_color=theme.TEXT_DARK).grid(row=0, column=2, padx=(20, 0), sticky="w")
        self.tier_var = tk.StringVar(value="cash")
        ctk.CTkOptionMenu(top, values=["cash", "credit", "wholesale"], variable=self.tier_var).grid(
            row=1, column=2, padx=(20, 0))

        ctk.CTkLabel(top, text="Payment:", text_color=theme.TEXT_DARK).grid(row=0, column=3, padx=(16, 0), sticky="w")
        self.payment_var = tk.StringVar(value="cash")
        ctk.CTkOptionMenu(top, values=["cash", "card", "cheque", "credit", "split"],
                           variable=self.payment_var).grid(row=1, column=3, padx=(16, 0))

        ctk.CTkLabel(top, text="Customer:", text_color=theme.TEXT_DARK).grid(row=0, column=4, padx=(16, 0), sticky="w")
        self.customer_var = tk.StringVar(value="Walk-in")
        self.customer_menu = ctk.CTkOptionMenu(top, values=["Walk-in"], variable=self.customer_var, width=160)
        self.customer_menu.grid(row=1, column=4, padx=(16, 0))

        ctk.CTkLabel(top, text="Cashier:", text_color=theme.TEXT_DARK).grid(row=0, column=5, padx=(16, 0), sticky="w")
        self.cashier_label = ctk.CTkLabel(top, text="Not logged in", font=ctk.CTkFont(weight="bold"),
                                           text_color=theme.DANGER_RED)
        self.cashier_label.grid(row=1, column=5, padx=(16, 0), sticky="w")

        results_frame = ctk.CTkFrame(body, fg_color="transparent")
        results_frame.pack(fill="x", padx=24, pady=(6, 0))
        self.results_tree = make_treeview(
            results_frame, ["Code", "Name", "Cash Price", "Stock"],
            {"Code": 90, "Name": 300, "Cash Price": 100, "Stock": 80},
            rows=5,
        )
        # .configure(height=...) alone doesn't stick here: by default a
        # frame resizes itself to fit whatever its own children ask for
        # (grid_propagate defaults to True), so the treeview's own natural
        # height silently overrode the 140px request - this box was meant
        # to be a compact 5-row search-results list, not one stretching to
        # fill most of the window. grid_propagate(False) makes the
        # requested height actually stick.
        self.results_tree.master_frame.configure(height=140)
        self.results_tree.master_frame.grid_propagate(False)
        self.results_tree.master_frame.pack(fill="x")
        self.results_tree.bind("<Double-1>", lambda e: self.add_selected_to_cart())

        qty_row = ctk.CTkFrame(body, fg_color="transparent")
        qty_row.pack(fill="x", padx=24, pady=6)
        ctk.CTkLabel(qty_row, text="Qty:", text_color=theme.TEXT_DARK).pack(side="left")
        self.qty_var = tk.StringVar(value="1")
        ctk.CTkEntry(qty_row, textvariable=self.qty_var, width=70).pack(side="left", padx=8)
        styled_button(qty_row, "Add to Cart", self.add_selected_to_cart).pack(side="left")

        ctk.CTkLabel(body, text="Quick Items (top sellers - tap to add):",
                     text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11)).pack(anchor="w", padx=24, pady=(4, 0))
        self.quick_items_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.quick_items_frame.pack(fill="x", padx=24, pady=(2, 6))
        self.tier_var.trace_add("write", lambda *a: self._refresh_quick_items())

        # fill="both" WITHOUT expand=True: inside a CTkScrollableFrame,
        # "expand" fights with the scrollbar's own idea of how tall the
        # content is (the inner frame tries to stretch to match the
        # visible viewport instead of its natural content size), which
        # silently breaks scrolling to anything below it - the results-box
        # sizing bug's sibling. Giving the cart a fixed, generous row
        # count (with its own internal scrollbar for a long basket) keeps
        # its height predictable instead.
        cart_frame = ctk.CTkFrame(body, fg_color="transparent")
        cart_frame.pack(fill="both", padx=24, pady=(6, 0))
        self.cart_tree = make_treeview(
            cart_frame, ["Item", "Qty", "Unit Price", "Line Total"],
            {"Item": 320, "Qty": 80, "Unit Price": 100, "Line Total": 110},
            rows=10,
        )
        self.cart_tree.master_frame.pack(fill="both")

        bottom = ctk.CTkFrame(body, fg_color="transparent")
        bottom.pack(fill="x", padx=24, pady=(4, 0))
        styled_button(bottom, "Remove Selected Line", self.remove_selected_line, kind="secondary").pack(side="left")
        styled_button(bottom, "Clear Cart", self.clear_cart, kind="secondary").pack(side="left", padx=8)
        self.total_label = ctk.CTkLabel(bottom, text="Net Total: LKR 0.00", font=ctk.CTkFont(size=16, weight="bold"),
                                         text_color=theme.NAVY_DARK)
        self.total_label.pack(side="left", padx=30)
        styled_button(bottom, "Checkout", self.checkout, kind="success", width=140,
                      height=40).pack(side="right")

        # grid (not pack/side="left") so these shrink to fit a narrower window
        # or a smaller/scaled-up-DPI screen instead of running off the edge -
        # four buttons packed at their natural width stopped fitting once
        # "Phone Scanner" was added here.
        bottom2 = ctk.CTkFrame(body, fg_color="transparent")
        bottom2.pack(fill="x", padx=24, pady=(8, 12))
        for col in range(4):
            bottom2.grid_columnconfigure(col, weight=1, uniform="bottom2")
        styled_button(bottom2, "Hold Invoice", self.hold_invoice, kind="secondary").grid(
            row=0, column=0, sticky="ew", padx=(0, 4))
        styled_button(bottom2, "Resume Held", self.resume_invoice, kind="secondary").grid(
            row=0, column=1, sticky="ew", padx=4)
        styled_button(bottom2, "Return / Refund", self.open_return_dialog, kind="danger").grid(
            row=0, column=2, sticky="ew", padx=4)
        styled_button(bottom2, "Phone Scanner", self.open_phone_scanner_dialog, kind="secondary").grid(
            row=0, column=3, sticky="ew", padx=(4, 0))

        self._search_results: list = []

    def on_show(self):
        names = [r["name"] for r in customers_module.list_customers(self.conn)] or ["Walk-in"]
        if "Walk-in" not in names:
            names = ["Walk-in"] + names
        self.customer_menu.configure(values=names)
        if self.customer_var.get() not in names:
            self.customer_var.set("Walk-in")

        if self.app.logged_in:
            staff = self.conn.execute("SELECT name FROM staff WHERE id=?", (self.app.current_staff_id,)).fetchone()
            self.cashier_label.configure(text=staff["name"] if staff else "Unknown", text_color=theme.SUCCESS_GREEN)
        else:
            self.cashier_label.configure(text="Not logged in", text_color=theme.DANGER_RED)

        self._refresh_quick_items()
        # Most USB/Bluetooth barcode scanners are "keyboard wedge" devices -
        # they just type the code + Enter into whatever field has focus, no
        # driver needed. Focusing the search box here means a cashier can
        # scan the moment this screen appears without clicking into it first.
        self.search_entry.focus_set()

    def _selected_customer_row(self):
        name = self.customer_var.get()
        row = self.conn.execute("SELECT * FROM customers WHERE name=?", (name,)).fetchone()
        return row

    def _refresh_quick_items(self):
        for w in self.quick_items_frame.winfo_children():
            w.destroy()
        rows = reports.best_sellers(self.conn, days=30, top_n=12)
        tier_field = {"cash": "cash_price", "credit": "credit_price", "wholesale": "wholesale_price"}[self.tier_var.get()]
        cols = 4
        for i, r in enumerate(rows):
            product_id = r["product_id"]
            p = self.conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if p is None:
                continue
            price = p[tier_field]
            btn = styled_button(
                self.quick_items_frame, f"{p['name_en'][:16]}\nLKR {price:.2f}",
                lambda pid=product_id: self._quick_add(pid), kind="secondary",
                font=ctk.CTkFont(size=11), height=48,
            )
            btn.grid(row=i // cols, column=i % cols, padx=4, pady=4, sticky="nsew")
        for c in range(cols):
            self.quick_items_frame.grid_columnconfigure(c, weight=1)

    def _quick_add(self, product_id: int):
        p = self.conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if p is None:
            return
        tier_field = {"cash": "cash_price", "credit": "credit_price", "wholesale": "wholesale_price"}[self.tier_var.get()]
        self.cart.append({"product_id": product_id, "name": p["name_en"], "qty": 1.0, "unit_price": p[tier_field]})
        self.refresh_cart()

    def handle_phone_scan(self, code: str) -> dict:
        """Called from the phone-scanner's own background HTTP thread
        (see mobile_scan.MobileScanServer) - never the Tkinter thread, so
        this opens its own database connection rather than touching
        self.conn, and hands the actual cart update back to the main
        thread via .after() instead of mutating self.cart here directly.
        The response returned to the phone is answered from the DB lookup
        alone (not from waiting on that main-thread update), which is
        simpler than cross-thread synchronization and is right in all but
        a vanishingly rare failure between this reply and the .after()
        call actually running."""
        conn = db_module.get_conn(self.app.db_path)
        try:
            product = pos.get_product_by_code(conn, code)
        finally:
            conn.close()
        if product is None:
            return {"unknown": True}
        self.after(0, lambda: self._quick_add(product["id"]))
        return {"name": product["name_en"], "added": True}

    def open_phone_scanner_dialog(self):
        if getattr(self.app, "mobile_scan_server", None) is None:
            self.app.mobile_scan_server = mobile_scan.MobileScanServer(self.handle_phone_scan)
        server = self.app.mobile_scan_server
        if not server.running:
            try:
                server.start()
            except OSError as e:
                messagebox.showerror("Could not start phone scanner", str(e))
                return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Phone Scanner")
        dialog.geometry("400x700")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        # Scrollable, same reasoning as the POS screen itself: this has a
        # fair amount stacked vertically (QR code, address, pairing code,
        # two blocks of instructions) and a resizable Toplevel is easy to
        # leave too short on a smaller screen with nothing below the fold
        # reachable otherwise.
        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(body, text="Scan this with your phone's camera app", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=theme.NAVY_DARK).pack(pady=(16, 6))

        try:
            from PIL import Image
            import io
            png_bytes = mobile_scan.generate_qr_png_bytes(server.url)
            qr_img = Image.open(io.BytesIO(png_bytes))
            ctk_qr = ctk.CTkImage(light_image=qr_img, dark_image=qr_img, size=(220, 220))
            ctk.CTkLabel(body, image=ctk_qr, text="").pack(pady=6)
        except Exception:
            pass  # the URL/code below still work even if the QR image can't be rendered here

        ctk.CTkLabel(body, text="or open this address in the phone's browser:",
                     text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11)).pack(pady=(6, 0))
        ctk.CTkLabel(body, text=server.url, font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=theme.ACCENT_BLUE).pack(pady=(0, 12))

        ctk.CTkLabel(body, text="Then enter this pairing code once on the phone:",
                     text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11)).pack()
        ctk.CTkLabel(body, text=server.pairing_code, font=ctk.CTkFont(size=30, weight="bold"),
                     text_color=theme.NAVY_DARK).pack(pady=(2, 14))

        warning_card = ctk.CTkFrame(body, fg_color=theme.CARD_BG_ALT, corner_radius=8)
        warning_card.pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkLabel(
            warning_card,
            text="The FIRST time you open that address, the phone will warn "
                 "\"Connection not private\" / \"Not Secure\" - that's expected, not a "
                 "problem: it's this PC talking to your phone directly, not the "
                 "internet. On Android/Chrome tap Advanced, then \"Proceed\". On "
                 "iPhone/Safari tap Show Details, then \"visit this website\". You "
                 "only need to do this once per phone.",
            text_color=theme.TEXT_DARK, font=ctk.CTkFont(size=11), wraplength=320, justify="left",
        ).pack(padx=12, pady=10)

        ctk.CTkLabel(
            body,
            text="Both devices must be on the same Wi-Fi. Once connected, point the "
                 "camera at items - they're added automatically, no need to tap "
                 "anything per item. Scans keep landing in this cart even while this "
                 "window is closed - close it and keep ringing up items on the till "
                 "at the same time.",
            text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11), wraplength=320, justify="left",
        ).pack(padx=20, pady=(0, 14))

        def stop_server():
            server.stop()
            dialog.destroy()

        styled_button(dialog, "Stop Phone Scanner", stop_server, kind="danger", width=200).pack(pady=(0, 16))

    def do_search(self):
        query = self.search_var.get().strip()
        if not query:
            return

        # Exact barcode match (what a scanner sends): skip straight to the
        # cart instead of making the cashier click into the results list and
        # double-click the row - this is the difference between "scan and
        # it's in the cart" and "scan, then still do two clicks" for every
        # single item, which is where a lot of the perceived slowness at the
        # till comes from.
        exact = pos.get_product_by_code(self.conn, query)
        if exact is not None:
            self._add_product_to_cart(exact["id"])
            self.search_var.set("")
            tree_clear(self.results_tree)
            self.search_entry.focus_set()
            return

        self._search_results = pos.search_products(self.conn, query)
        tree_clear(self.results_tree)
        for p in self._search_results:
            stock = pos.get_stock_on_hand(self.conn, p["id"])
            tree_insert(self.results_tree, (p["code"], p["name_en"], f"{p['cash_price']:.2f}", f"{stock:.0f}"),
                        iid=str(p["id"]))
        if not self._search_results:
            # Previously this failed silently (an empty results box looks
            # the same as "still typing") - spelling it out, and echoing
            # back exactly what was searched for, makes a genuine
            # code/name mismatch obvious immediately rather than the
            # cashier assuming the scan just didn't register.
            messagebox.showinfo("Not found", f"No product in the catalog matches '{query}'.\n\n"
                                 "If you just added this item, check the Inventory screen's Code "
                                 "column for the exact code that was saved and compare it with what "
                                 "you're scanning/typing here - they must match exactly.")

    def add_selected_to_cart(self):
        sel = self.results_tree.selection()
        if not sel:
            messagebox.showwarning("No item selected", "Search and select an item first.")
            return
        self._add_product_to_cart(int(sel[0]))

    def _add_product_to_cart(self, product_id: int):
        """Shared by the manual 'select a result + Add to Cart' flow and an
        exact-barcode scan - both add the same way, at the quantity in the
        Qty box (so a scanner reading a multi-pack barcode still respects a
        cashier-entered quantity if they typed one first)."""
        try:
            qty = float(self.qty_var.get())
        except ValueError:
            messagebox.showerror("Invalid quantity", "Quantity must be a number.")
            return
        p = self.conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if p is None:
            return
        tier_field = {"cash": "cash_price", "credit": "credit_price", "wholesale": "wholesale_price"}[self.tier_var.get()]
        self.cart.append({"product_id": product_id, "name": p["name_en"], "qty": qty, "unit_price": p[tier_field]})
        self.refresh_cart()

    def refresh_cart(self):
        tree_clear(self.cart_tree)
        total = 0.0
        for i, line in enumerate(self.cart):
            line_total = line["qty"] * line["unit_price"]
            total += line_total
            tree_insert(self.cart_tree, (line["name"], line["qty"], f"{line['unit_price']:.2f}", f"{line_total:.2f}"),
                        iid=str(i))
        self.total_label.configure(text=f"Net Total: LKR {total:,.2f}")

    def remove_selected_line(self):
        sel = self.cart_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        del self.cart[idx]
        self.refresh_cart()

    def clear_cart(self):
        self.cart = []
        self.refresh_cart()

    def checkout(self):
        if not self.cart:
            messagebox.showwarning("Empty cart", "Add at least one item before checking out.")
            return
        if not self.app.logged_in:
            messagebox.showwarning(
                "Not logged in", "Log in from the sidebar (Login / Open Day) before ringing up a sale."
            )
            return
        staff_id = self.app.current_staff_id
        customer_row = self._selected_customer_row()
        cart_lines = [pos.CartLine(product_id=c["product_id"], qty=c["qty"]) for c in self.cart]
        net_total = sum(c["qty"] * c["unit_price"] for c in self.cart)

        payment_choice = self.payment_var.get()
        payments = None
        payment_type = payment_choice
        cash_paid = None
        if payment_choice == "split":
            payments = prompt_split_payment(self, round(net_total, 2))
            if payments is None:
                return  # cashier cancelled the split-payment dialog
        elif payment_choice == "credit":
            cash_paid = 0.0

        try:
            result = pos.create_invoice(
                self.conn, staff_id=staff_id, cart=cart_lines, price_tier=self.tier_var.get(),
                payment_type=payment_type, customer_name=customer_row["name"] if customer_row else "Walk-in",
                customer_id=customer_row["id"] if customer_row else None, cash_paid=cash_paid, payments=payments,
            )
        except pos.InsufficientStockError as e:
            messagebox.showerror("Insufficient stock", str(e))
            return
        except customers_module.CreditLimitExceededError as e:
            messagebox.showerror("Credit limit exceeded", str(e))
            return
        except ValueError as e:
            messagebox.showerror("Cannot complete sale", str(e))
            return

        receipt_path = None
        try:
            receipt_path = receipts_module.save_receipt(self.conn, result.invoice_id)
            receipts_module.open_for_printing(receipt_path)
        except Exception:
            pass  # the sale already went through - a receipt failure shouldn't block the cashier

        msg = f"Invoice {result.invoice_no}\nNet total: LKR {result.net_total:,.2f}"
        if result.balance > 0.01:
            msg += f"\nChange due: LKR {result.balance:,.2f}"
        if receipt_path:
            msg += f"\nReceipt saved to {receipt_path}"
        messagebox.showinfo("Sale complete", msg)
        self.clear_cart()
        tree_clear(self.results_tree)
        self._refresh_quick_items()

    def hold_invoice(self):
        if not self.cart:
            messagebox.showwarning("Empty cart", "Nothing to hold.")
            return
        if not self.app.logged_in:
            messagebox.showwarning("Not logged in", "Log in from the sidebar before holding a cart.")
            return
        staff_id = self.app.current_staff_id
        customer_row = self._selected_customer_row()
        cart_lines = [pos.CartLine(product_id=c["product_id"], qty=c["qty"]) for c in self.cart]
        pos.hold_cart(self.conn, staff_id=staff_id, cart=cart_lines, price_tier=self.tier_var.get(),
                       customer_id=customer_row["id"] if customer_row else None)
        messagebox.showinfo("Held", "Cart held. Use 'Resume Held' to bring it back later.")
        self.clear_cart()

    def resume_invoice(self):
        held = pos.list_held_invoices(self.conn)
        if not held:
            messagebox.showinfo("No held invoices", "There are no held invoices right now.")
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Resume Held Invoice")
        dialog.geometry("460x320")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        tree = make_treeview(dialog, ["Held At", "Customer", "Note"],
                              {"Held At": 150, "Customer": 140, "Note": 140})
        tree.master_frame.pack(fill="both", expand=True, padx=12, pady=12)
        for h in held:
            tree_insert(tree, (h["held_at"], h["customer_name"], h["note"] or ""), iid=str(h["id"]))

        def do_resume():
            sel = tree.selection()
            if not sel:
                return
            held_id = int(sel[0])
            cart_lines, tier, customer_id = pos.resume_held_invoice(self.conn, held_id)
            new_cart = []
            tier_field = {"cash": "cash_price", "credit": "credit_price", "wholesale": "wholesale_price"}[tier]
            for line in cart_lines:
                p = self.conn.execute("SELECT * FROM products WHERE id=?", (line.product_id,)).fetchone()
                if p is None:
                    continue
                new_cart.append({"product_id": line.product_id, "name": p["name_en"], "qty": line.qty,
                                  "unit_price": p[tier_field]})
            self.cart = new_cart
            self.tier_var.set(tier)
            if customer_id:
                crow = customers_module.get_customer(self.conn, customer_id)
                if crow:
                    self.on_show()
                    self.customer_var.set(crow["name"])
            pos.delete_held_invoice(self.conn, held_id)
            self.refresh_cart()
            dialog.destroy()

        styled_button(dialog, "Resume Selected", do_resume, kind="primary").pack(pady=10)

    def open_return_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Return / Refund")
        dialog.geometry("500x460")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Invoice No:", text_color=theme.TEXT_DARK).pack(anchor="w", padx=16, pady=(16, 0))
        inv_row = ctk.CTkFrame(dialog, fg_color="transparent")
        inv_row.pack(fill="x", padx=16)
        inv_var = tk.StringVar()
        ctk.CTkEntry(inv_row, textvariable=inv_var, width=200).pack(side="left")

        tree = make_treeview(dialog, ["Item", "Returnable Qty", "Unit Price"],
                              {"Item": 230, "Returnable Qty": 120, "Unit Price": 100})
        tree.master_frame.pack(fill="both", expand=True, padx=16, pady=10)

        state = {"invoice_id": None, "items": []}

        def load():
            inv = self.conn.execute(
                "SELECT id FROM invoices WHERE invoice_no=? AND voided=0", (inv_var.get().strip(),)
            ).fetchone()
            if not inv:
                messagebox.showerror("Not found", "No such invoice (or it was voided).")
                return
            state["invoice_id"] = inv["id"]
            state["items"] = pos.returnable_items(self.conn, inv["id"])
            tree_clear(tree)
            for it in state["items"]:
                tree_insert(tree, (it["name_en"], f"{it['returnable_qty']:.2f}", f"{it['unit_price']:.2f}"),
                            iid=str(it["invoice_item_id"]))
            if not state["items"]:
                messagebox.showinfo("Nothing left to return", "Every line on this invoice is already fully returned.")

        styled_button(inv_row, "Load Invoice", load, kind="secondary").pack(side="left", padx=8)

        qty_row = ctk.CTkFrame(dialog, fg_color="transparent")
        qty_row.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(qty_row, text="Return qty (selected line):", text_color=theme.TEXT_DARK).pack(side="left")
        return_qty_var = tk.StringVar(value="1")
        ctk.CTkEntry(qty_row, textvariable=return_qty_var, width=80).pack(side="left", padx=8)

        reason_row = ctk.CTkFrame(dialog, fg_color="transparent")
        reason_row.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(reason_row, text="Reason:", text_color=theme.TEXT_DARK).pack(side="left")
        reason_var = tk.StringVar()
        ctk.CTkEntry(reason_row, textvariable=reason_var, width=260).pack(side="left", padx=8)

        def do_return():
            sel = tree.selection()
            if not sel or state["invoice_id"] is None:
                messagebox.showwarning("Select a line", "Load an invoice and select a line to return.")
                return
            item_id = int(sel[0])
            item = next((it for it in state["items"] if it["invoice_item_id"] == item_id), None)
            if item is None:
                return
            try:
                qty = float(return_qty_var.get())
            except ValueError:
                messagebox.showerror("Invalid quantity", "Enter a number.")
                return
            if qty <= 0 or qty > item["returnable_qty"] + 1e-6:
                messagebox.showerror("Invalid quantity", f"Max returnable is {item['returnable_qty']:.2f}.")
                return
            refund = pos.return_items(
                self.conn, state["invoice_id"], [(item["product_id"], item["batch_id"], qty)],
                reason=reason_var.get().strip(),
            )
            messagebox.showinfo("Returned", f"Refunded LKR {refund:,.2f}")
            load()

        styled_button(dialog, "Process Return", do_return, kind="danger").pack(pady=14)


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #

class InventoryScreen(BaseScreen):
    """Product catalog + stock levels. Everyone can view it; adding a new
    item, receiving stock, and editing an item's details all need Admin or
    Owner (a cashier rings up sales, but shouldn't be the one deciding what
    goes in the catalog or how much stock is on hand). Removing an item
    from the catalog is Owner-only and, like staff, never actually deletes
    it - see pos.deactivate_product for why (stock_batches/invoice_items
    reference it) - it's marked inactive and its stock is zeroed instead,
    and the Owner can restore it (same button, it toggles) if it comes back
    into stock later. All of these actions are written to the Activity Log."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Inventory")

        self.hint = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, wraplength=760, justify="left",
        )
        self.hint.pack(anchor="w", padx=24, pady=(0, 8))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=24)
        styled_button(btn_row, "Refresh", self.refresh, kind="secondary").pack(side="left")
        styled_button(btn_row, "Export CSV", self.export, kind="secondary").pack(side="left", padx=8)
        self.add_btn = styled_button(btn_row, "Add / Scan Item", self.open_add_product_dialog, kind="primary")
        self.add_btn.pack(side="left", padx=8)
        self.grn_btn = styled_button(btn_row, "Receive Stock (GRN)", self.open_grn_dialog, kind="primary")
        self.grn_btn.pack(side="left", padx=8)
        self.edit_btn = styled_button(btn_row, "Edit Item", self.open_edit_product_dialog, kind="secondary")
        self.edit_btn.pack(side="left", padx=8)
        self.remove_btn = styled_button(btn_row, "Remove / Restore Item", self.toggle_product_active, kind="danger")
        self.remove_btn.pack(side="left", padx=8)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24, pady=10)
        self.tree = make_treeview(
            tree_frame, ["Code", "Name", "Category", "On Hand", "Reorder Level", "Status"],
            {"Code": 90, "Name": 260, "Category": 150, "On Hand": 90, "Reorder Level": 100, "Status": 100},
        )
        self.tree.master_frame.pack(fill="both", expand=True)

    def on_show(self):
        self.refresh()

    def _current_role(self) -> str | None:
        if not self.app.logged_in:
            return None
        row = self.conn.execute(
            "SELECT role FROM staff WHERE id=?", (self.app.current_staff_id,)
        ).fetchone()
        return row["role"] if row else None

    def _is_admin(self) -> bool:
        return self._current_role() in ("admin", "owner")

    def _is_owner(self) -> bool:
        return self._current_role() == "owner"

    def _actor_label(self) -> str:
        row = self.conn.execute(
            "SELECT name, role FROM staff WHERE id=?", (self.app.current_staff_id,)
        ).fetchone()
        return f"{row['role'].capitalize()} {row['name']}" if row else "Someone"

    def refresh(self):
        is_admin = self._is_admin()
        is_owner = self._is_owner()
        self.hint.configure(
            text="Logged in as " + ("Owner" if is_owner else "Admin")
            + " - you can add/receive items and edit their details below; "
            + ("you can also remove or restore an item." if is_owner else "removing/restoring one is Owner-only.")
            if is_admin else
            "Only Admin/Owner can add items, receive stock, or edit an item's details "
            "(Owner-only to remove/restore one) - log in from the sidebar first. Anyone can view stock levels."
        )
        self.add_btn.configure(state="normal" if is_admin else "disabled")
        self.grn_btn.configure(state="normal" if is_admin else "disabled")
        self.edit_btn.configure(state="normal" if is_admin else "disabled")
        self.remove_btn.configure(state="normal" if is_owner else "disabled")

        raw = self.conn.execute(
            """SELECT p.id, p.code, p.name_en, p.category, p.reorder_level, p.active,
                      COALESCE(SUM(b.qty_remaining),0) on_hand
               FROM products p LEFT JOIN stock_batches b ON b.product_id = p.id
               GROUP BY p.id ORDER BY p.active DESC, p.category, p.name_en"""
        ).fetchall()
        self._rows = []
        tree_clear(self.tree)
        for r in raw:
            if not r["active"]:
                status, tag = "REMOVED", "inactive"
            elif r["on_hand"] < r["reorder_level"]:
                status, tag = "LOW STOCK", "low"
            else:
                status, tag = "OK", None
            self._rows.append(dict(r) | {"status": status})
            tree_insert(self.tree, (r["code"], r["name_en"], r["category"], f"{r['on_hand']:.0f}",
                                     r["reorder_level"], status), tag=tag, iid=str(r["id"]))

    def export(self):
        path = reports.export_csv(self._rows, db_module.ensure_output_dir() / "inventory_stock_summary.csv") \
            if hasattr(self, "_rows") else None
        if path:
            messagebox.showinfo("Exported", f"Saved to {path}")

    def open_edit_product_dialog(self):
        if not self._is_admin():
            messagebox.showwarning("Admin required", "Log in as Admin or Owner (sidebar) to edit an item.")
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No item selected", "Select an item in the list first.")
            return
        product_id = int(sel[0])
        product = self.conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if product is None:
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Edit Item - {product['code']}")
        dialog.geometry("420x620")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        scroll = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        ctk.CTkLabel(
            scroll, text=f"Barcode/code: {product['code']} (can't be changed here)",
            font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED,
        ).pack(anchor="w", padx=12, pady=(12, 6))

        def field(label, value):
            ctk.CTkLabel(scroll, text=label).pack(anchor="w", padx=12, pady=(8, 0))
            var = tk.StringVar(value=str(value))
            ctk.CTkEntry(scroll, textvariable=var, width=360).pack(padx=12)
            return var

        name_var = field("Name:", product["name_en"])
        name_si_var = field("Name (Sinhala, optional):", product["name_si"] or "")
        category_var = field("Category:", product["category"])
        unit_var = field("Unit:", product["unit"])
        cost_var = field("Cost price:", product["cost_price"])
        cash_var = field("Cash price:", product["cash_price"])
        credit_var = field("Credit price:", product["credit_price"])
        wholesale_var = field("Wholesale price:", product["wholesale_price"])
        pack_var = field("Pack size:", product["pack_size"])
        reorder_var = field("Reorder level:", product["reorder_level"])

        flags_row = ctk.CTkFrame(scroll, fg_color="transparent")
        flags_row.pack(anchor="w", padx=12, pady=(12, 0))
        perishable_var = tk.BooleanVar(value=bool(product["is_perishable"]))
        staple_var = tk.BooleanVar(value=bool(product["is_staple"]))
        child_var = tk.BooleanVar(value=bool(product["child_target"]))
        ctk.CTkCheckBox(flags_row, text="Perishable", variable=perishable_var).pack(side="left")
        ctk.CTkCheckBox(flags_row, text="Staple item", variable=staple_var).pack(side="left", padx=12)
        ctk.CTkCheckBox(flags_row, text="Child-target item", variable=child_var).pack(side="left")

        def save():
            try:
                pos.update_product(
                    self.conn, product_id,
                    name_en=name_var.get(), name_si=name_si_var.get(), category=category_var.get(),
                    unit=unit_var.get(), cost_price=float(cost_var.get()), cash_price=float(cash_var.get()),
                    credit_price=float(credit_var.get()), wholesale_price=float(wholesale_var.get()),
                    pack_size=int(float(pack_var.get())), reorder_level=int(float(reorder_var.get())),
                    is_perishable=perishable_var.get(), is_staple=staple_var.get(), child_target=child_var.get(),
                )
            except ValueError as e:
                messagebox.showerror("Cannot save", str(e))
                return
            audit.record(
                self.conn, actor_staff_id=self.app.current_staff_id, action="product.edit",
                details=f"{product['code']} - {name_var.get().strip()}",
            )
            dialog.destroy()
            self.refresh()

        styled_button(scroll, "Save", save, kind="primary", width=140).pack(pady=18)

    def toggle_product_active(self):
        if not self._is_owner():
            messagebox.showwarning("Owner required", "Only the Owner can remove or restore an item (sidebar login).")
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No item selected", "Select an item in the list first.")
            return
        product_id = int(sel[0])
        product = self.conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if product is None:
            return

        if product["active"]:
            if not messagebox.askyesno(
                "Remove item?",
                f"Remove \"{product['name_en']}\" from the catalog? Its stock on hand will be set to 0 - "
                "past sales/GRN history for it is kept, and you can restore it later from here.",
            ):
                return
            pos.deactivate_product(self.conn, product_id)
            audit.record(
                self.conn, actor_staff_id=self.app.current_staff_id, action="product.deactivate",
                details=f"{product['code']} - {product['name_en']}",
            )
        else:
            pos.reactivate_product(self.conn, product_id)
            audit.record(
                self.conn, actor_staff_id=self.app.current_staff_id, action="product.reactivate",
                details=f"{product['code']} - {product['name_en']}",
            )
        self.refresh()

    def open_grn_dialog(self):
        if not self._is_admin():
            messagebox.showwarning("Admin required", "Log in as Admin or Owner (sidebar) to receive stock.")
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Receive Stock (GRN)")
        dialog.geometry("420x340")
        dialog.configure(fg_color=theme.BG_LIGHT)

        products = self.conn.execute("SELECT id, code, name_en FROM products WHERE active=1 ORDER BY name_en").fetchall()
        names = [f"{p['code']} - {p['name_en']}" for p in products]

        ctk.CTkLabel(dialog, text="Product:").pack(anchor="w", padx=16, pady=(16, 0))
        product_var = tk.StringVar(value=names[0] if names else "")
        ctk.CTkOptionMenu(dialog, values=names, variable=product_var, width=360).pack(padx=16)

        suppliers_list = suppliers_module.list_suppliers(self.conn)
        supplier_names = ["(none)"] + [s["name"] for s in suppliers_list]
        ctk.CTkLabel(dialog, text="Supplier:").pack(anchor="w", padx=16, pady=(12, 0))
        supplier_var = tk.StringVar(value=supplier_names[0])
        ctk.CTkOptionMenu(dialog, values=supplier_names, variable=supplier_var, width=360).pack(padx=16)

        ctk.CTkLabel(dialog, text="Quantity received:").pack(anchor="w", padx=16, pady=(12, 0))
        qty_var = tk.StringVar(value="10")
        ctk.CTkEntry(dialog, textvariable=qty_var).pack(fill="x", padx=16)

        ctk.CTkLabel(dialog, text="Cost price (per unit):").pack(anchor="w", padx=16, pady=(12, 0))
        cost_var = tk.StringVar()
        ctk.CTkEntry(dialog, textvariable=cost_var).pack(fill="x", padx=16)

        ctk.CTkLabel(dialog, text="Expiry date (YYYY-MM-DD, blank if none):").pack(anchor="w", padx=16, pady=(12, 0))
        expiry_var = tk.StringVar()
        ctk.CTkEntry(dialog, textvariable=expiry_var).pack(fill="x", padx=16)

        def save():
            try:
                idx = names.index(product_var.get())
                product = products[idx]
                qty = float(qty_var.get())
                cost = float(cost_var.get()) if cost_var.get().strip() else None
                expiry = expiry_var.get().strip() or None
                supplier_id = None
                if supplier_var.get() != "(none)":
                    match = next((s for s in suppliers_list if s["name"] == supplier_var.get()), None)
                    supplier_id = match["id"] if match else None
                pos.receive_stock(self.conn, product_id=product["id"], qty=qty, cost_price=cost,
                                   expiry_date=expiry, supplier_id=supplier_id)
                dialog.destroy()
                self.refresh()
            except ValueError as e:
                messagebox.showerror("Invalid input", str(e))

        styled_button(dialog, "Save", save, kind="primary", width=140).pack(pady=18)

    def open_add_product_dialog(self):
        """One entry point for "put an item into the store" that figures
        out which of two things you mean, from the barcode itself - you
        shouldn't have to know in advance whether a product is already in
        the catalog before you can scan it in:

        - Scan/type a code that's NOT in the catalog yet -> this is a
          brand-new product, so the full details form appears (name,
          category, prices, etc.) to register it for the first time.
        - Scan/type a code that IS already in the catalog -> there's
          nothing to ask except how many more units just came in, so it
          shows the existing product's name/category/current stock and
          just asks for the quantity received (same as the "Receive Stock
          (GRN)" dialog, sharing pos.receive_stock with it).

        The barcode field can be filled by typing on the keyboard, by a
        USB/Bluetooth barcode scanner (it just types the digits into
        whichever field has focus, so the field is auto-focused for that),
        or by scanning it with a phone via the same phone-scanner feature
        used at the till."""
        if not self._is_admin():
            messagebox.showwarning("Admin required", "Log in as Admin or Owner (sidebar) to add an item.")
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Add / Scan Item")
        dialog.geometry("440x780")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        def field(parent, label, initial=""):
            ctk.CTkLabel(parent, text=label).pack(anchor="w", padx=16, pady=(10, 0))
            var = tk.StringVar(value=initial)
            entry = ctk.CTkEntry(parent, textvariable=var)
            entry.pack(fill="x", padx=16)
            return var, entry

        ctk.CTkLabel(body, text="Barcode / Code:").pack(anchor="w", padx=16, pady=(12, 0))
        code_row = ctk.CTkFrame(body, fg_color="transparent")
        code_row.pack(fill="x", padx=16)
        code_var = tk.StringVar()
        code_entry = ctk.CTkEntry(code_row, textvariable=code_var)
        code_entry.pack(side="left", fill="x", expand=True)
        styled_button(
            code_row, "Scan with Phone",
            lambda: self._open_phone_capture_dialog(lambda scanned: (code_var.set(scanned), check_code())),
            kind="secondary", width=130,
        ).pack(side="left", padx=(8, 0))

        status_label = ctk.CTkLabel(
            body, text="Type, scan with a USB/Bluetooth scanner, or tap \"Scan with Phone\" - "
                       "then press Enter (or click away) to check the catalog.",
            text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11), wraplength=380, justify="left",
        )
        status_label.pack(anchor="w", padx=16, pady=(6, 6))

        # --- Existing-product path: just ask how much stock came in. ---
        existing_frame = ctk.CTkFrame(body, fg_color="transparent")
        existing_info = ctk.CTkLabel(existing_frame, text="", text_color=theme.NAVY_DARK,
                                      font=ctk.CTkFont(size=13, weight="bold"), wraplength=380, justify="left")
        existing_info.pack(anchor="w", padx=16, pady=(4, 8))
        qty_received_var, _ = field(existing_frame, "Quantity received:", "10")
        existing_cost_var, _ = field(existing_frame, "Cost price (per unit, blank = use last cost):")
        existing_expiry_var, _ = field(existing_frame, "Expiry date (YYYY-MM-DD, blank if none):")

        # --- New-product path: the full catalog-entry form. ---
        new_frame = ctk.CTkFrame(body, fg_color="transparent")
        name_en_var, _ = field(new_frame, "Product name (English):")
        name_si_var, _ = field(new_frame, "Product name (Sinhala, optional):")
        category_var, _ = field(new_frame, "Category:")
        unit_var, _ = field(new_frame, "Unit (e.g. pcs, kg, g, L):", "pcs")
        cost_var, _ = field(new_frame, "Cost price (what you pay):")
        cash_var, _ = field(new_frame, "Cash selling price:")
        credit_var, _ = field(new_frame, "Credit selling price (blank = same as cash):")
        wholesale_var, _ = field(new_frame, "Wholesale price (blank = same as cost):")
        pack_size_var, _ = field(new_frame, "Pack size (units per pack):", "1")
        reorder_var, _ = field(new_frame, "Reorder level (low-stock alert threshold):", "10")
        initial_qty_var, _ = field(new_frame, "Opening stock quantity (optional):", "0")
        expiry_var, _ = field(new_frame, "Expiry date (YYYY-MM-DD, optional):")
        flags_row = ctk.CTkFrame(new_frame, fg_color="transparent")
        flags_row.pack(fill="x", padx=16, pady=(14, 0))
        perishable_var = tk.BooleanVar(value=False)
        staple_var = tk.BooleanVar(value=False)
        child_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(flags_row, text="Perishable", variable=perishable_var).pack(side="left")
        ctk.CTkCheckBox(flags_row, text="Staple item", variable=staple_var).pack(side="left", padx=12)
        ctk.CTkCheckBox(flags_row, text="Child-target item", variable=child_var).pack(side="left")

        save_btn = styled_button(dialog, "Save", lambda: None, kind="primary", width=160)
        save_btn.pack(pady=16)

        state = {"mode": None, "product": None}

        def save_existing():
            code = code_var.get().strip()
            product = pos.get_product_by_code(self.conn, code)
            if product is None:
                messagebox.showerror("Invalid input", "That barcode no longer matches a product - re-check it.")
                check_code()
                return
            try:
                pos.receive_stock(
                    self.conn, product_id=product["id"],
                    qty=float(qty_received_var.get() or 0),
                    cost_price=float(existing_cost_var.get()) if existing_cost_var.get().strip() else None,
                    expiry_date=existing_expiry_var.get().strip() or None,
                )
            except ValueError as e:
                messagebox.showerror("Invalid input", str(e))
                return
            dialog.destroy()
            self.refresh()
            messagebox.showinfo("Stock added", f"Added {qty_received_var.get()} {product['unit']} to '{product['name_en']}'.")

        def save_new():
            try:
                pos.add_product(
                    self.conn,
                    code=code_var.get(),
                    name_en=name_en_var.get(),
                    name_si=name_si_var.get(),
                    category=category_var.get(),
                    unit=unit_var.get() or "pcs",
                    cost_price=float(cost_var.get() or 0),
                    cash_price=float(cash_var.get() or 0),
                    credit_price=float(credit_var.get()) if credit_var.get().strip() else None,
                    wholesale_price=float(wholesale_var.get()) if wholesale_var.get().strip() else None,
                    pack_size=int(pack_size_var.get() or 1),
                    reorder_level=int(reorder_var.get() or 0),
                    is_perishable=perishable_var.get(),
                    is_staple=staple_var.get(),
                    child_target=child_var.get(),
                    initial_qty=float(initial_qty_var.get() or 0),
                    expiry_date=expiry_var.get().strip() or None,
                )
            except ValueError as e:
                messagebox.showerror("Invalid input", str(e))
                return
            dialog.destroy()
            self.refresh()
            messagebox.showinfo("Product added", f"'{name_en_var.get().strip()}' was added to the catalog.")

        def check_code(event=None):
            code = code_var.get().strip()
            if not code:
                existing_frame.pack_forget()
                new_frame.pack_forget()
                save_btn.configure(state="disabled")
                status_label.configure(text="Type, scan with a USB/Bluetooth scanner, or tap \"Scan with "
                                             "Phone\" - then press Enter (or click away) to check the catalog.")
                state["mode"] = None
                return
            product = pos.get_product_by_code(self.conn, code)
            save_btn.configure(state="normal")
            if product is not None:
                state["mode"] = "existing"
                new_frame.pack_forget()
                on_hand = pos.get_stock_on_hand(self.conn, product["id"])
                existing_info.configure(
                    text=f"Already in the catalog: {product['name_en']} ({product['category']}) - "
                         f"{on_hand:.0f} {product['unit']} in stock now. Just enter how many more came in."
                )
                existing_frame.pack(fill="x")
                status_label.configure(text=f"Found an existing product for code '{code}'.")
                save_btn.configure(text="Add Stock", command=save_existing)
            else:
                state["mode"] = "new"
                existing_frame.pack_forget()
                new_frame.pack(fill="x")
                status_label.configure(text=f"Code '{code}' isn't in the catalog yet - fill in the new "
                                             f"product's details below.")
                save_btn.configure(text="Save New Product", command=save_new)

        code_entry.bind("<Return>", check_code)
        code_entry.bind("<FocusOut>", check_code)
        save_btn.configure(state="disabled")
        dialog.after(150, code_entry.focus_set)

    def _open_phone_capture_dialog(self, on_captured):
        """Open a small phone-scanner dialog whose only job is to capture
        one barcode/QR code and hand the decoded text to on_captured(code)
        - used to fill in a text field (like the new-product barcode
        field above) from the phone's camera. Reuses the same background
        server the till's own Phone Scanner feature uses (POSScreen.
        open_phone_scanner_dialog), so if that's already running for cart
        scanning, this borrows it for one scan and hands it back exactly
        as it was - the till keeps working normally either way."""
        if getattr(self.app, "mobile_scan_server", None) is None:
            self.app.mobile_scan_server = mobile_scan.MobileScanServer(lambda code: {"unknown": True})
        server = self.app.mobile_scan_server
        if not server.running:
            try:
                server.start()
            except OSError as e:
                messagebox.showerror("Could not start phone scanner", str(e))
                return

        previous_on_scan = server.on_scan
        state = {"restored": False}

        def restore():
            if not state["restored"]:
                state["restored"] = True
                server.on_scan = previous_on_scan

        def captured(code):
            restore()
            self.after(0, lambda: (on_captured(code), dialog.destroy()))
            return {"name": code, "added": True}

        server.on_scan = captured

        dialog = ctk.CTkToplevel(self)
        dialog.title("Scan Barcode with Phone")
        dialog.geometry("400x620")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", lambda: (restore(), dialog.destroy()))

        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(body, text="Scan the product's barcode", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=theme.NAVY_DARK).pack(pady=(16, 6))

        try:
            from PIL import Image
            import io
            png_bytes = mobile_scan.generate_qr_png_bytes(server.url)
            qr_img = Image.open(io.BytesIO(png_bytes))
            ctk_qr = ctk.CTkImage(light_image=qr_img, dark_image=qr_img, size=(200, 200))
            ctk.CTkLabel(body, image=ctk_qr, text="").pack(pady=6)
        except Exception:
            pass

        ctk.CTkLabel(body, text="Open this address on the phone (or scan the QR above):",
                     text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11)).pack(pady=(6, 0))
        ctk.CTkLabel(body, text=server.url, font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=theme.ACCENT_BLUE).pack(pady=(0, 12))
        ctk.CTkLabel(body, text="Pairing code:", text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11)).pack()
        ctk.CTkLabel(body, text=server.pairing_code, font=ctk.CTkFont(size=28, weight="bold"),
                     text_color=theme.NAVY_DARK).pack(pady=(2, 12))
        ctk.CTkLabel(
            body,
            text="Point the camera at the barcode - it fills in the field automatically as "
                 "soon as it's read, and this window closes on its own. (First-time on a new "
                 "phone: it'll show a one-time \"Connection not private\" warning - tap "
                 "Advanced/Show Details then Proceed/visit this website.)",
            text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11), wraplength=340, justify="left",
        ).pack(padx=20, pady=(0, 14))

        styled_button(dialog, "Cancel", lambda: (restore(), dialog.destroy()), kind="secondary", width=140).pack(pady=(0, 16))


# --------------------------------------------------------------------------- #
# Promotions & Expiry
# --------------------------------------------------------------------------- #

class PromotionsScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Promotions & Expiry (Urgency Ranking)")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24)
        styled_button(top, "Refresh", self.refresh, kind="secondary").pack(side="left")
        legend = ctk.CTkFrame(top, fg_color="transparent")
        legend.pack(side="left", padx=20)
        for label, color in [("Overdue", theme.ALERT_COLORS["overdue"]),
                              ("≤ 3 days", theme.ALERT_COLORS["critical_3day"]),
                              ("≤ 7 days", theme.ALERT_COLORS["warning_7day"])]:
            swatch = ctk.CTkFrame(legend, width=14, height=14, fg_color=color, corner_radius=3)
            swatch.pack(side="left", padx=(10, 4))
            swatch.pack_propagate(False)
            ctk.CTkLabel(legend, text=label, font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED).pack(side="left")

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24, pady=10)
        columns = ["Product", "Qty", "Expiry", "Days Left", "Alert", "Urgency",
                   "Discount %", "Suggested Price", "Est. Clearance"]
        widths = {"Product": 220, "Qty": 60, "Expiry": 90, "Days Left": 80, "Alert": 100,
                  "Urgency": 80, "Discount %": 90, "Suggested Price": 110, "Est. Clearance": 110}
        self.tree = make_treeview(tree_frame, columns, widths)
        self.tree.master_frame.pack(fill="both", expand=True)

    def on_show(self):
        self.refresh()

    def refresh(self):
        rows = promotions.compute_promotions(self.conn)
        tree_clear(self.tree)
        for r in rows:
            tree_insert(self.tree, (
                r["name"], f"{r['qty_remaining']:.0f}", r["expiry_date"], r["days_to_expiry"],
                r["alert_tier"] or "-", r["urgency"], f"{r['suggested_discount_pct']}%",
                f"{r['suggested_price']:.2f}", r["projected_clearance_date"] or "-",
            ), tag=r["alert_tier"])


# --------------------------------------------------------------------------- #
# Forecasting
# --------------------------------------------------------------------------- #

class ForecastingScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Demand Forecasting")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24)
        ctk.CTkLabel(top, text="Product:", text_color=theme.TEXT_DARK).pack(side="left")
        products = self.conn.execute("SELECT id, name_en FROM products WHERE active=1 ORDER BY name_en").fetchall()
        self._products = products
        names = [p["name_en"] for p in products]
        self.product_var = tk.StringVar(value=names[0] if names else "")
        ctk.CTkOptionMenu(top, values=names, variable=self.product_var, width=280).pack(side="left", padx=8)
        self.run_forecast_btn = styled_button(top, "Run Forecast", self.run_forecast)
        self.run_forecast_btn.pack(side="left", padx=8)
        self.purchase_list_btn = styled_button(top, "Generate Purchase List (all products)", self.run_purchase_list,
                                                kind="secondary")
        self.purchase_list_btn.pack(side="left", padx=20)

        self.status_label = ctk.CTkLabel(self, text="", text_color=theme.TEXT_MUTED)
        self.status_label.pack(anchor="w", padx=24, pady=(6, 0))

        self.chart_frame = ctk.CTkFrame(self, fg_color="transparent", height=280)
        self.chart_frame.pack(fill="x", padx=24, pady=10)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24)
        self.tree = make_treeview(tree_frame, ["Model / Date", "Value"], {"Model / Date": 260, "Value": 140})
        self.tree.master_frame.pack(fill="both", expand=True)

    def run_forecast(self):
        idx = [p["name_en"] for p in self._products].index(self.product_var.get())
        product = self._products[idx]
        self.status_label.configure(text="Running rolling-origin cross-validation... (this can take a few seconds)")
        self.run_forecast_btn.configure(state="disabled")

        def work():
            conn = db_module.get_conn(self.app.db_path)
            try:
                return forecasting.select_best_model(conn, product["id"])
            finally:
                conn.close()

        run_in_background(self, work, self._on_forecast_done, self._on_forecast_error)

    def _on_forecast_error(self, err):
        self.run_forecast_btn.configure(state="normal")
        messagebox.showerror("Forecast failed", str(err))

    def _on_forecast_done(self, result):
        self.run_forecast_btn.configure(state="normal")
        tree_clear(self.tree)
        if not result["model_scores"] and result["forecast"] is None:
            self.status_label.configure(text="Not enough sales history for this product yet (need 30+ days).")
            return

        for name, score in sorted(result["model_scores"].items(), key=lambda kv: kv[1]):
            marker = "  <- selected" if name == result["best_model"] else ""
            tree_insert(self.tree, (f"nRMSE: {name}{marker}", f"{score:.3f}"))
        if result.get("future_dates") is not None:
            for d, v in zip(result["future_dates"], result["forecast"]):
                tree_insert(self.tree, (d.date().isoformat(), f"{v:.1f}"))

        self.status_label.configure(
            text=f"Best model: {result['best_model']}  |  anomalies excluded from training: {len(result['anomalies'])}"
        )
        self._draw_chart(result)

    def _draw_chart(self, result):
        for w in self.chart_frame.winfo_children():
            w.destroy()
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
        except ImportError:
            return
        fig = Figure(figsize=(9, 3), dpi=100)
        ax = fig.add_subplot(111)
        series = result["series"].tail(60)
        ax.plot(series.index, series.values, label="Actual (last 60 days)", color="#1a73e8")
        if result.get("future_dates") is not None:
            ax.plot(result["future_dates"], result["forecast"], label=f"Forecast ({result['best_model']})",
                    color="#e06666", linestyle="--", marker="o")
        ax.legend(fontsize=8)
        ax.tick_params(labelsize=7)
        fig.autofmt_xdate()
        canvas = FigureCanvasTkAgg(fig, master=self.chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    def run_purchase_list(self):
        self.status_label.configure(
            text="Generating purchase list across all products - this runs a forecast per product "
                 "and can take a while; the rest of the app stays usable meanwhile."
        )
        self.purchase_list_btn.configure(state="disabled")

        def work():
            conn = db_module.get_conn(self.app.db_path)
            try:
                return forecasting.generate_purchase_list(conn)
            finally:
                conn.close()

        run_in_background(self, work, self._on_purchase_list_done, self._on_purchase_list_error)

    def _on_purchase_list_error(self, err):
        self.purchase_list_btn.configure(state="normal")
        messagebox.showerror("Purchase list failed", str(err))

    def _on_purchase_list_done(self, rows):
        self.purchase_list_btn.configure(state="normal")
        tree_clear(self.tree)
        self.tree.configure(columns=["Product", "On Hand", "Forecast Demand", "Suggested Reorder", "Model"])
        for col in ["Product", "On Hand", "Forecast Demand", "Suggested Reorder", "Model"]:
            self.tree.heading(col, text=col)
        for r in rows:
            tree_insert(self.tree, (r["name"], r["on_hand"], r["forecast_demand_next_period"],
                                     r["suggested_reorder_qty"], r["best_model"]))
        path = reports.export_csv(rows, db_module.ensure_output_dir() / "purchase_list.csv")
        self.status_label.configure(text=f"{len(rows)} products need reordering. Exported to {path}")


# --------------------------------------------------------------------------- #
# Bundles
# --------------------------------------------------------------------------- #

class BundlesScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Bundle Recommendations (Association Rule Mining)")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24)
        self.compute_btn = styled_button(top, "Compute Bundles", self.compute)
        self.compute_btn.pack(side="left")
        self.status_label = ctk.CTkLabel(top, text="min support 2%, min confidence 30%, min lift 1.1",
                                          text_color=theme.TEXT_MUTED)
        self.status_label.pack(side="left", padx=16)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24, pady=10)
        columns = ["Bundle", "Support", "Confidence", "Lift", "Sum Price", "Bundle Price", "Savings"]
        widths = {"Bundle": 320, "Support": 80, "Confidence": 90, "Lift": 70,
                  "Sum Price": 100, "Bundle Price": 100, "Savings": 90}
        self.tree = make_treeview(tree_frame, columns, widths)
        self.tree.master_frame.pack(fill="both", expand=True)

    def compute(self):
        self.compute_btn.configure(state="disabled")
        self.status_label.configure(text="Mining association rules over your sales history...")

        def work():
            conn = db_module.get_conn(self.app.db_path)
            try:
                return association.get_bundle_recommendations(conn)
            finally:
                conn.close()

        def on_done(rows):
            self.compute_btn.configure(state="normal")
            tree_clear(self.tree)
            for r in rows:
                bundle_name = " + ".join(r["antecedent"] + r["consequent"])
                tree_insert(self.tree, (bundle_name, r["support"], r["confidence"], r["lift"],
                                         f"{r['sum_price_lkr']:.2f}", f"{r['bundle_price_lkr']:.2f}",
                                         f"{r['savings_lkr']:.2f}"))
            self.status_label.configure(text=f"{len(rows)} bundle recommendations")

        def on_error(err):
            self.compute_btn.configure(state="normal")
            messagebox.showerror("Bundle computation failed", str(err))

        run_in_background(self, work, on_done, on_error)


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #

class LayoutScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Store Layout Optimisation")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24)
        self.compute_btn = styled_button(top, "Compute Layout", self.compute)
        self.compute_btn.pack(side="left")
        self.status_label = ctk.CTkLabel(top, text="", text_color=theme.TEXT_MUTED)
        self.status_label.pack(side="left", padx=16)

        self.image_frame = ctk.CTkFrame(self, fg_color=theme.CARD_BG, border_width=1, border_color=theme.BORDER)
        self.image_frame.pack(padx=24, pady=10, fill="both", expand=True)
        self.image_label = ctk.CTkLabel(self.image_frame, text="Click \"Compute Layout\" to generate a planogram.",
                                         text_color=theme.TEXT_MUTED)
        self.image_label.pack(padx=16, pady=16)

    def compute(self):
        self.compute_btn.configure(state="disabled")
        self.status_label.configure(text="Computing layout assignments...")

        def work():
            conn = db_module.get_conn(self.app.db_path)
            try:
                assignments = layout.compute_layout(conn)
                layout.persist_layout(conn, assignments)
            finally:
                conn.close()
            out_path = str(db_module.ensure_output_dir() / "planogram.png")
            layout.render_planogram(assignments, out_path)
            return assignments, out_path

        def on_done(result):
            assignments, out_path = result
            self.compute_btn.configure(state="normal")
            self.status_label.configure(text=f"Layout computed for {len(assignments)} products. Saved to {out_path}")
            try:
                from PIL import Image
                img = Image.open(out_path)
                w, h = img.size
                scale = min(1.0, 900 / w)
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(int(w * scale), int(h * scale)))
                self.image_label.configure(image=ctk_img, text="")
                self.image_label.image = ctk_img
            except ImportError:
                self.image_label.configure(text=f"Planogram saved to {out_path} (install Pillow to preview it here).")

        def on_error(err):
            self.compute_btn.configure(state="normal")
            messagebox.showerror("Layout computation failed", str(err))

        run_in_background(self, work, on_done, on_error)


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #

REPORT_OPTIONS = ["Best Sellers (30d)", "Stock Summary", "Low Stock Alert",
                   "Waste Summary", "Cashier Daily Statement", "Credit Outstanding",
                   "Returns Summary (30d)", "Supplier Summary", "Cash Sessions (Day Open/Close)"]


class ReportsScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Reports")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24)
        self.report_var = tk.StringVar(value=REPORT_OPTIONS[0])
        ctk.CTkOptionMenu(top, values=REPORT_OPTIONS, variable=self.report_var,
                          command=self._on_report_change).pack(side="left")
        styled_button(top, "Run Report", self.run_report).pack(side="left", padx=8)
        styled_button(top, "Export CSV", self.export, kind="secondary").pack(side="left", padx=8)
        styled_button(top, "Backup Database", self.backup, kind="secondary").pack(side="left", padx=20)

        self.cashier_row = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkLabel(self.cashier_row, text="Staff:", text_color=theme.TEXT_DARK).pack(side="left", padx=(24, 4))
        staff_names = [r["name"] for r in self.conn.execute("SELECT name FROM staff").fetchall()] or ["Admin"]
        self.cashier_staff_var = tk.StringVar(value=staff_names[0])
        ctk.CTkOptionMenu(self.cashier_row, values=staff_names, variable=self.cashier_staff_var).pack(side="left")
        ctk.CTkLabel(self.cashier_row, text="Date (YYYY-MM-DD):", text_color=theme.TEXT_DARK).pack(
            side="left", padx=(16, 4))
        self.cashier_date_var = tk.StringVar(value=date.today().isoformat())
        ctk.CTkEntry(self.cashier_row, textvariable=self.cashier_date_var, width=120).pack(side="left")
        # hidden until "Cashier Daily Statement" is selected - see _on_report_change

        self.summary_label = ctk.CTkLabel(self, text="", text_color=theme.NAVY_DARK,
                                           font=ctk.CTkFont(size=14, weight="bold"))
        self.summary_label.pack(anchor="w", padx=24, pady=(8, 0))

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24, pady=10)
        self.tree = make_treeview(tree_frame, ["A", "B", "C", "D"], {})
        self.tree.master_frame.pack(fill="both", expand=True)
        self._rows: list[dict] = []

    def _on_report_change(self, choice: str):
        if choice == "Cashier Daily Statement":
            self.cashier_row.pack(fill="x", pady=(6, 0), before=self.summary_label)
        else:
            self.cashier_row.pack_forget()

    def _set_columns(self, columns: list[str]):
        self.tree.configure(columns=columns)
        for c in columns:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=140)

    def run_report(self):
        choice = self.report_var.get()
        tree_clear(self.tree)
        self.summary_label.configure(text="")

        if choice == "Best Sellers (30d)":
            rows = [dict(r) for r in reports.best_sellers(self.conn)]
            self._set_columns(["name_en", "category", "qty", "revenue"])
        elif choice == "Stock Summary":
            rows = reports.stock_summary(self.conn)
            self._set_columns(["code", "name_en", "category", "on_hand", "reorder_level"])
        elif choice == "Low Stock Alert":
            rows = reports.low_stock_alert(self.conn)
            self._set_columns(["code", "name_en", "on_hand", "reorder_level"])
        elif choice == "Cashier Daily Statement":
            staff_row = self.conn.execute(
                "SELECT id FROM staff WHERE name=?", (self.cashier_staff_var.get(),)
            ).fetchone()
            try:
                day = date.fromisoformat(self.cashier_date_var.get().strip())
            except ValueError:
                messagebox.showerror("Invalid date", "Use YYYY-MM-DD format.")
                return
            statement = reports.cashier_daily_statement(self.conn, staff_row["id"] if staff_row else 1, day)
            rows = [{"payment_type": k, "count": v["count"], "total": v["total"]}
                    for k, v in statement["by_payment_type"].items()]
            self._set_columns(["payment_type", "count", "total"])
            self.summary_label.configure(
                text=f"{statement['staff_name']} — {statement['date']}: "
                     f"{statement['invoice_count']} invoices, LKR {statement['grand_total']:,.2f} total"
            )
        elif choice == "Credit Outstanding":
            rows = reports.credit_outstanding_summary(self.conn)
            self._set_columns(["name", "phone", "credit_limit", "credit_balance", "available_credit"])
        elif choice == "Returns Summary (30d)":
            rows = reports.returns_summary(self.conn)
            self._set_columns(["name_en", "category", "events", "qty", "refunded"])
        elif choice == "Supplier Summary":
            rows = suppliers_module.supplier_summary(self.conn)
            self._set_columns(["name", "batches_received", "qty_received", "value_received"])
        elif choice == "Cash Sessions (Day Open/Close)":
            rows = [dict(r) for r in cash_drawer_module.session_history(self.conn)]
            self._set_columns(["staff_name", "opened_at", "closed_at", "opening_float",
                                "counted_cash", "expected_cash", "variance"])
        else:
            rows = reports.waste_summary(self.conn)
            self._set_columns(["name_en", "category", "events", "qty", "value_lost"])

        columns = self.tree["columns"]
        for r in rows:
            tree_insert(self.tree, [r.get(c, "") for c in columns])
        self._rows = rows

    def export(self):
        if not self._rows:
            messagebox.showwarning("Nothing to export", "Run a report first.")
            return
        path = reports.export_csv(self._rows, db_module.ensure_output_dir() / "report_export.csv")
        messagebox.showinfo("Exported", f"Saved to {path}")

    def backup(self):
        path = reports.backup_database(db_module.DEFAULT_DB_PATH, db_module.ensure_output_dir() / "backups")
        messagebox.showinfo("Backup complete", f"Saved to {path}")


# --------------------------------------------------------------------------- #
# Customers (credit accounts)
# --------------------------------------------------------------------------- #

class CustomersScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Customers & Credit Accounts")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24)
        styled_button(top, "Refresh", self.refresh, kind="secondary").pack(side="left")
        styled_button(top, "Add Customer", self.open_add_dialog, kind="primary").pack(side="left", padx=8)
        styled_button(top, "Settle Credit", self.open_settle_dialog, kind="success").pack(side="left", padx=8)
        styled_button(top, "View Statement", self.open_statement_dialog, kind="secondary").pack(side="left", padx=8)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24, pady=10)
        self.tree = make_treeview(
            tree_frame, ["Name", "Phone", "Credit Limit", "Balance Owed", "Available Credit"],
            {"Name": 220, "Phone": 130, "Credit Limit": 110, "Balance Owed": 110, "Available Credit": 130},
        )
        self.tree.master_frame.pack(fill="both", expand=True)

    def on_show(self):
        self.refresh()

    def refresh(self):
        tree_clear(self.tree)
        for c in customers_module.list_customers(self.conn):
            available = round(c["credit_limit"] - c["credit_balance"], 2)
            tag = "overdue" if c["credit_limit"] > 0 and c["credit_balance"] >= c["credit_limit"] else None
            tree_insert(
                self.tree,
                (c["name"], c["phone"] or "", f"{c['credit_limit']:,.2f}", f"{c['credit_balance']:,.2f}",
                 f"{available:,.2f}"),
                tag=tag, iid=str(c["id"]),
            )

    def _selected_customer_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def open_add_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Add Customer")
        dialog.geometry("380x340")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        fields = {}
        for label, key, default in [("Name:", "name", ""), ("Phone:", "phone", ""),
                                     ("Address:", "address", ""), ("Credit Limit:", "credit_limit", "0")]:
            ctk.CTkLabel(dialog, text=label).pack(anchor="w", padx=16, pady=(12, 0))
            v = tk.StringVar(value=default)
            ctk.CTkEntry(dialog, textvariable=v, width=320).pack(padx=16)
            fields[key] = v

        def save():
            name = fields["name"].get().strip()
            if not name:
                messagebox.showerror("Missing name", "Customer name is required.")
                return
            try:
                limit = float(fields["credit_limit"].get() or 0)
            except ValueError:
                messagebox.showerror("Invalid credit limit", "Enter a number.")
                return
            customers_module.add_customer(
                self.conn, name, fields["phone"].get(), fields["address"].get(), limit
            )
            dialog.destroy()
            self.refresh()

        styled_button(dialog, "Save", save, kind="primary", width=140).pack(pady=18)

    def open_settle_dialog(self):
        customer_id = self._selected_customer_id()
        if customer_id is None:
            messagebox.showwarning("Select a customer", "Select a customer in the list first.")
            return
        c = customers_module.get_customer(self.conn, customer_id)
        if c is None or c["credit_balance"] <= 0:
            messagebox.showinfo("Nothing owed", f"{c['name'] if c else 'This customer'} has no outstanding balance.")
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Settle Credit")
        dialog.geometry("360x260")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text=f"{c['name']} owes LKR {c['credit_balance']:,.2f}",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color=theme.NAVY_DARK).pack(pady=(16, 10))

        ctk.CTkLabel(dialog, text="Amount received:").pack(anchor="w", padx=20)
        amount_var = tk.StringVar(value=f"{c['credit_balance']:.2f}")
        ctk.CTkEntry(dialog, textvariable=amount_var, width=300).pack(padx=20)

        ctk.CTkLabel(dialog, text="Method:").pack(anchor="w", padx=20, pady=(10, 0))
        method_var = tk.StringVar(value="cash")
        ctk.CTkOptionMenu(dialog, values=["cash", "card", "cheque"], variable=method_var, width=300).pack(padx=20)

        def save():
            try:
                amount = float(amount_var.get())
            except ValueError:
                messagebox.showerror("Invalid amount", "Enter a number.")
                return
            if amount <= 0 or amount > c["credit_balance"] + 0.01:
                messagebox.showerror("Invalid amount", f"Enter an amount up to LKR {c['credit_balance']:,.2f}.")
                return
            customers_module.settle_credit(self.conn, customer_id, amount, method_var.get())
            dialog.destroy()
            self.refresh()

        styled_button(dialog, "Record Payment", save, kind="success", width=160).pack(pady=18)

    def open_statement_dialog(self):
        customer_id = self._selected_customer_id()
        if customer_id is None:
            messagebox.showwarning("Select a customer", "Select a customer in the list first.")
            return
        c = customers_module.get_customer(self.conn, customer_id)
        rows = customers_module.credit_statement(self.conn, customer_id)

        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Credit Statement — {c['name'] if c else ''}")
        dialog.geometry("560x420")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        stmt_tree = make_treeview(dialog, ["Date", "Description", "Charge", "Payment", "Balance"],
                                   {"Date": 130, "Description": 160, "Charge": 80, "Payment": 80, "Balance": 90})
        stmt_tree.master_frame.pack(fill="both", expand=True, padx=16, pady=16)
        for r in rows:
            tree_insert(stmt_tree, (r["ts"], r["description"], f"{r['charge']:.2f}" if r["charge"] else "",
                                     f"{r['payment']:.2f}" if r["payment"] else "", f"{r['balance']:.2f}"))
        if not rows:
            ctk.CTkLabel(dialog, text="No credit activity yet.", text_color=theme.TEXT_MUTED).pack(pady=10)


# --------------------------------------------------------------------------- #
# Suppliers
# --------------------------------------------------------------------------- #

class SuppliersScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Suppliers")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24)
        styled_button(top, "Refresh", self.refresh, kind="secondary").pack(side="left")
        styled_button(top, "Add Supplier", self.open_add_dialog, kind="primary").pack(side="left", padx=8)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24, pady=10)
        self.tree = make_treeview(
            tree_frame, ["Name", "Phone", "Address"], {"Name": 220, "Phone": 140, "Address": 260},
        )
        self.tree.master_frame.pack(fill="both", expand=True)

    def on_show(self):
        self.refresh()

    def refresh(self):
        tree_clear(self.tree)
        for s in suppliers_module.list_suppliers(self.conn):
            tree_insert(self.tree, (s["name"], s["phone"] or "", s["address"] or ""), iid=str(s["id"]))

    def open_add_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Add Supplier")
        dialog.geometry("380x280")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        fields = {}
        for label, key in [("Name:", "name"), ("Phone:", "phone"), ("Address:", "address")]:
            ctk.CTkLabel(dialog, text=label).pack(anchor="w", padx=16, pady=(12, 0))
            v = tk.StringVar(value="")
            ctk.CTkEntry(dialog, textvariable=v, width=320).pack(padx=16)
            fields[key] = v

        def save():
            name = fields["name"].get().strip()
            if not name:
                messagebox.showerror("Missing name", "Supplier name is required.")
                return
            suppliers_module.add_supplier(self.conn, name, fields["phone"].get(), fields["address"].get())
            dialog.destroy()
            self.refresh()

        styled_button(dialog, "Save", save, kind="primary", width=140).pack(pady=18)


# --------------------------------------------------------------------------- #
# Staff / cashiers
# --------------------------------------------------------------------------- #

class StaffScreen(BaseScreen):
    """Add cashiers/admins/owners and set their login PIN, without needing a
    developer to reach into the database by hand every time someone new
    joins the till. Everyone can see the list, since a cashier glancing at
    who's on shift is harmless. Adding staff or activating/deactivating
    someone needs Admin or Owner; resetting a PIN is Owner-only - a PIN is
    how every account is protected, so only the Owner can hand out a new
    one, rather than any Admin being able to lock a cashier out (or back
    in) of their own account.

    Every sensitive change made here (adding staff, resetting a PIN,
    activating/deactivating) is written to the Activity Log (audit.py) with
    who did it, and a best-effort notification (notifications.py) is sent
    to the Owner so nothing has to be discovered after the fact. Adding
    staff or resetting a PIN also messages that PERSON directly (their new
    PIN, by email/SMS/WhatsApp) if they have an email or phone on file -
    the Owner's notification says who made the change, theirs tells them
    what changed and what their new PIN is."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Staff / Cashiers")

        self.hint = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, wraplength=700,
        )
        self.hint.pack(anchor="w", padx=24, pady=(0, 8))

        # grid, not pack/side="left" - four buttons shrink to fit a narrower
        # window or a scaled-up-DPI screen instead of the last one or two
        # running off the edge.
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24)
        for col in range(4):
            top.grid_columnconfigure(col, weight=1, uniform="staff_top")
        styled_button(top, "Refresh", self.refresh, kind="secondary").grid(
            row=0, column=0, sticky="ew", padx=(0, 4))
        self.add_btn = styled_button(top, "Add Staff", self.open_add_dialog, kind="primary")
        self.add_btn.grid(row=0, column=1, sticky="ew", padx=4)
        self.pin_btn = styled_button(top, "Reset PIN", self.open_reset_pin_dialog, kind="secondary")
        self.pin_btn.grid(row=0, column=2, sticky="ew", padx=4)
        self.toggle_btn = styled_button(top, "Activate / Deactivate", self.toggle_active, kind="danger")
        self.toggle_btn.grid(row=0, column=3, sticky="ew", padx=(4, 0))

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24, pady=10)
        self.tree = make_treeview(
            tree_frame, ["Name", "Role", "Status"], {"Name": 220, "Role": 120, "Status": 120},
        )
        self.tree.master_frame.pack(fill="both", expand=True)

    def on_show(self):
        self.refresh()

    def _current_role(self) -> str | None:
        if not self.app.logged_in:
            return None
        row = self.conn.execute(
            "SELECT role FROM staff WHERE id=?", (self.app.current_staff_id,)
        ).fetchone()
        return row["role"] if row else None

    def _is_admin(self) -> bool:
        """True for Admin OR Owner - Owner is a superset of Admin's
        privileges, so anywhere Admin can manage staff, Owner can too."""
        return self._current_role() in ("admin", "owner")

    def _is_owner(self) -> bool:
        return self._current_role() == "owner"

    def _any_owner_exists(self) -> bool:
        return self.conn.execute("SELECT 1 FROM staff WHERE role='owner' LIMIT 1").fetchone() is not None

    def _actor_label(self) -> str:
        row = self.conn.execute(
            "SELECT name, role FROM staff WHERE id=?", (self.app.current_staff_id,)
        ).fetchone()
        if not row:
            return "Someone"
        return f"{row['role'].capitalize()} {row['name']}"

    def refresh(self):
        is_admin = self._is_admin()
        is_owner = self._is_owner()
        if is_owner:
            hint_text = "Logged in as Owner - you can add staff, reset PINs, or deactivate someone below."
        elif is_admin:
            hint_text = ("Logged in as Admin - you can add staff or deactivate someone below. "
                         "Only the Owner can reset a PIN.")
        else:
            hint_text = ("Only Admin/Owner can add staff or deactivate someone (Owner-only for PIN resets) - "
                        "log in from the sidebar first. Anyone can view this list.")
        self.hint.configure(text=hint_text)
        self.add_btn.configure(state="normal" if is_admin else "disabled")
        self.toggle_btn.configure(state="normal" if is_admin else "disabled")
        self.pin_btn.configure(state="normal" if is_owner else "disabled")
        tree_clear(self.tree)
        for s in staff_module.list_staff(self.conn):
            tree_insert(
                self.tree,
                (s["name"], s["role"].capitalize(), "Active" if s["active"] else "Inactive"),
                iid=str(s["id"]),
            )

    def _require_admin(self) -> bool:
        if self._is_admin():
            return True
        messagebox.showwarning("Admin required", "Log in as Admin or Owner (sidebar) to manage staff.")
        return False

    def _require_owner(self) -> bool:
        if self._is_owner():
            return True
        messagebox.showwarning("Owner required", "Only the Owner can reset a PIN - log in as Owner (sidebar).")
        return False

    def open_add_dialog(self):
        if not self._require_admin():
            return
        # Only an existing Owner can create another Owner - unless there is
        # no Owner account anywhere yet, in which case anyone with Admin
        # rights can bootstrap the very first one. This stops a compromised
        # or dishonest Admin from quietly promoting themselves once a real
        # Owner already exists.
        can_offer_owner = self._is_owner() or not self._any_owner_exists()
        role_choices = ["cashier", "admin", "owner"] if can_offer_owner else ["cashier", "admin"]

        dialog = ctk.CTkToplevel(self)
        dialog.title("Add Staff")
        dialog.geometry("360x460")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Name:").pack(anchor="w", padx=16, pady=(16, 0))
        name_var = tk.StringVar()
        ctk.CTkEntry(dialog, textvariable=name_var, width=300).pack(padx=16)

        ctk.CTkLabel(dialog, text="Role:").pack(anchor="w", padx=16, pady=(12, 0))
        role_var = tk.StringVar(value="cashier")
        ctk.CTkOptionMenu(dialog, values=role_choices, variable=role_var, width=300).pack(padx=16)
        if not can_offer_owner:
            ctk.CTkLabel(
                dialog, text="Only an existing Owner can create another Owner account.",
                font=ctk.CTkFont(size=10), text_color=theme.TEXT_MUTED, wraplength=300,
            ).pack(anchor="w", padx=16, pady=(2, 0))

        ctk.CTkLabel(dialog, text="PIN (numbers, used to log in at the till):").pack(
            anchor="w", padx=16, pady=(12, 0))
        pin_var = tk.StringVar()
        ctk.CTkEntry(dialog, textvariable=pin_var, width=300).pack(padx=16)

        ctk.CTkLabel(dialog, text="Email (optional - lets us tell them their PIN directly):").pack(
            anchor="w", padx=16, pady=(12, 0))
        email_var = tk.StringVar()
        ctk.CTkEntry(dialog, textvariable=email_var, width=300).pack(padx=16)

        ctk.CTkLabel(dialog, text="Phone (optional - for SMS/WhatsApp, needs Twilio set up):").pack(
            anchor="w", padx=16, pady=(12, 0))
        phone_var = tk.StringVar()
        ctk.CTkEntry(dialog, textvariable=phone_var, width=300).pack(padx=16)

        def save():
            role = role_var.get()
            if role == "owner" and not can_offer_owner:
                # Defensive - shouldn't be reachable since "owner" isn't
                # even offered in this case, but never trust client state.
                messagebox.showerror("Not allowed", "Only an existing Owner can create another Owner.")
                return
            pin_saved = pin_var.get().strip()
            try:
                new_id = staff_module.add_staff(
                    self.conn, name_var.get(), role, pin_saved,
                    email=email_var.get(), phone=phone_var.get(),
                )
            except ValueError as e:
                messagebox.showerror("Cannot add staff", str(e))
                return
            name_saved = name_var.get().strip()
            audit.record(
                self.conn, actor_staff_id=self.app.current_staff_id, action="staff.add",
                target_staff_id=new_id, details=f"role={role}",
            )
            notifications.send(
                "SmartGrocer: staff added",
                f"{self._actor_label()} added a new {role} account: {name_saved}.",
            )
            target_email = email_var.get().strip() or None
            target_phone = phone_var.get().strip() or None
            if target_email or target_phone:
                notifications.send(
                    "SmartGrocer: welcome",
                    f"You've been added to SmartGrocer as {role} by {self._actor_label()}. "
                    f"Your login PIN is: {pin_saved}",
                    to_email=target_email, to_phone=target_phone,
                )
            dialog.destroy()
            self.refresh()

        styled_button(dialog, "Save", save, kind="primary", width=140).pack(pady=20)

    def open_reset_pin_dialog(self):
        if not self._require_owner():
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No staff selected", "Select a staff member first.")
            return
        staff_id = int(sel[0])
        target = self.conn.execute("SELECT name, email, phone FROM staff WHERE id=?", (staff_id,)).fetchone()
        target_name = target["name"] if target else f"staff#{staff_id}"
        target_email = (target["email"] if target else None) or None
        target_phone = (target["phone"] if target else None) or None

        dialog = ctk.CTkToplevel(self)
        dialog.title("Reset PIN")
        dialog.geometry("320x180")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="New PIN:").pack(anchor="w", padx=16, pady=(16, 0))
        pin_var = tk.StringVar()
        ctk.CTkEntry(dialog, textvariable=pin_var, width=280).pack(padx=16)

        def save():
            new_pin = pin_var.get().strip()
            try:
                staff_module.update_pin(self.conn, staff_id, new_pin)
            except ValueError as e:
                messagebox.showerror("Cannot update PIN", str(e))
                return
            audit.record(
                self.conn, actor_staff_id=self.app.current_staff_id, action="staff.pin_reset",
                target_staff_id=staff_id,
            )
            # Two different messages: the Owner's own copy says WHO changed
            # it (there's only one Owner, so this is really a self-record,
            # but it still lands in their inbox/phone as a live alert); the
            # target's copy says WHAT changed and gives them the new PIN.
            notifications.send(
                "SmartGrocer: PIN changed",
                f"{self._actor_label()} changed the PIN for {target_name}.",
            )
            if target_email or target_phone:
                notifications.send(
                    "SmartGrocer: your PIN was changed",
                    f"Your SmartGrocer PIN was changed by {self._actor_label()}. Your new PIN is: {new_pin}\n\n"
                    "If you didn't expect this, tell the shop owner.",
                    to_email=target_email, to_phone=target_phone,
                )
            dialog.destroy()
            messagebox.showinfo("PIN updated", "The PIN has been changed.")

        styled_button(dialog, "Save", save, kind="primary", width=140).pack(pady=16)

    def toggle_active(self):
        if not self._require_admin():
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No staff selected", "Select a staff member first.")
            return
        staff_id = int(sel[0])
        row = self.conn.execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone()
        if row is None:
            return
        if row["active"] and staff_id == self.app.current_staff_id:
            messagebox.showerror("Cannot deactivate", "You cannot deactivate the account you're currently logged in as.")
            return
        new_active = not row["active"]
        staff_module.set_active(self.conn, staff_id, new_active)
        audit.record(
            self.conn, actor_staff_id=self.app.current_staff_id,
            action="staff.activate" if new_active else "staff.deactivate",
            target_staff_id=staff_id,
        )
        notifications.send(
            f"SmartGrocer: staff {'activated' if new_active else 'deactivated'}",
            f"{self._actor_label()} {'activated' if new_active else 'deactivated'} {row['name']}.",
        )
        self.refresh()


# --------------------------------------------------------------------------- #
# Multi-till networking
# --------------------------------------------------------------------------- #

class NetworkScreen(BaseScreen):
    """Connect more than one till to the same live data, wired or
    wireless - one PC (the "Main Till") keeps the real database and shares
    it over the shop's network; any other PC ("Cashier Till") connects to
    it here instead of keeping its own separate copy. See netserver.py /
    netclient.py for how the connection itself works; this screen is just
    the on/off switch and pairing UI for it, in the same spirit as the
    Phone Scanner dialog on the POS screen."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Network / Multi-Till")
        self.hint = ctk.CTkLabel(
            self, text="Connect more than one till to the same stock and sales data - wired Ethernet "
                       "or Wi-Fi both work, this only cares that the tills are on the same network.",
            font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, wraplength=760, justify="left",
        )
        self.hint.pack(anchor="w", padx=24, pady=(0, 10))

        self.status_card = ctk.CTkFrame(self, fg_color=theme.CARD_BG_ALT, corner_radius=10)
        self.status_card.pack(fill="x", padx=24, pady=(0, 14))
        self.status_label = ctk.CTkLabel(
            self.status_card, text="", font=ctk.CTkFont(size=14, weight="bold"),
            text_color=theme.NAVY_DARK, justify="left", wraplength=700,
        )
        self.status_label.pack(anchor="w", padx=16, pady=(14, 4))
        self.status_detail = ctk.CTkLabel(
            self.status_card, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED,
            justify="left", wraplength=700,
        )
        self.status_detail.pack(anchor="w", padx=16, pady=(0, 14))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=24)
        self.share_btn = styled_button(btn_row, "Share This Till's Data", self.start_sharing, kind="primary")
        self.share_btn.pack(side="left")
        self.stop_share_btn = styled_button(btn_row, "Stop Sharing", self.stop_sharing, kind="danger")
        self.stop_share_btn.pack(side="left", padx=8)
        self.connect_btn = styled_button(btn_row, "Connect to Another Till", self.open_connect_dialog,
                                          kind="secondary")
        self.connect_btn.pack(side="left", padx=8)
        self.disconnect_btn = styled_button(btn_row, "Disconnect (Use This PC's Own Data)",
                                             self.disconnect, kind="danger")
        self.disconnect_btn.pack(side="left", padx=8)

        warning_card = ctk.CTkFrame(self, fg_color=theme.CARD_BG_ALT, corner_radius=8)
        warning_card.pack(fill="x", padx=24, pady=(16, 0))
        ctk.CTkLabel(
            warning_card,
            text="One till has to be the \"Main Till\" - the one whose database is the real one - and "
                 "every other till connects to it as a \"Cashier Till\". Pick whichever PC is least "
                 "likely to be turned off during the day. The first time another till connects, its "
                 "browser/app will show a one-time \"connection not private\" warning, same as the "
                 "phone-scanner feature - that's expected for a private shop network, not a problem.",
            text_color=theme.TEXT_DARK, font=ctk.CTkFont(size=11), wraplength=740, justify="left",
        ).pack(padx=14, pady=12)

    def on_show(self):
        self.refresh()

    def refresh(self):
        is_client = self.app.is_client_till()
        server = self.app.net_server

        if is_client:
            host = self.app.till_config.get("server_host")
            port = self.app.till_config.get("server_port")
            reachable = self.app.conn.ping() if hasattr(self.app.conn, "ping") else False
            self.status_label.configure(
                text=f"Cashier Till - connected to {host}:{port}" + ("" if reachable else " (unreachable right now)")
            )
            self.status_detail.configure(
                text="This till is using the Main Till's shared data. Disconnect to go back to a "
                     "database that only this PC uses."
                if reachable else
                "Can't reach the Main Till right now - check it's turned on and both PCs are on the "
                "same network. This till may be showing a temporary local fallback copy in the "
                "meantime (see the warning shown when it lost the connection)."
            )
        elif server is not None and server.running:
            self.status_label.configure(text="Main Till - sharing this PC's data")
            self.status_detail.configure(
                text=f"Address: {server.url}    Pairing code: {server.pairing_code}\n"
                     f"Connected cashier tills right now: {server.connected_till_count}\n"
                     "On each other till, open the Network screen -> \"Connect to Another Till\" and "
                     "enter this PC's address and pairing code."
            )
        else:
            self.status_label.configure(text="Standalone - this till's data isn't shared")
            self.status_detail.configure(
                text="This is the normal single-till setup: everything stays on this PC. Use "
                     "\"Share This Till's Data\" to let other cashier tills connect to it, or "
                     "\"Connect to Another Till\" if this PC should use another till's data instead."
            )

        self.share_btn.configure(state="disabled" if (is_client or (server and server.running)) else "normal")
        self.stop_share_btn.configure(state="normal" if (server and server.running) else "disabled")
        self.connect_btn.configure(state="disabled" if (is_client or (server and server.running)) else "normal")
        self.disconnect_btn.configure(state="normal" if is_client else "disabled")

    def start_sharing(self):
        try:
            self.app.start_sharing()
        except OSError as e:
            messagebox.showerror("Could not start sharing", str(e))
            return
        self.refresh()
        messagebox.showinfo(
            "Now sharing",
            f"This till is now the Main Till.\n\nAddress: {self.app.net_server.url}\n"
            f"Pairing code: {self.app.net_server.pairing_code}\n\n"
            "Enter these on each other till's Network screen (\"Connect to Another Till\").",
        )

    def stop_sharing(self):
        if self.app.net_server and self.app.net_server.connected_till_count > 0:
            if not messagebox.askyesno(
                "Cashier tills are connected",
                f"{self.app.net_server.connected_till_count} other till(s) are currently using this "
                "PC's data. Stopping sharing will disconnect them immediately - they won't be able to "
                "ring up sales until reconnected. Stop sharing anyway?",
            ):
                return
        self.app.stop_sharing()
        self.refresh()

    def open_connect_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Connect to Another Till")
        dialog.geometry("380x340")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog, text="Enter the address and pairing code shown on the Main Till's Network screen:",
            wraplength=340, justify="left",
        ).pack(anchor="w", padx=16, pady=(16, 10))

        ctk.CTkLabel(dialog, text="Main Till address (e.g. 192.168.1.5):").pack(anchor="w", padx=16)
        host_var = tk.StringVar()
        ctk.CTkEntry(dialog, textvariable=host_var).pack(fill="x", padx=16)

        ctk.CTkLabel(dialog, text="Port (leave as shown on the Main Till):").pack(anchor="w", padx=16, pady=(10, 0))
        port_var = tk.StringVar(value=str(netserver.DEFAULT_PORT))
        ctk.CTkEntry(dialog, textvariable=port_var).pack(fill="x", padx=16)

        ctk.CTkLabel(dialog, text="Pairing code:").pack(anchor="w", padx=16, pady=(10, 0))
        code_var = tk.StringVar()
        ctk.CTkEntry(dialog, textvariable=code_var).pack(fill="x", padx=16)

        def connect():
            host = host_var.get().strip()
            if not host:
                messagebox.showerror("Invalid input", "Enter the Main Till's address.")
                return
            try:
                port = int(port_var.get().strip())
            except ValueError:
                messagebox.showerror("Invalid input", "Port must be a number.")
                return
            try:
                self.app.connect_to_till(host, port, code_var.get().strip())
            except netclient.RemoteError as e:
                messagebox.showerror("Could not connect", str(e))
                return
            dialog.destroy()
            self.refresh()
            messagebox.showinfo("Connected", "This till is now using the Main Till's shared data.")

        styled_button(dialog, "Connect", connect, kind="primary", width=140).pack(pady=18)

    def disconnect(self):
        if not messagebox.askyesno(
            "Disconnect this till?",
            "This till will go back to using a database that only this PC can see, starting empty "
            "unless it had one before. Any sales made while connected stay on the Main Till - they "
            "won't be copied here. Continue?",
        ):
            return
        self.app.disconnect_from_till()
        self.refresh()


# --------------------------------------------------------------------------- #
# Activity log (Owner only)
# --------------------------------------------------------------------------- #

class ActivityLogScreen(BaseScreen):
    """View of every sensitive staff/account action ever taken - who added a
    cashier, who reset whose PIN, who deactivated whom or edited/removed an
    item, and when. This is the "who did that" answer without having to ask
    around - open to Admin as well as Owner, since an Admin managing staff
    day to day benefits from being able to check the same history. Deciding
    who gets NOTIFIED about these actions (the Notification Settings dialog,
    with the SMTP/Twilio credentials in it) stays Owner-only, though - that's
    a higher-trust configuration action than just reading the log."""

    COLUMNS = ["When", "Who", "Role", "Action", "Target", "Details"]
    ACTION_LABELS = {
        "staff.add": "Added staff",
        "staff.pin_reset": "Reset PIN",
        "staff.pin_self_change": "Changed own PIN",
        "staff.pin_forgot_reset": "Reset PIN (forgot)",
        "staff.activate": "Activated staff",
        "staff.deactivate": "Deactivated staff",
        "product.edit": "Edited item",
        "product.deactivate": "Removed item",
        "product.reactivate": "Restored item",
    }

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Activity Log")

        self.hint = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, wraplength=760, justify="left",
        )
        self.hint.pack(anchor="w", padx=24, pady=(0, 8))

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24)
        self.refresh_btn = styled_button(top, "Refresh", self.refresh, kind="secondary")
        self.refresh_btn.pack(side="left")
        self.notify_btn = styled_button(top, "Notification Settings", self.open_notification_settings, kind="secondary")
        self.notify_btn.pack(side="left", padx=8)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24, pady=10)
        self.tree = make_treeview(
            tree_frame, self.COLUMNS,
            {"When": 140, "Who": 140, "Role": 80, "Action": 150, "Target": 140, "Details": 160},
        )
        self.tree.master_frame.pack(fill="both", expand=True)

    def on_show(self):
        self.refresh()

    def _current_role(self) -> str | None:
        if not self.app.logged_in:
            return None
        row = self.conn.execute(
            "SELECT role FROM staff WHERE id=?", (self.app.current_staff_id,)
        ).fetchone()
        return row["role"] if row else None

    def _is_owner(self) -> bool:
        return self._current_role() == "owner"

    def _can_view(self) -> bool:
        return self._current_role() in ("admin", "owner")

    def refresh(self):
        can_view = self._can_view()
        is_owner = self._is_owner()
        self.hint.configure(
            text="Every add/deactivate/PIN-reset staff action and item edit/removal, newest first."
            if can_view else
            "Only Admin/Owner can view the Activity Log - log in from the sidebar first."
        )
        self.refresh_btn.configure(state="normal" if can_view else "disabled")
        self.notify_btn.configure(state="normal" if is_owner else "disabled")
        tree_clear(self.tree)
        if not can_view:
            return
        for row in audit.list_log(self.conn):
            tree_insert(
                self.tree,
                (
                    row["at"],
                    row["actor_name"],
                    row["actor_role"].capitalize(),
                    self.ACTION_LABELS.get(row["action"], row["action"]),
                    row["target_name"] or "",
                    row["details"] or "",
                ),
                iid=str(row["id"]),
            )

    def open_notification_settings(self):
        if not self._is_owner():
            messagebox.showwarning("Owner required", "Log in as Owner (sidebar) to change notification settings.")
            return

        config = notifications.load()

        dialog = ctk.CTkToplevel(self)
        dialog.title("Notification Settings")
        dialog.geometry("460x620")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        scroll = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        def section(text):
            ctk.CTkLabel(
                scroll, text=text, font=ctk.CTkFont(size=13, weight="bold"), text_color=theme.NAVY_DARK,
            ).pack(anchor="w", padx=12, pady=(14, 2))

        def field(label, key, show=None):
            ctk.CTkLabel(scroll, text=label, font=ctk.CTkFont(size=11)).pack(anchor="w", padx=12, pady=(6, 0))
            var = tk.StringVar(value=str(config.get(key, "") or ""))
            ctk.CTkEntry(scroll, textvariable=var, width=400, show=show).pack(padx=12)
            return var

        ctk.CTkLabel(
            scroll,
            text="When an Admin or Owner adds staff, resets a PIN, or activates/deactivates someone, "
                 "send a notification here. Email works right away with a Gmail app password. SMS/"
                 "WhatsApp need a paid Twilio account - leave those blank to skip them.",
            font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED, wraplength=400, justify="left",
        ).pack(anchor="w", padx=12, pady=(10, 0))

        section("Email")
        email_enabled_var = tk.BooleanVar(value=bool(config.get("email_enabled")))
        ctk.CTkCheckBox(scroll, text="Enable email notifications", variable=email_enabled_var).pack(
            anchor="w", padx=12, pady=(4, 0))
        smtp_host_var = field("SMTP host (usually smtp.gmail.com):", "smtp_host")
        smtp_port_var = field("SMTP port (usually 587):", "smtp_port")
        smtp_user_var = field("Sending Gmail address:", "smtp_user")
        smtp_pass_var = field("Gmail app password:", "smtp_app_password", show="*")
        owner_email_var = field("Owner's email (receives notifications):", "owner_email")

        section("SMS / WhatsApp (Twilio - optional, paid)")
        sid_var = field("Twilio Account SID:", "twilio_account_sid")
        token_var = field("Twilio Auth Token:", "twilio_auth_token", show="*")
        from_sms_var = field("Twilio SMS number (e.g. +14155551234):", "twilio_from_sms")
        from_wa_var = field("Twilio WhatsApp sender (e.g. whatsapp:+14155238886):", "twilio_from_whatsapp")
        owner_phone_var = field("Owner's phone (receives SMS/WhatsApp, e.g. +94771234567):", "owner_phone")

        status_label = ctk.CTkLabel(scroll, text="", font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED)
        status_label.pack(anchor="w", padx=12, pady=(10, 0))

        def collect() -> dict:
            return {
                "email_enabled": bool(email_enabled_var.get()),
                "smtp_host": smtp_host_var.get().strip(),
                "smtp_port": int(smtp_port_var.get().strip() or 587) if smtp_port_var.get().strip().isdigit() else 587,
                "smtp_user": smtp_user_var.get().strip(),
                "smtp_app_password": smtp_pass_var.get(),
                "owner_email": owner_email_var.get().strip(),
                "twilio_account_sid": sid_var.get().strip(),
                "twilio_auth_token": token_var.get().strip(),
                "twilio_from_sms": from_sms_var.get().strip(),
                "twilio_from_whatsapp": from_wa_var.get().strip(),
                "owner_phone": owner_phone_var.get().strip(),
            }

        def save():
            notifications.save(collect())
            status_label.configure(text="Saved.", text_color=theme.TEXT_MUTED)

        def send_test():
            notifications.save(collect())
            sent = notifications.send("SmartGrocer: test notification", "This is a test notification from SmartGrocer.")
            if sent:
                status_label.configure(text=f"Saved and sent via: {', '.join(sent)}", text_color=theme.TEXT_MUTED)
            else:
                status_label.configure(
                    text="Saved, but nothing sent - check the settings above are filled in and correct.",
                    text_color=theme.TEXT_MUTED,
                )

        btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=16)
        styled_button(btn_row, "Save", save, kind="primary").pack(side="left")
        styled_button(btn_row, "Save & Send Test", send_test, kind="secondary").pack(side="left", padx=8)
