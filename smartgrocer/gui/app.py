"""
SmartGrocer - main application window.

Professional POS layout:
  • Dark charcoal sidebar with teal accents
  • Brand header with logo, operator info, day-status pill
  • Grouped navigation: Sales, Stock, People, Analytics, System
  • Compact status bar at bottom
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

from .. import audit
from .. import cash_drawer
from .. import db
from .. import netclient
from .. import netserver
from .. import offline_sync
from .. import notifications
from .. import staff as staff_module
from .. import till_config
from . import screens
from . import theme

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# ---------------------------------------------------------------------------
# Navigation item definitions - (display_label, nav_key, screen_class, group)
# ---------------------------------------------------------------------------
NAV_ITEMS = [
    # Sales
    ("📊 Dashboard",    "dashboard",   screens.DashboardScreen,   "Sales"),
    ("🛒 Checkout",     "pos",         screens.POSScreen,          "Sales"),
    ("📄 Reports",      "reports",     screens.ReportsScreen,      "Sales"),
    # Stock
    ("📦 Inventory",    "inventory",   screens.InventoryScreen,    "Stock"),
    ("🚚 Suppliers",    "suppliers",   screens.SuppliersScreen,    "Stock"),
    # People
    ("👥 Customers",    "customers",   screens.CustomersScreen,    "People"),
    ("👤 Staff",        "staff",       screens.StaffScreen,        "People"),
    # Promotions
    ("🏷️ Offers",       "offers",      screens.OffersScreen,       "Promos"),
    ("⏰ Promotions",   "promotions",  screens.PromotionsScreen,   "Promos"),
    ("🔗 Bundles",      "bundles",     screens.BundlesScreen,      "Promos"),
    # Analytics
    ("📈 Forecast",     "forecasting", screens.ForecastingScreen,  "Analytics"),
    # System
    ("🌐 Network",      "network",     screens.NetworkScreen,      "System"),
    ("🛡️ Audit Log",    "audit",       screens.ActivityLogScreen,  "System"),
]

NAV_GROUPS = ["Sales", "Stock", "People", "Promos", "Analytics", "System"]

# Some NAV keys reference screens that may not exist if a backup removed them.
# Wrap in a try so the app still starts gracefully.
_SAFE_NAV_ITEMS: list[tuple] = []
for _item in NAV_ITEMS:
    _label, _key, _cls, _group = _item
    try:
        # Just ensure the class attribute exists
        _ = _cls
        _SAFE_NAV_ITEMS.append(_item)
    except AttributeError:
        pass
NAV_ITEMS = _SAFE_NAV_ITEMS


class SmartGrocerApp(ctk.CTk):
    def __init__(self, db_path=None):
        super().__init__()
        self.title("SmartGrocer POS — Balasuriya Group")
        self.geometry("1400x860")
        self.minsize(1100, 700)

        self.db_path = db_path or db.DEFAULT_DB_PATH
        self.net_server = None
        self.till_config = till_config.load()

        # ---- Database / connection setup (unchanged logic) -----------------
        if self.till_config["mode"] == "client":
            self.conn = offline_sync.OfflineConnection(
                self.till_config["server_host"], self.till_config["server_port"],
                self.till_config["pairing_code"], offline_sync.mirror_db_path(self.db_path),
            )
            if not self.conn.is_online:
                messagebox.showwarning(
                    "Can't reach the Main Till right now",
                    "This till will keep working offline - anything rung up now is queued locally and "
                    "will sync to the Main Till automatically once it's reachable again (Network screen "
                    "shows the queue, and has a \"Sync Now\" button).",
                )
        else:
            self.conn = db.get_conn(self.db_path)
            db.init_db(self.conn)

        self.current_staff_id = 1
        self.logged_in = False
        self.mobile_scan_server = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._schedule_offline_sync_check()

        theme.apply_global_style()
        self.configure(fg_color=theme.BG_MAIN)

        # ---- Root grid: sidebar | content area ----------------------------
        self.grid_columnconfigure(0, weight=0)   # sidebar - fixed width
        self.grid_columnconfigure(1, weight=1)   # content area - stretches
        self.grid_rowconfigure(0, weight=1)

        # ====================================================================
        # SIDEBAR
        # ====================================================================
        self.sidebar = ctk.CTkFrame(self, corner_radius=0, fg_color=theme.CHARCOAL_DARK, width=220)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_rowconfigure(99, weight=1)  # pushes bottom items down

        # --- Brand header inside sidebar ------------------------------------
        brand_frame = ctk.CTkFrame(self.sidebar, fg_color=theme.CHARCOAL_MED, corner_radius=0)
        brand_frame.pack(fill="x")

        # Logo
        logo_frame = ctk.CTkFrame(brand_frame, fg_color="transparent")
        logo_frame.pack(fill="x", padx=16, pady=(18, 10))
        try:
            from PIL import Image
            logo_img = Image.open(theme.LOGO_PATH)
            logo_ctk = ctk.CTkImage(light_image=logo_img, dark_image=logo_img, size=(40, 40))
            ctk.CTkLabel(logo_frame, image=logo_ctk, text="").pack(side="left", padx=(0, 10))
        except Exception:
            # Fallback circle if no logo
            ctk.CTkLabel(logo_frame, text="●", font=ctk.CTkFont(size=28), text_color=theme.TEAL).pack(
                side="left", padx=(0, 10))
        brand_text = ctk.CTkFrame(logo_frame, fg_color="transparent")
        brand_text.pack(side="left")
        ctk.CTkLabel(brand_text, text="SmartGrocer",
                     font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
                     text_color=theme.TEXT_ON_DARK).pack(anchor="w")
        ctk.CTkLabel(brand_text, text="Balasuriya Group",
                     font=ctk.CTkFont(size=10),
                     text_color="#7A8BA6").pack(anchor="w")

        # Thin teal divider
        ctk.CTkFrame(brand_frame, height=2, fg_color=theme.TEAL, corner_radius=0).pack(fill="x")

        # --- Operator / Day status strip -----------------------------------
        self._build_operator_strip()

        # Thin separator
        ctk.CTkFrame(self.sidebar, height=1, fg_color=theme.BORDER_DARK, corner_radius=0).pack(
            fill="x", pady=(0, 8))

        # --- Navigation groups ---------------------------------------------
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self._build_sidebar_nav()

        # --- Sidebar bottom: scanner shortcut + version --------------------
        bottom_strip = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        bottom_strip.pack(side="bottom", fill="x", padx=12, pady=12)

        ctk.CTkFrame(bottom_strip, height=1, fg_color=theme.BORDER_DARK, corner_radius=0).pack(
            fill="x", pady=(0, 10))

        ctk.CTkButton(
            bottom_strip, text="📷  Scanner", height=34, width=196,
            font=ctk.CTkFont(size=12),
            fg_color=theme.CHARCOAL_MED, hover_color=theme.CHARCOAL_HOVER,
            text_color=theme.TEXT_ON_DARK, border_width=1, border_color=theme.BORDER_DARK,
            corner_radius=8, command=lambda: self.frames["pos"].open_phone_scanner_dialog(),
        ).pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(bottom_strip, text="v2.0 — SmartGrocer POS",
                     font=ctk.CTkFont(size=9), text_color="#4A5568").pack()

        # ====================================================================
        # CONTENT AREA
        # ====================================================================
        self.container = ctk.CTkFrame(self, corner_radius=0, fg_color=theme.BG_MAIN)
        self.container.grid(row=0, column=1, sticky="nsew")
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        # Build all screens
        self.frames: dict[str, ctk.CTkFrame] = {}
        for _label, key, cls, _group in NAV_ITEMS:
            try:
                frame = cls(self.container, self)
                self.frames[key] = frame
                frame.grid(row=0, column=0, sticky="nsew")
            except Exception as exc:
                print(f"[SmartGrocer] Could not build screen '{key}': {exc}")

        self.show_frame("dashboard")
        self.refresh_day_status()

        if self.till_config["mode"] == "server":
            self.start_sharing(pairing_code=self.till_config.get("pairing_code"))

    # -----------------------------------------------------------------------
    # Sidebar builder helpers
    # -----------------------------------------------------------------------

    def _build_operator_strip(self):
        """Compact operator / day-status card inside the sidebar."""
        op_card = ctk.CTkFrame(self.sidebar, fg_color=theme.CHARCOAL_MED,
                               corner_radius=10, border_width=1, border_color=theme.BORDER_DARK)
        op_card.pack(fill="x", padx=12, pady=(10, 6))

        op_inner = ctk.CTkFrame(op_card, fg_color="transparent")
        op_inner.pack(fill="x", padx=10, pady=10)

        # Cashier row
        cashier_row = ctk.CTkFrame(op_inner, fg_color="transparent")
        cashier_row.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(cashier_row, text="OPERATOR", font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
                     text_color="#64748B").pack(side="left")
        self.cashier_label = ctk.CTkLabel(cashier_row, text="Not logged in",
                                           font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                                           text_color="#CBD5E1")
        self.cashier_label.pack(side="right")

        # Day status row
        day_row = ctk.CTkFrame(op_inner, fg_color="transparent")
        day_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(day_row, text="DAY STATUS", font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
                     text_color="#64748B").pack(side="left")
        self.day_label = ctk.CTkLabel(day_row, text="● CLOSED",
                                       font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                                       text_color=theme.DANGER_RED)
        self.day_label.pack(side="right")

        # Action buttons row
        btn_row = ctk.CTkFrame(op_inner, fg_color="transparent")
        btn_row.pack(fill="x")

        ctk.CTkButton(
            btn_row, text="Login", height=28, width=86,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color=theme.TEAL, hover_color=theme.TEAL_HOVER,
            text_color=theme.TEXT_ON_TEAL, corner_radius=6,
            command=self.open_login_dialog,
        ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            btn_row, text="Close Day", height=28, width=86,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            fg_color="transparent", hover_color=theme.CHARCOAL_HOVER,
            text_color="#EF4444", border_width=1, border_color="#DC2626",
            corner_radius=6, command=self.open_close_day_dialog,
        ).pack(side="left")

        # Change PIN link-style button (only shows when logged in)
        self.change_pin_btn = ctk.CTkButton(
            op_inner, text="🔑 Change my PIN", height=20, width=176,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            fg_color="transparent", hover_color=theme.CHARCOAL_HOVER,
            text_color="#94A3B8", corner_radius=4,
            command=self.open_change_my_pin_dialog, state="disabled",
        )
        self.change_pin_btn.pack(fill="x", pady=(6, 0))

    def _build_sidebar_nav(self):
        """Build grouped navigation buttons in the sidebar."""
        group_items: dict[str, list] = {g: [] for g in NAV_GROUPS}
        for label, key, _cls, group in NAV_ITEMS:
            if group in group_items:
                group_items[group].append((label, key))

        nav_scroll = ctk.CTkScrollableFrame(
            self.sidebar, fg_color="transparent", scrollbar_button_color=theme.CHARCOAL_HOVER,
        )
        nav_scroll.pack(fill="both", expand=True, padx=0, pady=0)

        for group in NAV_GROUPS:
            items = group_items.get(group, [])
            if not items:
                continue

            # Group label
            ctk.CTkLabel(nav_scroll, text=f"  {group.upper()}",
                         font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
                         text_color="#64748B").pack(anchor="w", padx=14, pady=(12, 4))

            for label, key in items:
                btn = ctk.CTkButton(
                    nav_scroll, text=f"  {label}", anchor="w",
                    corner_radius=8, height=36, width=196,
                    font=ctk.CTkFont(family="Segoe UI", size=12),
                    fg_color="transparent", hover_color=theme.CHARCOAL_HOVER,
                    text_color="#94A3B8",
                    command=lambda k=key: self.show_frame(k),
                )
                btn.pack(fill="x", padx=8, pady=2)
                self.nav_buttons[key] = btn

    # -----------------------------------------------------------------------
    # Screen navigation
    # -----------------------------------------------------------------------

    def show_frame(self, key: str):
        self.current_frame_key = key
        if key not in self.frames:
            return
        self.frames[key].tkraise()
        for k, btn in self.nav_buttons.items():
            is_active = k == key
            btn.configure(
                fg_color=theme.TEAL if is_active else "transparent",
                text_color=theme.TEXT_ON_TEAL if is_active else "#94A3B8",
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold" if is_active else "normal"),
            )
        frame = self.frames[key]
        if hasattr(frame, "on_show"):
            frame.on_show()

    def refresh_current_frame(self):
        """Re-run the current screen's on_show() - needed after login/logout."""
        frame = self.frames.get(getattr(self, "current_frame_key", None))
        if frame is not None and hasattr(frame, "on_show"):
            frame.on_show()

    # -----------------------------------------------------------------------
    # Day status
    # -----------------------------------------------------------------------

    def refresh_day_status(self):
        staff = self.conn.execute("SELECT name FROM staff WHERE id=?", (self.current_staff_id,)).fetchone()
        if self.logged_in and staff:
            self.cashier_label.configure(text=staff["name"], text_color=theme.TEAL)
        else:
            self.cashier_label.configure(text="Not logged in", text_color="#94A3B8")
        self.change_pin_btn.configure(state="normal" if self.logged_in else "disabled")
        session = cash_drawer.get_open_session(self.conn)
        if session:
            since = session["opened_at"][11:16]
            self.day_label.configure(text=f"● OPEN  {since}", text_color=theme.SUCCESS_GREEN)
        else:
            self.day_label.configure(text="● CLOSED", text_color=theme.DANGER_RED)

    # -----------------------------------------------------------------------
    # Dialogs (Login, Forgot PIN, Change PIN, Close Day) — logic unchanged,
    # visual style updated to use new theme colors.
    # -----------------------------------------------------------------------

    def open_login_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Cashier Login")
        screens.fit_dialog(dialog, 380, 460)
        dialog.configure(fg_color=theme.BG_MAIN)
        dialog.grab_set()

        # ---- Header banner ----
        hdr = ctk.CTkFrame(dialog, fg_color=theme.CHARCOAL_DARK, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="🔐  Cashier Login",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=theme.TEXT_ON_DARK).pack(pady=16, padx=20, anchor="w")

        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        # Form
        def _label(text):
            ctk.CTkLabel(body, text=text, font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=theme.TEXT_DARK).pack(anchor="w", padx=20, pady=(14, 4))

        staff_names = [r["name"] for r in staff_module.list_staff(self.conn, active_only=True)]
        _label("Select Staff Member")
        staff_var = tk.StringVar(value=staff_names[0] if staff_names else "")
        screens.SearchableDropdown(body, values=staff_names, variable=staff_var, width=320, label="Staff").pack(
            padx=20)

        _label("Enter PIN")
        pin_var = tk.StringVar()
        pin_entry = ctk.CTkEntry(body, textvariable=pin_var, width=320, show="●",
                                  placeholder_text="4-6 digit PIN",
                                  border_color=theme.BORDER, corner_radius=8)
        pin_entry.pack(padx=20)

        already_open = cash_drawer.get_open_session(self.conn) is not None
        float_var = tk.StringVar(value="0")
        if already_open:
            info = ctk.CTkFrame(body, fg_color=theme.TEAL_LIGHT, corner_radius=8)
            info.pack(fill="x", padx=20, pady=(14, 0))
            ctk.CTkLabel(info, text="ℹ  The day is already open — this just logs you in.",
                         text_color=theme.CHARCOAL_DARK, font=ctk.CTkFont(size=11),
                         wraplength=280).pack(padx=12, pady=10)
        else:
            _label("Opening Cash Float (LKR)")
            ctk.CTkEntry(body, textvariable=float_var, width=320, corner_radius=8).pack(padx=20)

        def submit():
            row = self.conn.execute(
                "SELECT * FROM staff WHERE name=? AND active=1", (staff_var.get(),)
            ).fetchone()
            if row is None or not staff_module.verify_pin(self.conn, row["id"], pin_var.get().strip()):
                messagebox.showerror("Login Failed", "Incorrect PIN. Please try again.")
                return
            if not already_open:
                try:
                    opening_float = float(float_var.get())
                except ValueError:
                    messagebox.showerror("Invalid Amount", "Enter a number for the opening float.")
                    return
                cash_drawer.open_session(self.conn, row["id"], opening_float)
            self.current_staff_id = row["id"]
            self.logged_in = True
            self.refresh_day_status()
            self.refresh_current_frame()
            dialog.destroy()

        dialog.bind("<Return>", lambda e: submit())

        # ---- Action buttons ----
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=16)
        ctk.CTkButton(btn_frame, text="Login", command=submit, width=200, height=40,
                      fg_color=theme.TEAL, hover_color=theme.TEAL_HOVER,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      text_color=theme.TEXT_ON_TEAL, corner_radius=8).pack()
        ctk.CTkButton(
            btn_frame, text="Forgot PIN?", fg_color="transparent", text_color=theme.TEAL,
            hover_color=theme.BG_MAIN, height=28, font=ctk.CTkFont(size=11),
            command=lambda: self.open_forgot_pin_dialog(staff_var.get()),
        ).pack(pady=(6, 0))

        dialog.after(150, pin_entry.focus_set)

    def open_forgot_pin_dialog(self, preselected_name: str = ""):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Forgot PIN — Reset")
        screens.fit_dialog(dialog, 380, 360)
        dialog.configure(fg_color=theme.BG_MAIN)
        dialog.grab_set()

        hdr = ctk.CTkFrame(dialog, fg_color=theme.CHARCOAL_DARK, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="🔒  PIN Reset",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=theme.TEXT_ON_DARK).pack(pady=16, padx=20, anchor="w")

        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(
            body, text="A new PIN will be emailed to the address saved for this account.",
            wraplength=320, justify="left", text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=20, pady=(16, 10))

        staff_names = [r["name"] for r in staff_module.list_staff(self.conn, active_only=True)]
        ctk.CTkLabel(body, text="Staff Member", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=theme.TEXT_DARK).pack(anchor="w", padx=20)
        default_name = preselected_name if preselected_name in staff_names else (staff_names[0] if staff_names else "")
        staff_var = tk.StringVar(value=default_name)
        screens.SearchableDropdown(body, values=staff_names, variable=staff_var, width=320, label="Staff").pack(
            padx=20, pady=(0, 12))

        status_label = ctk.CTkLabel(body, text="", font=ctk.CTkFont(size=11),
                                     text_color=theme.TEXT_MUTED, wraplength=320)
        status_label.pack(anchor="w", padx=20)

        def send_reset():
            row = self.conn.execute("SELECT * FROM staff WHERE name=? AND active=1", (staff_var.get(),)).fetchone()
            if row is None:
                status_label.configure(text="Account not found.", text_color=theme.DANGER_RED)
                return
            if not row["email"] and not row["phone"]:
                status_label.configure(
                    text="No email or phone saved for this account — ask an Admin to add one first.",
                    text_color=theme.DANGER_RED)
                return
            new_pin = staff_module.generate_temp_pin()
            sent = notifications.send(
                "SmartGrocer: your new PIN",
                f"Your new SmartGrocer PIN is: {new_pin}\n\nIf you didn't request this, contact the owner.",
                to_email=row["email"] or None, to_phone=row["phone"] or None,
            )
            if not sent:
                status_label.configure(text="Couldn't send — PIN unchanged. Check notification settings.", text_color=theme.DANGER_RED)
                return
            staff_module.update_pin(self.conn, row["id"], new_pin)
            audit.record(self.conn, actor_staff_id=row["id"], action="staff.pin_forgot_reset",
                         target_staff_id=row["id"], details="self-service forgot-PIN reset")
            notifications.send("SmartGrocer: PIN reset via Forgot PIN",
                               f"{row['name']} ({row['role']}) used Forgot PIN.")
            status_label.configure(text=f"✓ Sent via: {', '.join(sent)}. Check your inbox.",
                                    text_color=theme.SUCCESS_GREEN)

        dialog.bind("<Return>", lambda e: send_reset())
        ctk.CTkButton(dialog, text="Send New PIN", command=send_reset, width=200, height=40,
                      fg_color=theme.TEAL, hover_color=theme.TEAL_HOVER,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      text_color=theme.TEXT_ON_TEAL, corner_radius=8).pack(pady=16)

    def open_change_my_pin_dialog(self):
        if not self.logged_in:
            messagebox.showwarning("Not Logged In", "Log in first to change your PIN.")
            return
        staff_id = self.current_staff_id
        row = self.conn.execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone()
        if row is None:
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Change My PIN")
        screens.fit_dialog(dialog, 380, 420)
        dialog.configure(fg_color=theme.BG_MAIN)
        dialog.grab_set()

        hdr = ctk.CTkFrame(dialog, fg_color=theme.CHARCOAL_DARK, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text=f"🔑  Change PIN — {row['name']}",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=theme.TEXT_ON_DARK).pack(pady=16, padx=20, anchor="w")

        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        def _field(label, show="●"):
            ctk.CTkLabel(body, text=label, font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=theme.TEXT_DARK).pack(anchor="w", padx=20, pady=(14, 4))
            var = tk.StringVar()
            ctk.CTkEntry(body, textvariable=var, width=320, show=show, corner_radius=8).pack(padx=20)
            return var

        current_var = _field("Current PIN")
        new_var = _field("New PIN")
        confirm_var = _field("Confirm New PIN")

        def save():
            if not staff_module.verify_pin(self.conn, staff_id, current_var.get().strip()):
                messagebox.showerror("Incorrect PIN", "Your current PIN doesn't match.")
                return
            if new_var.get().strip() != confirm_var.get().strip():
                messagebox.showerror("PINs Don't Match", "New PIN and confirmation don't match.")
                return
            try:
                staff_module.update_pin(self.conn, staff_id, new_var.get())
            except ValueError as e:
                messagebox.showerror("Cannot Change PIN", str(e))
                return
            audit.record(self.conn, actor_staff_id=staff_id, action="staff.pin_self_change",
                         target_staff_id=staff_id, details="changed their own PIN")
            notifications.send("SmartGrocer: PIN changed",
                               f"{row['role'].capitalize()} {row['name']} changed their own PIN.")
            if row["email"] or row["phone"]:
                notifications.send("SmartGrocer: your PIN was changed",
                                   "Your SmartGrocer PIN was just changed. If this wasn't you, contact the owner.",
                                   to_email=row["email"] or None, to_phone=row["phone"] or None)
            dialog.destroy()
            messagebox.showinfo("PIN Updated", "Your PIN has been updated successfully.")

        dialog.bind("<Return>", lambda e: save())
        ctk.CTkButton(dialog, text="Save New PIN", command=save, width=200, height=40,
                      fg_color=theme.TEAL, hover_color=theme.TEAL_HOVER,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      text_color=theme.TEXT_ON_TEAL, corner_radius=8).pack(pady=18)

    def open_close_day_dialog(self):
        session = cash_drawer.get_open_session(self.conn)
        if session is None:
            messagebox.showinfo("No Open Day", "There is no open cash drawer session to close.")
            return
        expected = cash_drawer.expected_cash(self.conn, session)

        dialog = ctk.CTkToplevel(self)
        dialog.title("Close Day — End of Day Summary")
        screens.fit_dialog(dialog, 400, 320)
        dialog.configure(fg_color=theme.BG_MAIN)
        dialog.grab_set()

        hdr = ctk.CTkFrame(dialog, fg_color=theme.CHARCOAL_DARK, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="🏁  Close Trading Day",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=theme.TEXT_ON_DARK).pack(pady=16, padx=20, anchor="w")

        card = ctk.CTkFrame(dialog, fg_color=theme.CARD_BG, corner_radius=10,
                             border_width=1, border_color=theme.BORDER)
        card.pack(fill="x", padx=20, pady=16)
        ctk.CTkLabel(card, text=f"Expected cash in drawer",
                     font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED).pack(anchor="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(card, text=f"LKR {expected:,.2f}",
                     font=ctk.CTkFont(size=22, weight="bold"), text_color=theme.CHARCOAL_DARK).pack(anchor="w", padx=16, pady=(0, 12))

        ctk.CTkLabel(dialog, text="Counted cash (LKR):",
                     font=ctk.CTkFont(size=12, weight="bold"), text_color=theme.TEXT_DARK).pack(anchor="w", padx=20)
        counted_var = tk.StringVar(value=f"{expected:.2f}")
        ctk.CTkEntry(dialog, textvariable=counted_var, width=340, corner_radius=8).pack(padx=20, pady=(4, 0))

        def submit():
            try:
                counted = float(counted_var.get())
            except ValueError:
                messagebox.showerror("Invalid Amount", "Enter a number.")
                return
            result = cash_drawer.close_session(self.conn, session["id"], counted)
            variance = result["variance"]
            msg = (f"Expected:  LKR {result['expected_cash']:,.2f}\n"
                   f"Counted:   LKR {result['counted_cash']:,.2f}\n"
                   f"Variance:  LKR {variance:,.2f}")
            if abs(variance) > 0.01:
                messagebox.showwarning("Day Closed — Cash Variance Found", msg)
            else:
                messagebox.showinfo("Day Closed — Cash Matches", msg)
            self.logged_in = False
            self.refresh_day_status()
            self.refresh_current_frame()
            dialog.destroy()

        ctk.CTkButton(dialog, text="Close Day & Generate Report", command=submit, width=300, height=40,
                      fg_color=theme.DANGER_RED, hover_color=theme.DANGER_RED_HOVER,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      text_color="#FFFFFF", corner_radius=8).pack(pady=16)

    # -----------------------------------------------------------------------
    # Network / till modes (logic unchanged from original)
    # -----------------------------------------------------------------------

    def is_client_till(self) -> bool:
        return isinstance(self.conn, (netclient.RemoteConnection, offline_sync.OfflineConnection))

    def start_sharing(self, pairing_code: str | None = None) -> None:
        if self.net_server is None:
            self.net_server = netserver.NetDBServer(self.db_path, pairing_code=pairing_code)
        if not self.net_server.running:
            self.net_server.start()
        till_config.save("server", pairing_code=self.net_server.pairing_code)
        self.till_config = till_config.load()

    def stop_sharing(self) -> None:
        if self.net_server is not None:
            self.net_server.stop()
        till_config.save("standalone")
        self.till_config = till_config.load()

    def connect_to_till(self, host: str, port: int, pairing_code: str) -> None:
        netclient.RemoteConnection(host, port, pairing_code).close()
        new_conn = offline_sync.OfflineConnection(host, port, pairing_code, offline_sync.mirror_db_path(self.db_path))
        old_conn = self.conn
        self.conn = new_conn
        try:
            old_conn.close()
        except Exception:
            pass
        till_config.save("client", server_host=host, server_port=port, pairing_code=pairing_code)
        self.till_config = till_config.load()
        self.logged_in = False
        self.current_staff_id = 1
        self.refresh_day_status()
        self.refresh_current_frame()

    def disconnect_from_till(self) -> None:
        old_conn = self.conn
        self.conn = db.get_conn(self.db_path)
        db.init_db(self.conn)
        try:
            old_conn.close()
        except Exception:
            pass
        till_config.save("standalone")
        self.till_config = till_config.load()
        self.logged_in = False
        self.current_staff_id = 1
        self.refresh_day_status()
        self.refresh_current_frame()

    # -----------------------------------------------------------------------
    # Offline sync heartbeat (logic unchanged)
    # -----------------------------------------------------------------------

    _OFFLINE_SYNC_CHECK_INTERVAL_MS = 20_000

    def _schedule_offline_sync_check(self):
        self.after(self._OFFLINE_SYNC_CHECK_INTERVAL_MS, self._offline_sync_tick)

    def _offline_sync_tick(self):
        conn = self.conn
        if isinstance(conn, offline_sync.OfflineConnection) and not conn.is_online:
            def work():
                return conn.try_reconnect()
            def done(outcome):
                if not outcome["still_offline"]:
                    messagebox.showinfo(
                        "Back Online",
                        f"Reconnected to Main Till — {outcome['synced']} queued transaction(s) synced."
                        if outcome["synced"] else "Reconnected to Main Till — nothing was queued.",
                    )
                self.refresh_current_frame()
            screens.run_in_background(self, work, done, lambda e: None)
        self._schedule_offline_sync_check()

    # -----------------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------------

    def _on_close(self):
        if self.mobile_scan_server is not None and self.mobile_scan_server.running:
            self.mobile_scan_server.stop()
        if self.net_server is not None and self.net_server.running:
            self.net_server.stop()
        try:
            self.conn.close()
        except Exception:
            pass
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(db_path=None):
    app = SmartGrocerApp(db_path)
    app.mainloop()


if __name__ == "__main__":
    run()
