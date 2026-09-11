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
from .. import db as db_module
from . import theme
from .theme import tree_clear, tree_insert


def make_treeview(parent, columns: list[str], widths: dict[str, int] | None = None) -> ttk.Treeview:
    widths = widths or {}
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=16)
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
        self.header("POS / Checkout")
        self.cart: list[dict] = []

        top = ctk.CTkFrame(self, fg_color="transparent")
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

        results_frame = ctk.CTkFrame(self, fg_color="transparent")
        results_frame.pack(fill="x", padx=24, pady=(6, 0))
        self.results_tree = make_treeview(
            results_frame, ["Code", "Name", "Cash Price", "Stock"],
            {"Code": 90, "Name": 300, "Cash Price": 100, "Stock": 80},
        )
        self.results_tree.master_frame.configure(height=140)
        self.results_tree.master_frame.pack(fill="x")
        self.results_tree.bind("<Double-1>", lambda e: self.add_selected_to_cart())

        qty_row = ctk.CTkFrame(self, fg_color="transparent")
        qty_row.pack(fill="x", padx=24, pady=6)
        ctk.CTkLabel(qty_row, text="Qty:", text_color=theme.TEXT_DARK).pack(side="left")
        self.qty_var = tk.StringVar(value="1")
        ctk.CTkEntry(qty_row, textvariable=self.qty_var, width=70).pack(side="left", padx=8)
        styled_button(qty_row, "Add to Cart", self.add_selected_to_cart).pack(side="left")

        ctk.CTkLabel(self, text="Quick Items (top sellers - tap to add):",
                     text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11)).pack(anchor="w", padx=24, pady=(4, 0))
        self.quick_items_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.quick_items_frame.pack(fill="x", padx=24, pady=(2, 6))
        self.tier_var.trace_add("write", lambda *a: self._refresh_quick_items())

        cart_frame = ctk.CTkFrame(self, fg_color="transparent")
        cart_frame.pack(fill="both", expand=True, padx=24, pady=(6, 0))
        self.cart_tree = make_treeview(
            cart_frame, ["Item", "Qty", "Unit Price", "Line Total"],
            {"Item": 320, "Qty": 80, "Unit Price": 100, "Line Total": 110},
        )
        self.cart_tree.master_frame.pack(fill="both", expand=True)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=24, pady=(4, 0))
        styled_button(bottom, "Remove Selected Line", self.remove_selected_line, kind="secondary").pack(side="left")
        styled_button(bottom, "Clear Cart", self.clear_cart, kind="secondary").pack(side="left", padx=8)
        self.total_label = ctk.CTkLabel(bottom, text="Net Total: LKR 0.00", font=ctk.CTkFont(size=16, weight="bold"),
                                         text_color=theme.NAVY_DARK)
        self.total_label.pack(side="left", padx=30)
        styled_button(bottom, "Checkout", self.checkout, kind="success", width=140,
                      height=40).pack(side="right")

        bottom2 = ctk.CTkFrame(self, fg_color="transparent")
        bottom2.pack(fill="x", padx=24, pady=(8, 12))
        styled_button(bottom2, "Hold Invoice", self.hold_invoice, kind="secondary").pack(side="left")
        styled_button(bottom2, "Resume Held", self.resume_invoice, kind="secondary").pack(side="left", padx=8)
        styled_button(bottom2, "Return / Refund", self.open_return_dialog, kind="danger").pack(side="left", padx=8)

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
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Inventory")

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=24)
        styled_button(btn_row, "Refresh", self.refresh, kind="secondary").pack(side="left")
        styled_button(btn_row, "Export CSV", self.export, kind="secondary").pack(side="left", padx=8)
        styled_button(btn_row, "Receive Stock (GRN)", self.open_grn_dialog, kind="primary").pack(side="left", padx=8)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24, pady=10)
        self.tree = make_treeview(
            tree_frame, ["Code", "Name", "Category", "On Hand", "Reorder Level", "Status"],
            {"Code": 90, "Name": 260, "Category": 150, "On Hand": 90, "Reorder Level": 100, "Status": 100},
        )
        self.tree.master_frame.pack(fill="both", expand=True)

    def on_show(self):
        self.refresh()

    def refresh(self):
        self._rows = reports.stock_summary(self.conn)
        tree_clear(self.tree)
        for r in self._rows:
            tag = "low" if r["below_reorder"] else None
            tree_insert(self.tree, (r["code"], r["name_en"], r["category"], f"{r['on_hand']:.0f}",
                                     r["reorder_level"], "LOW STOCK" if r["below_reorder"] else "OK"), tag=tag)

    def export(self):
        path = reports.export_csv(self._rows, db_module.ensure_output_dir() / "inventory_stock_summary.csv") \
            if hasattr(self, "_rows") else None
        if path:
            messagebox.showinfo("Exported", f"Saved to {path}")

    def open_grn_dialog(self):
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
                cost = float(cost_var.get()) if cost_var.get() else (
                    self.conn.execute("SELECT cost_price FROM products WHERE id=?", (product["id"],)).fetchone()["cost_price"])
                expiry = expiry_var.get().strip() or None
                if expiry:
                    date.fromisoformat(expiry)  # validates format
                supplier_id = None
                if supplier_var.get() != "(none)":
                    match = next((s for s in suppliers_list if s["name"] == supplier_var.get()), None)
                    supplier_id = match["id"] if match else None
                self.conn.execute(
                    """INSERT INTO stock_batches (product_id,batch_no,qty_received,qty_remaining,
                       received_date,expiry_date,cost_price,supplier_id) VALUES (?,?,?,?,?,?,?,?)""",
                    (product["id"], f"GRN-MANUAL-{date.today().isoformat()}", qty, qty,
                     date.today().isoformat(), expiry, cost, supplier_id),
                )
                self.conn.commit()
                dialog.destroy()
                self.refresh()
            except ValueError as e:
                messagebox.showerror("Invalid input", str(e))

        styled_button(dialog, "Save", save, kind="primary", width=140).pack(pady=18)


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
