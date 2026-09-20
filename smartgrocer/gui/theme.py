"""
SmartGrocer - Balasuriya Group brand theme.

Colors sampled directly from the company logo (navy on white). Centralised
here so the whole app reads as one system instead of CustomTkinter's stock
green theme + default OS-grey Treeview tables.
"""

from __future__ import annotations

from pathlib import Path
from tkinter import ttk

NAVY_DARK = "#0B1B3F"      # sidebar background, table headers
NAVY_DARKER = "#071227"    # hover/pressed states on navy surfaces
ACCENT_BLUE = "#2F5C9E"    # primary buttons / active nav item
ACCENT_BLUE_HOVER = "#24487E"
BG_LIGHT = "#F3F5F9"       # main content background
CARD_BG = "#FFFFFF"
CARD_BG_ALT = "#EEF1F7"    # alternating table row stripe
BORDER = "#DDE2EB"
TEXT_DARK = "#111827"
TEXT_MUTED = "#6B7280"
TEXT_ON_NAVY = "#FFFFFF"
SUCCESS_GREEN = "#2E7D32"
SUCCESS_GREEN_HOVER = "#1B5E20"
DANGER_RED = "#B23B3B"
DANGER_RED_HOVER = "#8A2C2C"

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_PATH = ASSETS_DIR / "balasuriya_group_logo.png"

# Alert-tier colors reused across Promotions/Inventory screens
ALERT_COLORS = {
    "overdue": "#F4A6A6",
    "critical_3day": "#FBD3A2",
    "warning_7day": "#FBF3A2",
    "low": "#FBD9D9",
    "inactive": "#DADFE6",   # removed/deactivated item - greyed out, still visible so Owner can reactivate it
    "needs_price": "#FBE7A2",  # price_verified=0 - a bulk-imported placeholder price, not yet checked
}

# Dashboard "Insights" cards and the "Ask a question" answer (rag_insights.py)
# are read by the shop owner, not a developer, so each kind of insight gets
# its own icon and accent color instead of one flat bulleted list - lets
# someone glance down the panel and tell "this is an expiry warning" from
# "this is just a heads-up" without reading every word. Keyed by the
# "kind" field rag_insights.generate() already returns, plus the three
# `answer_question()` intents and a fallback for the unmatched case.
INSIGHT_STYLES = {
    "festival":     ("🎉", ACCENT_BLUE),      # upcoming festival demand
    "weather":      ("🌦️", ACCENT_BLUE),  # weather-driven demand
    "expiry":       ("⏰", DANGER_RED),           # near-expiry stock
    "stock":        ("📦", "#B7791F"),        # below reorder level (amber, distinct from red)
    "ok":           ("✅", SUCCESS_GREEN),        # nothing needs attention
    "purchase":     ("📦", "#B7791F"),
    "fast_moving":  ("🔥", ACCENT_BLUE),
    "promotion":    ("🏷️", DANGER_RED),
    "unmatched":    ("💡", TEXT_MUTED),       # question didn't match a known intent
    "error":        ("⚠️", DANGER_RED),
}


def insight_style(kind: str | None) -> tuple[str, str]:
    """(icon, accent_color) for an insight/answer "kind" - falls back to a
    plain neutral bullet for anything not in the table above so a future
    new kind never renders as a crash, just a slightly less decorated card."""
    return INSIGHT_STYLES.get(kind or "unmatched", ("•", TEXT_MUTED))


def apply_global_style() -> None:
    """Configure ttk.Style once at app startup. ttk widgets (Treeview,
    Scrollbar) sit inside CustomTkinter frames but aren't restyled by CTk
    itself, so without this they render with the plain default OS look."""
    style = ttk.Style()
    # "clam" is the only built-in ttk theme that reliably honours custom
    # colors on Windows/Mac/Linux alike - the native themes ignore most of it.
    style.theme_use("clam")

    style.configure(
        "Treeview",
        background=CARD_BG,
        fieldbackground=CARD_BG,
        foreground=TEXT_DARK,
        rowheight=30,
        borderwidth=0,
        font=("Segoe UI", 10),
    )
    style.map(
        "Treeview",
        background=[("selected", ACCENT_BLUE)],
        foreground=[("selected", "#FFFFFF")],
    )
    style.configure(
        "Treeview.Heading",
        background=NAVY_DARK,
        foreground=TEXT_ON_NAVY,
        relief="flat",
        font=("Segoe UI", 10, "bold"),
        padding=(8, 8),
    )
    style.map("Treeview.Heading", background=[("active", NAVY_DARKER)])

    style.configure("Vertical.TScrollbar", background=BG_LIGHT, troughcolor=BG_LIGHT, borderwidth=0)


def tree_insert(tree: ttk.Treeview, values, tag: str | None = None, iid: str | None = None) -> str:
    """Insert a row, auto-striping alternating rows unless a semantic tag
    (e.g. an expiry-alert color) is given, in which case that tag wins."""
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
