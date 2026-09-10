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

import customtkinter as ctk

from .. import db
from . import screens
from . import theme

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")  # base theme; brand colors are applied per-widget below

NAV_ITEMS = [
    ("📊  Dashboard", "dashboard", screens.DashboardScreen),
    ("🛒  POS / Checkout", "pos", screens.POSScreen),
    ("📦  Inventory", "inventory", screens.InventoryScreen),
    ("⏰  Promotions & Expiry", "promotions", screens.PromotionsScreen),
    ("📈  Forecasting", "forecasting", screens.ForecastingScreen),
    ("🔗  Bundle Recommendations", "bundles", screens.BundlesScreen),
    ("🗺️  Store Layout", "layout", screens.LayoutScreen),
    ("📄  Reports", "reports", screens.ReportsScreen),
]


class SmartGrocerApp(ctk.CTk):
    def __init__(self, db_path=None):
        super().__init__()
        self.title("SmartGrocer - Decision Support System")
        self.geometry("1280x800")
        self.minsize(1000, 650)

        self.conn = db.get_conn(db_path or db.DEFAULT_DB_PATH)
        db.init_db(self.conn)
        self.current_staff_id = 1  # default to Admin; POS screen lets you switch

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
        subtitle = ctk.CTkLabel(self.sidebar, text="Decision Support System", font=ctk.CTkFont(size=12),
                                 text_color="#9FB0CC")
        subtitle.pack(pady=(0, 4), padx=20)
        brand = ctk.CTkLabel(self.sidebar, text="by Balasuriya Group", font=ctk.CTkFont(size=11, slant="italic"),
                              text_color="#7A8CAD")
        brand.pack(pady=(0, 22), padx=20)

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


def run(db_path=None):
    app = SmartGrocerApp(db_path)
    app.mainloop()


if __name__ == "__main__":
    run()
