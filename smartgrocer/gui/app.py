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

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("green")

NAV_ITEMS = [
    ("Dashboard", "dashboard", screens.DashboardScreen),
    ("POS / Checkout", "pos", screens.POSScreen),
    ("Inventory", "inventory", screens.InventoryScreen),
    ("Promotions & Expiry", "promotions", screens.PromotionsScreen),
    ("Forecasting", "forecasting", screens.ForecastingScreen),
    ("Bundle Recommendations", "bundles", screens.BundlesScreen),
    ("Store Layout", "layout", screens.LayoutScreen),
    ("Reports", "reports", screens.ReportsScreen),
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

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)

        title = ctk.CTkLabel(self.sidebar, text="SmartGrocer", font=ctk.CTkFont(size=22, weight="bold"))
        title.pack(pady=(24, 2), padx=20)
        subtitle = ctk.CTkLabel(self.sidebar, text="Decision Support System", font=ctk.CTkFont(size=12),
                                 text_color="gray")
        subtitle.pack(pady=(0, 20), padx=20)

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for label, key, _cls in NAV_ITEMS:
            btn = ctk.CTkButton(self.sidebar, text=label, anchor="w",
                                 command=lambda k=key: self.show_frame(k))
            btn.pack(fill="x", padx=16, pady=4)
            self.nav_buttons[key] = btn

        self.container = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
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
            btn.configure(fg_color=("gray75", "gray25") if k == key else ("gray85", "gray20"))
        if hasattr(frame, "on_show"):
            frame.on_show()


def run(db_path=None):
    app = SmartGrocerApp(db_path)
    app.mainloop()


if __name__ == "__main__":
    run()
