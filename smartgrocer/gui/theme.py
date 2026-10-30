"""
SmartGrocer - Professional POS Theme (Dark Charcoal + Teal)

A modern, professional color system inspired by enterprise-grade POS
terminals. Dark charcoal backgrounds reduce eye strain during long
shifts; teal accents give instant visual clarity for primary actions.
All colors centralised here so the whole app reads as one system.
"""

from __future__ import annotations

from pathlib import Path
from tkinter import ttk

# ---------------------------------------------------------------------------
# Core palette - Modern Enterprise Dark Slate + Emerald-Teal
# ---------------------------------------------------------------------------
CHARCOAL_DARK   = "#0F172A"   # deep slate-900: cleaner, richer sidebar background
CHARCOAL_MED    = "#1E293B"   # slate-800: card backgrounds on dark surfaces / headers
CHARCOAL_HOVER  = "#334155"   # slate-700: hover / pressed on dark surfaces

TEAL            = "#0D9488"   # teal-600: vibrant, elegant enterprise teal
TEAL_HOVER      = "#0F766E"   # teal-700: rich tactile hover
TEAL_LIGHT      = "#CCFBF1"   # teal-100: crisp light tint for badge pills
TEAL_ACCENT     = "#14B8A6"   # teal-500: bright focus ring & indicators

ACCENT_AMBER    = "#F59E0B"   # warnings, stock alerts (amber-500)
ACCENT_AMBER_HOVER = "#D97706"# amber-600

# Background / surface system
BG_MAIN         = "#F8FAFC"   # slate-50: crisp, ultra-clean neutral background
BG_DARK         = "#0F172A"   # dark surface (= CHARCOAL_DARK)
CARD_BG         = "#FFFFFF"   # pure white cards
CARD_BG_ALT     = "#F8FAFC"   # alternating table row stripe
SURFACE_HOVER   = "#F1F5F9"   # table & list item hover highlight

# Borders & Dividers
BORDER          = "#E2E8F0"   # slate-200: subtle, crisp card borders
BORDER_SUBTLE   = "#EDF2F7"   # very faint divider
BORDER_DARK     = "#334155"   # slate-700: borders on dark surfaces

# Typography
TEXT_DARK       = "#0F172A"   # slate-900: high legibility dark text
TEXT_MUTED      = "#64748B"   # slate-500: crisp secondary/meta text
TEXT_ON_DARK    = "#F8FAFC"   # slate-50: high-contrast text on dark surfaces
TEXT_ON_TEAL    = "#FFFFFF"   # pure white text on teal buttons

# Semantic colors
SUCCESS_GREEN       = "#10B981"   # emerald-500
SUCCESS_GREEN_HOVER = "#059669"   # emerald-600
DANGER_RED          = "#EF4444"   # red-500
DANGER_RED_HOVER    = "#DC2626"   # red-600
INFO_BLUE           = "#3B82F6"   # blue-500
INFO_BLUE_HOVER     = "#2563EB"   # blue-600

# ---------------------------------------------------------------------------
# Legacy aliases kept so existing code that references the old navy names
# compiles without changes. Any screen that still uses NAVY_DARK / ACCENT_BLUE
# will automatically get the new charcoal / teal equivalents.
# ---------------------------------------------------------------------------
NAVY_DARK           = CHARCOAL_DARK
NAVY_DARKER         = CHARCOAL_HOVER
ACCENT_BLUE         = TEAL
ACCENT_BLUE_HOVER   = TEAL_HOVER
BG_LIGHT            = BG_MAIN
SUCCESS_GREEN_HOVER = SUCCESS_GREEN_HOVER   # already defined above
DANGER_RED_HOVER    = DANGER_RED_HOVER      # already defined above

# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_PATH  = ASSETS_DIR / "balasuriya_group_logo.png"

# ---------------------------------------------------------------------------
# Alert colors for expiry / stock / promo rows in tables
# ---------------------------------------------------------------------------
ALERT_COLORS = {
    "overdue":       "#FEE2E2",   # light red
    "critical_3day": "#FEF3C7",   # light amber
    "warning_7day":  "#FEF9C3",   # light yellow
    "low":           "#FFE4E6",   # light rose
    "inactive":      "#F1F5F9",   # greyed out
    "needs_price":   "#FFF7ED",   # light orange
}

# ---------------------------------------------------------------------------
# Dashboard Insights card styles
# ---------------------------------------------------------------------------
INSIGHT_STYLES = {
    "festival":     ("🎉", TEAL),
    "weather":      ("🌦️", TEAL),
    "expiry":       ("⏰", DANGER_RED),
    "stock":        ("📦", ACCENT_AMBER),
    "ok":           ("✅", SUCCESS_GREEN),
    "purchase":     ("📦", ACCENT_AMBER),
    "fast_moving":  ("🔥", TEAL),
    "promotion":    ("🏷️", DANGER_RED),
    "unmatched":    ("💡", TEXT_MUTED),
    "error":        ("⚠️", DANGER_RED),
    "sales_today":  ("💰", SUCCESS_GREEN),
    "item_lookup":  ("🔎", TEAL),
    "greeting":     ("👋", TEAL),
    "thanks":       ("👍", SUCCESS_GREEN),
}


def insight_style(kind: str | None) -> tuple[str, str]:
    """(icon, accent_color) for a dashboard/answer 'kind'."""
    return INSIGHT_STYLES.get(kind or "unmatched", ("•", TEXT_MUTED))


# ---------------------------------------------------------------------------
# Global ttk style (Treeview tables, Scrollbars)
# ---------------------------------------------------------------------------

def apply_global_style() -> None:
    """Configure ttk.Style once at app startup.

    ttk widgets (Treeview, Scrollbar) sit inside CustomTkinter frames but
    aren't re-styled by CTk itself, so without this they render with the
    plain default OS look.
    """
    style = ttk.Style()
    style.theme_use("clam")  # only built-in theme that honours custom colors on Win/Mac/Linux

    # Treeview rows
    style.configure(
        "Treeview",
        background=CARD_BG,
        fieldbackground=CARD_BG,
        foreground=TEXT_DARK,
        rowheight=36,
        borderwidth=0,
        font=("Segoe UI", 10),
    )
    style.map(
        "Treeview",
        background=[("selected", TEAL)],
        foreground=[("selected", "#FFFFFF")],
    )

    # Column headings
    style.configure(
        "Treeview.Heading",
        background=CHARCOAL_DARK,
        foreground=TEXT_ON_DARK,
        relief="flat",
        font=("Segoe UI", 10, "bold"),
        padding=(10, 11),
    )
    style.map("Treeview.Heading", background=[("active", CHARCOAL_MED)])

    # Scrollbars
    style.configure(
        "Vertical.TScrollbar",
        background="#CBD5E1",
        troughcolor="#F1F5F9",
        borderwidth=0,
        arrowcolor=TEXT_MUTED,
    )
    style.configure(
        "Horizontal.TScrollbar",
        background="#CBD5E1",
        troughcolor="#F1F5F9",
        borderwidth=0,
        arrowcolor=TEXT_MUTED,
    )

    patch_customtkinter_scrolling()


# ---------------------------------------------------------------------------
# CustomTkinter scroll patch (preserved from original, no logic changes)
# ---------------------------------------------------------------------------

def patch_customtkinter_scrolling() -> None:
    """Fix CustomTkinter's CTkScrollableFrame mouse pad / trackpad scrolling on Windows.

    Key problems solved:
    1. Tkinter routes scroll events to the focused widget (often the root window '.'),
       NOT the widget under the pointer. CustomTkinter tested `event.widget`, which
       caused `_check_if_valid_scroll` to fail and discard the event. We resolve the
       actual widget under the cursor using `self.winfo_containing(event.x_root, event.y_root)`.
    2. In Python 3.14 (Tk 9.0+), Windows precision touchpads fire `<TouchpadScroll>`,
       which CustomTkinter never bound. We bind `<TouchpadScroll>`, `<Control-TouchpadScroll>`,
       and `<Control-MouseWheel>`.
    3. CustomTkinter's `-int(event.delta / 6)` truncated touchpad micro-deltas (<6) to 0.
       We unpack TouchpadScroll deltas, scale micro-deltas for natural glide, and accumulate
       sub-pixel remainders so scrolling is immediate and fluid.
    4. Child tables (ttk.Treeview) handle their own scrolling; `_check_if_valid_scroll`
       returns False when hovering over them so the parent frame doesn't hijack the scroll.
    """
    try:
        import sys
        import tkinter as tk
        from tkinter import ttk
        from customtkinter.windows.widgets.ctk_scrollable_frame import CTkScrollableFrame

        if getattr(CTkScrollableFrame, "_smooth_scroll_patched", False):
            return
        CTkScrollableFrame._smooth_scroll_patched = True

        orig_init = CTkScrollableFrame.__init__
        orig_check = CTkScrollableFrame._check_if_valid_scroll

        def smooth_init(self, *args, **kwargs):
            orig_init(self, *args, **kwargs)
            for ev in ("<TouchpadScroll>", "<Control-TouchpadScroll>", "<Control-MouseWheel>"):
                try:
                    self.bind_all(ev, self._mouse_wheel_all, add=True)
                except Exception:
                    pass

        def smooth_check_if_valid_scroll(self, widget):
            if widget is None:
                return False
            if widget == self._parent_canvas:
                return True
            if isinstance(widget, (ttk.Treeview, ttk.Scrollbar, tk.Scrollbar, tk.Listbox, tk.Text)):
                return False
            if isinstance(widget, CTkScrollableFrame):
                return widget._parent_canvas == self._parent_canvas
            if getattr(widget, "master", None) is not None:
                return self._check_if_valid_scroll(widget.master)
            return False

        def decode_delta(event) -> tuple[float, float]:
            raw_delta = getattr(event, "delta", 0)
            ev_type = getattr(event, "type", None)
            is_touchpad = False
            try:
                if ev_type == tk.EventType.TouchpadScroll or "Touchpad" in str(ev_type):
                    is_touchpad = True
            except Exception:
                pass

            if is_touchpad and raw_delta:
                raw = int(raw_delta) & 0xFFFFFFFF
                dy = raw & 0xFFFF
                dx = (raw >> 16) & 0xFFFF
                if dy >= 0x8000:
                    dy -= 0x10000
                if dx >= 0x8000:
                    dx -= 0x10000
                if dy != 0 or dx != 0:
                    return float(dx), float(dy)
                return 0.0, float(raw_delta)

            if raw_delta:
                return 0.0, float(raw_delta)
            if getattr(event, "num", None) == 4:
                return 0.0, 120.0
            if getattr(event, "num", None) == 5:
                return 0.0, -120.0
            return 0.0, 0.0

        def smooth_mouse_wheel_all(self, event):
            target = None
            try:
                if hasattr(event, "x_root") and hasattr(event, "y_root"):
                    target = self.winfo_containing(event.x_root, event.y_root)
            except Exception:
                pass
            if target is None:
                target = getattr(event, "widget", None)

            if not self._check_if_valid_scroll(target):
                return

            dx, dy = decode_delta(event)
            if dy == 0.0 and dx == 0.0:
                return

            if sys.platform.startswith("win"):
                shift = getattr(self, "_shift_pressed", False)
                if shift or (dx != 0.0 and dy == 0.0):
                    val = dx if dx != 0.0 else dy
                    pixel_step = (val / 6.0) if abs(val) >= 120 else (val * 2.5)
                    if self._parent_canvas.xview() != (0.0, 1.0):
                        accum = getattr(self, "_x_wheel_accum", 0.0) + pixel_step
                        step = int(accum)
                        self._x_wheel_accum = accum - step
                        if step != 0:
                            self._parent_canvas.xview("scroll", -step, "units")
                else:
                    pixel_step = (dy / 6.0) if abs(dy) >= 120 else (dy * 2.5)
                    if self._parent_canvas.yview() != (0.0, 1.0):
                        accum = getattr(self, "_y_wheel_accum", 0.0) + pixel_step
                        step = int(accum)
                        self._y_wheel_accum = accum - step
                        if step != 0:
                            self._parent_canvas.yview("scroll", -step, "units")
            elif sys.platform == "darwin":
                if getattr(self, "_shift_pressed", False):
                    if self._parent_canvas.xview() != (0.0, 1.0):
                        self._parent_canvas.xview("scroll", -int(dx if dx != 0 else dy), "units")
                else:
                    if self._parent_canvas.yview() != (0.0, 1.0):
                        self._parent_canvas.yview("scroll", -int(dy), "units")
            else:
                if getattr(self, "_shift_pressed", False):
                    if self._parent_canvas.xview() != (0.0, 1.0):
                        self._parent_canvas.xview_scroll(-1 if dy > 0 else 1, "units")
                else:
                    if self._parent_canvas.yview() != (0.0, 1.0):
                        self._parent_canvas.yview_scroll(-1 if dy > 0 else 1, "units")

        CTkScrollableFrame.__init__ = smooth_init
        CTkScrollableFrame._check_if_valid_scroll = smooth_check_if_valid_scroll
        CTkScrollableFrame._mouse_wheel_all = smooth_mouse_wheel_all
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Treeview helper utilities (unchanged API, preserved for all existing callers)
# ---------------------------------------------------------------------------

def tree_insert(tree: ttk.Treeview, values, tag: str | None = None, iid: str | None = None) -> str:
    """Insert a row, auto-striping alternating rows unless a semantic tag is given."""
    if tag:
        tags = (tag,)
    else:
        count = getattr(tree, "_row_counter", 0)
        tags = ("evenrow" if count % 2 == 0 else "oddrow",)
    count = getattr(tree, "_row_counter", 0)
    tree._row_counter = count + 1
    kwargs = {"iid": iid} if iid is not None else {}
    return tree.insert("", "end", values=values, tags=tags, **kwargs)


def tree_clear(tree: ttk.Treeview) -> None:
    tree.delete(*tree.get_children())
    tree._row_counter = 0


def configure_stripes(tree: ttk.Treeview) -> None:
    tree.tag_configure("evenrow", background=CARD_BG)
    tree.tag_configure("oddrow", background=CARD_BG_ALT)
    for tag, color in ALERT_COLORS.items():
        tree.tag_configure(tag, background=color)
