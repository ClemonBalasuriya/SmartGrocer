"""
SmartGrocer - screen (page) implementations. See app.py's module docstring
for the testing caveat on this GUI layer.
"""

from __future__ import annotations

import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk

import customtkinter as ctk

from .. import association, forecasting, layout, pos, promotions, reports
from .. import db as db_module


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
    return tree


class BaseScreen(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app

    @property
    def conn(self):
        return self.app.conn

    def on_show(self):
        pass

    def header(self, text: str):
        lbl = ctk.CTkLabel(self, text=text, font=ctk.CTkFont(size=20, weight="bold"))
        lbl.pack(anchor="w", padx=24, pady=(20, 10))


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
            card = ctk.CTkFrame(self.cards_frame, corner_radius=10)
            card.grid(row=i // 3, column=i % 3, padx=10, pady=10, sticky="nsew")
            self.cards_frame.grid_columnconfigure(i % 3, weight=1)
            ctk.CTkLabel(card, text=label, font=ctk.CTkFont(size=12), text_color="gray").pack(
                anchor="w", padx=16, pady=(14, 0))
            value_lbl = ctk.CTkLabel(card, text="-", font=ctk.CTkFont(size=26, weight="bold"))
            value_lbl.pack(anchor="w", padx=16, pady=(0, 14))
            self.card_labels[key] = value_lbl

        top_seller_frame = ctk.CTkFrame(self, corner_radius=10)
        top_seller_frame.pack(fill="x", padx=24, pady=(0, 10))
        self.top_seller_label = ctk.CTkLabel(top_seller_frame, text="Top seller (30d): -",
                                              font=ctk.CTkFont(size=14))
        self.top_seller_label.pack(anchor="w", padx=16, pady=12)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=10)
        ctk.CTkButton(btn_row, text="Refresh", command=self.refresh).pack(side="left", padx=(0, 10))
        ctk.CTkButton(btn_row, text="Rebuild synthetic demo data", fg_color="#b23b3b",
                      hover_color="#8a2c2c", command=self.rebuild_demo_data).pack(side="left")

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

class POSScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("POS / Checkout")
        self.cart: list[dict] = []

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24)

        ctk.CTkLabel(top, text="Scan barcode or search item:").grid(row=0, column=0, sticky="w")
        self.search_var = tk.StringVar()
        search_entry = ctk.CTkEntry(top, textvariable=self.search_var, width=280,
                                     placeholder_text="barcode / name (English or Sinhala)")
        search_entry.grid(row=1, column=0, padx=(0, 10), pady=6)
        search_entry.bind("<Return>", lambda e: self.do_search())
        ctk.CTkButton(top, text="Search", width=90, command=self.do_search).grid(row=1, column=1)

        ctk.CTkLabel(top, text="Price tier:").grid(row=0, column=2, padx=(30, 0), sticky="w")
        self.tier_var = tk.StringVar(value="cash")
        ctk.CTkOptionMenu(top, values=["cash", "credit", "wholesale"], variable=self.tier_var).grid(
            row=1, column=2, padx=(30, 0))

        ctk.CTkLabel(top, text="Staff:").grid(row=0, column=3, padx=(20, 0), sticky="w")
        self.staff_var = tk.StringVar(value="Admin")
        staff_names = [r["name"] for r in self.conn.execute("SELECT name FROM staff").fetchall()] or ["Admin"]
        ctk.CTkOptionMenu(top, values=staff_names, variable=self.staff_var).grid(row=1, column=3, padx=(20, 0))

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
        ctk.CTkLabel(qty_row, text="Qty:").pack(side="left")
        self.qty_var = tk.StringVar(value="1")
        ctk.CTkEntry(qty_row, textvariable=self.qty_var, width=70).pack(side="left", padx=8)
        ctk.CTkButton(qty_row, text="Add to Cart", command=self.add_selected_to_cart).pack(side="left")

        cart_frame = ctk.CTkFrame(self, fg_color="transparent")
        cart_frame.pack(fill="both", expand=True, padx=24, pady=(6, 0))
        self.cart_tree = make_treeview(
            cart_frame, ["Item", "Qty", "Unit Price", "Line Total"],
            {"Item": 320, "Qty": 80, "Unit Price": 100, "Line Total": 110},
        )
        self.cart_tree.master_frame.pack(fill="both", expand=True)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=24, pady=12)
        ctk.CTkButton(bottom, text="Remove Selected Line", command=self.remove_selected_line).pack(side="left")
        ctk.CTkButton(bottom, text="Clear Cart", command=self.clear_cart).pack(side="left", padx=8)
        self.total_label = ctk.CTkLabel(bottom, text="Net Total: LKR 0.00", font=ctk.CTkFont(size=16, weight="bold"))
        self.total_label.pack(side="left", padx=30)
        ctk.CTkButton(bottom, text="Checkout", fg_color="#2e7d32", hover_color="#1b5e20",
                      command=self.checkout).pack(side="right")

        self._search_results: list = []

    def do_search(self):
        query = self.search_var.get().strip()
        if not query:
            return
        self._search_results = pos.search_products(self.conn, query)
        self.results_tree.delete(*self.results_tree.get_children())
        for p in self._search_results:
            stock = pos.get_stock_on_hand(self.conn, p["id"])
            self.results_tree.insert("", "end", iid=str(p["id"]),
                                      values=(p["code"], p["name_en"], f"{p['cash_price']:.2f}", f"{stock:.0f}"))

    def add_selected_to_cart(self):
        sel = self.results_tree.selection()
        if not sel:
            messagebox.showwarning("No item selected", "Search and select an item first.")
            return
        product_id = int(sel[0])
        try:
            qty = float(self.qty_var.get())
        except ValueError:
            messagebox.showerror("Invalid quantity", "Quantity must be a number.")
            return
        p = self.conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        tier_field = {"cash": "cash_price", "credit": "credit_price", "wholesale": "wholesale_price"}[self.tier_var.get()]
        self.cart.append({"product_id": product_id, "name": p["name_en"], "qty": qty, "unit_price": p[tier_field]})
        self.refresh_cart()

    def refresh_cart(self):
        self.cart_tree.delete(*self.cart_tree.get_children())
        total = 0.0
        for i, line in enumerate(self.cart):
            line_total = line["qty"] * line["unit_price"]
            total += line_total
            self.cart_tree.insert("", "end", iid=str(i),
                                   values=(line["name"], line["qty"], f"{line['unit_price']:.2f}", f"{line_total:.2f}"))
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
        staff_row = self.conn.execute("SELECT id FROM staff WHERE name=?", (self.staff_var.get(),)).fetchone()
        staff_id = staff_row["id"] if staff_row else self.app.current_staff_id
        cart_lines = [pos.CartLine(product_id=c["product_id"], qty=c["qty"]) for c in self.cart]
        try:
            result = pos.create_invoice(self.conn, staff_id=staff_id, cart=cart_lines,
                                         price_tier=self.tier_var.get())
        except pos.InsufficientStockError as e:
            messagebox.showerror("Insufficient stock", str(e))
            return
        messagebox.showinfo("Sale complete", f"Invoice {result.invoice_no}\nNet total: LKR {result.net_total:,.2f}")
        self.clear_cart()
        self.results_tree.delete(*self.results_tree.get_children())


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #

class InventoryScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Inventory")

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=24)
        ctk.CTkButton(btn_row, text="Refresh", command=self.refresh).pack(side="left")
        ctk.CTkButton(btn_row, text="Export CSV", command=self.export).pack(side="left", padx=8)
        ctk.CTkButton(btn_row, text="Receive Stock (GRN)", command=self.open_grn_dialog).pack(side="left", padx=8)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24, pady=10)
        self.tree = make_treeview(
            tree_frame, ["Code", "Name", "Category", "On Hand", "Reorder Level", "Status"],
            {"Code": 90, "Name": 260, "Category": 150, "On Hand": 90, "Reorder Level": 100, "Status": 100},
        )
        self.tree.master_frame.pack(fill="both", expand=True)
        self.tree.tag_configure("low", background="#fde2e2")

    def on_show(self):
        self.refresh()

    def refresh(self):
        self._rows = reports.stock_summary(self.conn)
        self.tree.delete(*self.tree.get_children())
        for r in self._rows:
            tag = "low" if r["below_reorder"] else ""
            self.tree.insert("", "end", values=(r["code"], r["name_en"], r["category"], f"{r['on_hand']:.0f}",
                                                 r["reorder_level"], "LOW STOCK" if r["below_reorder"] else "OK"),
                              tags=(tag,))

    def export(self):
        path = reports.export_csv(self._rows, db_module.ensure_output_dir() / "inventory_stock_summary.csv") \
            if hasattr(self, "_rows") else None
        if path:
            messagebox.showinfo("Exported", f"Saved to {path}")

    def open_grn_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Receive Stock (GRN)")
        dialog.geometry("420x320")

        products = self.conn.execute("SELECT id, code, name_en FROM products WHERE active=1 ORDER BY name_en").fetchall()
        names = [f"{p['code']} - {p['name_en']}" for p in products]

        ctk.CTkLabel(dialog, text="Product:").pack(anchor="w", padx=16, pady=(16, 0))
        product_var = tk.StringVar(value=names[0] if names else "")
        ctk.CTkOptionMenu(dialog, values=names, variable=product_var, width=360).pack(padx=16)

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
                self.conn.execute(
                    """INSERT INTO stock_batches (product_id,batch_no,qty_received,qty_remaining,
                       received_date,expiry_date,cost_price) VALUES (?,?,?,?,?,?,?)""",
                    (product["id"], f"GRN-MANUAL-{date.today().isoformat()}", qty, qty,
                     date.today().isoformat(), expiry, cost),
                )
                self.conn.commit()
                dialog.destroy()
                self.refresh()
            except ValueError as e:
                messagebox.showerror("Invalid input", str(e))

        ctk.CTkButton(dialog, text="Save", command=save).pack(pady=18)


# --------------------------------------------------------------------------- #
# Promotions & Expiry
# --------------------------------------------------------------------------- #

class PromotionsScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Promotions & Expiry (Urgency Ranking)")

        ctk.CTkButton(self, text="Refresh", command=self.refresh).pack(anchor="w", padx=24)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24, pady=10)
        columns = ["Product", "Qty", "Expiry", "Days Left", "Alert", "Urgency",
                   "Discount %", "Suggested Price", "Est. Clearance"]
        widths = {"Product": 220, "Qty": 60, "Expiry": 90, "Days Left": 80, "Alert": 100,
                  "Urgency": 80, "Discount %": 90, "Suggested Price": 110, "Est. Clearance": 110}
        self.tree = make_treeview(tree_frame, columns, widths)
        self.tree.master_frame.pack(fill="both", expand=True)
        self.tree.tag_configure("overdue", background="#f4a6a6")
        self.tree.tag_configure("critical_3day", background="#fbd3a2")
        self.tree.tag_configure("warning_7day", background="#fbf3a2")

    def on_show(self):
        self.refresh()

    def refresh(self):
        rows = promotions.compute_promotions(self.conn)
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            self.tree.insert("", "end", values=(
                r["name"], f"{r['qty_remaining']:.0f}", r["expiry_date"], r["days_to_expiry"],
                r["alert_tier"] or "-", r["urgency"], f"{r['suggested_discount_pct']}%",
                f"{r['suggested_price']:.2f}", r["projected_clearance_date"] or "-",
            ), tags=(r["alert_tier"] or "",))


# --------------------------------------------------------------------------- #
# Forecasting
# --------------------------------------------------------------------------- #

class ForecastingScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Demand Forecasting")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24)
        ctk.CTkLabel(top, text="Product:").pack(side="left")
        products = self.conn.execute("SELECT id, name_en FROM products WHERE active=1 ORDER BY name_en").fetchall()
        self._products = products
        names = [p["name_en"] for p in products]
        self.product_var = tk.StringVar(value=names[0] if names else "")
        ctk.CTkOptionMenu(top, values=names, variable=self.product_var, width=280).pack(side="left", padx=8)
        ctk.CTkButton(top, text="Run Forecast", command=self.run_forecast).pack(side="left", padx=8)
        ctk.CTkButton(top, text="Generate Purchase List (all products)",
                      command=self.run_purchase_list).pack(side="left", padx=20)

        self.status_label = ctk.CTkLabel(self, text="", text_color="gray")
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
        self.status_label.configure(text="Running rolling-origin cross-validation...")
        self.update_idletasks()
        result = forecasting.select_best_model(self.conn, product["id"])

        self.tree.delete(*self.tree.get_children())
        if not result["model_scores"] and result["forecast"] is None:
            self.status_label.configure(text="Not enough sales history for this product yet (need 30+ days).")
            return

        for name, score in sorted(result["model_scores"].items(), key=lambda kv: kv[1]):
            marker = "  <- selected" if name == result["best_model"] else ""
            self.tree.insert("", "end", values=(f"nRMSE: {name}{marker}", f"{score:.3f}"))
        if result.get("future_dates") is not None:
            for d, v in zip(result["future_dates"], result["forecast"]):
                self.tree.insert("", "end", values=(d.date().isoformat(), f"{v:.1f}"))

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
        self.status_label.configure(text="Generating purchase list across all products (may take a few seconds)...")
        self.update_idletasks()
        rows = forecasting.generate_purchase_list(self.conn)
        self.tree.delete(*self.tree.get_children())
        self.tree.configure(columns=["Product", "On Hand", "Forecast Demand", "Suggested Reorder", "Model"])
        for col in ["Product", "On Hand", "Forecast Demand", "Suggested Reorder", "Model"]:
            self.tree.heading(col, text=col)
        for r in rows:
            self.tree.insert("", "end", values=(r["name"], r["on_hand"], r["forecast_demand_next_period"],
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
        ctk.CTkButton(top, text="Compute Bundles", command=self.compute).pack(side="left")
        self.status_label = ctk.CTkLabel(top, text="min support 2%, min confidence 30%, min lift 1.1", text_color="gray")
        self.status_label.pack(side="left", padx=16)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24, pady=10)
        columns = ["Bundle", "Support", "Confidence", "Lift", "Sum Price", "Bundle Price", "Savings"]
        widths = {"Bundle": 320, "Support": 80, "Confidence": 90, "Lift": 70,
                  "Sum Price": 100, "Bundle Price": 100, "Savings": 90}
        self.tree = make_treeview(tree_frame, columns, widths)
        self.tree.master_frame.pack(fill="both", expand=True)

    def compute(self):
        rows = association.get_bundle_recommendations(self.conn)
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            bundle_name = " + ".join(r["antecedent"] + r["consequent"])
            self.tree.insert("", "end", values=(bundle_name, r["support"], r["confidence"], r["lift"],
                                                 f"{r['sum_price_lkr']:.2f}", f"{r['bundle_price_lkr']:.2f}",
                                                 f"{r['savings_lkr']:.2f}"))
        self.status_label.configure(text=f"{len(rows)} bundle recommendations")


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #

class LayoutScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Store Layout Optimisation")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24)
        ctk.CTkButton(top, text="Compute Layout", command=self.compute).pack(side="left")
        self.status_label = ctk.CTkLabel(top, text="", text_color="gray")
        self.status_label.pack(side="left", padx=16)

        self.image_label = ctk.CTkLabel(self, text="")
        self.image_label.pack(padx=24, pady=10)

    def compute(self):
        assignments = layout.compute_layout(self.conn)
        layout.persist_layout(self.conn, assignments)
        out_path = str(db_module.ensure_output_dir() / "planogram.png")
        layout.render_planogram(assignments, out_path)
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


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #

REPORT_OPTIONS = ["Best Sellers (30d)", "Stock Summary", "Low Stock Alert", "Waste Summary"]


class ReportsScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Reports")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24)
        self.report_var = tk.StringVar(value=REPORT_OPTIONS[0])
        ctk.CTkOptionMenu(top, values=REPORT_OPTIONS, variable=self.report_var).pack(side="left")
        ctk.CTkButton(top, text="Run Report", command=self.run_report).pack(side="left", padx=8)
        ctk.CTkButton(top, text="Export CSV", command=self.export).pack(side="left", padx=8)
        ctk.CTkButton(top, text="Backup Database", command=self.backup).pack(side="left", padx=20)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=24, pady=10)
        self.tree = make_treeview(tree_frame, ["A", "B", "C", "D"], {})
        self.tree.master_frame.pack(fill="both", expand=True)
        self._rows: list[dict] = []

    def _set_columns(self, columns: list[str]):
        self.tree.configure(columns=columns)
        for c in columns:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=140)

    def run_report(self):
        choice = self.report_var.get()
        self.tree.delete(*self.tree.get_children())
        if choice == "Best Sellers (30d)":
            rows = [dict(r) for r in reports.best_sellers(self.conn)]
            self._set_columns(["name_en", "category", "qty", "revenue"])
        elif choice == "Stock Summary":
            rows = reports.stock_summary(self.conn)
            self._set_columns(["code", "name_en", "category", "on_hand", "reorder_level"])
        elif choice == "Low Stock Alert":
            rows = reports.low_stock_alert(self.conn)
            self._set_columns(["code", "name_en", "on_hand", "reorder_level"])
        else:
            rows = reports.waste_summary(self.conn)
            self._set_columns(["name_en", "category", "events", "qty", "value_lost"])

        columns = self.tree["columns"]
        for r in rows:
            self.tree.insert("", "end", values=[r.get(c, "") for c in columns])
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
