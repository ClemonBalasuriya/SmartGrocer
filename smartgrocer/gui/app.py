"""
SmartGrocer - main application window.

NOTE ON TESTING: this GUI is written for CustomTkinter but could not be
executed inside the sandbox this project was originally built in (no
display server / no tkinter available there - see README). It has been
reviewed carefully against the CustomTkinter/Tkinter APIs, and every
non-GUI module it calls into (db, pos, forecasting, promotions, association,
layout, reports) is independently unit-tested. Run `python main.py` on your
own machine (which needs a normal desktop Python with tkinter, which ships
with the standard Windows/Mac installer) to exercise the GUI itself, and
please report back anything that doesn't look right - screen layout bugs
are the most likely thing to have slipped through without a live display.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from .. import cash_drawer
from .. import db
from .. import staff as staff_module
from . import screens
from . import theme

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")  # base theme; brand colors are applied per-widget below

NAV_ITEMS = [
    ("📊  Dashboard", "dashboard", screens.DashboardScreen),
    ("🛒  POS / Checkout", "pos", screens.POSScreen),
    ("📦  Inventory", "inventory", screens.InventoryScreen),
    ("👥  Customers", "customers", screens.CustomersScreen),
    ("🚚  Suppliers", "suppliers", screens.SuppliersScreen),
    ("⏰  Promotions & Expiry", "promotions", screens.PromotionsScreen),
    ("📈  Forecasting", "forecasting", screens.ForecastingScreen),
    ("🔗  Bundle Recommendations", "bundles", screens.BundlesScreen),
    ("🗺️  Store Layout", "layout", screens.LayoutScreen),
    ("📄  Reports", "reports", screens.ReportsScreen),
    ("🧑‍💼  Staff", "staff", screens.StaffScreen),
]


class SmartGrocerApp(ctk.CTk):
    def __init__(self, db_path=None):
        super().__init__()
        self.title("SmartGrocer - POS & Decision Support System")
        self.geometry("1280x800")
        self.minsize(1000, 650)

        self.db_path = db_path or db.DEFAULT_DB_PATH
        self.conn = db.get_conn(self.db_path)
        db.init_db(self.conn)
        self.current_staff_id = 1  # default to Admin until someone logs in via the sidebar
        self.logged_in = False

        theme.apply_global_style()
        self.configure(fg_color=theme.BG_LIGHT)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=240, corner_radius=0, fg_color=theme.NAVY_DARK)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)

        try:
            from PIL import Image
            logo_img = Image.open(theme.LOGO_PATH)
            logo_ctk = ctk.CTkImage(light_image=logo_img, dark_image=logo_img, size=(72, 72))
            ctk.CTkLabel(self.sidebar, image=logo_ctk, text="").pack(pady=(28, 6))
        except Exception:
            pass  # logo is decorative - never block app startup over a missing/unreadable asset

        title = ctk.CTkLabel(self.sidebar, text="SmartGrocer", font=ctk.CTkFont(size=22, weight="bold"),
                              text_color=theme.TEXT_ON_NAVY)
        title.pack(pady=(0, 2), padx=20)
        subtitle = ctk.CTkLabel(self.sidebar, text="POS & Decision Support", font=ctk.CTkFont(size=12),
                                 text_color="#9FB0CC")
        subtitle.pack(pady=(0, 4), padx=20)
        brand = ctk.CTkLabel(self.sidebar, text="by Balasuriya Group", font=ctk.CTkFont(size=11, slant="italic"),
                              text_color="#7A8CAD")
        brand.pack(pady=(0, 18), padx=20)

        # --- Day status / cashier login - the daily open/close ritual every
        # till-based shop does, and what ties every sale to who actually rang
        # it up rather than whoever last happened to be picked from a list.
        self.status_frame = ctk.CTkFrame(self.sidebar, fg_color=theme.NAVY_DARKER, corner_radius=8)
        self.status_frame.pack(fill="x", padx=14, pady=(0, 14))
        self.cashier_label = ctk.CTkLabel(self.status_frame, text="🔒 Not logged in",
                                           font=ctk.CTkFont(size=12), text_color="#D6DEEC")
        self.cashier_label.pack(anchor="w", padx=10, pady=(10, 2))
        self.day_label = ctk.CTkLabel(self.status_frame, text="Day: CLOSED",
                                       font=ctk.CTkFont(size=12, weight="bold"), text_color="#F4A6A6")
        self.day_label.pack(anchor="w", padx=10, pady=(0, 8))
        status_btn_row = ctk.CTkFrame(self.status_frame, fg_color="transparent")
        status_btn_row.pack(fill="x", padx=8, pady=(0, 10))
        ctk.CTkButton(status_btn_row, text="Login / Open Day", height=28, font=ctk.CTkFont(size=11),
                      fg_color=theme.ACCENT_BLUE, hover_color=theme.ACCENT_BLUE_HOVER,
                      command=self.open_login_dialog).pack(fill="x", pady=2)
        ctk.CTkButton(status_btn_row, text="Close Day", height=28, font=ctk.CTkFont(size=11),
                      fg_color=theme.DANGER_RED, hover_color=theme.DANGER_RED_HOVER,
                      command=self.open_close_day_dialog).pack(fill="x", pady=2)

        divider = ctk.CTkFrame(self.sidebar, height=1, fg_color="#1E2D52")
        divider.pack(fill="x", padx=16, pady=(0, 12))

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for label, key, _cls in NAV_ITEMS:
            btn = ctk.CTkButton(
                self.sidebar, text=label, anchor="w", corner_radius=8, height=38,
                font=ctk.CTkFont(size=13),
                fg_color="transparent", text_color="#D6DEEC",
                hover_color=theme.NAVY_DARKER,
                command=lambda k=key: self.show_frame(k),
            )
            btn.pack(fill="x", padx=14, pady=3)
            self.nav_buttons[key] = btn

        self.container = ctk.CTkFrame(self, corner_radius=0, fg_color=theme.BG_LIGHT)
        self.container.grid(row=0, column=1, sticky="nsew")
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.frames: dict[str, ctk.CTkFrame] = {}
        for _label, key, cls in NAV_ITEMS:
            frame = cls(self.container, self)
            self.frames[key] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        self.show_frame("dashboard")
        self.refresh_day_status()

    def show_frame(self, key: str):
        frame = self.frames[key]
        frame.tkraise()
        for k, btn in self.nav_buttons.items():
            is_active = k == key
            btn.configure(
                fg_color=theme.ACCENT_BLUE if is_active else "transparent",
                text_color="#FFFFFF" if is_active else "#D6DEEC",
            )
        if hasattr(frame, "on_show"):
            frame.on_show()

    def refresh_day_status(self):
        staff = self.conn.execute("SELECT name FROM staff WHERE id=?", (self.current_staff_id,)).fetchone()
        if self.logged_in and staff:
            self.cashier_label.configure(text=f"🔓 {staff['name']}")
        else:
            self.cashier_label.configure(text="🔒 Not logged in")
        session = cash_drawer.get_open_session(self.conn)
        if session:
            self.day_label.configure(text=f"Day: OPEN (since {session['opened_at'][11:16]})",
                                      text_color="#8FD19E")
        else:
            self.day_label.configure(text="Day: CLOSED", text_color="#F4A6A6")

    def open_login_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Login")
        dialog.geometry("340x360")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        staff_names = [r["name"] for r in staff_module.list_staff(self.conn, active_only=True)]
        ctk.CTkLabel(dialog, text="Staff:").pack(anchor="w", padx=16, pady=(16, 0))
        staff_var = tk.StringVar(value=staff_names[0] if staff_names else "")
        ctk.CTkOptionMenu(dialog, values=staff_names, variable=staff_var, width=280).pack(padx=16)

        ctk.CTkLabel(dialog, text="PIN:").pack(anchor="w", padx=16, pady=(12, 0))
        pin_var = tk.StringVar()
        ctk.CTkEntry(dialog, textvariable=pin_var, width=280, show="*").pack(padx=16)

        already_open = cash_drawer.get_open_session(self.conn) is not None
        float_var = tk.StringVar(value="0")
        if already_open:
            ctk.CTkLabel(dialog, text="The day is already open - this just logs you in.",
                         text_color=theme.TEXT_MUTED, wraplength=280).pack(padx=16, pady=(14, 0))
        else:
            ctk.CTkLabel(dialog, text="Opening cash float (starting the day):").pack(
                anchor="w", padx=16, pady=(12, 0))
            ctk.CTkEntry(dialog, textvariable=float_var, width=280).pack(padx=16)

        def submit():
            row = self.conn.execute(
                "SELECT * FROM staff WHERE name=? AND active=1", (staff_var.get(),)
            ).fetchone()
            if row is None or row["pin"] != pin_var.get().strip():
                messagebox.showerror("Login failed", "Incorrect PIN.")
                return
            if not already_open:
                try:
                    opening_float = float(float_var.get())
                except ValueError:
                    messagebox.showerror("Invalid amount", "Enter a number for the opening float.")
                    return
                cash_drawer.open_session(self.conn, row["id"], opening_float)
            self.current_staff_id = row["id"]
            self.logged_in = True
            self.refresh_day_status()
            dialog.destroy()

        ctk.CTkButton(dialog, text="Login", command=submit, fg_color=theme.SUCCESS_GREEN,
                      hover_color=theme.SUCCESS_GREEN_HOVER).pack(pady=20)

    def open_close_day_dialog(self):
        session = cash_drawer.get_open_session(self.conn)
        if session is None:
            messagebox.showinfo("No open day", "There is no open cash drawer session to close.")
            return
        expected = cash_drawer.expected_cash(self.conn, session)

        dialog = ctk.CTkToplevel(self)
        dialog.title("Close Day")
        dialog.geometry("360x260")
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text=f"Expected cash in drawer: LKR {expected:,.2f}",
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=theme.NAVY_DARK).pack(pady=(16, 10))
        ctk.CTkLabel(dialog, text="Counted cash:").pack(anchor="w", padx=20)
        counted_var = tk.StringVar(value=f"{expected:.2f}")
        ctk.CTkEntry(dialog, textvariable=counted_var, width=300).pack(padx=20)

        def submit():
            try:
                counted = float(counted_var.get())
            except ValueError:
                messagebox.showerror("Invalid amount", "Enter a number.")
                return
            result = cash_drawer.close_session(self.conn, session["id"], counted)
            variance = result["variance"]
            msg = (f"Expected: LKR {result['expected_cash']:,.2f}\n"
                   f"Counted:  LKR {result['counted_cash']:,.2f}\n"
                   f"Variance: LKR {variance:,.2f}")
            if abs(variance) > 0.01:
                messagebox.showwarning("Day closed - cash variance found", msg)
            else:
                messagebox.showinfo("Day closed - cash matches", msg)
            self.logged_in = False
            self.refresh_day_status()
            dialog.destroy()

        ctk.CTkButton(dialog, text="Close Day", command=submit, fg_color=theme.DANGER_RED,
                      hover_color=theme.DANGER_RED_HOVER).pack(pady=20)


def run(db_path=None):
    app = SmartGrocerApp(db_path)
    app.mainloop()


if __name__ == "__main__":
    run()
