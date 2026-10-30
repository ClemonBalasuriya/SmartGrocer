"""
SmartGrocer - screen (page) implementations. See app.py's module docstring
for the testing caveat on this GUI layer.
"""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import date
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from .. import association, forecasting, offers as offers_module, pos, promotions, reports
from .. import cash_drawer as cash_drawer_module
from .. import customers as customers_module
from .. import receipts as receipts_module
from .. import suppliers as suppliers_module
from .. import staff as staff_module
from .. import stock_take as stock_take_module
from .. import mobile_scan
from .. import netclient
from .. import netserver
from .. import offline_sync
from .. import rag_insights
from .. import audit
from .. import notifications
from .. import db as db_module
from .. import import_products
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
    theme.configure_stripes(tree)
    tree._row_counter = 0
    tree.master_frame = frame  # convenience handle so callers can .pack()/.grid() `frame`

    def _tree_mousewheel(event):
        if hasattr(event, "delta") and event.delta:
            accum = getattr(tree, "_tree_wheel_accum", 0.0) + event.delta
            rows_to_scroll = int(accum / 40.0)
            if rows_to_scroll != 0:
                tree._tree_wheel_accum = accum - (rows_to_scroll * 40.0)
                tree.yview_scroll(-rows_to_scroll, "units")
            else:
                tree._tree_wheel_accum = accum
        elif getattr(event, "num", None) == 4:
            tree.yview_scroll(-2, "units")
        elif getattr(event, "num", None) == 5:
            tree.yview_scroll(2, "units")
        return "break"

    tree.bind("<MouseWheel>", _tree_mousewheel)
    tree.bind("<Button-4>", _tree_mousewheel)
    tree.bind("<Button-5>", _tree_mousewheel)
    vsb.bind("<MouseWheel>", _tree_mousewheel)
    vsb.bind("<Button-4>", _tree_mousewheel)
    vsb.bind("<Button-5>", _tree_mousewheel)
    return tree


PRIMARY_BTN = dict(
    fg_color=theme.TEAL, hover_color=theme.TEAL_HOVER,
    text_color=theme.TEXT_ON_TEAL, corner_radius=8,
)
SUCCESS_BTN = dict(
    fg_color=theme.SUCCESS_GREEN, hover_color=theme.SUCCESS_GREEN_HOVER,
    text_color="#FFFFFF", corner_radius=8,
)
DANGER_BTN = dict(
    fg_color=theme.DANGER_RED, hover_color=theme.DANGER_RED_HOVER,
    text_color="#FFFFFF", corner_radius=8,
)
SECONDARY_BTN = dict(
    fg_color=theme.CARD_BG, hover_color=theme.CARD_BG_ALT,
    text_color=theme.TEXT_DARK, border_width=1, border_color=theme.BORDER,
    corner_radius=8,
)
WARNING_BTN = dict(
    fg_color=theme.ACCENT_AMBER, hover_color=theme.ACCENT_AMBER_HOVER,
    text_color="#FFFFFF", corner_radius=8,
)


def styled_button(parent, text, command, kind="primary", **kwargs):
    """Create a consistently-styled button. Kinds: primary, success, danger, secondary, warning."""
    style = {
        "primary": PRIMARY_BTN,
        "success": SUCCESS_BTN,
        "danger": DANGER_BTN,
        "secondary": SECONDARY_BTN,
        "warning": WARNING_BTN,
    }.get(kind, PRIMARY_BTN)
    merged = {**style, **kwargs}
    return ctk.CTkButton(parent, text=text, command=command, **merged)



def fit_dialog(dialog, width: int, height: int) -> None:
    """Size a popup dialog to fit the screen, and center it.

    Clamps the width/height guessed at each call site below to the actual
    screen size (minus room for the taskbar/title bar) so a dialog can
    never open taller or wider than the screen itself - the original bug
    report here ("need to maximize to see the Save button").

    This does NOT try to measure or compensate for Windows display scaling
    (125%/150%/175%, common on real laptops) making CustomTkinter render
    every label/entry/button bigger than the pixel guess below assumes -
    an earlier version of this function tried to fix that by measuring the
    dialog's rendered size after the fact and resizing again, which turned
    out to be unreliable in practice. The real, reliable fix for that is
    structural instead: every dialog's fields are packed into a
    CTkScrollableFrame (`body`/`scroll` in each call site below) with
    fill="both", expand=True, while its Save/action button(s) are packed
    directly onto `dialog` afterwards, OUTSIDE that scrollable frame.
    Tkinter's pack() always gives a plain (non-expanding) widget its full
    requested size first and only lets an expand=True widget grow into
    whatever's left over - so the button is always fully visible no matter
    how tall Windows renders the fields above it; a shorter window (from
    a small screen, high scaling, or both at once) just means more of the
    form scrolls, never that the button loses its space. See
    open_login_dialog in app.py for the first, most-commented example of
    this pattern."""
    screen_w = dialog.winfo_screenwidth()
    screen_h = dialog.winfo_screenheight()
    max_w = max(300, screen_w - 80)
    max_h = max(300, screen_h - 120)  # leaves room for the taskbar + title bar
    w = min(width, max_w)
    h = min(height, max_h)
    x = max(0, (screen_w - w) // 2)
    y = max(0, (screen_h - h) // 2 - 20)
    dialog.geometry(f"{w}x{h}+{x}+{y}")
    dialog.minsize(min(w, 300), min(h, 300))
    dialog.lift()
    dialog.focus_force()
    # CustomTkinter does some of its own window setup a few milliseconds
    # after a CTkToplevel is created (e.g. fixing the titlebar color on
    # Windows), which can steal focus/stacking back to the main window
    # right after this dialog opens - re-asserting once that's had time to
    # run keeps it on top and focused instead of appearing to vanish.
    dialog.after(120, dialog.lift)
    dialog.after(120, dialog.focus_force)


class SearchableDropdown(ctk.CTkButton):
    """Drop-in replacement for `ctk.CTkOptionMenu(values=[...], variable=a_var,
    width=...)` for any list that can grow past a screenful - a category
    list, the customer list, a product/supplier picker, the staff list.
    CTkOptionMenu's own dropdown has no scrollbar: past a certain length it
    just becomes a stack of buttons taller than the screen, with no way to
    reach whatever doesn't fit. This opens a small popup instead with its
    own scrollable list AND a search box that filters it as you type -
    which also makes picking one item out of a long list faster than
    scanning it by eye.

    Supports enough of CTkOptionMenu's interface to swap in directly at
    any of this app's call sites: construct with the same `values`/
    `variable`/`width` arguments, `.pack()`/`.grid()` it the same way, and
    `.configure(values=new_list)` still works afterwards (e.g. when
    on_show() repopulates it from the database)."""

    _ARROW = "  ▾"

    def __init__(self, parent, values: list[str], variable: tk.StringVar,
                 width: int = 200, placeholder: str = "(none)", label: str | None = None, **kwargs):
        self._values: list[str] = list(values)
        self._variable = variable
        self._placeholder = placeholder
        # What this list IS (e.g. "Category", "Staff") - shown as a bold
        # header inside the popup, so it doesn't open as a nameless box.
        self._label = label or placeholder
        self._popup: ctk.CTkFrame | None = None
        self._global_click_bound = False
        super().__init__(
            parent, text=self._display_text(), width=width, anchor="w",
            fg_color=theme.CARD_BG, hover_color=theme.CARD_BG_ALT, text_color=theme.NAVY_DARK,
            border_width=1, border_color=theme.BORDER, corner_radius=8,
            command=self._toggle_popup, **kwargs,
        )
        self._variable.trace_add("write", lambda *_a: self._sync_label())

    def _display_text(self) -> str:
        return (self._variable.get() or self._placeholder) + self._ARROW

    def _sync_label(self) -> None:
        # Guard against firing after the underlying Tk widget is gone (the
        # variable can outlive it briefly during dialog teardown).
        if self.winfo_exists():
            super().configure(text=self._display_text())

    def configure(self, **kwargs):
        if "values" in kwargs:
            self._values = list(kwargs.pop("values"))
            if self._popup is not None and self._popup.winfo_exists():
                self._populate_list(self._filter_var.get())
        return super().configure(**kwargs)

    def _toggle_popup(self) -> None:
        if self._popup is not None and self._popup.winfo_exists():
            self._close_popup()
        else:
            self._open_popup()

    def _open_popup(self) -> None:
        # A real CTkToplevel, positioned under the button - a plain child
        # widget placed on top of the main window (an earlier version of
        # this) turned out to render behind/under the rest of the screen
        # in practice, so back to its own small window, which is guaranteed
        # to draw on top of everything regardless of the app's own widget
        # stacking. What actually needed fixing (see the click-outside
        # handling further down) was WHEN it closes, not WHAT it is: a
        # plain "close this on focus-out" used before could destroy the
        # popup as a side effect of the very click that was selecting an
        # item in it, before that item's own click had a chance to
        # register - which is what made clicking a list item look like it
        # silently did nothing.
        popup = ctk.CTkToplevel(self)
        popup.withdraw()
        # No OS title-bar text - the bold header packed into the popup
        # below (self._label) is the one and only name shown for this
        # list. An earlier version also set this to self._label, which on
        # Windows drew the same word a second time in the title bar right
        # above that header - two labels for the same thing on screen.
        popup.title("")
        # NOT overrideredirect(True) (an earlier version of this used it,
        # for a cleaner borderless "dropdown" look) - on Windows, a
        # window shown that way is never handed proper window-manager
        # focus/activation, which turned out to be why mouse-wheel
        # scrolling never reached anything inside it no matter which
        # widget or binding was tried (clicks still worked, because
        # Windows routes those by cursor position regardless; wheel
        # input is routed by which window actually has focus, and this
        # kind of window can never really get it). "-toolwindow" is the
        # closest fully-window-manager-owned equivalent: a slim title
        # bar and no taskbar entry, still not overrideredirect - Windows
        # only, so it's wrapped in case this ever runs elsewhere.
        try:
            popup.wm_attributes("-toolwindow", True)
        except tk.TclError:
            pass
        popup.attributes("-topmost", True)
        # Same background the app's other popup dialogs (Login, Receive
        # Stock, ...) use directly on the dialog window itself, rather
        # than a separate white bordered "card" floating on some other
        # background - so this reads as one more dialog in the same
        # family as those, not a visually different kind of popup.
        popup.configure(fg_color=theme.BG_LIGHT)
        self._popup = popup
        # Windows' own native title-bar close button doesn't run any of
        # this app's code by default - Tk just destroys the window
        # directly, bypassing _close_popup() entirely. That left a stale
        # reference in self._popup (harmless - _toggle_popup and
        # _on_global_click both check winfo_exists(), which correctly
        # reports a destroyed widget as gone) but ALSO left the app-wide
        # "<Button-1>" click-outside binding (below) permanently attached
        # with self._global_click_bound stuck True, since the cleanup
        # that unbinds it only lives in _close_popup(). Routing the
        # window's close button through the same _close_popup() this
        # app's own controls already use - instead of Tk's default
        # handling - makes every way of closing this popup go through
        # exactly one path, so nothing gets left stuck behind.
        popup.protocol("WM_DELETE_WINDOW", self._close_popup)

        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        popup_w = max(self.winfo_width(), 240)
        popup_h = 280
        screen_h = self.winfo_screenheight()
        if y + popup_h > screen_h - 40:  # flip above the button rather than run off the bottom
            y = max(0, self.winfo_rooty() - popup_h)
        popup.geometry(f"{popup_w}x{popup_h}+{x}+{y}")

        # The title bar (above) has no text - this bold header naming the
        # list is the one and only name shown, the same way every other
        # dialog in this app opens with a name for what it is. Closing the
        # popup itself is left to Windows' own native title-bar close
        # button (above) plus clicking away/Escape (below) - an earlier
        # version also drew a second, plain "x" as ordinary app content
        # right here, which just meant two close buttons doing the same
        # thing side by side; removing Windows' native one instead was
        # tried and reverted (see README), since reaching past Tk into the
        # raw Win32 API for that risked corrupting how the popup itself
        # rendered - so this one was the one to drop instead.
        ctk.CTkLabel(popup, text=self._label, font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=theme.NAVY_DARK).pack(anchor="w", padx=10, pady=(10, 0))

        self._filter_var = tk.StringVar()
        search_entry = ctk.CTkEntry(popup, textvariable=self._filter_var, placeholder_text="Type to filter...")
        search_entry.pack(fill="x", padx=10, pady=(4, 6))
        self._filter_var.trace_add("write", lambda *_a: self._populate_list(self._filter_var.get()))
        search_entry.bind("<Escape>", lambda e: self._close_popup())
        search_entry.bind("<Return>", lambda e: self._select_first_match())

        # A plain ttk.Treeview instead of a CTkScrollableFrame full of
        # buttons - CustomTkinter's scrollable frame reimplements
        # scrolling on its own canvas, which (as documented elsewhere in
        # this file/README) only ever reliably wires up wheel/trackpad
        # scrolling for its own bare background, and reaching it from
        # outside a popup window like this one turned out not to work
        # either. A ttk.Treeview is a native, standard Tk widget with
        # mousewheel scrolling built in by Tk itself - no custom event
        # wiring needed at all, which is exactly what every OTHER
        # scrollable list in this app (make_treeview) already relies on.
        list_holder = ctk.CTkFrame(popup, fg_color="transparent")
        list_holder.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        list_holder.grid_rowconfigure(0, weight=1)
        list_holder.grid_columnconfigure(0, weight=1)

        tree = ttk.Treeview(list_holder, columns=["value"], show="", height=8, selectmode="none")
        tree.column("value", anchor="w")
        vsb = ttk.Scrollbar(list_holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tree.tag_configure("current", background=theme.CARD_BG_ALT)
        theme.configure_stripes(tree)
        self._list_frame = tree

        # Tk's own default mouse-wheel binding for a Treeview moves it
        # only ONE row per wheel notch, which feels much slower than
        # normal scrolling elsewhere in Windows (most apps move 3+ lines
        # per notch, matching the Windows mouse setting for it). Bound
        # directly on `tree` (fires before Tk's own slower default, which
        # is attached to the widget CLASS rather than this specific
        # widget) and returning "break" stops that default from ALSO
        # firing right after and scrolling a second time.
        def _fast_scroll(event):
            if getattr(event, "num", None) == 4:
                tree.yview_scroll(-2, "units")
            elif getattr(event, "num", None) == 5:
                tree.yview_scroll(2, "units")
            elif hasattr(event, "delta") and event.delta:
                accum = getattr(tree, "_fast_wheel_accum", 0.0) + event.delta
                rows = int(accum / 40.0)
                if rows != 0:
                    tree._fast_wheel_accum = accum - (rows * 40.0)
                    tree.yview_scroll(-rows, "units")
                else:
                    tree._fast_wheel_accum = accum
            return "break"

        tree.bind("<MouseWheel>", _fast_scroll)
        tree.bind("<Button-4>", _fast_scroll)  # Linux scroll up
        tree.bind("<Button-5>", _fast_scroll)  # Linux scroll down
        # selectmode="none" plus this click binding (rather than relying
        # on the Treeview's own row-selection/<<TreeviewSelect>>) is
        # deliberate: highlighting the CURRENT value's row further down in
        # _populate_list would otherwise itself count as a "selection" and
        # fire that same event the instant the list is drawn, closing the
        # popup on open before anyone clicked anything.
        tree.bind("<ButtonRelease-1>", self._on_tree_click)

        self._populate_list("")
        popup.deiconify()
        popup.lift()
        # Forces real OS-level input focus onto this brand-new window
        # immediately - without this, the very first click on a
        # just-created window can be swallowed by Windows just activating
        # the window rather than delivering the click to the button under
        # the cursor (this popup is destroyed and rebuilt every time it
        # opens, so every click on it is that "first click").
        popup.focus_force()
        search_entry.focus_set()

        # Closes as soon as a click lands anywhere outside the popup - same
        # as any normal dropdown/combobox - checked by walking UP from
        # whatever widget the click actually landed on. Deliberately NOT a
        # "close on focus-out" (an earlier version used that): that closed
        # the popup as an immediate side effect of a click landing on one
        # of ITS OWN list buttons (which also shifts focus away from the
        # search box), destroying the button out from under its own click
        # before the selection could register. Checking what was actually
        # clicked, instead of merely that focus moved, avoids that - a
        # click on the popup's own widgets (including re-clicking this
        # button itself) is recognized as "inside" and left alone.
        #
        # A later version tried closing on window-focus changes instead
        # (Tk's <Deactivate> event, plus polling focus_get() as a
        # fallback for switching to a different application entirely),
        # hoping to catch clicks Windows sometimes swallows for window
        # activation without ever delivering to Tk (see the note in
        # README about that). In practice that back-fired much worse: on
        # this machine it kept reading the popup's OWN brand-new-window
        # setup as an immediate loss of focus, closing it a moment after
        # every single open - a box you can't even use is a worse bug
        # than a close button that sometimes needs a second click. Back
        # to plain click detection; the popup's own "x" (above) and
        # Escape (below) are always guaranteed to close it regardless.
        root = self.winfo_toplevel()
        root.bind_all("<Button-1>", self._on_global_click)
        self._global_click_bound = True

    def _on_global_click(self, event) -> None:
        if self._popup is None or not self._popup.winfo_exists():
            return
        widget = event.widget
        while widget is not None:
            if widget == self._popup or widget == self:
                return  # click landed inside the popup (or re-clicked the button itself) - leave it
            widget = getattr(widget, "master", None)
        self._close_popup()

    def _populate_list(self, filter_text: str) -> None:
        tree = self._list_frame
        tree.delete(*tree.get_children())
        filter_text = filter_text.strip().lower()
        matches = [v for v in self._values if filter_text in v.lower()] if filter_text else list(self._values)
        self._current_matches = matches
        if not matches:
            tree.insert("", "end", values=("No matches",))
            return
        current = self._variable.get()
        for value in matches:
            tree.insert("", "end", values=(value,), tags=("current",) if value == current else ())

    def _on_tree_click(self, event) -> None:
        row = self._list_frame.identify_row(event.y)
        if not row or not self._current_matches:
            return
        values = self._list_frame.item(row, "values")
        if values:
            self._select(values[0])

    def _select_first_match(self) -> None:
        matches = getattr(self, "_current_matches", [])
        if matches:
            self._select(matches[0])

    def _select(self, value: str) -> None:
        self._variable.set(value)
        self._close_popup()

    def _close_popup(self) -> None:
        popup, self._popup = self._popup, None
        if self._global_click_bound:
            try:
                self.winfo_toplevel().unbind_all("<Button-1>")
            except Exception:  # noqa: BLE001 - window may already be gone during teardown
                pass
            self._global_click_bound = False
        if popup is not None and popup.winfo_exists():
            popup.destroy()


class SuggestionBox:
    """Attaches a live-updating suggestion dropdown to an existing
    CTkEntry + StringVar the cashier is actively typing free text into -
    the customer phone box and the add-item barcode/name boxes on the POS
    screen. Deliberately separate from SearchableDropdown above: that one
    IS the input (a button that opens a picker on click, no typing of its
    own); this one sits underneath a real text entry and just previews
    what the typed-so-far text currently matches, refreshing on every
    keystroke. Enter in the entry itself is left completely alone - it
    still does whatever it always did (look the typed text up directly) -
    so this is a pure shortcut: click a suggestion, or arrow down into the
    list and press Enter there, to jump straight to the same outcome
    without finishing typing an exact phone number/code/name.

    `fetch_fn(query) -> list[(display_text, payload)]` computes matches
    for the current text; `on_select(payload)` runs when one is picked."""

    def __init__(self, entry: ctk.CTkEntry, variable: tk.StringVar, fetch_fn, on_select, min_chars: int = 1):
        self._entry = entry
        self._variable = variable
        self._fetch_fn = fetch_fn
        self._on_select = on_select
        self._min_chars = min_chars
        self._popup: ctk.CTkToplevel | None = None
        self._list_frame: ttk.Treeview | None = None
        self._matches: list[tuple[str, object]] = []
        self._global_click_bound = False
        # Selecting a suggestion clears the entry (matching this app's own
        # convention of emptying a box right after it's done its job) -
        # that clear itself fires this same trace, which would otherwise
        # immediately reopen the popup on an empty query. This flag skips
        # that one, single reopen.
        self._suppress_next = False
        # Pending debounce timer id (see _on_text_changed) - None when no
        # query is currently scheduled.
        self._debounce_id: str | None = None

        variable.trace_add("write", lambda *_a: self._on_text_changed())
        entry.bind("<Down>", self._focus_list)
        entry.bind("<Escape>", lambda e: self._close_popup())
        entry.bind("<FocusOut>", self._on_entry_focus_out)
        # Also show it the moment the box is CLICKED, not only after the
        # first keystroke - the customer phone box in particular wants its
        # default "Walk-in" entry visible on a plain click, before typing
        # anything (min_chars=0 there lets an empty query still fetch and
        # render something). For a box with min_chars=1 this is a no-op
        # while the box is still empty (_on_text_changed just closes an
        # already-closed popup), so it's safe to wire up unconditionally.
        #
        # Deliberately bound to an actual click (<Button-1>), NOT
        # <FocusIn> - an earlier version used <FocusIn> here, which broke
        # closing this popup at all: picking a suggestion, and a
        # successful identify_customer() lookup, both clear this box and
        # call entry.focus_set() themselves right after closing it (to
        # leave the cursor ready for the next lookup) - and focus_set()
        # itself raises <FocusIn>, which immediately reopened the very
        # popup that closing code had just closed, showing "Walk-in"
        # again every single time. A real mouse click doesn't happen as a
        # side effect of our own code calling focus_set(), so binding
        # here instead means this only reopens when the cashier actually
        # clicks the box again, and a deliberate close (selection, Escape,
        # clicking away) actually stays closed.
        #
        # Deferred via after_idle rather than called directly: this click
        # is often the SAME click that's still in the middle of giving
        # the box its actual keyboard focus (Tk processes a widget's own
        # bindings before its class's default "focus %W" binding for the
        # same click), and _open_popup() below registers a fresh
        # root.bind_all("<Button-1>", ...) of its own the instant it
        # opens - if that happens WHILE this same original click is still
        # being dispatched, Tk applies that brand-new global binding to
        # the tail end of that very click's own dispatch, so the popup's
        # own "did this click land outside me?" check runs immediately
        # against the click that just opened it, and it closed itself on
        # the spot ("pop and disappear" again, this time only on boxes
        # that can open on a bare click rather than needing a keystroke
        # first). after_idle lets this click finish being handled first
        # (focus and all), then opens the popup fresh on its own turn.
        entry.bind("<Button-1>", lambda e: self._entry.after_idle(self._on_text_changed), add="+")

    def _on_text_changed(self) -> None:
        if self._suppress_next:
            self._suppress_next = False
            self._cancel_debounce()
            self._close_popup()
            return
        query = self._variable.get().strip()
        if len(query) < self._min_chars:
            self._cancel_debounce()
            self._close_popup()
            return
        # Debounced rather than querying/rendering on every single
        # keystroke: a barcode scanner "types" a whole code (often 8-13
        # digits) far faster than anyone could by hand, and each one of
        # those characters used to run its own synchronous DB query and
        # popup render right here - a burst of work that visibly lagged
        # behind the actual, near-instant scan, which is what showed up
        # as a scan seeming slow to register. Waiting for a short pause
        # collapses a whole fast-typed/scanned burst into one query.
        # <Return> (bound separately on the entry, not through this
        # class at all) always reads the fully-typed value directly and
        # immediately - so accuracy never depends on whether this timer
        # has fired yet, only the live preview's snappiness does.
        self._cancel_debounce()
        self._debounce_id = self._entry.after(120, self._run_query)

    def _cancel_debounce(self) -> None:
        if self._debounce_id is not None:
            try:
                self._entry.after_cancel(self._debounce_id)
            except Exception:  # noqa: BLE001 - entry may already be gone during teardown
                pass
            self._debounce_id = None

    def _run_query(self) -> None:
        self._debounce_id = None
        query = self._variable.get().strip()
        if len(query) < self._min_chars:
            self._close_popup()
            return
        try:
            matches = self._fetch_fn(query)
        except Exception:  # noqa: BLE001 - a bad suggestion query should never break typing
            matches = []
        self._matches = matches
        if not matches:
            self._close_popup()
            return
        self._render(matches)

    def _render(self, matches) -> None:
        if self._popup is None or not self._popup.winfo_exists():
            self._open_popup()
        tree = self._list_frame
        tree.delete(*tree.get_children())
        for display, _payload in matches:
            tree.insert("", "end", values=(display,))

    def _open_popup(self) -> None:
        # Same small-borderless-utility-window recipe as SearchableDropdown
        # above (see its own comments for why: "-toolwindow" instead of
        # overrideredirect so it still gets real focus/wheel input on
        # Windows, closed on an outside click rather than plain focus-out).
        popup = ctk.CTkToplevel(self._entry)
        popup.withdraw()
        popup.title("")
        try:
            popup.wm_attributes("-toolwindow", True)
        except tk.TclError:
            pass
        popup.attributes("-topmost", True)
        popup.configure(fg_color=theme.BG_LIGHT)
        popup.protocol("WM_DELETE_WINDOW", self._close_popup)
        self._popup = popup

        # Placed below the box normally, above it if there's not enough
        # room below - but a fixed 170px height for the "above" case could
        # still reach past the TOP of the screen on a box positioned high
        # up (e.g. this app's scrollable body scrolled so the box sits
        # near the top of the window), which clamped the popup down to
        # y=0 and let it cover the very box it belongs to - exactly what
        # made it impossible to see what was being typed. This instead
        # measures the actual room available above and below the box and
        # never asks for more popup height than genuinely fits on
        # whichever side has more of it, so it always ends up fully
        # beside the box, never on top of it, wherever that box happens
        # to be on screen.
        x = self._entry.winfo_rootx()
        entry_y = self._entry.winfo_rooty()
        entry_h = self._entry.winfo_height()
        popup_w = max(self._entry.winfo_width(), 260)
        screen_h = self._entry.winfo_screenheight()
        margin = 10
        desired_h = 170
        min_h = 60
        space_below = screen_h - (entry_y + entry_h) - margin
        space_above = entry_y - margin
        if space_below >= min_h and space_below >= space_above:
            y = entry_y + entry_h
            popup_h = max(min_h, min(desired_h, space_below))
        else:
            popup_h = max(min_h, min(desired_h, space_above))
            y = entry_y - popup_h
        popup.geometry(f"{popup_w}x{popup_h}+{x}+{y}")

        list_holder = ctk.CTkFrame(popup, fg_color="transparent")
        list_holder.pack(fill="both", expand=True, padx=4, pady=4)
        list_holder.grid_rowconfigure(0, weight=1)
        list_holder.grid_columnconfigure(0, weight=1)
        tree = ttk.Treeview(list_holder, columns=["value"], show="", height=6, selectmode="browse")
        tree.column("value", anchor="w")
        vsb = ttk.Scrollbar(list_holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        theme.configure_stripes(tree)
        self._list_frame = tree

        tree.bind("<ButtonRelease-1>", self._on_tree_click)
        tree.bind("<Return>", self._on_tree_return)
        tree.bind("<Escape>", lambda e: self._close_and_refocus())
        tree.bind("<Up>", self._on_tree_up_from_top)

        # Without its own wheel handler, scrolling over this list did
        # nothing here and fell straight through to whatever's bound
        # globally instead - which, on this screen, is the CTkScrollableFrame
        # the whole POS/Checkout page sits in (CustomTkinter wires that up
        # with bind_all, which really does mean EVERY widget in the app,
        # popups included, not just its own frame). So scrolling this
        # dropdown scrolled the page underneath it at the same time. Same
        # fix as SearchableDropdown's own list above: handle the wheel here
        # and "break" so it stops right here instead of reaching bind_all.
        def _popup_scroll(event):
            if getattr(event, "num", None) == 4:
                tree.yview_scroll(-2, "units")
            elif getattr(event, "num", None) == 5:
                tree.yview_scroll(2, "units")
            elif hasattr(event, "delta") and event.delta:
                accum = getattr(tree, "_popup_wheel_accum", 0.0) + event.delta
                rows = int(accum / 40.0)
                if rows != 0:
                    tree._popup_wheel_accum = accum - (rows * 40.0)
                    tree.yview_scroll(-rows, "units")
                else:
                    tree._popup_wheel_accum = accum
            return "break"

        tree.bind("<MouseWheel>", _popup_scroll)
        tree.bind("<Button-4>", _popup_scroll)  # Linux scroll up
        tree.bind("<Button-5>", _popup_scroll)  # Linux scroll down

        popup.deiconify()

        # Showing a brand-new Toplevel window can itself steal the actual
        # OS/window-manager input focus away from the entry, even though
        # nothing here ever calls focus on the popup or its list - some
        # window managers (particularly Windows, and particularly for a
        # "-topmost" utility window like this one) hand a newly-mapped
        # window the focus automatically. Reclaim it immediately so the
        # cashier keeps typing right where they were without noticing
        # anything happened - and, just as importantly, so anything that
        # asks Tk "which widget actually has the focus right now"
        # (POSScreen's scanner routing, self.focus_get() in
        # _current_focus_target) keeps seeing this entry as focused
        # instead of seeing this popup (or nothing at all).
        self._entry.focus_set()

        root = self._entry.winfo_toplevel()
        root.bind_all("<Button-1>", self._on_global_click)
        self._global_click_bound = True

    def _focus_list(self, event=None):
        if self._popup is None or not self._popup.winfo_exists():
            return None
        children = self._list_frame.get_children()
        if children:
            self._list_frame.focus_set()
            self._list_frame.selection_set(children[0])
            self._list_frame.focus(children[0])
        return "break"

    def _on_tree_up_from_top(self, event) -> str | None:
        sel = self._list_frame.selection()
        children = self._list_frame.get_children()
        if sel and children and sel[0] == children[0]:
            self._entry.focus_set()
            return "break"
        return None

    def _on_tree_click(self, event) -> None:
        row = self._list_frame.identify_row(event.y)
        if row:
            self._select_row(row)

    def _on_tree_return(self, event) -> None:
        sel = self._list_frame.selection()
        if sel:
            self._select_row(sel[0])

    def _select_row(self, row) -> None:
        idx = self._list_frame.index(row)
        if not (0 <= idx < len(self._matches)):
            return
        _display, payload = self._matches[idx]
        self._suppress_next = True
        self._variable.set("")
        self._close_popup()
        self._entry.focus_set()
        self._on_select(payload)

    def close(self) -> None:
        """Force this box's popup shut right now, regardless of why -
        <Return> in the entry deliberately isn't wired to anything in this
        class (see the class docstring), so a caller that commits on Enter
        needs to close a still-open popup itself rather than leaving it
        sitting there looking like it's still offering choices after the
        typed/scanned code has already been accepted."""
        self._close_popup()

    def suppress_next_change(self) -> None:
        """Call this immediately before anything OUTSIDE this box's own
        typing/selection flow sets its variable - e.g. resetting the
        customer phone box back to blank when this screen first shows, or
        after a completed sale. Without it, that .set("") is
        indistinguishable from the user's own typing, so it fires this
        box's normal "text changed" handling exactly like a keystroke
        would - which, for a box with min_chars=0, means popping its
        default suggestion (e.g. "Walk-in") open with nobody having
        clicked or typed anything. That's what made the customer box's
        list appear to open and immediately vanish right as this screen
        loads: on_show() resetting the box to "" opened it, and then
        moving focus to the search box a moment later (also part of
        on_show()) closed it again - all without the cashier doing
        anything. This skips exactly that one, external, non-typing
        change."""
        self._suppress_next = True

    def _on_global_click(self, event) -> None:
        if self._popup is None or not self._popup.winfo_exists():
            return
        widget = event.widget
        while widget is not None:
            # A click landing back on the entry itself - e.g. clicking to
            # move the cursor while the list is open - must NOT count as
            # "clicked away", same as SearchableDropdown's own `widget ==
            # self` check above. Missing this was the actual cause of the
            # list "popping and disappearing" the instant it opened: on
            # some platforms just creating the popup Toplevel generates a
            # stray click/focus event that this walk-up saw as landing
            # outside both the popup and the entry, and closed on the spot.
            if widget == self._popup or widget == self._entry:
                return
            widget = getattr(widget, "master", None)
        self._close_popup()

    def _on_entry_focus_out(self, event) -> None:
        # A click/arrow-down into the popup's own list moves focus AWAY
        # from the entry, which would otherwise close this immediately as
        # a plain focus-out - give it a moment to land first, same
        # reasoning SearchableDropdown's own comments give for not closing
        # on bare focus-out.
        self._entry.after(150, self._close_if_focus_elsewhere)

    def _close_if_focus_elsewhere(self) -> None:
        if self._popup is None or not self._popup.winfo_exists():
            return
        focused = self._entry.focus_get()
        w = focused
        while w is not None:
            # Same fix as _on_global_click above: focus landing back on
            # the entry itself (the normal case - just showing/creating
            # the popup Toplevel can flash a spurious FocusOut on the
            # entry even though nothing else ever actually took focus) is
            # NOT "focus moved elsewhere". Without this, that spurious
            # FocusOut's 150ms timer fired, found focus back on the entry
            # (not nested inside the popup), and closed the just-opened
            # popup immediately - the "pop and disappear" bug.
            if w == self._popup or w == self._entry:
                return
            w = getattr(w, "master", None)
        self._close_popup()

    def _close_and_refocus(self) -> None:
        self._close_popup()
        self._entry.focus_set()

    def _close_popup(self) -> None:
        # Also cancels any debounced query still waiting to fire (see
        # _on_text_changed) - otherwise a deliberate close via Escape,
        # clicking away, or picking a suggestion could get silently
        # reopened ~120ms later by a stale timer left over from
        # whatever was typed just before the close.
        self._cancel_debounce()
        popup, self._popup = self._popup, None
        if self._global_click_bound:
            try:
                self._entry.winfo_toplevel().unbind_all("<Button-1>")
            except Exception:  # noqa: BLE001 - window may already be gone during teardown
                pass
            self._global_click_bound = False
        if popup is not None and popup.winfo_exists():
            popup.destroy()


def run_in_background(widget: ctk.CTkBaseClass, work_fn, on_done, on_error=None):
    """Run a slow, DB-heavy computation (forecasting, association-rule
    mining - anything that can take seconds over a
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
        super().__init__(parent, fg_color=theme.BG_MAIN)
        self.app = app

    @property
    def conn(self):
        return self.app.conn

    def on_show(self):
        pass

    def header(self, text: str, subtitle: str = ""):
        """Professional page header with teal left accent bar.

        Used by every screen once (first call in __init__) to give a
        consistent, branded title bar across all pages.
        """
        hdr = ctk.CTkFrame(self, fg_color=theme.CARD_BG,
                            corner_radius=0, border_width=0, height=64)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        # Teal accent bar on the left
        ctk.CTkFrame(hdr, width=4, fg_color=theme.TEAL, corner_radius=0).pack(
            side="left", fill="y")

        text_col = ctk.CTkFrame(hdr, fg_color="transparent")
        text_col.pack(side="left", padx=(16, 0), pady=12)

        ctk.CTkLabel(text_col, text=text,
                     font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
                     text_color=theme.TEXT_DARK).pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(text_col, text=subtitle,
                         font=ctk.CTkFont(family="Segoe UI", size=11),
                         text_color=theme.TEXT_MUTED).pack(anchor="w")

        # Crisp bottom border
        ctk.CTkFrame(self, height=1, fg_color=theme.BORDER, corner_radius=0).pack(fill="x")




    def _open_phone_capture_dialog(self, on_captured, *, title="Scan Barcode with Phone",
                                    instruction="Scan the product's barcode", continuous=False,
                                    on_closed=None):
        """Open a small phone-scanner dialog that hands decoded barcode/QR
        text to on_captured(code) - used to fill in a text field (like the
        new-product barcode field in Inventory, or the search box in POS)
        from the phone's camera. Reuses the same background server the
        till's own Phone Scanner feature uses (POSScreen.open_phone_scanner_
        dialog), so if that's already running for cart scanning, this
        borrows it and hands it back exactly as it was - the till keeps
        working normally either way. Lives on BaseScreen (not just one
        screen) since any screen with a barcode-ish field can use it -
        `title`/`instruction` let a caller scanning something other than a
        product (a customer's membership QR, say) say so instead of the
        default product-barcode wording.

        continuous=False (the default) is the original one-shot behaviour:
        capture exactly one code, hand it to on_captured, and close. Some
        callers want to add a whole run of items one after another without
        re-opening this each time (Inventory's "Add / Scan Item" bulk-add
        flow, say) - continuous=True keeps this dialog open and calls
        on_captured(code) for EVERY scan instead, showing a running "Last
        scanned" line so the cashier can see each one land without looking
        away from the phone. Either way, this returns a restore() function
        - safe to call more than once, and safe even after this popup
        already closed itself - that gives the phone scanner's on_scan
        handler back and closes this popup if it's still open. A caller
        using continuous=True should call the returned restore() from ITS
        OWN close handler too (not just rely on this popup's own Done
        button), so that a scan arriving after the CALLER's own fields are
        gone can't try to act on them.

        on_closed, if given, fires exactly once, however this popup ends up
        closing (its own Done button, its window-close X, or the caller
        invoking the returned restore() itself) - a continuous=True caller
        that released its OWN window's modal grab to let this popup and its
        window both be clickable at once (see start_phone_scan in
        open_add_product_dialog for why that's needed) uses this to grab
        its window back the moment this one goes away, rather than leaving
        the caller's window stuck non-modal forever after."""
        if getattr(self.app, "mobile_scan_server", None) is None:
            self.app.mobile_scan_server = mobile_scan.MobileScanServer(lambda code: {"unknown": True})
        server = self.app.mobile_scan_server
        if not server.running:
            try:
                server.start()
            except OSError as e:
                messagebox.showerror("Could not start phone scanner", str(e))
                if on_closed is not None:
                    try:
                        on_closed()
                    except Exception:
                        pass
                return lambda: None

        previous_on_scan = server.on_scan
        state = {"restored": False}

        def restore():
            if not state["restored"]:
                state["restored"] = True
                server.on_scan = previous_on_scan
                if on_closed is not None:
                    try:
                        on_closed()
                    except Exception:
                        pass
            try:
                dialog.destroy()
            except Exception:
                pass  # already closed (or never got this far) - nothing to clean up

        def captured(code):
            if continuous:
                self.after(0, lambda: (
                    on_captured(code),
                    last_scanned_label.configure(text=f"✓ Scanned: {code}", text_color=theme.SUCCESS_GREEN),
                ))
            else:
                restore()
                self.after(0, lambda: on_captured(code))
            return {"name": code, "added": True}

        server.on_scan = captured

        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        fit_dialog(dialog, 400, 660 if continuous else 620)
        dialog.configure(fg_color=theme.BG_LIGHT)
        if not continuous:
            # One-shot capture (e.g. POS's search box, or Inventory before
            # continuous mode existed): the window this fills in is closed
            # or out of focus anyway the instant a code lands, so a modal
            # grab here was harmless.
            #
            # continuous=True is different, and grabbing here was an
            # actual bug, not just an inconvenience: this popup is meant
            # to stay open across a WHOLE RUN of scans (see the docstring
            # above), and the caller's own window is what shows each
            # scanned item's Save/Add-Stock button and its quantity/detail
            # fields - grab_set() made THIS popup the only clickable
            # window on screen, so there was no way to actually click that
            # Save button without closing this one first. That's exactly
            # the "every item I have to link the phone again" complaint -
            # closing this to click Save, then reopening "Scan with Phone"
            # (which shows the link screen again, even though it's really
            # the same still-running connection) for every single item.
            # Leaving this non-modal, like the till's own always-open
            # phone-scanner dialog (open_phone_scanner_dialog) already
            # does for the same reason, lets both windows stay up at once:
            # click the popup to scan the next item, click the caller's
            # window to fill in details and hit Save, without ever closing
            # either one until "Done".
            dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", restore)

        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(body, text=instruction, font=ctk.CTkFont(size=14, weight="bold"),
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

        if continuous:
            last_scanned_label = ctk.CTkLabel(body, text="Nothing scanned yet.",
                                               font=ctk.CTkFont(size=12, weight="bold"),
                                               text_color=theme.TEXT_MUTED)
            last_scanned_label.pack(pady=(0, 4))
            main_instruction = ("Point the camera at each item's barcode in turn - every one you "
                                 "scan fills it in and checks the catalog automatically, without "
                                 "coming back to this screen. Keep going through as many items as "
                                 "you like; tap Done below when you're finished scanning.")
        else:
            main_instruction = ("Point the camera at the barcode - it fills in the field "
                                 "automatically as soon as it's read, and this window closes on "
                                 "its own.")

        ctk.CTkLabel(
            body,
            text=main_instruction +
                 " (First-time on a new phone: it'll show a one-time \"Connection not private\" "
                 "warning - tap Advanced/Show Details then Proceed/visit this website. Also, if "
                 "Windows pops up a Firewall permission prompt the first time this runs on this "
                 "PC - it can appear behind this window - click \"Allow access\": until it's "
                 "allowed, the phone can't connect at all. Only needed once, ever.)",
            text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11), wraplength=340, justify="left",
        ).pack(padx=20, pady=(0, 14))

        styled_button(dialog, "Done" if continuous else "Cancel", restore, kind="secondary", width=140).pack(pady=(0, 16))

        return restore


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #

class DashboardScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("📊 Executive Dashboard", subtitle="Real-time overview of today's trading activity and KPI metrics")

        # Everything below goes in a scrollable frame so smaller screens
        # can still reach every section without cutting anything off.
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=0, pady=0)

        # ---- Section: KPI Cards -------------------------------------------
        section_label = ctk.CTkLabel(body, text="KEY PERFORMANCE INDICATORS",
                                      font=ctk.CTkFont(size=10, weight="bold"),
                                      text_color=theme.TEXT_MUTED)
        section_label.pack(anchor="w", padx=16, pady=(14, 6))

        self.cards_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.cards_frame.pack(fill="x", padx=12, pady=(0, 6))
        self.card_labels: dict[str, ctk.CTkLabel] = {}

        # (key, label, icon, accent_color)
        specs = [
            ("today_sales_total",       "Today's Sales",          "💰", theme.TEAL),
            ("today_invoice_count",      "Invoices Today",         "🧾", theme.INFO_BLUE),
            ("today_gross_margin_pct",   "Gross Margin %",         "📊", theme.SUCCESS_GREEN),
            ("low_stock_count",          "Low Stock Alerts",       "📦", theme.ACCENT_AMBER),
            ("expiring_within_7_days",   "Expiring in 7 Days",     "⏰", theme.DANGER_RED),
            ("historical_waste_value_lkr","Total Waste (LKR)",     "♻️",  theme.TEXT_MUTED),
        ]
        for i, (key, label, icon, accent) in enumerate(specs):
            card = ctk.CTkFrame(self.cards_frame, corner_radius=12, fg_color=theme.CARD_BG,
                                border_width=1, border_color=theme.BORDER)
            card.grid(row=i // 3, column=i % 3, padx=8, pady=8, sticky="nsew")
            self.cards_frame.grid_columnconfigure(i % 3, weight=1)

            # Coloured top accent strip
            ctk.CTkFrame(card, height=4, fg_color=accent, corner_radius=0).pack(fill="x")

            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="both", padx=16, pady=(10, 14))

            icon_row = ctk.CTkFrame(inner, fg_color="transparent")
            icon_row.pack(fill="x")
            ctk.CTkLabel(icon_row, text=icon, font=ctk.CTkFont(size=20)).pack(side="left")
            ctk.CTkLabel(icon_row, text=label, font=ctk.CTkFont(size=11),
                         text_color=theme.TEXT_MUTED).pack(side="left", padx=(8, 0))

            value_lbl = ctk.CTkLabel(inner, text="—",
                                      font=ctk.CTkFont(family="Segoe UI", size=28, weight="bold"),
                                      text_color=accent)
            value_lbl.pack(anchor="w", pady=(6, 0))
            self.card_labels[key] = value_lbl

        # ---- Section: Top Seller ------------------------------------------
        top_row = ctk.CTkFrame(body, fg_color="transparent")
        top_row.pack(fill="x", padx=12, pady=(4, 8))
        ts_card = ctk.CTkFrame(top_row, corner_radius=12, fg_color=theme.CARD_BG,
                                border_width=1, border_color=theme.BORDER)
        ts_card.pack(fill="x")
        ts_inner = ctk.CTkFrame(ts_card, fg_color="transparent")
        ts_inner.pack(fill="x", padx=16, pady=12)
        ctk.CTkLabel(ts_inner, text="🏆  TOP SELLER (30 days)",
                     font=ctk.CTkFont(size=10, weight="bold"), text_color=theme.TEXT_MUTED).pack(side="left")
        self.top_seller_label = ctk.CTkLabel(ts_inner, text="—",
                                              font=ctk.CTkFont(size=14, weight="bold"),
                                              text_color=theme.CHARCOAL_DARK)
        self.top_seller_label.pack(side="left", padx=(16, 0))

        # ---- Section: Insights -------------------------------------------
        ctk.CTkLabel(body, text="SMART INSIGHTS",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=theme.TEXT_MUTED).pack(anchor="w", padx=16, pady=(8, 6))

        insights_frame = ctk.CTkFrame(body, corner_radius=12, fg_color=theme.CARD_BG,
                                       border_width=1, border_color=theme.BORDER)
        insights_frame.pack(fill="x", padx=12, pady=(0, 10))
        self.insights_container = ctk.CTkFrame(insights_frame, fg_color="transparent")
        self.insights_container.pack(fill="x", padx=12, pady=12)

        # ---- Section: Ask AI ---------------------------------------------
        ctk.CTkLabel(body, text="ASK AI ASSISTANT",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=theme.TEXT_MUTED).pack(anchor="w", padx=16, pady=(8, 6))

        ask_frame = ctk.CTkFrame(body, corner_radius=12, fg_color=theme.CARD_BG,
                                  border_width=1, border_color=theme.BORDER)
        ask_frame.pack(fill="x", padx=12, pady=(0, 10))

        ask_hdr = ctk.CTkFrame(ask_frame, fg_color=theme.CHARCOAL_DARK, corner_radius=0)
        ask_hdr.pack(fill="x")
        ctk.CTkLabel(ask_hdr,
                     text="💬  Ask a question about your shop's data",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=theme.TEXT_ON_DARK).pack(anchor="w", padx=14, pady=10)

        ask_body = ctk.CTkFrame(ask_frame, fg_color="transparent")
        ask_body.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(
            ask_body,
            text='Try: "What do I need to buy?"  ·  "What is fast moving?"  ·  '
                 '"What promotion should I give?"  ·  "How much did I sell today?"',
            font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED, wraplength=900, justify="left",
        ).pack(anchor="w", pady=(0, 8))
        ask_row = ctk.CTkFrame(ask_body, fg_color="transparent")
        ask_row.pack(fill="x")
        self.ask_entry = ctk.CTkEntry(ask_row, placeholder_text="Type your question here...",
                                       height=38, corner_radius=8, border_color=theme.BORDER)
        self.ask_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.ask_entry.bind("<Return>", lambda _event: self.ask_question())
        self.ask_btn = styled_button(ask_row, "  Ask  ", self.ask_question, kind="primary",
                                      height=38, width=90)
        self.ask_btn.pack(side="left")

        # Answer card - hidden until a question is answered
        self.ask_answer_card = ctk.CTkFrame(ask_body, corner_radius=8, fg_color=theme.BG_MAIN)
        self.ask_answer_accent = ctk.CTkFrame(self.ask_answer_card, width=4, fg_color=theme.TEAL,
                                               corner_radius=0)
        self.ask_answer_accent.pack(side="left", fill="y")
        ask_answer_inner = ctk.CTkFrame(self.ask_answer_card, fg_color="transparent")
        ask_answer_inner.pack(side="left", fill="both", expand=True, padx=(12, 14), pady=12)
        self.ask_answer_icon = ctk.CTkLabel(ask_answer_inner, text="",
                                             font=ctk.CTkFont(size=16), text_color=theme.TEXT_DARK)
        self.ask_answer_icon.pack(side="left", anchor="n", padx=(0, 10))
        self.ask_answer_label = ctk.CTkLabel(
            ask_answer_inner, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_DARK,
            wraplength=820, justify="left", anchor="w",
        )
        self.ask_answer_label.pack(side="left", fill="both", expand=True)
        # Not packed here on purpose - shown for the first time in ask_question()/done().

        # ---- Bottom action row -------------------------------------------
        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 16))
        styled_button(btn_row, "↺  Refresh", self.refresh, kind="secondary",
                      height=36).pack(side="left", padx=(0, 8))
        styled_button(btn_row, "⚠  Rebuild Demo Data", self.rebuild_demo_data,
                      kind="danger", height=36).pack(side="left")



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

        for w in self.insights_container.winfo_children():
            w.destroy()
        for card in rag_insights.dashboard_insights(self.conn):
            icon, accent = theme.insight_style(card["kind"])
            row = ctk.CTkFrame(self.insights_container, corner_radius=8, fg_color=theme.BG_LIGHT)
            row.pack(fill="x", pady=4)
            ctk.CTkFrame(row, width=4, fg_color=accent, corner_radius=0).pack(side="left", fill="y")
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(side="left", fill="both", expand=True, padx=(12, 14), pady=10)
            ctk.CTkLabel(inner, text=icon, font=ctk.CTkFont(size=15), text_color=theme.TEXT_DARK).pack(
                side="left", anchor="n", padx=(0, 10))
            ctk.CTkLabel(inner, text=card["text"], font=ctk.CTkFont(size=12), text_color=theme.TEXT_DARK,
                         wraplength=820, justify="left", anchor="w").pack(side="left", fill="both", expand=True)

    def ask_question(self):
        question = self.ask_entry.get().strip()
        if not question:
            return
        self.ask_btn.configure(state="disabled", text="Thinking...")

        def work():
            # This runs on a background thread (see run_in_background's
            # docstring) - sqlite3 connections can't cross threads, so this
            # opens its own rather than reusing self.conn. Reusing self.conn
            # here previously raised "SQLite objects created in a thread can
            # only be used in that same thread" on every single question,
            # which is what made the Ask-a-question box look completely
            # broken - it wasn't the RAG logic, it was this thread handoff.
            conn = db_module.get_conn(self.app.db_path)
            try:
                return rag_insights.answer_question(conn, question)
            finally:
                conn.close()

        def show_answer(text: str, kind: str):
            icon, accent = theme.insight_style(kind)
            self.ask_answer_icon.configure(text=icon)
            self.ask_answer_accent.configure(fg_color=accent)
            self.ask_answer_label.configure(text=text)
            if not self.ask_answer_card.winfo_ismapped():
                self.ask_answer_card.pack(fill="x", padx=16, pady=(10, 14))

        def done(result: dict):
            self.ask_btn.configure(state="normal", text="Ask")
            show_answer(result["text"], result["intent"] or "unmatched")

        def on_error(exc: Exception):
            self.ask_btn.configure(state="normal", text="Ask")
            show_answer(f"Sorry, something went wrong answering that: {exc}", "error")

        run_in_background(self, work, done, on_error)

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
    fit_dialog(dialog, 360, 340)
    dialog.configure(fg_color=theme.BG_LIGHT)
    dialog.grab_set()

    # Fields go in a scrollable body, packed BEFORE the button row below -
    # guarantees the buttons their own space regardless of Windows display
    # scaling; see open_login_dialog in app.py for the full reasoning.
    body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
    body.pack(fill="both", expand=True)

    ctk.CTkLabel(body, text=f"Total to collect: LKR {total:,.2f}",
                 font=ctk.CTkFont(size=14, weight="bold"), text_color=theme.NAVY_DARK).pack(pady=(16, 10))

    method_vars: dict[str, tk.StringVar] = {}
    for method in ["cash", "card", "cheque", "credit"]:
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=4)
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

        # ---- Professional POS Header (full-width dark bar) ----------------
        # This replaces the plain header() other screens use - Checkout needs
        # live status items (cashier name, offline badge, customer) visible
        # at all times, not just a static title.
        pos_hdr = ctk.CTkFrame(self, fg_color=theme.CHARCOAL_DARK, corner_radius=0, height=62)
        pos_hdr.pack(fill="x")
        pos_hdr.pack_propagate(False)

        # Left: title + offline badge
        hdr_left = ctk.CTkFrame(pos_hdr, fg_color="transparent")
        hdr_left.pack(side="left", padx=18, pady=12)
        ctk.CTkLabel(hdr_left, text="🛒  CHECKOUT",
                     font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
                     text_color=theme.TEXT_ON_DARK).pack(side="left")
        self.offline_badge = ctk.CTkLabel(hdr_left, text="",
                                           font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                                           text_color=theme.DANGER_RED)
        self.offline_badge.pack(side="left", padx=(14, 0))

        # Right: modern cashier pill chip
        hdr_right = ctk.CTkFrame(pos_hdr, fg_color=theme.CHARCOAL_MED, corner_radius=20,
                                 border_width=1, border_color=theme.BORDER_DARK)
        hdr_right.pack(side="right", padx=16, pady=12)
        hdr_right_inner = ctk.CTkFrame(hdr_right, fg_color="transparent")
        hdr_right_inner.pack(padx=14, pady=6)
        ctk.CTkLabel(hdr_right_inner, text="CASHIER:", font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
                     text_color="#94A3B8").pack(side="left", padx=(0, 6))
        self.cashier_label = ctk.CTkLabel(hdr_right_inner, text="Not logged in",
                                           font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                                           text_color="#CBD5E1")
        self.cashier_label.pack(side="left")

        # Thin teal accent divider under header
        ctk.CTkFrame(self, height=2, fg_color=theme.TEAL, corner_radius=0).pack(fill="x")


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

        # ---- Search card -------------------------------------------------
        # Search/find-an-item lives right at the top of the screen - the first
        # thing a cashier reaches the moment this screen opens, so a scan or
        # typed search can start immediately. The barcode/name boxes further down
        # (right above the checkout list) are a second, quicker entry point
        # once a sale is already under way.
        search_card = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10,
                                    border_width=1, border_color=theme.BORDER)
        search_card.pack(fill="x", padx=12, pady=(10, 0))

        search_hdr = ctk.CTkFrame(search_card, fg_color=theme.CHARCOAL_DARK, corner_radius=0)
        search_hdr.pack(fill="x")
        ctk.CTkLabel(search_hdr, text="🔍  PRODUCT SEARCH",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=theme.TEXT_ON_DARK).pack(side="left", padx=14, pady=8)

        search_row = ctk.CTkFrame(search_card, fg_color="transparent")
        search_row.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(search_row, text="Scan / Search:", text_color=theme.TEXT_MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=0, column=0, sticky="w")
        self.search_var = tk.StringVar()
        self.search_entry = ctk.CTkEntry(search_row, textvariable=self.search_var, width=280,
                                          height=34, corner_radius=6,
                                          placeholder_text="Barcode or product name…")
        self.search_entry.grid(row=1, column=0, sticky="w")
        self.search_entry.bind("<Return>", lambda e: self.do_search())
        # Live-as-you-type: fires on every keystroke (not just Enter/Search),
        # so the results list below fills in with whatever matches the
        # letters typed so far - a cashier doesn't have to finish typing or
        # press anything to see candidates. Debounced (see
        # _on_search_changed) rather than querying on every single
        # keystroke, since a barcode scanner "types" a whole code far
        # faster than anyone could by hand.
        self._search_debounce_id: str | None = None
        self.search_var.trace_add("write", lambda *a: self._on_search_changed())
        # Remembers that this box had the cursor last, out of every input
        # on this screen a scan could plausibly land in - see
        # _route_scanned_code, which the one "Link Phone Scanner" button
        # (in the sidebar) now routes every scan through instead of each
        # box needing its own separate "Scan with Phone" button.
        self.search_entry.bind("<FocusIn>", lambda e: self._mark_focus("search"))
        # <FocusIn> alone turned out not to be trustworthy enough to base
        # scanner routing on - cashiers reported a scan still landing in
        # the "Add item - barcode" box even right after clicking into
        # this one. A plain mouse click or a keystroke landing in this
        # box is about as certain a signal as Tkinter offers that this is
        # genuinely the box in use right now, so both are wired to the
        # same flag as a second, independent way of catching it (add="+"
        # so this doesn't replace the <FocusIn> binding above, and
        # <Button-1>/<KeyRelease> because a widget always gets its own
        # literal click/keystroke, unlike focus-change notifications).
        self.search_entry.bind("<Button-1>", lambda e: self._mark_focus("search"), add="+")
        self.search_entry.bind("<KeyRelease>", lambda e: self._mark_focus("search"), add="+")
        # No separate Search button - typing already searches live
        # (_on_search_changed, bound to search_var's trace below), and
        # Enter still runs do_search directly for a scanner's Enter/wedge
        # keystroke, so the button was a redundant extra click/target.

        ctk.CTkLabel(search_row, text="Category:", text_color=theme.TEXT_MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=0, column=1, padx=(16, 0), sticky="w")
        self.category_var = tk.StringVar(value="All categories")
        self.category_menu = SearchableDropdown(
            search_row, values=["All categories"], variable=self.category_var, width=160, label="Category")
        self.category_menu.grid(row=1, column=1, padx=(16, 0))
        # Picking a category re-runs the live search immediately (same as
        # typing a letter) so it narrows/filters what's already been typed,
        # rather than waiting for another keystroke.
        self.category_var.trace_add("write", lambda *a: self._on_search_changed())

        # Qty + Add button in the same row, on the right
        ctk.CTkLabel(search_row, text="Qty:", text_color=theme.TEXT_MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=0, column=2, padx=(16, 0), sticky="w")
        self.qty_var = tk.StringVar(value="1")
        ctk.CTkEntry(search_row, textvariable=self.qty_var, width=60, height=34,
                     corner_radius=6).grid(row=1, column=2, padx=(16, 0), sticky="w")
        styled_button(search_row, "＋ Add to Cart", self.add_selected_to_cart,
                      kind="primary", height=34, width=130).grid(row=1, column=3, padx=(10, 0), sticky="w")

        # A quiet inline "not found" label (not a modal popup) - a popup is a
        # modal dialog that steals the keyboard/mouse and brings a continuous
        # scanning session to a dead stop the moment ONE scanned code doesn't
        # match anything. This label says the same thing without blocking.
        self.search_status_label = ctk.CTkLabel(
            search_card, text="", text_color=theme.DANGER_RED, font=ctk.CTkFont(size=11),
            wraplength=560, justify="left",
        )
        self.search_status_label.pack(fill="x", padx=14, pady=(0, 4), anchor="w")

        # Search results compact table
        results_card_inner = ctk.CTkFrame(search_card, fg_color=theme.BG_MAIN, corner_radius=6)
        results_card_inner.pack(fill="x", padx=12, pady=(0, 12))
        self.results_tree = make_treeview(
            results_card_inner, ["Code", "Name", "Cash Price", "Stock"],
            {"Code": 90, "Name": 300, "Cash Price": 100, "Stock": 80},
            rows=5,
        )
        # .configure(height=...) alone doesn't stick here: by default a
        # frame resizes itself to fit whatever its own children ask for
        # (grid_propagate defaults to True), so the treeview's own natural
        # height silently overrode the 140px request - this box was meant
        # to be a compact 5-row search-results list, not one stretching to
        # fill most of the window. grid_propagate(False) makes the
        # requested height actually stick, and it now scrolls internally
        # (make_treeview's own scrollbar) once live search fills it with
        # more matches than fit in those 5 rows.
        self.results_tree.master_frame.configure(height=140)
        self.results_tree.master_frame.grid_propagate(False)
        self.results_tree.master_frame.pack(fill="x")
        self.results_tree.bind("<Double-1>", lambda e: self.add_selected_to_cart())

        self._search_results: list = []



        # ---- Sale Settings card (Tier / Payment / Customer) ---------------
        # Per-sale settings (tier/payment/customer) - things you set once and
        # rarely touch again mid-sale. Customer lookup moved here from the old
        # search row - who's serving and who's being served sit together.
        sale_card = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10,
                                  border_width=1, border_color=theme.BORDER)
        sale_card.pack(fill="x", padx=12, pady=(10, 0))

        sale_hdr = ctk.CTkFrame(sale_card, fg_color=theme.CHARCOAL_DARK, corner_radius=0)
        sale_hdr.pack(fill="x")
        ctk.CTkLabel(sale_hdr, text="⚙  SALE SETTINGS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=theme.TEXT_ON_DARK).pack(side="left", padx=14, pady=8)

        top = ctk.CTkFrame(sale_card, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(top, text="Price Tier:", text_color=theme.TEXT_MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=0, column=0, sticky="w")
        self.tier_var = tk.StringVar(value="cash")
        ctk.CTkOptionMenu(top, values=["cash", "credit", "wholesale"], variable=self.tier_var,
                          height=32, fg_color=theme.BG_MAIN, text_color=theme.TEXT_DARK,
                          button_color=theme.TEAL, button_hover_color=theme.TEAL_HOVER,
                          corner_radius=6).grid(row=1, column=0)

        ctk.CTkLabel(top, text="Payment:", text_color=theme.TEXT_MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=0, column=1, padx=(16, 0), sticky="w")
        self.payment_var = tk.StringVar(value="cash")
        ctk.CTkOptionMenu(top, values=["cash", "card", "cheque", "credit", "split"],
                          variable=self.payment_var, height=32,
                          fg_color=theme.BG_MAIN, text_color=theme.TEXT_DARK,
                          button_color=theme.TEAL, button_hover_color=theme.TEAL_HOVER,
                          corner_radius=6).grid(row=1, column=1, padx=(16, 0))

        # One box does the whole job of attaching a customer to this sale -
        # a phone number IS their loyalty ID (see customers.py). Type a
        # number and press Enter (live suggestions drop down as you type -
        # no separate Search button needed any more) to identify them -
        # their name then appears right next to the box, inline, in
        # customer_found_label. Leave it blank for an anonymous Walk-in
        # sale, which is the default.
        ctk.CTkLabel(top, text="Customer (phone):", text_color=theme.TEXT_MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=0, column=2, padx=(16, 0), sticky="w")
        customer_lookup_row = ctk.CTkFrame(top, fg_color="transparent")
        customer_lookup_row.grid(row=1, column=2, padx=(16, 0), sticky="w")
        self.customer_lookup_var = tk.StringVar()
        self.customer_lookup_entry = ctk.CTkEntry(customer_lookup_row,
                                                   textvariable=self.customer_lookup_var,
                                                   width=160, height=32, corner_radius=6,
                                                   placeholder_text="Phone number…")
        self.customer_lookup_entry.pack(side="left")
        self.customer_lookup_entry.bind("<Return>", lambda e: self.identify_customer(self.customer_lookup_var.get()))
        self.customer_lookup_entry.bind("<FocusIn>", lambda e: self._mark_focus("customer"))
        # See the matching comment on search_entry above - <FocusIn> alone
        # wasn't reliable enough for scanner routing, so a click or a
        # keystroke landing here also marks it as the current box.
        self.customer_lookup_entry.bind(
            "<Button-1>", lambda e: self._mark_focus("customer"), add="+")
        self.customer_lookup_entry.bind(
            "<KeyRelease>", lambda e: self._mark_focus("customer"), add="+")
        # Right next to the box, not below it - the moment a phone number
        # resolves to someone, their name appears here immediately, same
        # row, no separate loyalty tag alongside it (whether the sale
        # counts as a loyalty sale is still tracked internally via
        # _current_is_loyalty() for offer pricing - it just isn't its own
        # label on screen any more).
        self.customer_found_label = ctk.CTkLabel(customer_lookup_row, text="Walk-in",
                                                  font=ctk.CTkFont(size=12, weight="bold"),
                                                  text_color=theme.TEXT_MUTED)
        self.customer_found_label.pack(side="left", padx=(10, 0))
        # Live suggestions as digits (or a name) go in - matching customers
        # drop down right below, phone-prefix matches first; picking one
        # identifies them immediately, same as typing the full number and
        # pressing Enter would. min_chars=0 (rather than the usual 1) so
        # this also opens on a plain click into an still-empty box,
        # showing "Walk-in" as a default, explicit option to pick - the
        # same reset Enter-on-an-empty-box already does, just discoverable
        # without needing to know that shortcut.
        def _customer_suggestions(q: str):
            if not q:
                return [("🚶 Walk-in (not a loyalty customer)", None)]
            return [(f"{c['phone']} — {c['name']}", c) for c in customers_module.search_customers(self.conn, q)]

        def _on_customer_suggestion(payload):
            self.identify_customer(payload["phone"] if payload is not None else "")

        # Kept as an attribute (unlike the other SuggestionBox instances
        # below) so code elsewhere on this screen that resets this box's
        # text from OUTSIDE normal typing can call
        # customer_suggestion_box.suppress_next_change() first - see that
        # method's own docstring for why that's needed here specifically.
        self.customer_suggestion_box = SuggestionBox(
            self.customer_lookup_entry, self.customer_lookup_var,
            fetch_fn=_customer_suggestions, on_select=_on_customer_suggestion, min_chars=0,
        )

        # Internal state only - not shown as a widget of its own any more.
        # Tracks whichever customer (by name) is currently attached to the
        # sale; identify_customer() sets it after a successful phone
        # lookup, "Walk-in" otherwise. Recompute the cart's offer discounts
        # the moment it changes - a loyalty-only offer switches on/off with
        # it, so a line added before the customer was identified needs
        # re-pricing too, not just lines added afterward.
        self.customer_var = tk.StringVar(value="Walk-in")
        self.customer_var.trace_add("write", lambda *a: self._on_customer_changed())

        # Cashier label is now in the POS header bar (pos_hdr / hdr_right),
        # no longer duplicated in the `top` sale-settings row.

        # ---- Quick Items panel (top sellers / customer favourites) ---------
        quick_card = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10,
                                   border_width=1, border_color=theme.BORDER)
        quick_card.pack(fill="x", padx=12, pady=(10, 0))
        qi_hdr = ctk.CTkFrame(quick_card, fg_color=theme.CHARCOAL_DARK, corner_radius=0)
        qi_hdr.pack(fill="x")
        ctk.CTkLabel(qi_hdr, text="⚡  QUICK ADD",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=theme.TEXT_ON_DARK).pack(side="left", padx=14, pady=8)
        self.quick_items_frame = ctk.CTkFrame(quick_card, fg_color="transparent")
        self.quick_items_frame.pack(fill="x", padx=12, pady=10)
        self.tier_var.trace_add("write", lambda *a: self._refresh_quick_items())



        # ---- Add Item card (barcode / name direct entry) ------------------
        # A second way to add an item, right above the checkout list itself
        # rather than at the search box at the top of the screen - two
        # separate, dedicated boxes (barcode, and item name) rather than
        # one box guessing which of the two you meant, each with its own
        # live suggestion dropdown (SuggestionBox) so a cashier can either
        # finish typing/scanning an exact value or just pick a match off
        # the list. One shared Qty and "Add" apply to whichever of the two
        # currently has something typed in it.
        add_card = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10,
                                 border_width=1, border_color=theme.BORDER)
        add_card.pack(fill="x", padx=12, pady=(10, 0))
        add_hdr = ctk.CTkFrame(add_card, fg_color=theme.CHARCOAL_DARK, corner_radius=0)
        add_hdr.pack(fill="x")
        ctk.CTkLabel(add_hdr, text="📥  ADD ITEM DIRECTLY",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=theme.TEXT_ON_DARK).pack(side="left", padx=14, pady=8)

        add_item_row = ctk.CTkFrame(add_card, fg_color="transparent")
        add_item_row.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(add_item_row, text="Barcode:", text_color=theme.TEXT_MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=0, column=0, sticky="w")
        self.cart_add_barcode_var = tk.StringVar()
        self.cart_add_barcode_entry = ctk.CTkEntry(add_item_row, textvariable=self.cart_add_barcode_var,
                                                    width=160, height=32, corner_radius=6)
        self.cart_add_barcode_entry.grid(row=1, column=0, sticky="w")
        self.cart_add_barcode_entry.bind("<Return>", lambda e: self.add_item_by_barcode())
        self.cart_add_barcode_entry.bind("<FocusIn>", lambda e: self._mark_focus("barcode"))
        # See the matching comment on search_entry above.
        self.cart_add_barcode_entry.bind(
            "<Button-1>", lambda e: self._mark_focus("barcode"), add="+")
        self.cart_add_barcode_entry.bind(
            "<KeyRelease>", lambda e: self._mark_focus("barcode"), add="+")
        SuggestionBox(
            self.cart_add_barcode_entry, self.cart_add_barcode_var,
            fetch_fn=lambda q: [(f"{p['code']} — {p['name_en']}", p) for p in pos.search_products_by_code(self.conn, q)],
            on_select=self._add_product_from_suggestion(self.cart_add_barcode_entry),
        )

        ctk.CTkLabel(add_item_row, text="Item Name:", text_color=theme.TEXT_MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=0, column=1, padx=(16, 0), sticky="w")
        self.cart_add_name_var = tk.StringVar()
        self.cart_add_name_entry = ctk.CTkEntry(add_item_row, textvariable=self.cart_add_name_var,
                                                 width=200, height=32, corner_radius=6)
        self.cart_add_name_entry.grid(row=1, column=1, padx=(16, 0), sticky="w")
        self.cart_add_name_entry.bind("<Return>", lambda e: self.add_item_by_name())
        self.cart_add_name_entry.bind("<FocusIn>", lambda e: self._mark_focus("name"))
        # See the matching comment on search_entry above.
        self.cart_add_name_entry.bind(
            "<Button-1>", lambda e: self._mark_focus("name"), add="+")
        self.cart_add_name_entry.bind(
            "<KeyRelease>", lambda e: self._mark_focus("name"), add="+")
        SuggestionBox(
            self.cart_add_name_entry, self.cart_add_name_var,
            fetch_fn=lambda q: [(f"{p['name_en']} ({p['code']})", p) for p in pos.search_products(self.conn, q)],
            on_select=self._add_product_from_suggestion(self.cart_add_name_entry),
        )

        ctk.CTkLabel(add_item_row, text="Qty:", text_color=theme.TEXT_MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=0, column=2, padx=(16, 0), sticky="w")
        self.cart_add_qty_var = tk.StringVar(value="1")
        ctk.CTkEntry(add_item_row, textvariable=self.cart_add_qty_var, width=60,
                     height=32, corner_radius=6).grid(row=1, column=2, padx=(16, 0), sticky="w")
        styled_button(add_item_row, "＋ Add", self._add_item_from_whichever_box_has_text,
                      kind="primary", height=32, width=90).grid(row=1, column=3, padx=(10, 0), sticky="w")

        # Same reasoning as search_status_label above - a "not found" here
        # is exactly as likely to come from a continuous scanning session
        # as the top search box is (this pair is what a scan lands in
        # once the cashier's already clicked into one of them for this
        # sale), so it gets the same non-blocking treatment instead of a
        # popup that would stop every scan after it cold.
        self.cart_add_status_label = ctk.CTkLabel(
            add_item_row, text="", text_color=theme.DANGER_RED, font=ctk.CTkFont(size=11),
            wraplength=560, justify="left",
        )
        self.cart_add_status_label.grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))
        # add_item_by_barcode/add_item_by_name only clear this label when
        # THEY run (Enter, the Add button, or a scan landing here) - typing
        # or deleting text without triggering either of those (e.g.
        # clearing the box by hand after a "Not found" line appeared, or
        # just backspacing it out) left a stale message sitting there with
        # nothing left in the box to explain it, reported directly. These

        # traces clear it on every change to either box - typed, deleted,
        # or a suggestion picked - same as search_var's own trace already
        # does for search_status_label above; no debounce needed since
        # clearing a label is free, unlike that box's live results query.
        self.cart_add_barcode_var.trace_add("write", self._on_cart_add_changed)
        self.cart_add_name_var.trace_add("write", self._on_cart_add_changed)

        # ---- Cart card (bordered) ----------------------------------------
        # fill="both" WITHOUT expand=True: inside a CTkScrollableFrame, "expand"
        # fights the scrollbar's own content sizing. Fixed row count + internal
        # scrollbar keeps height predictable.
        cart_card = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10,
                                  border_width=1, border_color=theme.BORDER)
        cart_card.pack(fill="both", padx=12, pady=(8, 0))

        cart_hdr = ctk.CTkFrame(cart_card, fg_color=theme.CHARCOAL_DARK, corner_radius=0)
        cart_hdr.pack(fill="x")
        ctk.CTkLabel(cart_hdr, text="🛒  CART ITEMS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=theme.TEXT_ON_DARK).pack(side="left", padx=14, pady=8)

        cart_body = ctk.CTkFrame(cart_card, fg_color="transparent")
        cart_body.pack(fill="both", padx=4, pady=4)
        self.cart_tree = make_treeview(
            cart_body, ["Item", "Qty", "Unit Price", "Discount", "Line Total"],
            {"Item": 280, "Qty": 70, "Unit Price": 90, "Discount": 90, "Line Total": 100},
            rows=10,
        )
        self.cart_tree.master_frame.pack(fill="both")
        # Prefills the qty box below with whatever's already on the line
        # picked - a cashier adjusting "3" to "5" shouldn't have to first
        # go check what it currently says.
        self.cart_tree.bind("<<TreeviewSelect>>", lambda e: self._on_cart_line_selected())

        # ---- Edit-qty row (inside the cart card) -------------------------
        eq_row = ctk.CTkFrame(cart_card, fg_color="transparent")
        eq_row.pack(fill="x", padx=12, pady=(4, 8))
        ctk.CTkLabel(eq_row, text="Selected line qty:", text_color=theme.TEXT_MUTED,
                     font=ctk.CTkFont(size=11)).pack(side="left")
        self.cart_edit_qty_var = tk.StringVar()
        ctk.CTkEntry(eq_row, textvariable=self.cart_edit_qty_var, width=70, height=32,
                     corner_radius=6).pack(side="left", padx=6)
        styled_button(eq_row, "Update Qty", self.update_selected_line_qty,
                      kind="secondary", height=32).pack(side="left")
        styled_button(eq_row, "✕ Remove Line", self.remove_selected_line,
                      kind="danger", height=32).pack(side="left", padx=6)
        styled_button(eq_row, "Clear All", self.clear_cart,
                      kind="secondary", height=32).pack(side="left")

        # ---- Totals + Checkout summary bar --------------------------------
        summary_bar = ctk.CTkFrame(body, fg_color=theme.CHARCOAL_DARK,
                                    corner_radius=12, border_width=1, border_color=theme.BORDER_DARK)
        summary_bar.pack(fill="x", padx=12, pady=(12, 0))

        # Left: Net total + discount controls
        sb_left = ctk.CTkFrame(summary_bar, fg_color="transparent")
        sb_left.pack(side="left", padx=20, pady=14)

        self.total_label = ctk.CTkLabel(sb_left, text="Net Total: LKR 0.00",
                                         font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
                                         text_color=theme.TEXT_ON_DARK)
        self.total_label.pack(anchor="w")

        # A one-off cashier-typed discount for this sale only (Admin/Owner only).
        # Separate from automatic Offers/Promotions discounts. Spread proportionally
        # across cart lines at checkout (see checkout()) for per-line receipt records.
        disc_row = ctk.CTkFrame(sb_left, fg_color="transparent")
        disc_row.pack(anchor="w", pady=(8, 0))
        ctk.CTkLabel(disc_row, text="Manual Discount:", text_color="#94A3B8",
                     font=ctk.CTkFont(family="Segoe UI", size=11)).pack(side="left")
        self.discount_mode_var = tk.StringVar(value="LKR")
        self.discount_mode_var.trace_add("write", lambda *a: self._update_payable_label())
        self.discount_mode_menu = ctk.CTkOptionMenu(
            disc_row, values=["LKR", "%"], variable=self.discount_mode_var,
            width=70, height=30, fg_color=theme.CHARCOAL_MED,
            button_color=theme.CHARCOAL_HOVER, text_color=theme.TEXT_ON_DARK,
            corner_radius=6, state="disabled")
        self.discount_mode_menu.pack(side="left", padx=(8, 0))
        self.manual_discount_var = tk.StringVar(value="0")
        self.manual_discount_var.trace_add("write", lambda *a: self._update_payable_label())
        self.manual_discount_entry = ctk.CTkEntry(
            disc_row, textvariable=self.manual_discount_var, width=96, height=30,
            corner_radius=6, fg_color=theme.CHARCOAL_MED, text_color=theme.TEXT_ON_DARK,
            border_color=theme.BORDER_DARK, state="disabled")
        self.manual_discount_entry.pack(side="left", padx=(6, 0))

        # Right: Payable amount + big Checkout button
        # Payable sits directly above the Checkout button - it's the last figure
        # a cashier's eye lands on before pressing Checkout.
        sb_right = ctk.CTkFrame(summary_bar, fg_color="transparent")
        sb_right.pack(side="right", padx=20, pady=12)

        self.payable_label = ctk.CTkLabel(
            sb_right, text="Payable: LKR 0.00",
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=theme.SUCCESS_GREEN)
        self.payable_label.pack(anchor="e")

        styled_button(sb_right, "✓  PROCEED TO CHECKOUT", self.checkout, kind="success",
                      width=220, height=44,
                      font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold")).pack(anchor="e", pady=(6, 0))

        # ---- Action buttons row (Hold / Resume / Return / Reprint / Void) --
        # grid (not pack/side="left") so these shrink to fit a narrower window
        # or a smaller/scaled-up-DPI screen instead of running off the edge.
        bottom2 = ctk.CTkFrame(body, fg_color="transparent")
        bottom2.pack(fill="x", padx=12, pady=(8, 16))
        for col in range(5):
            bottom2.grid_columnconfigure(col, weight=1, uniform="bottom2")
        styled_button(bottom2, "⏸  Hold Invoice", self.hold_invoice, kind="secondary",
                      height=36).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        styled_button(bottom2, "▶  Resume Held", self.resume_invoice, kind="secondary",
                      height=36).grid(row=0, column=1, sticky="ew", padx=4)
        styled_button(bottom2, "↩  Return / Refund", self.open_return_dialog, kind="danger",
                      height=36).grid(row=0, column=2, sticky="ew", padx=4)
        styled_button(bottom2, "🖨  Reprint Receipt", self.open_reprint_dialog, kind="secondary",
                      height=36).grid(row=0, column=3, sticky="ew", padx=4)
        styled_button(bottom2, "⊗  Void Sale", self.open_void_dialog, kind="danger",
                      height=36).grid(row=0, column=4, sticky="ew", padx=(4, 0))



        # Which of this screen's own text boxes last had the keyboard
        # cursor - "search" by default, since on_show() below focuses the
        # search box the moment this screen opens. This is the ONE thing
        # that decides where a linked phone's scan lands (_route_scanned_
        # code): rather than a separate "Scan with Phone" button next to
        # every single box, there's one "Link Phone Scanner" button (now in
        # the app's left sidebar), and once it's linked, a scan just goes
        # wherever you were about to type anyway - exactly like a real
        # USB/Bluetooth scanner does.
        self._mark_focus("search")

    def on_show(self):
        if isinstance(self.app.conn, offline_sync.OfflineConnection) and not self.app.conn.is_online:
            pending = self.app.conn.pending_count
            self.offline_badge.configure(
                text=f"\u26a0 OFFLINE - {pending} queued, will sync automatically" if pending
                     else "\u26a0 OFFLINE - sales queue locally until reconnected"
            )
        else:
            self.offline_badge.configure(text="")

        # No dropdown to refresh any more - just make sure whoever was
        # attached to the sale (if not Walk-in) is still a real, active
        # customer before trusting that name for pricing/quick-items.
        if self.customer_var.get() != "Walk-in":
            still_active = self.conn.execute(
                "SELECT id FROM customers WHERE active=1 AND name=?", (self.customer_var.get(),)
            ).fetchone()
            if still_active is None:
                self.customer_var.set("Walk-in")

        categories = ["All categories"] + pos.list_categories(self.conn)
        self.category_menu.configure(values=categories)
        if self.category_var.get() not in categories:
            self.category_var.set("All categories")

        if self.app.logged_in:
            staff = self.conn.execute("SELECT name FROM staff WHERE id=?", (self.app.current_staff_id,)).fetchone()
            self.cashier_label.configure(text=staff["name"] if staff else "Unknown",
                                          text_color=theme.TEAL)
        else:
            self.cashier_label.configure(text="Not logged in",
                                          text_color="#94A3B8")


        # The checkout discount (LKR or %) is Admin/Owner only - re-checked
        # every time this screen shows, since login/logout happens from the
        # sidebar and app.py's refresh_current_frame() re-runs on_show()
        # right after (see the comment there). A plain cashier gets both
        # controls disabled and any leftover value cleared, rather than a
        # discount silently surviving past the Admin who set it logging out.
        discount_state = "normal" if self._is_admin() else "disabled"
        self.discount_mode_menu.configure(state=discount_state)
        self.manual_discount_entry.configure(state=discount_state)
        if not self._is_admin():
            self.manual_discount_var.set("0")

        self._refresh_quick_items()
        self._update_customer_found_label()
        self.customer_suggestion_box.suppress_next_change()
        self.customer_lookup_var.set("")
        # Most USB/Bluetooth barcode scanners are "keyboard wedge" devices -
        # they just type the code + Enter into whatever field has focus, no
        # driver needed. Focusing the search box here means a cashier can
        # scan the moment this screen appears without clicking into it first.
        self.search_entry.focus_set()

    def _selected_customer_row(self):
        name = self.customer_var.get()
        row = self.conn.execute("SELECT * FROM customers WHERE name=?", (name,)).fetchone()
        return row

    def _current_is_loyalty(self) -> bool:
        return customers_module.is_loyalty_member(self._selected_customer_row())

    def _current_role(self) -> str | None:
        if not self.app.logged_in:
            return None
        row = self.conn.execute(
            "SELECT role FROM staff WHERE id=?", (self.app.current_staff_id,)
        ).fetchone()
        return row["role"] if row else None

    def _is_owner(self) -> bool:
        return self._current_role() == "owner"

    def _is_admin(self) -> bool:
        return self._current_role() in ("admin", "owner")

    def _on_customer_changed(self):
        self._update_customer_found_label()
        self._reprice_cart()
        self._refresh_quick_items()

    def _update_customer_found_label(self):
        """Keeps the "suitable place" the matched name shows in - right
        next to the phone-number box, inline - in sync with customer_var,
        whichever of the (several) ways it can change just happened: a
        successful phone lookup, a held invoice being resumed with its
        own customer, or this screen resetting a no-longer-active
        customer back to Walk-in in on_show(). No separate loyalty tag
        alongside it any more - loyalty status is still tracked
        internally (_current_is_loyalty(), used for offer pricing), it
        just isn't its own label on screen. The phone number shows
        alongside the name too - looked up fresh from the customer's own
        record (not the lookup box, which is cleared right after a
        successful identify_customer()) so it's right even when the
        customer got attached some other way, like resuming a held
        invoice."""
        name = self.customer_var.get()
        if name == "Walk-in":
            self.customer_found_label.configure(text="Walk-in", text_color=theme.TEXT_MUTED)
            return
        row = self._selected_customer_row()
        phone = row["phone"] if row is not None else ""
        text = f"→ {name} ({phone})" if phone else f"→ {name}"
        self.customer_found_label.configure(text=text, text_color=theme.SUCCESS_GREEN)

    def _reprice_cart(self):
        """Recompute every cart line's offer discount for whichever
        customer is now selected - a loyalty-only offer switches on/off
        with them, so a line added before the customer was identified
        needs re-pricing too, not just lines added afterward. Leaves qty
        and unit_price alone; only the discount/offer_name can change."""
        if not self.cart:
            return
        is_loyalty = self._current_is_loyalty()
        for line in self.cart:
            p = self.conn.execute("SELECT category FROM products WHERE id=?", (line["product_id"],)).fetchone()
            category = p["category"] if p else None
            discount, offer_name = offers_module.line_discount_amount(
                self.conn, line["product_id"], category, line["qty"], line["unit_price"], is_loyalty)
            line["discount"] = discount
            line["offer_name"] = offer_name
        self.refresh_cart()

    def identify_customer(self, raw_text: str):
        """The one way a customer gets attached to a sale - a phone number
        IS their loyalty ID (see customers.py), so this single box does
        what used to take a separate name dropdown plus a separate "Find"
        button. Looks the number up, sets customer_var to their name
        (which shows it in customer_found_label and re-prices the cart via
        _on_customer_changed), and offers to register them on the spot if
        the number doesn't match anyone yet. Clearing the box and pressing
        Enter puts the sale back to an anonymous Walk-in one."""
        phone = (raw_text or "").strip()
        if not phone:
            self.customer_var.set("Walk-in")
            return

        row = customers_module.find_customer_by_phone(self.conn, phone)
        if row is None:
            self.customer_found_label.configure(text="Not found", text_color=theme.DANGER_RED)
            if not messagebox.askyesno(
                "No loyalty account found",
                f"No loyalty customer is registered with the phone number '{phone}'.\n\n"
                "Register them now?",
            ):
                return
            row = open_customer_registration_dialog(
                self, self.conn, prefill_phone=phone, allow_credit_limit=self._is_admin())
            if row is None:
                return  # cashier cancelled the registration dialog

        self.customer_var.set(row["name"])
        self.customer_suggestion_box.suppress_next_change()
        self.customer_lookup_var.set("")
        self.customer_lookup_entry.focus_set()

    def _refresh_quick_items(self):
        for w in self.quick_items_frame.winfo_children():
            w.destroy()
        # A loyalty customer's own most-bought items make a more useful
        # quick-add list than the shop-wide top sellers once one's been
        # identified at checkout - falls back to the shop-wide list for a
        # Walk-in sale, or a loyalty customer with no purchase history yet.
        #
        # "Top sellers" is a ROLLING window (days=30 below), not a fixed
        # snapshot - every call to this method re-runs best_sellers/
        # customer_top_items against the database as of right now, so the
        # list naturally shifts as sales happen and the 30-day window
        # moves forward. This already runs every time it plausibly needs
        # to (screen open, customer identified/cleared, price tier
        # changed) - that refresh-on-relevant-change behaviour must keep
        # working; don't replace it with something that only computes the
        # list once and caches it.
        customer_row = self._selected_customer_row()
        rows = []
        # 8 (not 12) at 4 per row - two rows instead of three. Enough to
        # be a genuinely quick tap for the handful of things that come up
        # most, without eating the vertical space a third row took.
        quick_items_shown = 8
        if customer_row is not None and customers_module.is_loyalty_member(customer_row):
            rows = reports.customer_top_items(self.conn, customer_row["id"], top_n=quick_items_shown)
        if not rows:
            rows = reports.best_sellers(self.conn, days=30, top_n=quick_items_shown)
        tier_field = {"cash": "cash_price", "credit": "credit_price", "wholesale": "wholesale_price"}[self.tier_var.get()]
        cols = 4
        for i, r in enumerate(rows):
            product_id = r["product_id"]
            p = self.conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if p is None:
                continue
            price = p[tier_field]
            btn = styled_button(
                self.quick_items_frame, f"{p['name_en'][:15]}\nLKR {price:,.2f}",
                lambda pid=product_id: self._quick_add(pid), kind="secondary",
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), height=50,
            )
            btn.grid(row=i // cols, column=i % cols, padx=4, pady=4, sticky="nsew")
        for c in range(cols):
            self.quick_items_frame.grid_columnconfigure(c, weight=1)

    def _quick_add(self, product_id: int):
        p = self.conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if p is None:
            return
        tier_field = {"cash": "cash_price", "credit": "credit_price", "wholesale": "wholesale_price"}[self.tier_var.get()]
        unit_price = p[tier_field]
        discount, offer_name = offers_module.line_discount_amount(
            self.conn, product_id, p["category"], 1.0, unit_price, self._current_is_loyalty())
        self.cart.append({"product_id": product_id, "name": p["name_en"], "qty": 1.0, "unit_price": unit_price,
                           "discount": discount, "offer_name": offer_name})
        self.refresh_cart()

    def handle_phone_scan(self, code: str) -> dict:
        """Called from the phone-scanner's own background HTTP thread
        (see mobile_scan.MobileScanServer) - never the Tkinter thread, so
        this opens its own database connection rather than touching
        self.conn, and hands the actual UI update back to the main thread
        via .after() instead of touching any widget here directly.
        Routing to a specific box (_route_scanned_code) happens over
        there, on the main thread; this only needs its own DB lookup for
        the immediate "added"/"unknown" reply shown on the phone itself,
        which is right for the overwhelmingly common case (a product
        barcode) even though _route_scanned_code might land the code
        somewhere other than the cart if a different box has the cursor."""
        conn = db_module.get_conn(self.app.db_path)
        try:
            product = pos.get_product_by_code(conn, code)
        finally:
            conn.close()
        self.after(0, lambda: self._route_scanned_code(code))
        return {"name": product["name_en"], "added": True} if product is not None else {"unknown": True}

    def _mark_focus(self, name: str) -> None:
        """Called by every box's own <FocusIn>/<Button-1>/<KeyRelease>
        binding to record which one most recently had a click or
        keystroke land in it - the fallback _current_focus_target (below)
        uses if Tk's own focus_get() can't be matched to one of these
        boxes at the exact moment a scan arrives."""
        self._last_focused_input = name

    def _current_focus_target(self) -> str:
        """Which of this screen's own boxes a scan should land in, checked
        FRESH at the moment the scan arrives via Tkinter's own
        focus_get(), rather than trusted from _last_focused_input's cached
        <FocusIn>-binding value. That cached flag turned out not to be
        reliable enough to trust on its own - cashiers reported a scan
        landing in the barcode box even right after clicking into the
        search box, which means that click's <FocusIn> either didn't fire
        or fired too late/was overwritten before the scan arrived. Asking
        Tk directly "which widget actually has the keyboard cursor right
        now" sidesteps that entirely, since it's the same live truth Tk
        itself uses to decide where a keystroke would go.

        focus_get() usually returns a CTkEntry's own small internal entry
        widget rather than the CTkEntry object itself, so this can't just
        compare focus_get()'s result to self.search_entry etc. directly -
        instead it compares Tk's own widget PATH NAMES (e.g.
        ".!ctkframe.!ctkentry.!entry"), which is a plain string and is
        always exactly the parent's path plus ".something" for any
        descendant, no matter what CustomTkinter's internal wrapper
        structure looks like. That sidesteps needing to guess whether
        some internal widget's own .master attribute actually points back
        to the CTkEntry we created, the way SuggestionBox's click/focus
        checks above do - a guess that isn't safe to make twice over."""
        try:
            focused = self.focus_get()
        except Exception:  # noqa: BLE001 - focus_get() can raise mid-teardown/dialog-transition
            focused = None
        if focused is not None:
            focused_path = str(focused)
            for widget, name in (
                (self.cart_add_barcode_entry, "barcode"),
                (self.cart_add_name_entry, "name"),
                (self.customer_lookup_entry, "customer"),
                (self.search_entry, "search"),
            ):
                widget_path = str(widget)
                if focused_path == widget_path or focused_path.startswith(widget_path + "."):
                    return name
        # Nothing on this screen currently has the focus at all (e.g. the
        # very first scan right after this screen opened, before clicking
        # anywhere, or the desktop window not being the active window at
        # all right now) - fall back to whichever box a click or
        # keystroke most recently landed in (see the <Button-1>/
        # <KeyRelease> bindings added alongside each box's <FocusIn>
        # above), defaulting to "search" if that's never fired either.
        return getattr(self, "_last_focused_input", "search")

    def _route_scanned_code(self, code: str) -> None:
        """Where a scan from the ONE linked phone scanner actually lands -
        whichever of this screen's own boxes currently has the keyboard
        cursor (_current_focus_target, above), the same way a real USB/
        Bluetooth barcode scanner would just type into whatever's
        focused. Defaults to the search box, since on_show focuses it the
        moment this screen opens - so scanning right after walking up to
        the till, before clicking anywhere, shows the match in the results
        table above rather than adding anything on its own (see
        do_search)."""
        target = self._current_focus_target()
        if target == "barcode":
            self.cart_add_barcode_var.set(code)
            self.add_item_by_barcode()
        elif target == "name":
            self.cart_add_name_var.set(code)
            self.add_item_by_name()
        elif target == "customer":
            self.customer_lookup_var.set(code)
            self.identify_customer(code)
        else:
            self.search_var.set(code)
            self.do_search()

    def open_phone_scanner_dialog(self):
        if getattr(self.app, "mobile_scan_server", None) is None:
            self.app.mobile_scan_server = mobile_scan.MobileScanServer(self.handle_phone_scan)
        server = self.app.mobile_scan_server
        # The background scanner server is shared by the whole app (one
        # phone, one connection, whichever screen is using it) - Inventory's
        # "Add / Scan Item" -> "Scan with Phone" (and any other screen using
        # _open_phone_capture_dialog) borrows it for a while and points
        # on_scan at itself, then hands it back when done. If THIS dialog
        # is opened when the server already existed (created by one of
        # those screens first, not by POS), the `is None` check above never
        # runs, so on_scan could still be pointed at whatever that other
        # screen left it as (its own placeholder, say) instead of this
        # screen's handle_phone_scan - a scan would silently do nothing at
        # all here (no fill-in for search/add-by-barcode, nothing routed by
        # _route_scanned_code) with no error shown anywhere, exactly the
        # "linked but scanning doesn't do anything here anymore" symptom
        # reported. Setting it here unconditionally, every time this dialog
        # opens, makes the till reclaim its own scanner regardless of what
        # any other screen did with it beforehand.
        server.on_scan = self.handle_phone_scan
        if not server.running:
            try:
                server.start()
            except OSError as e:
                messagebox.showerror("Could not start phone scanner", str(e))
                return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Link Phone Scanner")
        fit_dialog(dialog, 400, 700)
        dialog.configure(fg_color=theme.BG_LIGHT)
        # Deliberately NOT dialog.grab_set() - this used to be modal, which
        # silently broke the cursor-routing feature this dialog itself
        # describes ("click into whichever box you want a scan to fill"):
        # a grab traps every click inside the dialog, so the cashier
        # physically couldn't click into the customer/name/search boxes
        # behind it to change where a scan should land - whatever box was
        # focused before the dialog opened (almost always "Add item -
        # barcode", the box used most) was the only place a scan could
        # ever go, no matter what the phone actually scanned next. Leaving
        # it non-modal lets the cashier click other boxes on the POS
        # screen while this stays open (or leave it open the whole shift -
        # it's just a QR code and a pairing code, nothing on it needs to
        # block the rest of the till). Clicking the main window brings it
        # to the front like any other ordinary window, exactly as needed
        # to click into a POS box; clicking the dialog again (or its
        # taskbar entry) brings the QR/pairing code back if it's needed
        # again.

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
                 "only need to do this once per phone.\n\n"
                 "Also, the FIRST time this Phone Scanner screen is opened on this "
                 "PC, Windows itself may pop up \"Windows Defender Firewall has "
                 "blocked some features of this app\" (it may appear behind this "
                 "window - check the taskbar if nothing seems to happen). Click "
                 "\"Allow access\", with both Private and Public networks ticked. "
                 "Until that's allowed, the phone can't reach this PC at all, and "
                 "every scan attempt will simply fail to connect no matter how good "
                 "the barcode looks - this only needs doing once, ever, on this PC.",
            text_color=theme.TEXT_DARK, font=ctk.CTkFont(size=11), wraplength=320, justify="left",
        ).pack(padx=12, pady=10)

        ctk.CTkLabel(
            body,
            text="Both devices must be on the same Wi-Fi. Once linked, this is the only "
                 "scanner button this screen needs: a scan lands wherever the cursor "
                 "already is - the search box, the barcode/name boxes above the "
                 "checkout list, or the customer phone box - exactly like typing it "
                 "there yourself would. Click into whichever box you want a scan to "
                 "fill before scanning; with nothing clicked into yet it goes to the "
                 "search box by default, which adds an exact barcode match straight to "
                 "the cart. Scans keep landing wherever the cursor is even while this "
                 "window is closed - close it and keep ringing up items at the same "
                 "time.",
            text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11), wraplength=320, justify="left",
        ).pack(padx=20, pady=(0, 14))

        def stop_server():
            server.stop()
            dialog.destroy()

        styled_button(dialog, "Unlink Phone Scanner", stop_server, kind="danger", width=200).pack(pady=(0, 16))

    def _selected_category(self) -> str | None:
        category = self.category_var.get()
        return None if category in ("", "All categories") else category

    def _on_search_changed(self, *_args) -> None:
        """Bound to search_var's and category_var's write trace, so this
        runs after every keystroke in the search box or every change of the
        category filter - the results list fills in live with whatever
        matches so far, instead of waiting for Enter or a click on
        "Search". No popup here even when nothing matches yet - a popup on
        every keystroke while someone is still mid-word would be unusable;
        do_search (Enter/Search/scanner) still shows one when a *finished*
        search comes up empty.

        Debounced rather than querying/rebuilding the results table on
        every single keystroke: a barcode scanner "types" a whole code
        (often 8-13 digits) far faster than anyone could by hand, and each
        one of those characters used to run its own synchronous DB query
        and full table rebuild right here - a burst of work that visibly
        lagged behind the actual, near-instant scan, which is what showed
        up as a scan seeming slow to register. <Return> (do_search, bound
        separately) always reads the fully-typed value directly and
        immediately, so accuracy never depends on this timer having
        fired - only the live preview's snappiness does."""
        self.search_status_label.configure(text="")  # a fresh keystroke/scan retires any stale "not found" line
        if self._search_debounce_id is not None:
            try:
                self.search_entry.after_cancel(self._search_debounce_id)
            except Exception:  # noqa: BLE001 - widget may already be gone during teardown
                pass
        self._search_debounce_id = self.search_entry.after(120, self._run_search_query)

    def _on_cart_add_changed(self, *_args) -> None:
        """Bound to both cart_add_barcode_var's and cart_add_name_var's
        write trace - retires any stale "Not found" line under the "Add
        item" row the moment either box's text changes at all (typing,
        deleting, a suggestion picked, or a fresh scan overwriting it),
        the same way _on_search_changed already does for the search box's
        own status line above. Unlike that one, there's no live results
        table to keep in sync here - add_item_by_barcode/add_item_by_name
        only look anything up once, on Enter/the Add button/a scan - so
        this needs no debounce, it's just clearing a label."""
        self.cart_add_status_label.configure(text="")

    def _run_search_query(self) -> None:
        self._search_debounce_id = None
        query = self.search_var.get().strip()
        category = self._selected_category()
        if not query and not category:
            self._search_results = []
            tree_clear(self.results_tree)
            return
        self._search_results = pos.search_products(self.conn, query, category=category)
        tree_clear(self.results_tree)
        for p in self._search_results:
            stock = pos.get_stock_on_hand(self.conn, p["id"])
            tree_insert(self.results_tree, (p["code"], p["name_en"], f"{p['cash_price']:.2f}", f"{stock:.0f}"),
                        iid=str(p["id"]))

    def do_search(self):
        # This box's whole job is to look up and show matches in the
        # results table above the cart, whether typed or scanned - it
        # never adds anything to the cart on its own. (An earlier version
        # skipped straight to the cart on an exact barcode match, on the
        # theory that it saved a click - but that made a scan into this
        # box behave exactly like scanning into "Add item - barcode"
        # instead, which is the opposite of the point of having two
        # separate boxes: this one is for finding and reviewing an item
        # first, that one is for adding it immediately.) An exact code
        # still surfaces here - search_products below matches on code as
        # well as name, so a full/exact code match is simply the one row
        # that comes back - the cashier picks it from the table (double-
        # click, or select it and use the Qty/"Add to Cart" row below)
        # same as any other search result.
        query = self.search_var.get().strip()
        if not query:
            self.search_status_label.configure(text="")
            return

        self._search_results = pos.search_products(self.conn, query, category=self._selected_category())
        tree_clear(self.results_tree)
        for p in self._search_results:
            stock = pos.get_stock_on_hand(self.conn, p["id"])
            tree_insert(self.results_tree, (p["code"], p["name_en"], f"{p['cash_price']:.2f}", f"{stock:.0f}"),
                        iid=str(p["id"]))
        if not self._search_results:
            # Previously this was a popup (messagebox.showinfo) - spelling
            # this out and echoing back exactly what was searched for
            # still makes a genuine code/name mismatch obvious rather than
            # the cashier assuming the scan just didn't register, but a
            # popup blocks the keyboard/mouse until dismissed, which turns
            # one unmatched code, mid a continuous USB/BT-scanner or
            # phone-scanner session, into a dead stop for every scan that
            # follows it - see search_status_label's own comment above.
            self.search_status_label.configure(
                text=f"Not found: '{query}' isn't in the catalog. If you just added this item, check "
                     "Inventory's Code column for the exact code and compare it with what you're "
                     "scanning/typing here - they must match exactly.",
            )
        else:
            self.search_status_label.configure(text="")

    def add_selected_to_cart(self):
        sel = self.results_tree.selection()
        if not sel:
            messagebox.showwarning("No item selected", "Search and select an item first.")
            return
        self._add_product_to_cart(int(sel[0]))

    def _cart_add_qty(self) -> float | None:
        """Shared quantity read for both add-item boxes: returns None (and
        shows the error itself) on anything invalid, so a caller can just
        bail out on a None result."""
        try:
            qty = float(self.cart_add_qty_var.get() or "1")
        except ValueError:
            messagebox.showerror("Invalid quantity", "Quantity must be a number.")
            return None
        if qty <= 0:
            messagebox.showerror("Invalid quantity", "Quantity must be greater than zero.")
            return None
        return qty

    def add_item_by_barcode(self):
        """The "Add item - barcode" box right above the checkout list: an
        exact-code lookup only (typed, scanned by a USB/BT scanner, or
        routed here by the linked phone scanner - see
        _route_scanned_code), at this row's shared quantity."""
        code = self.cart_add_barcode_var.get().strip()
        if not code:
            self.cart_add_status_label.configure(text="")
            return
        qty = self._cart_add_qty()
        if qty is None:
            return
        product = pos.get_product_by_code(self.conn, code)
        if product is None:
            # A quiet inline line, not a popup - see cart_add_status_label's
            # own comment above for why a blocking dialog here would stall
            # every scan after this one during a continuous scanning run.
            self.cart_add_status_label.configure(text=f"Not found: no product matches barcode '{code}'.")
            return
        self.cart_add_status_label.configure(text="")
        self._add_product_to_cart(product["id"], qty)
        self.cart_add_barcode_var.set("")
        self.cart_add_qty_var.set("1")
        self.cart_add_barcode_entry.focus_set()

    def add_item_by_name(self):
        """The "Add item - or item name" box right above the checkout
        list: a name/code search, same matching do_search() uses - exactly
        one match adds straight to the cart at this row's quantity; more
        than one drops the matches into the results list at the top of the
        screen (leaving the quantity for the cashier to apply after
        picking one there, since a single number can't be presumed to
        apply to whichever of several different items they end up
        choosing)."""
        query = self.cart_add_name_var.get().strip()
        if not query:
            self.cart_add_status_label.configure(text="")
            return
        qty = self._cart_add_qty()
        if qty is None:
            return

        matches = pos.search_products(self.conn, query, category=self._selected_category())
        if len(matches) == 1:
            self.cart_add_status_label.configure(text="")
            self._add_product_to_cart(matches[0]["id"], qty)
            self.cart_add_name_var.set("")
            self.cart_add_qty_var.set("1")
            self.cart_add_name_entry.focus_set()
            return
        if not matches:
            # Quiet inline line rather than a popup - same reasoning as
            # add_item_by_barcode just above.
            self.cart_add_status_label.configure(text=f"Not found: no product matches '{query}'.")
            return
        self.cart_add_status_label.configure(text="")

        self.search_var.set(query)
        self.do_search()
        self.cart_add_name_var.set("")
        messagebox.showinfo(
            "Multiple matches",
            f"{len(matches)} products match '{query}' - pick the right one from the results list above.",
        )

    def _add_item_from_whichever_box_has_text(self):
        """The single shared "Add" button next to the barcode/name pair -
        prefers the barcode box (an exact code is unambiguous) when both
        somehow have text, otherwise uses whichever one does."""
        if self.cart_add_barcode_var.get().strip():
            self.add_item_by_barcode()
        elif self.cart_add_name_var.get().strip():
            self.add_item_by_name()

    def _add_product_from_suggestion(self, entry: ctk.CTkEntry):
        """Returns a SuggestionBox on_select callback for either add-item
        box: picking a suggestion is already an unambiguous, specific
        product, so this adds it directly at the row's current quantity
        rather than re-running a fresh search on the display text."""
        def _select(product_row: sqlite3.Row) -> None:
            qty = self._cart_add_qty()
            if qty is None:
                return
            self._add_product_to_cart(product_row["id"], qty)
            self.cart_add_qty_var.set("1")
            entry.focus_set()
        return _select

    def _add_product_to_cart(self, product_id: int, qty: float | None = None):
        """Shared by the manual 'select a result + Add to Cart' flow, an
        exact-barcode scan into the search box, and the code/barcode box
        right above the checkout list - all add the same way. qty defaults
        to whatever's in the search section's own Qty box when the caller
        doesn't have a quantity of its own to pass (so a scanner reading a
        multi-pack barcode there still respects a cashier-entered quantity
        if they typed one first)."""
        if qty is None:
            try:
                qty = float(self.qty_var.get())
            except ValueError:
                messagebox.showerror("Invalid quantity", "Quantity must be a number.")
                return
        p = self.conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if p is None:
            return
        tier_field = {"cash": "cash_price", "credit": "credit_price", "wholesale": "wholesale_price"}[self.tier_var.get()]
        unit_price = p[tier_field]
        discount, offer_name = offers_module.line_discount_amount(
            self.conn, product_id, p["category"], qty, unit_price, self._current_is_loyalty())
        self.cart.append({"product_id": product_id, "name": p["name_en"], "qty": qty, "unit_price": unit_price,
                           "discount": discount, "offer_name": offer_name})
        self.refresh_cart()

    def refresh_cart(self):
        tree_clear(self.cart_tree)
        total = 0.0
        for i, line in enumerate(self.cart):
            discount = line.get("discount", 0.0)
            line_total = line["qty"] * line["unit_price"] - discount
            total += line_total
            # A brief tooltip-style label isn't available on a plain
            # Treeview cell, so the offer's name only shows up as the
            # amount here - the offer that produced it is one glance away
            # on the Offers screen if a cashier or the Owner needs to
            # check "why did this get 15% off".
            discount_text = f"-{discount:.2f}" if discount else "-"
            tree_insert(
                self.cart_tree,
                (line["name"], line["qty"], f"{line['unit_price']:.2f}", discount_text, f"{line_total:.2f}"),
                iid=str(i),
            )
        self.total_label.configure(text=f"Net Total: LKR {total:,.2f}")
        self._update_payable_label()

    def _manual_discount_amount(self, total: float) -> float:
        """Turns whatever's typed in the Admin-only discount box into an
        LKR amount off `total`, honoring whichever mode (flat LKR or %)
        is currently selected. Shared by the live Payable preview and the
        real calculation at checkout() so the two can never disagree -
        an invalid/blank box is silently treated as no discount rather
        than erroring here (checkout() does the actual validation before
        the sale is posted)."""
        try:
            raw = float(self.manual_discount_var.get() or "0")
        except ValueError:
            return 0.0
        if raw <= 0:
            return 0.0
        if self.discount_mode_var.get() == "%":
            return total * raw / 100.0
        return raw

    def _update_payable_label(self):
        """Keeps the "Payable" figure (Net Total minus the manual discount
        typed in beside it) live as either one changes, so a cashier sees
        the actual amount to collect before hitting Checkout rather than
        finding out only after confirming payment."""
        total = sum(line["qty"] * line["unit_price"] - line.get("discount", 0.0) for line in self.cart)
        payable = max(0.0, total - self._manual_discount_amount(total))
        self.payable_label.configure(text=f"Payable: LKR {payable:,.2f}")

    def _on_cart_line_selected(self):
        sel = self.cart_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.cart):
            self.cart_edit_qty_var.set(str(self.cart[idx]["qty"]))

    def remove_selected_line(self):
        sel = self.cart_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        del self.cart[idx]
        self.cart_edit_qty_var.set("")
        self.refresh_cart()

    def update_selected_line_qty(self):
        """Changes the quantity of a line already sitting in the checkout
        list, without removing and re-adding it. Re-runs the same
        offer-discount calculation _add_product_to_cart does, since some
        offers scale with quantity."""
        sel = self.cart_tree.selection()
        if not sel:
            messagebox.showwarning("No line selected", "Select a line in the checkout list first.")
            return
        try:
            qty = float(self.cart_edit_qty_var.get())
        except ValueError:
            messagebox.showerror("Invalid quantity", "Quantity must be a number.")
            return
        if qty <= 0:
            messagebox.showerror("Invalid quantity", "Quantity must be greater than zero - "
                                                        "use Remove Selected Line to take an item off the sale.")
            return
        idx = int(sel[0])
        line = self.cart[idx]
        p = self.conn.execute("SELECT category FROM products WHERE id=?", (line["product_id"],)).fetchone()
        category = p["category"] if p else None
        discount, offer_name = offers_module.line_discount_amount(
            self.conn, line["product_id"], category, qty, line["unit_price"], self._current_is_loyalty())
        line["qty"] = qty
        line["discount"] = discount
        line["offer_name"] = offer_name
        self.cart_edit_qty_var.set("")
        self.refresh_cart()

    def clear_cart(self):
        self.cart = []
        self.cart_edit_qty_var.set("")
        self.manual_discount_var.set("0")
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
        cart_lines = [pos.CartLine(product_id=c["product_id"], qty=c["qty"], discount=c.get("discount", 0.0))
                      for c in self.cart]
        net_total = sum(c["qty"] * c["unit_price"] - c.get("discount", 0.0) for c in self.cart)

        try:
            raw_discount = round(float(self.manual_discount_var.get() or "0"), 2)
        except ValueError:
            messagebox.showerror("Invalid discount", "Discount must be a number.")
            return
        if raw_discount < 0:
            messagebox.showerror("Invalid discount", "Discount can't be negative.")
            return
        discount_mode = self.discount_mode_var.get()
        if discount_mode == "%" and raw_discount > 100:
            messagebox.showerror("Invalid discount", "Discount % can't be more than 100.")
            return
        manual_discount = round(net_total * raw_discount / 100.0, 2) if discount_mode == "%" else raw_discount
        # Admin/Owner only, checked here too (not just the disabled entry
        # box) - the box only ever reaches this point non-zero if it was
        # left over from an Admin session, so this is a backstop, not the
        # main gate.
        if manual_discount > 0 and not self._is_admin():
            messagebox.showwarning(
                "Admin required", "Only Admin/Owner can apply a discount at checkout (sidebar login)."
            )
            return
        if manual_discount > net_total + 1e-6:
            messagebox.showerror("Invalid discount", "Discount can't be more than the sale's total.")
            return
        if manual_discount > 0:
            # Spread it across the cart lines rather than tacking it on as
            # a separate, unexplained number: each line's stored discount
            # (already possibly non-zero from an Offer) grows by its share
            # of this sale's total, so it lands in invoice_items/the
            # receipt the same auditable way an Offer discount does. The
            # last line absorbs whatever rounding leaves over so the
            # pieces always add back up to the exact amount typed in.
            line_totals = [c["qty"] * c["unit_price"] - c.get("discount", 0.0) for c in self.cart]
            total_of_lines = sum(line_totals) or 1.0
            remaining = manual_discount
            for i, line_total in enumerate(line_totals):
                if i == len(cart_lines) - 1:
                    share = round(remaining, 2)
                else:
                    share = round(manual_discount * (line_total / total_of_lines), 2)
                    remaining -= share
                cart_lines[i].discount += share
            net_total = round(net_total - manual_discount, 2)

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
        if manual_discount > 0:
            msg += f"\n(includes LKR {manual_discount:,.2f} discount applied at checkout)"
        if result.balance > 0.01:
            msg += f"\nChange due: LKR {result.balance:,.2f}"
        if receipt_path:
            msg += f"\nReceipt saved to {receipt_path}"
        messagebox.showinfo("Sale complete", msg)
        self.clear_cart()
        tree_clear(self.results_tree)
        # A finished sale is a clean break - the next person at the till
        # is presumably someone else, so the customer phone box (and
        # whichever customer it identified) resets back to blank/Walk-in
        # exactly like this screen looks when it first opens, rather than
        # silently carrying the last customer over into their sale too.
        # (Setting customer_var fires _on_customer_changed, which already
        # updates customer_found_label and reprices/refreshes on its
        # own - the explicit _refresh_quick_items() below just covers the
        # already-Walk-in case, where that trace wouldn't otherwise fire.)
        self.customer_suggestion_box.suppress_next_change()
        self.customer_lookup_var.set("")
        self.customer_var.set("Walk-in")
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
        cart_lines = [pos.CartLine(product_id=c["product_id"], qty=c["qty"], discount=c.get("discount", 0.0))
                      for c in self.cart]
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
        fit_dialog(dialog, 460, 320)
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
            self.tier_var.set(tier)
            if customer_id:
                crow = customers_module.get_customer(self.conn, customer_id)
                if crow:
                    self.on_show()
                    self.customer_var.set(crow["name"])
            # Re-price fresh rather than trusting the flat discount amount
            # stored when this was held - an offer may have started,
            # ended, or been switched off in the meantime, exactly like
            # _reprice_cart above.
            is_loyalty = self._current_is_loyalty()
            tier_field = {"cash": "cash_price", "credit": "credit_price", "wholesale": "wholesale_price"}[tier]
            new_cart = []
            for line in cart_lines:
                p = self.conn.execute("SELECT * FROM products WHERE id=?", (line.product_id,)).fetchone()
                if p is None:
                    continue
                unit_price = p[tier_field]
                discount, offer_name = offers_module.line_discount_amount(
                    self.conn, line.product_id, p["category"], line.qty, unit_price, is_loyalty)
                new_cart.append({"product_id": line.product_id, "name": p["name_en"], "qty": line.qty,
                                  "unit_price": unit_price, "discount": discount, "offer_name": offer_name})
            self.cart = new_cart
            pos.delete_held_invoice(self.conn, held_id)
            self.refresh_cart()
            dialog.destroy()

        styled_button(dialog, "Resume Selected", do_resume, kind="primary").pack(pady=10)

    def open_return_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Return / Refund")
        fit_dialog(dialog, 500, 460)
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

    def open_reprint_dialog(self):
        """Re-saves and re-opens the receipt file for any past invoice by
        its invoice number - covers a first print that failed, was closed
        before Ctrl+P, or a customer asking for a copy later. Works for a
        voided invoice too - the receipt just reflects whatever the
        invoice record still says, same as build_receipt_text always did."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Reprint Receipt")
        fit_dialog(dialog, 380, 170)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Invoice No:", text_color=theme.TEXT_DARK).pack(anchor="w", padx=16, pady=(16, 0))
        inv_row = ctk.CTkFrame(dialog, fg_color="transparent")
        inv_row.pack(fill="x", padx=16, pady=(0, 10))
        inv_var = tk.StringVar()
        entry = ctk.CTkEntry(inv_row, textvariable=inv_var, width=220)
        entry.pack(side="left")
        entry.focus_set()

        def do_reprint():
            inv = self.conn.execute(
                "SELECT id FROM invoices WHERE invoice_no=?", (inv_var.get().strip(),)
            ).fetchone()
            if not inv:
                messagebox.showerror("Not found", "No invoice with that number.")
                return
            try:
                path = receipts_module.save_receipt(self.conn, inv["id"])
                receipts_module.open_for_printing(path)
            except Exception as e:
                messagebox.showerror("Couldn't print", str(e))
                return
            messagebox.showinfo("Reprinted", f"Receipt reopened for printing.\nSaved to {path}")
            dialog.destroy()

        entry.bind("<Return>", lambda e: do_reprint())
        styled_button(dialog, "Reprint", do_reprint, kind="primary").pack(pady=(4, 16))

    def open_void_dialog(self):
        """Fully reverses a completed sale - restores its stock and undoes
        any credit-account charge it made (pos.void_invoice does the
        actual work). Unlike Return/Refund, which can take back just some
        of an invoice's items, this cancels the whole sale outright and
        can't itself be undone - so it's Owner-only and needs a reason
        typed in, logged to the audit trail like every other Owner-only
        action in this app."""
        if not self._is_owner():
            messagebox.showwarning("Owner required", "Only the Owner can void a completed sale (sidebar login).")
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Void Sale")
        fit_dialog(dialog, 440, 320)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Invoice No:", text_color=theme.TEXT_DARK).pack(anchor="w", padx=16, pady=(16, 0))
        inv_row = ctk.CTkFrame(dialog, fg_color="transparent")
        inv_row.pack(fill="x", padx=16)
        inv_var = tk.StringVar()
        ctk.CTkEntry(inv_row, textvariable=inv_var, width=200).pack(side="left")

        summary_label = ctk.CTkLabel(dialog, text="", text_color=theme.TEXT_DARK, justify="left")
        summary_label.pack(anchor="w", padx=16, pady=(12, 0))

        state = {"invoice_id": None, "invoice_no": None}

        def load():
            inv = self.conn.execute(
                "SELECT * FROM invoices WHERE invoice_no=?", (inv_var.get().strip(),)
            ).fetchone()
            state["invoice_id"] = None
            if not inv:
                messagebox.showerror("Not found", "No invoice with that number.")
                summary_label.configure(text="")
                return
            if inv["voided"]:
                messagebox.showinfo("Already voided", "This invoice was already voided.")
                summary_label.configure(text="")
                return
            state["invoice_id"] = inv["id"]
            state["invoice_no"] = inv["invoice_no"]
            summary_label.configure(
                text=f"Date: {inv['datetime']}\nCustomer: {inv['customer_name']}\n"
                     f"Net total: LKR {inv['net_total']:,.2f}"
            )

        styled_button(inv_row, "Load Invoice", load, kind="secondary").pack(side="left", padx=8)

        reason_row = ctk.CTkFrame(dialog, fg_color="transparent")
        reason_row.pack(fill="x", padx=16, pady=(20, 4))
        ctk.CTkLabel(reason_row, text="Reason:", text_color=theme.TEXT_DARK).pack(side="left")
        reason_var = tk.StringVar()
        ctk.CTkEntry(reason_row, textvariable=reason_var, width=260).pack(side="left", padx=8)

        def do_void():
            if state["invoice_id"] is None:
                messagebox.showwarning("Load an invoice", "Load a valid, not-already-voided invoice first.")
                return
            if not reason_var.get().strip():
                messagebox.showwarning("Reason required", "Enter a reason for voiding this sale.")
                return
            if not messagebox.askyesno(
                "Void this sale?",
                f"This reverses invoice {state['invoice_no']} completely - its stock is restored and "
                "any credit charge it made is undone. This cannot be undone. Continue?",
            ):
                return
            pos.void_invoice(self.conn, state["invoice_id"])
            audit.record(
                self.conn, actor_staff_id=self.app.current_staff_id, action="invoice.void",
                details=f"{state['invoice_no']} - {reason_var.get().strip()}",
            )
            messagebox.showinfo("Voided", f"Invoice {state['invoice_no']} has been voided.")
            dialog.destroy()

        styled_button(dialog, "Void Sale", do_void, kind="danger").pack(pady=14)


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #

class _AddProductCtx:
    """Bag of widgets/state shared by InventoryScreen.open_add_product_dialog
    and its _addproduct_* helper methods below - not a domain object, just
    named-attribute storage so those helpers don't each have to share a
    dozen+ separate bare local variables via closures. open_add_product_dialog
    builds one of these (assigning straight into it as each widget/var is
    created) instead of one 700+ line method holding all the dialog's
    behavior as nested functions - the widgets are still built once, in
    order, the same as before; only where the behavior *lives* has moved."""


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
        self.header("📦 Inventory & Stock Control", subtitle="Monitor stock levels, manage batches, and configure pricing tiers")

        self.hint = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, wraplength=760, justify="left",
        )
        self.hint.pack(anchor="w", padx=12, pady=(0, 8))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=12)
        styled_button(btn_row, "Refresh", self.refresh, kind="secondary").pack(side="left")
        styled_button(btn_row, "Export CSV", self.export, kind="secondary").pack(side="left", padx=8)
        self.import_btn = styled_button(
            btn_row, "Import Item List (CSV)", self.open_import_dialog, kind="secondary",
        )
        self.import_btn.pack(side="left", padx=8)
        # "Add / Scan Item" alone now covers what used to be two separate
        # buttons - it already told a brand-new barcode apart from an
        # existing one automatically; the "Don't have the barcode? Pick
        # from list" link (see open_add_product_dialog) covers the one
        # thing the old standalone "Receive Stock (GRN)" button could do
        # that this couldn't - restocking a product without scanning/typing
        # its code - so that separate button/dialog was retired rather than
        # kept as a second, overlapping way to do the same thing.
        self.add_btn = styled_button(btn_row, "Add / Scan Item", self.open_add_product_dialog, kind="primary")
        self.add_btn.pack(side="left", padx=8)
        self.edit_btn = styled_button(btn_row, "Edit Item", self.open_edit_product_dialog, kind="secondary")
        self.edit_btn.pack(side="left", padx=8)
        self.remove_btn = styled_button(btn_row, "Remove / Restore Item", self.toggle_product_active, kind="danger")
        self.remove_btn.pack(side="left", padx=8)
        self.stock_take_btn = styled_button(
            btn_row, "Stock Take", self.open_stock_take_dialog, kind="secondary",
        )
        self.stock_take_btn.pack(side="left", padx=8)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=12, pady=10)
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
        self.edit_btn.configure(state="normal" if is_admin else "disabled")
        self.remove_btn.configure(state="normal" if is_owner else "disabled")
        self.import_btn.configure(state="normal" if is_admin else "disabled")
        self.stock_take_btn.configure(state="normal" if is_admin else "disabled")

        raw = self.conn.execute(
            """SELECT p.id, p.code, p.name_en, p.category, p.reorder_level, p.active,
                      p.price_verified, COALESCE(SUM(b.qty_remaining),0) on_hand
               FROM products p LEFT JOIN stock_batches b ON b.product_id = p.id
               GROUP BY p.id ORDER BY p.active DESC, p.category, p.name_en"""
        ).fetchall()
        self._rows = []
        tree_clear(self.tree)
        for r in raw:
            if not r["active"]:
                status, tag = "REMOVED", "inactive"
            elif not r["price_verified"]:
                # Set by a bulk item-list import (see import_products.py) when no
                # real sale gave us this item's actual price - flagged ahead of a
                # plain low-stock warning so it gets checked before it's sold.
                status, tag = "CHECK PRICE", "needs_price"
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

    def open_import_dialog(self):
        """Bulk-load a real item-list CSV (code, name, category, prices - see
        import_products.py's own docstring for the exact shape) into the
        products table. Meant for the shop's own real item list - built once
        from a real POS export via scripts/build_real_catalog.py and shipped
        at smartgrocer/data/real_catalog.csv, or a newer CSV built the same
        way later. Safe to run more than once: an item already in the
        catalog (matched by its code) gets its details refreshed rather than
        being duplicated, and nothing about stock already received or past
        sales is touched."""
        if not self._is_admin():
            messagebox.showwarning("Admin required", "Log in as Admin or Owner (sidebar) to import items.")
            return
        default_dir = str(import_products.DEFAULT_CSV_PATH.parent)
        path = filedialog.askopenfilename(
            title="Choose an item-list CSV to import",
            initialdir=default_dir,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        if not messagebox.askyesno(
            "Import item list",
            "This will add any new item codes in that file to your catalog, and "
            "update the name/category/price of any item you already have (its "
            "stock on hand and sales history are not touched). Continue?",
        ):
            return

        def work():
            # run_in_background() runs this on a background thread - sqlite3
            # connections can't be shared across threads, so this opens its
            # own connection (see run_in_background's docstring) instead of
            # reusing self.conn, which would raise "SQLite objects created
            # in a thread can only be used in that same thread" every time.
            conn = db_module.get_conn(self.app.db_path)
            try:
                return import_products.import_from_csv(conn, path)
            finally:
                conn.close()

        def done(summary: dict):
            self.refresh()
            msg = f"Added {summary['inserted']} new item(s), updated {summary['updated']}."
            if summary["needs_price_review"]:
                msg += (
                    "\n\n" + str(summary["needs_price_review"]) + " item(s) had no real "
                    "price on record, so a placeholder price was used - look for "
                    "'CHECK PRICE' in the list below and fix the price (Edit Item) "
                    "before selling them."
                )
            if summary["skipped"]:
                msg += "\n\n" + str(summary["skipped"]) + " row(s) in the file were skipped (bad data)."
            messagebox.showinfo("Import finished", msg)

        def on_error(exc: Exception):
            messagebox.showerror("Import failed", str(exc))

        run_in_background(self, work, done, on_error)

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
        fit_dialog(dialog, 420, 620)
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

        dialog.bind("<Return>", lambda e: save())
        # On `dialog`, not `scroll` - so Save is always visible without
        # having to scroll all the way down through a long form to reach it.
        styled_button(dialog, "Save", save, kind="primary", width=140).pack(pady=18)

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

    def _add_supplier_on_the_fly(self, parent_dialog, suppliers_list: list, supplier_names: list,
                                  dropdown, var: tk.StringVar) -> None:
        """A small "New Supplier" popup usable from inside another dialog
        (Receive Stock / Add / Scan Item) - the point is a delivery from a
        supplier that isn't registered yet shouldn't force abandoning
        whatever's already been typed on this item just to go add it on
        the Suppliers screen first. Mutates suppliers_list/supplier_names
        IN PLACE (append, not reassignment) so the caller's own closures
        over those same list objects see the new supplier immediately,
        and refreshes the dropdown + selects the new supplier."""
        popup = ctk.CTkToplevel(parent_dialog)
        popup.title("New Supplier")
        fit_dialog(popup, 340, 280)
        popup.configure(fg_color=theme.BG_LIGHT)
        popup.grab_set()

        ctk.CTkLabel(popup, text="Supplier name:").pack(anchor="w", padx=16, pady=(16, 0))
        name_var = tk.StringVar()
        name_entry = ctk.CTkEntry(popup, textvariable=name_var)
        name_entry.pack(fill="x", padx=16)

        ctk.CTkLabel(popup, text="Phone (optional):").pack(anchor="w", padx=16, pady=(10, 0))
        phone_var = tk.StringVar()
        ctk.CTkEntry(popup, textvariable=phone_var).pack(fill="x", padx=16)

        def save_supplier():
            name = name_var.get().strip()
            if not name:
                messagebox.showerror("Invalid input", "Supplier name cannot be blank.")
                return
            if any(s["name"] == name for s in suppliers_list):
                messagebox.showerror("Invalid input", f"A supplier named '{name}' already exists.")
                return
            new_id = suppliers_module.add_supplier(self.conn, name, phone_var.get())
            suppliers_list.append({"id": new_id, "name": name, "phone": phone_var.get().strip(),
                                    "address": "", "active": 1})
            supplier_names.append(name)
            dropdown.configure(values=supplier_names)
            var.set(name)
            popup.destroy()

        popup.bind("<Return>", lambda e: save_supplier())
        styled_button(popup, "Add Supplier", save_supplier, kind="primary", width=140).pack(pady=18)
        popup.after(150, name_entry.focus_set)

    def open_add_product_dialog(self):
        """One entry point for "put an item into the store" - covers
        everything the old separate "Add / Scan Item" and "Receive Stock
        (GRN)" buttons used to split between them, since both really just
        meant "stock came in, record it":

        - Scan/type a code that's NOT in the catalog yet -> this is a
          brand-new product, so the full details form appears (name,
          category, prices, etc.) to register it for the first time.
        - Scan/type a code that IS already in the catalog -> there's
          nothing to ask except how much came in, so it shows the
          existing product's name/category/current stock and asks for
          the quantity.
        - Don't have a barcode handy at all (e.g. going off a supplier's
          paper invoice, not scanning each item)? "Don't have the
          barcode? Pick from list" opens a searchable list of every
          product already in the catalog and fills the code field for
          you, landing on exactly the same existing-product path above -
          this is what the standalone GRN dialog used to be for.

        Supplier and Payment (Cash/Cheque/Not paid yet) sit above both of
        those paths, not inside either one - a whole bulk-add session is
        normally one delivery from one supplier, paid one way, whether
        the items in it are restocks or brand-new products, so picking
        both once for "the first item" carries forward automatically for
        every item after it (a successful Save keeps them as they were;
        only the explicit Clear button resets them) instead of asking
        again for every single line.

        The barcode field itself can still be filled by typing on the
        keyboard, by a USB/Bluetooth barcode scanner (it just types the
        digits into whichever field has focus, so the field is
        auto-focused for that), or by scanning it with a phone via the
        same phone-scanner feature used at the till - and as it's typed
        or scanned, a live dropdown suggests matching products by code,
        so you can stop partway through and click the right one instead
        of finishing the whole code. Picking a suggestion, or pressing
        Enter after the code (typed or scanned) is complete, both land on
        the same result - see _addproduct_use_code.

        This method's job is just to BUILD the dialog's widgets, in order,
        into a shared `ctx` (_AddProductCtx) - the actual behavior (what
        happens on a scan, a save, a reset, etc.) lives in the
        `_addproduct_*` methods right after this one, each taking `ctx` so
        they can all reach the same widgets/state without needing a
        dozen+ separate parameters."""
        if not self._is_admin():
            messagebox.showwarning("Admin required", "Log in as Admin or Owner (sidebar) to add an item.")
            return
        ctx = _AddProductCtx()
        ctx.dialog = ctk.CTkToplevel(self)
        ctx.dialog.title("Add / Scan Item")
        fit_dialog(ctx.dialog, 440, 820)  # a bit taller - Payment now always shows, not just for a restock
        ctx.dialog.configure(fg_color=theme.BG_LIGHT)
        ctx.dialog.grab_set()

        # Holds the function _open_phone_capture_dialog hands back once the
        # phone is linked in continuous mode (see the "Scan with Phone"
        # button below) - calling it hands the till's phone-scanner
        # connection back to whatever was using it before and closes that
        # small popup if it's still open. Needs calling from HERE too (not
        # just that popup's own Done button) so that closing THIS whole
        # dialog while the phone is still linked can't leave a scan
        # arriving later trying to act on fields that no longer exist.
        ctx.phone_scan_restore = {"fn": None}

        ctx.dialog.protocol("WM_DELETE_WINDOW", lambda: self._addproduct_close_dialog(ctx))

        ctx.body = ctk.CTkScrollableFrame(ctx.dialog, fg_color="transparent")
        ctx.body.pack(fill="both", expand=True)

        ctx.session_count = {"n": 0}
        ctx.session_label = ctk.CTkLabel(
            ctx.body, text="", text_color=theme.SUCCESS_GREEN, font=ctk.CTkFont(size=12, weight="bold"),
        )
        ctx.session_label.pack(anchor="w", padx=16, pady=(10, 0))

        def field(parent, label, initial=""):
            ctk.CTkLabel(parent, text=label).pack(anchor="w", padx=16, pady=(10, 0))
            var = tk.StringVar(value=initial)
            entry = ctk.CTkEntry(parent, textvariable=var)
            entry.pack(fill="x", padx=16)
            return var, entry

        ctk.CTkLabel(ctx.body, text="Barcode / Code:").pack(anchor="w", padx=16, pady=(12, 0))
        code_row = ctk.CTkFrame(ctx.body, fg_color="transparent")
        code_row.pack(fill="x", padx=16)
        ctx.code_var = tk.StringVar()
        ctx.code_entry = ctk.CTkEntry(code_row, textvariable=ctx.code_var)
        ctx.code_entry.pack(side="left", fill="x", expand=True)

        # Live suggestions as this box is typed/scanned into - matches by
        # CODE PREFIX only (not name; see search_products_by_code's own
        # docstring for why a barcode box keeps that distinction), same
        # widget/behavior already proven on the POS screen's own barcode
        # box. A USB/Bluetooth scanner "types" the whole code far faster
        # than this can react to each character, so this is debounced
        # (SuggestionBox handles that) - either way, pressing Enter after
        # a full code (typed or scanned) is untouched by this and still
        # proceeds immediately via check_code below, exactly as before.
        ctx.code_suggestions = SuggestionBox(
            ctx.code_entry, ctx.code_var,
            fetch_fn=lambda q: [(f"{p['code']} — {p['name_en']}", p) for p in pos.search_products_by_code(self.conn, q)],
            on_select=lambda product: self._addproduct_use_code(ctx, product["code"]),
        )

        styled_button(
            code_row, "Scan with Phone", lambda: self._addproduct_start_phone_scan(ctx),
            kind="secondary", width=130,
        ).pack(side="left", padx=(8, 0))

        styled_button(
            ctx.body, "Don't have the barcode? Pick from list",
            lambda: self._addproduct_pick_from_list(ctx), kind="secondary",
        ).pack(anchor="w", padx=16, pady=(4, 0))

        ctx.status_label = ctk.CTkLabel(
            ctx.body, text="Type, scan with a USB/Bluetooth scanner, tap \"Scan with Phone\", or pick "
                           "from the list above - a suggestion list appears as you go, but nothing "
                           "happens until you press Enter or click a suggestion.",
            text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11), wraplength=380, justify="left",
        )
        ctx.status_label.pack(anchor="w", padx=16, pady=(6, 6))

        # Supplier - shared by both paths below, since either one creates a
        # stock batch (an opening batch for a brand-new product, a restock
        # batch for one that already exists) that can record who it came
        # from - what the standalone "Receive Stock (GRN)" dialog used to
        # be a separate button for. Optional either way - "(none)" if
        # you'd rather skip it.
        ctx.suppliers_list = suppliers_module.list_suppliers(self.conn)
        ctx.supplier_names = ["(none)"] + [s["name"] for s in ctx.suppliers_list]
        ctk.CTkLabel(ctx.body, text="Supplier (optional):").pack(anchor="w", padx=16, pady=(0, 0))
        supplier_row = ctk.CTkFrame(ctx.body, fg_color="transparent")
        supplier_row.pack(fill="x", padx=16, pady=(0, 6))
        ctx.supplier_var = tk.StringVar(value=ctx.supplier_names[0])
        supplier_dropdown = SearchableDropdown(
            supplier_row, values=ctx.supplier_names, variable=ctx.supplier_var, width=290, label="Supplier")
        supplier_dropdown.pack(side="left")
        styled_button(
            supplier_row, "+ New", lambda: self._add_supplier_on_the_fly(
                ctx.dialog, ctx.suppliers_list, ctx.supplier_names, supplier_dropdown, ctx.supplier_var),
            kind="secondary", width=80,
        ).pack(side="left", padx=(8, 0))

        # Payment - shared by both paths below, same as Supplier just
        # above, and for the same reason: a whole delivery/session of
        # items usually comes in paid the SAME way (all cash, or one
        # cheque/credit arrangement covering the lot), whether some of
        # those items are restocks of things already in the catalog or
        # brand-new products being registered for the first time. Not
        # every delivery is paid cash on the spot: sometimes it's a
        # cheque (handed over now, but the money only actually leaves the
        # bank once it clears - worth a reminder of the date/amount),
        # sometimes the stock is taken on credit and only settled later,
        # e.g. once it's been sold. What the standalone "Receive Stock
        # (GRN)" dialog's own Payment section used to cover, and now also
        # applies to a brand-new product's opening stock, which used to
        # have no way to record any of this at all.
        ctk.CTkLabel(ctx.body, text="Payment:").pack(anchor="w", padx=16, pady=(6, 0))
        ctx.payment_var = tk.StringVar(value="Cash")
        ctx.cheque_fields = ctk.CTkFrame(ctx.body, fg_color="transparent")
        ctx.cheque_number_var = tk.StringVar()
        ctx.cheque_date_var = tk.StringVar()

        ctk.CTkOptionMenu(
            ctx.body, values=["Cash", "Cheque", "Not paid yet (credit)"],
            variable=ctx.payment_var, command=lambda *_a: self._addproduct_on_payment_changed(ctx),
        ).pack(fill="x", padx=16)
        ctk.CTkLabel(ctx.cheque_fields, text="Cheque number:").pack(anchor="w", pady=(6, 0))
        ctk.CTkEntry(ctx.cheque_fields, textvariable=ctx.cheque_number_var).pack(fill="x")
        ctk.CTkLabel(ctx.cheque_fields, text="Cheque date (YYYY-MM-DD):").pack(anchor="w", pady=(6, 0))
        ctk.CTkEntry(ctx.cheque_fields, textvariable=ctx.cheque_date_var).pack(fill="x")

        # --- Existing-product path: just ask how much stock came in. ---
        ctx.existing_frame = ctk.CTkFrame(ctx.body, fg_color="transparent")
        ctx.existing_info = ctk.CTkLabel(ctx.existing_frame, text="", text_color=theme.NAVY_DARK,
                                          font=ctk.CTkFont(size=13, weight="bold"), wraplength=380, justify="left")
        ctx.existing_info.pack(anchor="w", padx=16, pady=(4, 8))
        ctx.qty_received_var, _ = field(ctx.existing_frame, "Quantity received:", "10")
        ctx.existing_cost_var, _ = field(ctx.existing_frame, "Cost price (per unit, blank = use last cost):")
        ctx.existing_expiry_var, _ = field(ctx.existing_frame, "Expiry date (YYYY-MM-DD, blank if none):")

        # --- New-product path: the full catalog-entry form. ---
        ctx.new_frame = ctk.CTkFrame(ctx.body, fg_color="transparent")
        ctx.name_en_var, _ = field(ctx.new_frame, "Product name (English):")
        ctx.name_si_var, _ = field(ctx.new_frame, "Product name (Sinhala, optional):")
        ctx.category_var, _ = field(ctx.new_frame, "Category:")
        ctx.unit_var, _ = field(ctx.new_frame, "Unit (e.g. pcs, kg, g, L):", "pcs")
        ctx.cost_var, _ = field(ctx.new_frame, "Cost price (what you pay):")
        ctx.cash_var, _ = field(ctx.new_frame, "Cash selling price:")
        ctx.credit_var, _ = field(ctx.new_frame, "Credit selling price (blank = same as cash):")
        ctx.wholesale_var, _ = field(ctx.new_frame, "Wholesale price (blank = same as cost):")
        ctx.pack_size_var, _ = field(ctx.new_frame, "Pack size (units per pack):", "1")
        ctx.reorder_var, _ = field(ctx.new_frame, "Reorder level (low-stock alert threshold):", "10")
        ctx.initial_qty_var, _ = field(ctx.new_frame, "Opening stock quantity (optional):", "0")
        ctx.expiry_var, _ = field(ctx.new_frame, "Expiry date (YYYY-MM-DD, optional):")
        flags_row = ctk.CTkFrame(ctx.new_frame, fg_color="transparent")
        flags_row.pack(fill="x", padx=16, pady=(14, 0))
        ctx.perishable_var = tk.BooleanVar(value=False)
        ctx.staple_var = tk.BooleanVar(value=False)
        ctx.child_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(flags_row, text="Perishable", variable=ctx.perishable_var).pack(side="left")
        ctk.CTkCheckBox(flags_row, text="Staple item", variable=ctx.staple_var).pack(side="left", padx=12)
        ctk.CTkCheckBox(flags_row, text="Child-target item", variable=ctx.child_var).pack(side="left")

        button_row = ctk.CTkFrame(ctx.dialog, fg_color="transparent")
        button_row.pack(pady=16)
        ctx.save_btn = styled_button(button_row, "Save", lambda: None, kind="primary", width=160)
        ctx.save_btn.pack(side="left")
        styled_button(
            button_row, "Clear", lambda: self._addproduct_reset_form(
                ctx, keep_supplier=False,
                message="Cleared. Type, scan with a USB/Bluetooth scanner, tap \"Scan with Phone\", or "
                        "pick from the list above - a suggestion list appears as you go, but nothing "
                        "happens until you press Enter or click a suggestion.",
            ), kind="secondary", width=90,
        ).pack(side="left", padx=(8, 0))
        styled_button(button_row, "Done", lambda: self._addproduct_close_dialog(ctx), kind="secondary", width=100).pack(
            side="left", padx=(8, 0))

        ctx.state = {"mode": None, "product": None}

        for _required_var in (ctx.name_en_var, ctx.category_var, ctx.cash_var):
            _required_var.trace_add("write", lambda *_a: self._addproduct_update_save_button_state(ctx))

        # Deliberately ONLY <Return> - typing/scanning into this box (with
        # the live suggestion dropdown previewing matches as it goes) never
        # proceeds to the existing/new-product view on its own; nothing
        # happens until you explicitly commit, either by pressing Enter
        # after a typed/scanned code or by clicking a suggestion (that
        # calls check_code itself, via _addproduct_use_code, since picking
        # one off the list is just as deliberate a choice as pressing
        # Enter). This used to also run on <FocusOut> (clicking/tabbing
        # away from the box), which meant just glancing at another field
        # mid-typing - or the suggestion popup itself briefly taking focus
        # - could flip the whole dialog over to "new product" for a
        # still-incomplete code. Removed for exactly that reason - nothing
        # here reacts until you say it's ready.
        ctx.code_entry.bind("<Return>", lambda event: self._addproduct_check_code(ctx))
        ctx.save_btn.configure(state="disabled")
        ctx.dialog.after(150, ctx.code_entry.focus_set)
        ctx.dialog.after(150, lambda: self._addproduct_refresh_scroll_region(ctx))

    def _addproduct_close_dialog(self, ctx) -> None:
        if ctx.phone_scan_restore["fn"] is not None:
            ctx.phone_scan_restore["fn"]()
        ctx.dialog.destroy()

    def _addproduct_update_session_label(self, ctx) -> None:
        if ctx.session_count["n"] == 0:
            ctx.session_label.configure(text="")
        elif ctx.session_count["n"] == 1:
            ctx.session_label.configure(text="1 item added so far - keep scanning/typing to add more.")
        else:
            ctx.session_label.configure(
                text=f"{ctx.session_count['n']} items added so far - keep scanning/typing to add more."
            )

    def _addproduct_use_code(self, ctx, code: str) -> None:
        """Common landing point for every way this dialog can learn a
        product's code other than typing the whole thing by hand -
        a phone scan, a live suggestion picked while typing/scanning,
        or "Pick from list". Just sets the code and re-checks it, same
        as pressing Enter after typing would - except guarded the same
        way a scan already was: a code for a NEW (not-yet-in-the-
        catalog) product shows a full details form the cashier is
        meant to fill in and Save, and swapping code_var out from
        under that mid-fill would hide new_frame and make the
        in-progress item look like it just vanished - no error, no
        chance to fix a mistake. An "existing product" match has
        nothing typed to lose (just a quantity that resets on save
        anyway) so those keep going straight through."""
        if ctx.state["mode"] == "new":
            ctx.status_label.configure(
                text="Still filling in the previous (new) item - click Save or Clear before "
                     "using another code.",
                text_color=theme.DANGER_RED,
            )
            return
        # This is an external, non-typing change to code_var (the user
        # already finished choosing, they didn't just type this) -
        # without suppressing it, setting it back to the real code
        # would look exactly like a fresh keystroke to the suggestion
        # box below and pop it open again a moment later, showing the
        # same single match right after it was already picked. See
        # SuggestionBox.suppress_next_change's own docstring.
        ctx.code_suggestions.suppress_next_change()
        ctx.code_var.set(code)
        self._addproduct_check_code(ctx)

    def _addproduct_start_phone_scan(self, ctx) -> None:
        def on_scanned(scanned):
            self._addproduct_use_code(ctx, scanned)

        # continuous=True: this keeps the phone linked and this
        # dialog's own barcode field being filled in for every scan
        # that follows, not just the first - that's what lets one
        # phone-scanner session add a whole delivery's worth of items
        # in a row (each one checked, filled in, and saved via the
        # normal Save button, same as typing the code by hand would)
        # instead of needing "Scan with Phone" tapped again per item.
        #
        # THIS dialog holds a modal grab (see ctx.dialog.grab_set() in
        # open_add_product_dialog), and Tk's grab is application-wide,
        # not per-window - so with it still held, the little phone-link
        # popup we're about to open would be completely unclickable
        # behind it. Release it here so BOTH windows are usable while
        # the phone is linked (click the popup to see the QR again,
        # click back here to fill in a quantity/details and hit Save) -
        # exactly what "one link, add every item" needs. on_closed grabs
        # it back the moment the popup goes away (its own Done button,
        # its X, or this dialog's own _addproduct_close_dialog calling
        # phone_scan_restore["fn"] first) so this dialog goes back to
        # blocking the rest of the app once the phone step is done, same
        # as before.
        ctx.dialog.grab_release()

        def _regrab():
            if ctx.dialog.winfo_exists():
                try:
                    ctx.dialog.grab_set()
                except Exception:
                    pass

        ctx.phone_scan_restore["fn"] = self._open_phone_capture_dialog(
            on_scanned, continuous=True,
            title="Scan Items to Add",
            instruction="Scan each item's barcode, one after another",
            on_closed=_regrab,
        )

    def _addproduct_pick_from_list(self, ctx) -> None:
        """For restocking without a barcode in hand at all - e.g.
        working off a supplier's paper invoice by product name rather
        than scanning each item. Picking one here just fills in its
        code and runs it through check_code(), landing on exactly the
        same existing-product path as if that code had been scanned -
        this is what the standalone "Receive Stock (GRN)" dialog used
        to be the only way to do."""
        if ctx.state["mode"] == "new":
            ctx.status_label.configure(
                text="Still filling in the previous (new) item - click Save or Clear before "
                     "picking another one.",
                text_color=theme.DANGER_RED,
            )
            return
        catalog = self.conn.execute(
            "SELECT id, code, name_en FROM products WHERE active=1 ORDER BY name_en"
        ).fetchall()
        if not catalog:
            messagebox.showinfo("No products yet", "The catalog is empty - nothing to pick from yet.")
            return
        labels = [f"{p['code']} - {p['name_en']}" for p in catalog]

        popup = ctk.CTkToplevel(ctx.dialog)
        popup.title("Pick a Product")
        fit_dialog(popup, 380, 220)
        popup.configure(fg_color=theme.BG_LIGHT)
        popup.grab_set()

        ctk.CTkLabel(popup, text="Product:").pack(anchor="w", padx=16, pady=(16, 0))
        pick_var = tk.StringVar(value=labels[0])
        SearchableDropdown(popup, values=labels, variable=pick_var, width=340, label="Product").pack(padx=16)

        def choose():
            idx = labels.index(pick_var.get())
            chosen_code = catalog[idx]["code"]
            popup.destroy()
            self._addproduct_use_code(ctx, chosen_code)

        popup.bind("<Return>", lambda e: choose())
        styled_button(popup, "Use This Product", choose, kind="primary", width=160).pack(pady=18)

    def _addproduct_resolve_supplier_id(self, ctx) -> int | None:
        if ctx.supplier_var.get() == "(none)":
            return None
        match = next((s for s in ctx.suppliers_list if s["name"] == ctx.supplier_var.get()), None)
        return match["id"] if match else None

    def _addproduct_on_payment_changed(self, ctx) -> None:
        if ctx.payment_var.get() == "Cheque":
            ctx.cheque_fields.pack(fill="x", padx=16, pady=(4, 6))
        else:
            ctx.cheque_fields.pack_forget()
        # Same stale-scroll-canvas glitch check_code() guards against
        # below (see _addproduct_refresh_scroll_region) - showing/hiding
        # the cheque fields changes ctx.body's content height too.
        self._addproduct_refresh_scroll_region(ctx)

    def _addproduct_new_ready(self, ctx) -> bool:
        """Whether the new-product form currently has everything
        pos.add_product actually requires - a name, a category, and a
        cash price above zero. Backs the "Save New Product" button's
        enabled state below, so a blank/incomplete form can't be
        clicked at all - it used to be clickable the whole time, only
        failing afterwards with an error message from pos.add_product
        once Save was actually pressed."""
        if not ctx.name_en_var.get().strip():
            return False
        if not ctx.category_var.get().strip():
            return False
        try:
            return float(ctx.cash_var.get() or 0) > 0
        except ValueError:
            return False

    def _addproduct_update_save_button_state(self, ctx) -> None:
        """Re-checked live as the required new-product fields are
        typed into, so Save unlocks the moment the form is actually
        complete rather than needing another action to notice."""
        if ctx.state["mode"] == "new":
            ctx.save_btn.configure(state="normal" if self._addproduct_new_ready(ctx) else "disabled")

    def _addproduct_reset_form(self, ctx, keep_supplier: bool = True, message: str | None = None) -> None:
        """Blank every field back to its default, ready for the next
        item, without closing the dialog. keep_supplier=True (used
        right after a successful save) leaves the Supplier dropdown -
        and now Payment (method, cheque number/date) too - exactly as
        they were: bulk-adding is usually one delivery, one supplier,
        paid one way, whether that next item is a restock of
        something already in the catalog or a brand-new product, so
        the user shouldn't have to re-pick "Cash" (or re-enter the
        same cheque details) for every single line of the same
        delivery. The explicit Clear button passes keep_supplier=False
        for a genuine start-over instead, which does reset both.
        `message`, when given, replaces the status line (used by
        Clear); left out, the status line is untouched (used right
        after a save, so the green "added!" confirmation isn't
        immediately overwritten by this same reset)."""
        if not keep_supplier:
            ctx.supplier_var.set(ctx.supplier_names[0])
            ctx.payment_var.set("Cash")
            ctx.cheque_number_var.set("")
            ctx.cheque_date_var.set("")
            ctx.cheque_fields.pack_forget()
        ctx.code_var.set("")
        ctx.qty_received_var.set("10")
        ctx.existing_cost_var.set("")
        ctx.existing_expiry_var.set("")
        ctx.name_en_var.set("")
        ctx.name_si_var.set("")
        ctx.category_var.set("")
        ctx.unit_var.set("pcs")
        ctx.cost_var.set("")
        ctx.cash_var.set("")
        ctx.credit_var.set("")
        ctx.wholesale_var.set("")
        ctx.pack_size_var.set("1")
        ctx.reorder_var.set("10")
        ctx.initial_qty_var.set("0")
        ctx.expiry_var.set("")
        ctx.perishable_var.set(False)
        ctx.staple_var.set(False)
        ctx.child_var.set(False)
        if message is not None:
            ctx.status_label.configure(text=message, text_color=theme.TEXT_MUTED)
        self._addproduct_check_code(ctx, keep_status=True)
        ctx.code_entry.focus_set()

    def _addproduct_save_existing(self, ctx) -> None:
        code = ctx.code_var.get().strip()
        product = pos.get_product_by_code(self.conn, code)
        if product is None:
            ctx.status_label.configure(
                text="That barcode no longer matches a product - re-check it.",
                text_color=theme.DANGER_RED,
            )
            self._addproduct_check_code(ctx)
            return
        payment_choice = ctx.payment_var.get()
        method = {"Cash": "cash", "Cheque": "cheque", "Not paid yet (credit)": "credit"}[payment_choice]
        supplier_id = self._addproduct_resolve_supplier_id(ctx)
        if method != "cash" and supplier_id is None:
            ctx.status_label.configure(
                text="A cheque or credit purchase needs a supplier selected above - who is it owed to?",
                text_color=theme.DANGER_RED,
            )
            return
        try:
            pos.receive_stock(
                self.conn, product_id=product["id"],
                qty=float(ctx.qty_received_var.get() or 0),
                cost_price=float(ctx.existing_cost_var.get()) if ctx.existing_cost_var.get().strip() else None,
                expiry_date=ctx.existing_expiry_var.get().strip() or None,
                supplier_id=supplier_id, payment_method=method,
                cheque_number=ctx.cheque_number_var.get(), cheque_date=ctx.cheque_date_var.get().strip() or None,
            )
        except ValueError as e:
            # A plain in-dialog message rather than messagebox.showerror
            # here - on this specific dialog, a popped-up messagebox was
            # reported to leave the "Add / Scan Item" window looking
            # like it had closed/gone back to the main page once the
            # messagebox was dismissed (very likely a Windows focus/
            # z-order quirk between a native modal dialog and this
            # CTkToplevel, not an actual close - but confusing either
            # way). Everything already typed stays exactly as it was;
            # only the status line changes color/text to show the error.
            ctx.status_label.configure(text=str(e), text_color=theme.DANGER_RED)
            return
        except Exception as e:  # noqa: BLE001 - anything unexpected still leaves everything you typed in place
            ctx.status_label.configure(
                text=f"Something went wrong and this wasn't saved: {e} - nothing you entered "
                     "was lost, fix the problem (or click Clear to start over) and try again.",
                text_color=theme.DANGER_RED,
            )
            return
        self.refresh()
        ctx.session_count["n"] += 1
        self._addproduct_update_session_label(ctx)
        ctx.status_label.configure(
            text=f"Added {ctx.qty_received_var.get()} {product['unit']} to '{product['name_en']}'. "
                 f"Ready for the next item - scan/type its code, or click Done to finish.",
            text_color=theme.SUCCESS_GREEN,
        )
        self._addproduct_reset_form(ctx)

    def _addproduct_save_new(self, ctx) -> None:
        new_name = ctx.name_en_var.get().strip()
        payment_choice = ctx.payment_var.get()
        method = {"Cash": "cash", "Cheque": "cheque", "Not paid yet (credit)": "credit"}[payment_choice]
        supplier_id = self._addproduct_resolve_supplier_id(ctx)
        initial_qty = float(ctx.initial_qty_var.get() or 0)
        if method != "cash" and initial_qty > 0 and supplier_id is None:
            ctx.status_label.configure(
                text="A cheque or credit purchase needs a supplier selected above - who is it owed to?",
                text_color=theme.DANGER_RED,
            )
            return
        try:
            pos.add_product(
                self.conn,
                code=ctx.code_var.get(),
                name_en=ctx.name_en_var.get(),
                name_si=ctx.name_si_var.get(),
                category=ctx.category_var.get(),
                unit=ctx.unit_var.get() or "pcs",
                cost_price=float(ctx.cost_var.get() or 0),
                cash_price=float(ctx.cash_var.get() or 0),
                credit_price=float(ctx.credit_var.get()) if ctx.credit_var.get().strip() else None,
                wholesale_price=float(ctx.wholesale_var.get()) if ctx.wholesale_var.get().strip() else None,
                pack_size=int(ctx.pack_size_var.get() or 1),
                reorder_level=int(ctx.reorder_var.get() or 0),
                is_perishable=ctx.perishable_var.get(),
                is_staple=ctx.staple_var.get(),
                child_target=ctx.child_var.get(),
                initial_qty=initial_qty,
                expiry_date=ctx.expiry_var.get().strip() or None,
                supplier_id=supplier_id, payment_method=method,
                cheque_number=ctx.cheque_number_var.get(), cheque_date=ctx.cheque_date_var.get().strip() or None,
            )
        except ValueError as e:
            # See the matching comment in _addproduct_save_existing() above -
            # a native messagebox popup on THIS dialog was reported to
            # leave the window looking like it had closed/gone back to
            # the main page after being dismissed, so this stays an
            # in-dialog message instead; nothing typed is lost either way.
            ctx.status_label.configure(text=str(e), text_color=theme.DANGER_RED)
            return
        except Exception as e:  # noqa: BLE001 - anything unexpected still leaves everything you typed in place
            ctx.status_label.configure(
                text=f"Something went wrong and this wasn't saved: {e} - nothing you entered "
                     "was lost, fix the problem (or click Clear to start over) and try again.",
                text_color=theme.DANGER_RED,
            )
            return
        self.refresh()
        ctx.session_count["n"] += 1
        self._addproduct_update_session_label(ctx)
        ctx.status_label.configure(
            text=f"'{new_name}' was added to the catalog. "
                 f"Ready for the next item - scan/type its code, or click Done to finish.",
            text_color=theme.SUCCESS_GREEN,
        )
        self._addproduct_reset_form(ctx)

    def _addproduct_refresh_scroll_region(self, ctx) -> None:
        """ctx.body (the whole form above the buttons) is a
        CTkScrollableFrame, and on Windows in particular its inner
        canvas can fail to notice that the content changed height the
        instant a child frame (existing_frame/new_frame/cheque_fields)
        gets pack_forget()'d or pack()'d back mid-dialog - the widgets
        are all really there and correctly configured (the Save
        button's own text/state proves the code above ran fine), but
        the canvas's scroll region goes stale and the visible area can
        render as a totally blank page until something else forces a
        redraw (resizing the window, scrolling, etc). That's the
        reported "Add / Scan Item goes blank" - a rendering glitch in
        the scroll container, not a crash and not lost data. Forcing
        an idle-task flush plus a fresh scrollregion recompute after
        every visibility change fixes that. Best-effort and silent:
        _parent_canvas is a private CustomTkinter attribute that has
        been stable across the 5.x line but isn't guaranteed forever,
        so if a future version renames it this just quietly does
        nothing rather than breaking the dialog over a cosmetic fix."""
        try:
            ctx.dialog.update_idletasks()
            canvas = ctx.body._parent_canvas
            canvas.configure(scrollregion=canvas.bbox("all"))
        except Exception:  # noqa: BLE001
            pass

    def _addproduct_check_code(self, ctx, keep_status: bool = False) -> None:
        """keep_status=True is used right after a successful save (via
        _addproduct_reset_form): the code field is now blank again, but we
        don't want to stomp on the green "added!" confirmation
        ctx.status_label just showed - it should stay up until the user
        starts on the next item."""
        try:
            # The live suggestion list (code_suggestions) never closes
            # itself on <Return> by design - only on a click/Escape/
            # focus-out, or picking one - so a code committed by
            # pressing Enter (typed or scanned) would otherwise leave
            # that popup sitting there looking like it's still
            # offering choices after this already acted on one.
            # Always close it the moment this dialog is about to act
            # on whatever's in the box.
            ctx.code_suggestions.close()
            code = ctx.code_var.get().strip()
            if not code:
                ctx.existing_frame.pack_forget()
                ctx.new_frame.pack_forget()
                ctx.save_btn.configure(state="disabled")
                if not keep_status:
                    ctx.status_label.configure(
                        text="Type, scan with a USB/Bluetooth scanner, tap \"Scan with Phone\", "
                             "or pick from the list above - a suggestion list appears as you go, "
                             "but nothing happens until you press Enter or click a suggestion.",
                        text_color=theme.TEXT_MUTED,
                    )
                ctx.state["mode"] = None
                ctx.state["product"] = None
                return
            # Everything below this point does real work (a DB lookup,
            # then hiding one frame and showing another) with no
            # safety net of its own - unlike every button click in
            # this dialog (Save, Add Stock, etc), which is wrapped in
            # its own try/except and reports a problem via
            # status_label. Before this try/except existed, an
            # unexpected error partway through (e.g. right after
            # new_frame.pack_forget() but before existing_frame.pack()
            # ran) left BOTH frames hidden - the whole dialog looking
            # like it had simply gone blank, with no error shown
            # anywhere to explain why. Wrapping the whole thing means
            # that instead always ends in a real error message, never
            # silent blankness.
            try:
                product = pos.get_product_by_code(self.conn, code)
            except Exception as e:  # noqa: BLE001
                ctx.existing_frame.pack_forget()
                ctx.new_frame.pack_forget()
                ctx.save_btn.configure(state="disabled")
                ctx.status_label.configure(
                    text=f"Couldn't look up that code: {e} - try again, or click Clear to start over.",
                    text_color=theme.DANGER_RED,
                )
                ctx.state["mode"] = None
                ctx.state["product"] = None
                return
            try:
                self._addproduct_apply_code_lookup_result(ctx, code, product)
            except Exception as e:  # noqa: BLE001
                ctx.existing_frame.pack_forget()
                ctx.new_frame.pack_forget()
                ctx.save_btn.configure(state="disabled")
                ctx.status_label.configure(
                    text=f"Something went wrong showing that item: {e} - try again, or click Clear "
                         "to start over. Nothing was saved.",
                    text_color=theme.DANGER_RED,
                )
                ctx.state["mode"] = None
                ctx.state["product"] = None
        finally:
            # Runs on every path through the above (blank code, a
            # found product, a new one, or either error branch) so a
            # stale-canvas blank page can never survive to be the last
            # thing check_code leaves on screen.
            self._addproduct_refresh_scroll_region(ctx)

    def _addproduct_apply_code_lookup_result(self, ctx, code: str, product) -> None:
        if product is not None:
            ctx.save_btn.configure(state="normal")
            ctx.state["mode"] = "existing"
            ctx.new_frame.pack_forget()
            # A NEW product deserves fresh per-item fields, not
            # whatever was typed for the LAST existing product checked
            # here - otherwise a quantity/cost/expiry typed for one
            # item could silently carry over and get applied to a
            # completely different one if the phone scans the next
            # item before this one's Save is clicked (continuous
            # scanning means that next scan can arrive at any moment,
            # not just after the cashier is done with this one).
            # Guarded on the product actually changing so check_code
            # re-firing for the SAME still-unsaved code (e.g. a
            # redundant FocusOut) doesn't wipe out mid-typed values.
            # Payment is deliberately NOT reset here (see
            # _addproduct_reset_form) - it's a whole-delivery choice,
            # not a per-item one, so switching to a different
            # not-yet-saved item mid-session should keep whatever was
            # already picked.
            if ctx.state["product"] != product["id"]:
                ctx.qty_received_var.set("10")
                ctx.existing_cost_var.set("")
                ctx.existing_expiry_var.set("")
            ctx.state["product"] = product["id"]
            on_hand = pos.get_stock_on_hand(self.conn, product["id"])
            ctx.existing_info.configure(
                text=f"Already in the catalog: {product['name_en']} ({product['category']}) - "
                     f"{on_hand:.0f} {product['unit']} in stock now. Just enter how many more came in."
            )
            ctx.existing_frame.pack(fill="x")
            ctx.status_label.configure(text=f"Found an existing product for code '{code}'.", text_color=theme.TEXT_MUTED)
            ctx.save_btn.configure(text="Add Stock", command=lambda: self._addproduct_save_existing(ctx))
        else:
            ctx.state["mode"] = "new"
            ctx.state["product"] = None
            ctx.existing_frame.pack_forget()
            ctx.new_frame.pack(fill="x")
            ctx.status_label.configure(text=f"Code '{code}' isn't in the catalog yet - fill in the new "
                                             f"product's details below. Save New Product unlocks once "
                                             f"the name, category, and cash price are filled in.",
                                        text_color=theme.TEXT_MUTED)
            ctx.save_btn.configure(text="Save New Product", command=lambda: self._addproduct_save_new(ctx))
            self._addproduct_update_save_button_state(ctx)

    # _open_phone_capture_dialog moved to BaseScreen - POSScreen's search box
    # uses it too now, not just this dialog's barcode field.

    def open_stock_take_dialog(self):
        """Physical stock count, one category at a time - see stock_take.py.
        Admin/Owner only, same bar as every other stock-affecting action on
        this screen."""
        if not self._is_admin():
            messagebox.showwarning("Admin/Owner only", "Log in as Admin or Owner to run a stock take.")
            return

        categories = pos.list_categories(self.conn)
        if not categories:
            messagebox.showinfo("No products", "There are no active products to count yet.")
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Stock Take")
        fit_dialog(dialog, 640, 640)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        top = ctk.CTkFrame(dialog, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(top, text="Category:", text_color=theme.TEXT_DARK).pack(side="left")
        category_var = tk.StringVar(value=categories[0])

        ctk.CTkLabel(
            dialog,
            text="Counted quantity is pre-filled with what the system currently expects - overtype "
                 "any row that's actually different on the shelf. A row you leave unchanged is "
                 "recorded as matching the system exactly, so count every row you can see.",
            text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11), wraplength=600, justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        state = {"rows": []}  # [(product_id, system_qty, unit_cost, counted_var), ...]

        def load_category():
            for w in body.winfo_children():
                w.destroy()
            state["rows"] = []
            products = stock_take_module.products_for_count(self.conn, category_var.get())
            header = ctk.CTkFrame(body, fg_color="transparent")
            header.pack(fill="x", pady=(0, 4))
            ctk.CTkLabel(header, text="Product", width=260, anchor="w",
                         font=ctk.CTkFont(weight="bold")).pack(side="left")
            ctk.CTkLabel(header, text="System Qty", width=90, anchor="w",
                         font=ctk.CTkFont(weight="bold")).pack(side="left")
            ctk.CTkLabel(header, text="Counted Qty", width=90, anchor="w",
                         font=ctk.CTkFont(weight="bold")).pack(side="left")
            for p in products:
                row = ctk.CTkFrame(body, fg_color="transparent")
                row.pack(fill="x", pady=2)
                ctk.CTkLabel(row, text=p["name_en"], width=260, anchor="w").pack(side="left")
                ctk.CTkLabel(row, text=f"{p['system_qty']:g}", width=90, anchor="w").pack(side="left")
                counted_var = tk.StringVar(value=f"{p['system_qty']:g}")
                ctk.CTkEntry(row, textvariable=counted_var, width=80).pack(side="left")
                state["rows"].append((p["product_id"], p["system_qty"], p["cost_price"], counted_var))
            if not products:
                ctk.CTkLabel(body, text="No active products in this category.",
                             text_color=theme.TEXT_MUTED).pack(pady=10)

        ctk.CTkOptionMenu(
            top, values=categories, variable=category_var, command=lambda _c: load_category(),
        ).pack(side="left", padx=8)
        load_category()

        apply_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            dialog, text="Apply these counts to stock now (otherwise just record the variance report)",
            variable=apply_var,
        ).pack(anchor="w", padx=16, pady=(0, 4))

        def save_and_finish():
            counts = []
            for product_id, system_qty, unit_cost, counted_var in state["rows"]:
                try:
                    counted = float(counted_var.get().strip())
                except ValueError:
                    messagebox.showerror("Invalid number", "Every counted quantity must be a number.")
                    return
                if counted < 0:
                    messagebox.showerror("Invalid number", "Counted quantity can't be negative.")
                    return
                counts.append((product_id, system_qty, counted, unit_cost))
            if not counts:
                messagebox.showwarning("Nothing to count", "No products in this category.")
                return

            staff_row = self.conn.execute(
                "SELECT name FROM staff WHERE id=?", (self.app.current_staff_id,)
            ).fetchone()
            stock_take_id = stock_take_module.start_session(
                self.conn, self.app.current_staff_id, category_var.get(),
            )
            stock_take_module.save_counts(self.conn, stock_take_id, counts)
            summary = stock_take_module.finish_session(
                self.conn, stock_take_id, apply_adjustments=apply_var.get(),
                actor_staff_id=self.app.current_staff_id,
                actor_name=staff_row["name"] if staff_row else "Someone",
            )
            messagebox.showinfo(
                "Stock take saved",
                f"{summary['lines_counted']} item(s) counted, {summary['lines_with_variance']} with a "
                f"variance.\nNet variance LKR {summary['total_variance_value']:,.2f} "
                f"(shortfall LKR {summary['shortfall_value']:,.2f}).\n\n"
                + ("Stock has been adjusted to match the count." if apply_var.get()
                   else "Stock was NOT changed - this was recorded as a report only.")
                + "\n\nSee Reports > Stock Take History for the full record."
            )
            dialog.destroy()
            self.refresh()

        styled_button(dialog, "Save Stock Take", save_and_finish, kind="success").pack(pady=14)


# --------------------------------------------------------------------------- #
# Promotions & Expiry
# --------------------------------------------------------------------------- #

class PromotionsScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("🏷️ Promotions & Expiry Urgency", subtitle="Rank expiring stock batches and launch promotional markdowns to prevent waste")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12)
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
        tree_frame.pack(fill="both", expand=True, padx=12, pady=10)
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
        self.header("📈 Demand Forecasting & Planning", subtitle="AI-powered sales prediction, model selection, and automated purchase list generation")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12)
        ctk.CTkLabel(top, text="Product:", text_color=theme.TEXT_DARK).pack(side="left")
        products = self.conn.execute("SELECT id, name_en FROM products WHERE active=1 ORDER BY name_en").fetchall()
        self._products = products
        names = [p["name_en"] for p in products]
        self.product_var = tk.StringVar(value=names[0] if names else "")
        SearchableDropdown(top, values=names, variable=self.product_var, width=280, label="Product").pack(
            side="left", padx=8)
        self.run_forecast_btn = styled_button(top, "Run Forecast", self.run_forecast)
        self.run_forecast_btn.pack(side="left", padx=8)
        self.purchase_list_btn = styled_button(top, "Generate Purchase List (all products)", self.run_purchase_list,
                                                kind="secondary")
        self.purchase_list_btn.pack(side="left", padx=20)

        self.status_label = ctk.CTkLabel(self, text="", text_color=theme.TEXT_MUTED)
        self.status_label.pack(anchor="w", padx=12, pady=(6, 0))

        self.chart_frame = ctk.CTkFrame(self, fg_color="transparent", height=280)
        self.chart_frame.pack(fill="x", padx=12, pady=10)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=12)
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
        self.header("🛍️ Bundle Recommendations", subtitle="Real pairings, ranked by the profit they already earn - not just how often they're bought together")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12)
        self.compute_btn = styled_button(top, "Compute Bundles", self.compute)
        self.compute_btn.pack(side="left")
        self.status_label = ctk.CTkLabel(
            top, text="min support 2%, min confidence 30%, min lift 1.1 — ranked by real profit potential",
            text_color=theme.TEXT_MUTED)
        self.status_label.pack(side="left", padx=16)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=12, pady=10)
        # Lift/confidence stay visible (they're why a pairing is trusted as
        # real, not noise) but the list itself is ranked - and the columns
        # most worth a glance - by real profit: Co-Purchases x margin per
        # bundle is literally how Est. Profit is computed (association.py's
        # bundle_profit_potential), so a cashier/owner can see the "why" of
        # the ranking, not just trust a sorted list.
        columns = ["Bundle", "Co-Purchases", "Confidence", "Lift", "Bundle Price", "Savings", "Est. Profit"]
        widths = {"Bundle": 270, "Co-Purchases": 95, "Confidence": 90, "Lift": 65,
                  "Bundle Price": 95, "Savings": 85, "Est. Profit": 110}
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
                tree_insert(self.tree, (bundle_name, r["co_occurrences"], r["confidence"], r["lift"],
                                         f"{r['bundle_price_lkr']:.2f}", f"{r['savings_lkr']:.2f}",
                                         f"{r['realized_profit_lkr']:,.2f}"))
            self.status_label.configure(
                text=f"{len(rows)} bundle recommendations, ranked by real profit potential (highest first)")

        def on_error(err):
            self.compute_btn.configure(state="normal")
            messagebox.showerror("Bundle computation failed", str(err))

        run_in_background(self, work, on_done, on_error)


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #

REPORT_OPTIONS = ["Daily Sales Summary", "Best Sellers (30d)", "Stock Summary", "Low Stock Alert",
                   "Waste Summary", "Cashier Daily Statement", "Credit Outstanding",
                   "Returns Summary (30d)", "Supplier Summary", "Cash Sessions (Day Open/Close)",
                   "Stock Take History"]
# A numbered picker, same idea as the sample POS system's own Report Name
# list (01 .. NN) - still backed by the plain REPORT_OPTIONS names above
# for every actual comparison below; _strip_report_number is the only
# place that ever has to know about the "01  " prefix.
NUMBERED_REPORT_OPTIONS = [f"{i + 1:02d}  {name}" for i, name in enumerate(REPORT_OPTIONS)]


def _strip_report_number(choice: str) -> str:
    return choice.split("  ", 1)[1] if "  " in choice else choice


class ReportsScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("📑 Business Reports & Analytics", subtitle="Financial statements, sales breakdowns, cashier reconciliations, and data exports")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12)
        self.report_var = tk.StringVar(value=NUMBERED_REPORT_OPTIONS[0])
        ctk.CTkOptionMenu(top, values=NUMBERED_REPORT_OPTIONS, variable=self.report_var, width=240,
                          command=self._on_report_change).pack(side="left")
        styled_button(top, "Run Report", self.run_report).pack(side="left", padx=8)
        styled_button(top, "Export CSV", self.export, kind="secondary").pack(side="left", padx=8)
        styled_button(top, "Backup Database", self.backup, kind="secondary").pack(side="left", padx=20)

        self.cashier_row = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkLabel(self.cashier_row, text="Staff:", text_color=theme.TEXT_DARK).pack(side="left", padx=(12, 4))
        staff_names = [r["name"] for r in self.conn.execute("SELECT name FROM staff").fetchall()] or ["Admin"]
        self.cashier_staff_var = tk.StringVar(value=staff_names[0])
        SearchableDropdown(
            self.cashier_row, values=staff_names, variable=self.cashier_staff_var, width=200, label="Staff",
        ).pack(side="left")
        ctk.CTkLabel(self.cashier_row, text="Date (YYYY-MM-DD):", text_color=theme.TEXT_DARK).pack(
            side="left", padx=(16, 4))
        self.cashier_date_var = tk.StringVar(value=date.today().isoformat())
        ctk.CTkEntry(self.cashier_row, textvariable=self.cashier_date_var, width=120).pack(side="left")
        # hidden until "Cashier Daily Statement" is selected - see _on_report_change

        self.date_row = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkLabel(self.date_row, text="Date (YYYY-MM-DD):", text_color=theme.TEXT_DARK).pack(
            side="left", padx=(12, 4))
        self.report_date_var = tk.StringVar(value=date.today().isoformat())
        ctk.CTkEntry(self.date_row, textvariable=self.report_date_var, width=120).pack(side="left")
        # hidden until "Daily Sales Summary" is selected - see _on_report_change

        self.summary_label = ctk.CTkLabel(self, text="", text_color=theme.NAVY_DARK,
                                           font=ctk.CTkFont(size=14, weight="bold"))
        self.summary_label.pack(anchor="w", padx=12, pady=(8, 0))

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=12, pady=10)
        self.tree = make_treeview(tree_frame, ["A", "B", "C", "D"], {})
        self.tree.master_frame.pack(fill="both", expand=True)
        self._rows: list[dict] = []
        self._on_report_change(self.report_var.get())

    def _on_report_change(self, choice: str):
        choice = _strip_report_number(choice)
        self.cashier_row.pack_forget()
        self.date_row.pack_forget()
        if choice == "Cashier Daily Statement":
            self.cashier_row.pack(fill="x", pady=(6, 0), before=self.summary_label)
        elif choice == "Daily Sales Summary":
            self.date_row.pack(fill="x", pady=(6, 0), before=self.summary_label)

    def _set_columns(self, columns: list[str]):
        self.tree.configure(columns=columns)
        for c in columns:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=140)

    def run_report(self):
        choice = _strip_report_number(self.report_var.get())
        tree_clear(self.tree)
        self.summary_label.configure(text="")

        if choice == "Daily Sales Summary":
            try:
                day = date.fromisoformat(self.report_date_var.get().strip())
            except ValueError:
                messagebox.showerror("Invalid date", "Use YYYY-MM-DD format.")
                return
            rows = [dict(r) for r in reports.daily_sales_summary(self.conn, day)]
            self._set_columns(["name_en", "qty", "revenue"])
            total = sum(r["revenue"] for r in rows)
            self.summary_label.configure(
                text=f"{day.isoformat()}: {len(rows)} product(s) sold, LKR {total:,.2f} total revenue"
            )
        elif choice == "Best Sellers (30d)":
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
        elif choice == "Stock Take History":
            rows = stock_take_module.session_history(self.conn)
            self._set_columns(["category", "staff_name", "started_at", "status",
                                "lines_counted", "lines_with_variance", "total_variance_value",
                                "shortfall_value"])
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

def open_customer_registration_dialog(
    parent, conn, *, prefill_phone: str = "", allow_credit_limit: bool = True
) -> sqlite3.Row | None:
    """Shared 'Add Customer' flow: the Customers screen's own "Add
    Customer" button, and the POS screen's on-the-spot registration when a
    phone number typed at checkout doesn't match anyone yet. Phone is
    required - it's this customer's loyalty ID (see customers.py), what
    they'll be identified by at every future checkout, so a customer with
    no phone on file could never be found again. Blocks until the dialog
    closes (a short modal form - fine to use Tkinter's own wait_window
    here) and returns the newly created customer row, or None if
    cancelled.

    allow_credit_limit controls whether the person registering this
    customer gets to choose their credit limit at all - callers pass their
    own _is_admin() here. A Cashier registering someone (at the till, or
    from this screen) always gets the shop's own default credit limit
    (customers_module.get_default_credit_limit) with no way to type a
    different number - letting any cashier hand out an arbitrary credit
    limit on the spot was the actual gap being closed, not just a UI
    nicety. Admin/Owner still sees the normal editable field, pre-filled
    with that same default as a starting point they're free to change."""
    result: dict = {"row": None}
    default_limit = customers_module.get_default_credit_limit(conn)
    dialog = ctk.CTkToplevel(parent)
    dialog.title("Register Loyalty Customer")
    fit_dialog(dialog, 380, 360)
    dialog.configure(fg_color=theme.BG_LIGHT)
    dialog.grab_set()

    body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
    body.pack(fill="both", expand=True)

    ctk.CTkLabel(
        body, text="Registering makes them a loyalty member - found at future checkouts by the "
                    "phone number below, and eligible for loyalty-only offers.",
        text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11), wraplength=320, justify="left",
    ).pack(padx=16, pady=(12, 0))

    fields = {}
    for label, key, default in [("Name:", "name", ""), ("Phone:", "phone", prefill_phone),
                                 ("Address:", "address", "")]:
        ctk.CTkLabel(body, text=label).pack(anchor="w", padx=16, pady=(12, 0))
        v = tk.StringVar(value=default)
        ctk.CTkEntry(body, textvariable=v, width=320).pack(padx=16)
        fields[key] = v

    if allow_credit_limit:
        ctk.CTkLabel(body, text="Credit Limit:").pack(anchor="w", padx=16, pady=(12, 0))
        credit_limit_var = tk.StringVar(value=f"{default_limit:g}")
        ctk.CTkEntry(body, textvariable=credit_limit_var, width=320).pack(padx=16)
    else:
        credit_limit_var = None
        ctk.CTkLabel(
            body,
            text=f"Credit limit: LKR {default_limit:,.2f} (the shop's default for a new customer - "
                 "only Admin/Owner can set a different one, either here or later from the "
                 "Customers screen's \"Edit Credit Limit\").",
            text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11), wraplength=320, justify="left",
        ).pack(anchor="w", padx=16, pady=(12, 0))

    def save():
        name = fields["name"].get().strip()
        phone = fields["phone"].get().strip()
        if not name:
            messagebox.showerror("Missing name", "Customer name is required.")
            return
        if not phone:
            messagebox.showerror("Missing phone", "Phone number is required - it's how this customer "
                                                    "gets identified at checkout.")
            return
        if credit_limit_var is not None:
            try:
                limit = float(credit_limit_var.get() or 0)
            except ValueError:
                messagebox.showerror("Invalid credit limit", "Enter a number.")
                return
        else:
            limit = default_limit
        try:
            customer_id = customers_module.add_customer(conn, name, phone, fields["address"].get(), limit)
        except ValueError as e:
            messagebox.showerror("Can't register customer", str(e))
            return
        result["row"] = customers_module.get_customer(conn, customer_id)
        dialog.destroy()

    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.bind("<Return>", lambda e: save())
    styled_button(dialog, "Save", save, kind="primary", width=140).pack(pady=18)

    parent.wait_window(dialog)
    return result["row"]


class CustomersScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("👥 Customers & Credit Accounts", subtitle="Manage loyalty profiles, outstanding credit ledgers, and credit limits")

        self.hint = ctk.CTkLabel(
            self, text="", text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11),
            wraplength=760, justify="left",
        )
        self.hint.pack(anchor="w", padx=12, pady=(0, 6))

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12)
        styled_button(top, "Refresh", self.refresh, kind="secondary").pack(side="left")
        styled_button(top, "Add Customer", self.open_add_dialog, kind="primary").pack(side="left", padx=8)
        styled_button(top, "Settle Credit", self.open_settle_dialog, kind="success").pack(side="left", padx=8)
        styled_button(top, "View Statement", self.open_statement_dialog, kind="secondary").pack(side="left", padx=8)
        # Both Admin/Owner-only - see refresh() below, which disables these
        # for a Cashier rather than hiding them, same as InventoryScreen
        # does for its own admin-only buttons.
        self.edit_limit_btn = styled_button(
            top, "Edit Credit Limit", self.open_edit_credit_limit_dialog, kind="secondary")
        self.edit_limit_btn.pack(side="left", padx=8)
        self.default_limit_btn = styled_button(
            top, "Default Credit Limit", self.open_default_credit_limit_dialog, kind="secondary")
        self.default_limit_btn.pack(side="left", padx=8)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=12, pady=10)
        self.tree = make_treeview(
            tree_frame, ["Name", "Phone", "Credit Limit", "Balance Owed", "Available Credit"],
            {"Name": 200, "Phone": 120, "Credit Limit": 100,
             "Balance Owed": 100, "Available Credit": 120},
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

    def refresh(self):
        is_admin = self._is_admin()
        self.hint.configure(
            text="Anyone can register a new loyalty customer (they get the shop's default credit "
                 "limit below), settle a payment, or view a statement. Changing a customer's own "
                 "credit limit, or the shop's default for new customers, is Admin/Owner-only "
                 "- log in from the sidebar first."
            if not is_admin else
            "As Admin/Owner, you can also set a different credit limit for a customer while "
            "registering them, change an existing customer's limit, or change the shop's default."
        )
        self.edit_limit_btn.configure(state="normal" if is_admin else "disabled")
        self.default_limit_btn.configure(state="normal" if is_admin else "disabled")
        tree_clear(self.tree)
        for c in customers_module.list_customers(self.conn):
            available = round(c["credit_limit"] - c["credit_balance"], 2)
            tag = "overdue" if c["credit_limit"] > 0 and c["credit_balance"] >= c["credit_limit"] else None
            tree_insert(
                self.tree,
                (c["name"], c["phone"] or "", f"{c['credit_limit']:,.2f}",
                 f"{c['credit_balance']:,.2f}", f"{available:,.2f}"),
                tag=tag, iid=str(c["id"]),
            )

    def _selected_customer_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def open_add_dialog(self):
        # A Cashier gets the shop's default credit limit with no way to
        # change it (allow_credit_limit=False shows it as a read-only
        # line instead of an entry) - only Admin/Owner can pick a
        # different one for a new customer here. See
        # open_customer_registration_dialog's own docstring.
        open_customer_registration_dialog(self, self.conn, allow_credit_limit=self._is_admin())
        self.refresh()

    def open_edit_credit_limit_dialog(self):
        if not self._is_admin():
            messagebox.showwarning(
                "Admin required", "Log in as Admin or Owner (sidebar) to change a customer's credit limit.")
            return
        customer_id = self._selected_customer_id()
        if customer_id is None:
            messagebox.showwarning("Select a customer", "Select a customer in the list first.")
            return
        c = customers_module.get_customer(self.conn, customer_id)
        if c is None:
            return
        if c["name"] == "Walk-in":
            messagebox.showinfo(
                "Not applicable",
                "\"Walk-in\" is the anonymous cash-sale customer, not a real registered one - it "
                "never has a credit limit of its own.",
            )
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Edit Credit Limit — {c['name']}")
        fit_dialog(dialog, 360, 300)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(body, text=f"{c['name']} ({c['phone'] or 'no phone on file'})",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color=theme.NAVY_DARK).pack(pady=(16, 2))
        ctk.CTkLabel(body, text=f"Currently owes LKR {c['credit_balance']:,.2f} of a "
                                 f"LKR {c['credit_limit']:,.2f} limit.",
                     text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11)).pack(pady=(0, 10))

        ctk.CTkLabel(body, text="New credit limit:").pack(anchor="w", padx=20)
        limit_var = tk.StringVar(value=f"{c['credit_limit']:g}")
        ctk.CTkEntry(body, textvariable=limit_var, width=300).pack(padx=20)

        def save():
            try:
                new_limit = float(limit_var.get())
            except ValueError:
                messagebox.showerror("Invalid credit limit", "Enter a number.")
                return
            if new_limit < 0:
                messagebox.showerror("Invalid credit limit", "Credit limit can't be negative.")
                return
            if new_limit < c["credit_balance"] and not messagebox.askyesno(
                "Below current balance",
                f"{c['name']} already owes LKR {c['credit_balance']:,.2f} - more than this new "
                f"limit of LKR {new_limit:,.2f}. They won't be able to buy on credit again until "
                "they pay some of it down. Save anyway?",
            ):
                return
            customers_module.update_customer(
                self.conn, customer_id, c["name"], c["phone"] or "", c["address"] or "", new_limit)
            dialog.destroy()
            self.refresh()

        dialog.bind("<Return>", lambda e: save())
        styled_button(dialog, "Save", save, kind="primary", width=140).pack(pady=18)

    def open_default_credit_limit_dialog(self):
        if not self._is_admin():
            messagebox.showwarning(
                "Admin required", "Log in as Admin or Owner (sidebar) to change the default credit limit.")
            return
        current = customers_module.get_default_credit_limit(self.conn)

        dialog = ctk.CTkToplevel(self)
        dialog.title("Default Credit Limit")
        fit_dialog(dialog, 380, 300)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(
            body,
            text="This is the credit limit a brand-new customer starts with when a Cashier "
                 "registers them - at the till or from \"Add Customer\" here. Cashiers can't "
                 "choose a different one themselves; Admin/Owner still can, either for one "
                 "customer while registering them, or afterwards with \"Edit Credit Limit\".",
            text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11), wraplength=320, justify="left",
        ).pack(padx=16, pady=(16, 10))

        ctk.CTkLabel(body, text="Default credit limit for new customers:").pack(anchor="w", padx=20)
        limit_var = tk.StringVar(value=f"{current:g}")
        ctk.CTkEntry(body, textvariable=limit_var, width=300).pack(padx=20)

        def save():
            try:
                new_default = float(limit_var.get())
            except ValueError:
                messagebox.showerror("Invalid credit limit", "Enter a number.")
                return
            try:
                customers_module.set_default_credit_limit(self.conn, new_default)
            except ValueError as e:
                messagebox.showerror("Invalid credit limit", str(e))
                return
            dialog.destroy()

        dialog.bind("<Return>", lambda e: save())
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
        # Was 260 - technically never NEEDED maximizing (fit_dialog's own
        # docstring explains why: the Save-equivalent button already lives
        # outside the scrollable body, so it's always visible) but still
        # felt visibly cramped for what's on it (an amount, a method
        # dropdown, the button) - reported directly. Taller now so it
        # reads as a normal-sized dialog at a glance, not a fix for
        # anything actually broken.
        fit_dialog(dialog, 360, 340)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        # See open_add_dialog above (Add Customer) for why fields live in a
        # scrollable body while the button stays directly on `dialog`.
        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(body, text=f"{c['name']} owes LKR {c['credit_balance']:,.2f}",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color=theme.NAVY_DARK).pack(pady=(16, 10))

        ctk.CTkLabel(body, text="Amount received:").pack(anchor="w", padx=20)
        amount_var = tk.StringVar(value=f"{c['credit_balance']:.2f}")
        ctk.CTkEntry(body, textvariable=amount_var, width=300).pack(padx=20)

        ctk.CTkLabel(body, text="Method:").pack(anchor="w", padx=20, pady=(10, 0))
        method_var = tk.StringVar(value="cash")
        ctk.CTkOptionMenu(body, values=["cash", "card", "cheque"], variable=method_var, width=300).pack(padx=20)

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

        dialog.bind("<Return>", lambda e: save())
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
        fit_dialog(dialog, 560, 420)
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
# Offers (manual, staff-created discounts - see offers.py)
# --------------------------------------------------------------------------- #

class OffersScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("🎁 Promotional Offers & Discounts", subtitle="Configure percentage or fixed-amount discounts across products or categories")
        ctk.CTkLabel(
            self, text="Discounts staff set up on purpose - a product or a whole category, for "
                        "everyone or for loyalty members only. Applied automatically at checkout.\n"
                        "(For automatic near-expiry clearance discounts instead, see Promotions & Expiry.)",
            text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11), justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 8))

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12)
        styled_button(top, "Refresh", self.refresh, kind="secondary").pack(side="left")
        styled_button(top, "New Offer", self.open_add_dialog, kind="primary").pack(side="left", padx=8)
        styled_button(top, "Turn Off Selected", self.deactivate_selected, kind="danger").pack(side="left", padx=8)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=12, pady=10)
        self.tree = make_treeview(
            tree_frame, ["Name", "Applies To", "Discount", "For", "Starts", "Ends", "Active"],
            {"Name": 150, "Applies To": 170, "Discount": 80, "For": 90, "Starts": 90, "Ends": 90, "Active": 60},
        )
        self.tree.master_frame.pack(fill="both", expand=True)

    def on_show(self):
        self.refresh()

    def refresh(self):
        tree_clear(self.tree)
        for o in offers_module.list_offers(self.conn):
            applies_to = o["product_name"] if o["product_id"] else f"Category: {o['category']}"
            for_ = "Loyalty only" if o["scope"] == offers_module.SCOPE_LOYALTY else "Everyone"
            tree_insert(
                self.tree,
                (o["name"], applies_to, f"{o['discount_pct']:g}%", for_,
                 o["start_date"] or "-", o["end_date"] or "-", "Yes" if o["active"] else "No"),
                iid=str(o["id"]),
            )

    def _selected_offer_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def deactivate_selected(self):
        offer_id = self._selected_offer_id()
        if offer_id is None:
            messagebox.showwarning("Select an offer", "Select an offer in the list first.")
            return
        if not messagebox.askyesno(
            "Turn off offer",
            "Turn this offer off? It stops applying at checkout immediately - the offer itself "
            "stays on record (not deleted), so it can be reviewed or turned back on later.",
        ):
            return
        offers_module.set_active(self.conn, offer_id, False)
        self.refresh()

    def open_add_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("New Offer")
        fit_dialog(dialog, 420, 620)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(body, text="Offer name:").pack(anchor="w", padx=16, pady=(14, 0))
        name_var = tk.StringVar()
        ctk.CTkEntry(body, textvariable=name_var, width=340).pack(padx=16)

        ctk.CTkLabel(body, text="Applies to:").pack(anchor="w", padx=16, pady=(12, 0))
        target_kind_var = tk.StringVar(value="One product")
        ctk.CTkOptionMenu(body, values=["One product", "Whole category"], variable=target_kind_var,
                           width=340).pack(padx=16)

        products = self.conn.execute("SELECT id, name_en FROM products WHERE active=1 ORDER BY name_en").fetchall()
        product_names = [p["name_en"] for p in products]
        product_var = tk.StringVar(value=product_names[0] if product_names else "")
        product_menu = SearchableDropdown(body, values=product_names or ["(no products)"],
                                           variable=product_var, width=340, label="Product")
        product_menu.pack(padx=16, pady=(6, 0))

        categories = pos.list_categories(self.conn)
        category_var = tk.StringVar(value=categories[0] if categories else "")
        category_menu = SearchableDropdown(body, values=categories or ["(no categories)"],
                                            variable=category_var, width=340, label="Category")

        def on_target_kind_change(*_a):
            if target_kind_var.get() == "One product":
                category_menu.pack_forget()
                product_menu.pack(padx=16, pady=(6, 0))
            else:
                product_menu.pack_forget()
                category_menu.pack(padx=16, pady=(6, 0))

        target_kind_var.trace_add("write", on_target_kind_change)

        ctk.CTkLabel(body, text="Discount %:").pack(anchor="w", padx=16, pady=(12, 0))
        pct_var = tk.StringVar(value="10")
        ctk.CTkEntry(body, textvariable=pct_var, width=340).pack(padx=16)

        ctk.CTkLabel(body, text="For:").pack(anchor="w", padx=16, pady=(12, 0))
        scope_var = tk.StringVar(value="Everyone")
        ctk.CTkOptionMenu(body, values=["Everyone", "Loyalty members only"], variable=scope_var,
                           width=340).pack(padx=16)

        ctk.CTkLabel(body, text="Starts (YYYY-MM-DD, optional):").pack(anchor="w", padx=16, pady=(12, 0))
        start_var = tk.StringVar()
        ctk.CTkEntry(body, textvariable=start_var, width=340).pack(padx=16)

        ctk.CTkLabel(body, text="Ends (YYYY-MM-DD, optional):").pack(anchor="w", padx=16, pady=(12, 0))
        end_var = tk.StringVar()
        ctk.CTkEntry(body, textvariable=end_var, width=340).pack(padx=16)

        def save():
            name = name_var.get().strip()
            if not name:
                messagebox.showerror("Missing name", "Give this offer a short name.")
                return
            try:
                pct = float(pct_var.get())
            except ValueError:
                messagebox.showerror("Invalid discount", "Discount % must be a number.")
                return

            product_id = None
            category = None
            if target_kind_var.get() == "One product":
                if not products:
                    messagebox.showerror("No products", "There are no active products to pick from.")
                    return
                match = next((p for p in products if p["name_en"] == product_var.get()), None)
                if match is None:
                    messagebox.showerror("Pick a product", "Select a product from the list.")
                    return
                product_id = match["id"]
            else:
                if not categories:
                    messagebox.showerror("No categories", "There are no categories to pick from.")
                    return
                category = category_var.get()

            for label, value in (("Start date", start_var.get()), ("End date", end_var.get())):
                if value.strip():
                    try:
                        date.fromisoformat(value.strip())
                    except ValueError:
                        messagebox.showerror("Invalid date", f"{label} must be in YYYY-MM-DD form.")
                        return

            try:
                offers_module.add_offer(
                    self.conn, name, pct, product_id=product_id, category=category,
                    scope=offers_module.SCOPE_LOYALTY if scope_var.get() == "Loyalty members only"
                    else offers_module.SCOPE_ALL,
                    start_date=start_var.get().strip() or None, end_date=end_var.get().strip() or None,
                )
            except ValueError as e:
                messagebox.showerror("Can't save offer", str(e))
                return
            dialog.destroy()
            self.refresh()

        dialog.bind("<Return>", lambda e: save())
        styled_button(dialog, "Save Offer", save, kind="primary", width=160).pack(pady=18)


# --------------------------------------------------------------------------- #
# Suppliers
# --------------------------------------------------------------------------- #

class SuppliersScreen(BaseScreen):
    """Supplier master records plus the shop's accounts-payable side - not
    every delivery is paid cash on the spot, so this screen also covers:
    paying down what's owed a supplier, a reminder list of cheques given
    to suppliers that haven't cleared yet (so the bank account has the
    funds ready), a running statement per supplier, and sending stock back
    to one. See suppliers.py's module docstring for the full design - it
    deliberately mirrors CustomersScreen's credit-account handling.

    Paying a supplier or returning stock to one is Admin/Owner-only, same
    as receiving stock is in Inventory's "Add / Scan Item" (these are all
    purchasing-side actions, not day-to-day sales-floor ones) - Cheques
    Due and View Statement are just read-only information, so anyone can
    check them."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("🚚 Supplier Management", subtitle="Track supplier accounts, outstanding purchase balances, and post-dated cheques")

        self.hint = ctk.CTkLabel(
            self, text="", text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11),
            wraplength=760, justify="left",
        )
        self.hint.pack(anchor="w", padx=12, pady=(0, 6))

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12)
        styled_button(top, "Refresh", self.refresh, kind="secondary").pack(side="left")
        styled_button(top, "Add Supplier", self.open_add_dialog, kind="primary").pack(side="left", padx=8)
        styled_button(top, "View Statement", self.open_statement_dialog, kind="secondary").pack(side="left", padx=8)
        styled_button(top, "Cheques Due", self.open_cheques_due_dialog, kind="secondary").pack(side="left", padx=8)
        # Both Admin/Owner-only - see refresh() below, which disables these
        # for a Cashier rather than hiding them, same as everywhere else in
        # this app that gates a button this way.
        self.pay_btn = styled_button(top, "Pay Supplier", self.open_pay_dialog, kind="success")
        self.pay_btn.pack(side="left", padx=8)
        self.return_btn = styled_button(top, "Return to Supplier", self.open_return_dialog, kind="danger")
        self.return_btn.pack(side="left", padx=8)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=12, pady=10)
        self.tree = make_treeview(
            tree_frame, ["Name", "Phone", "Address", "Payable Balance"],
            {"Name": 200, "Phone": 130, "Address": 240, "Payable Balance": 120},
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

    def refresh(self):
        is_admin = self._is_admin()
        self.hint.configure(
            text="Anyone can register a new supplier, view a statement, or check cheques due. "
                 "Paying a supplier down or returning stock to one is Admin/Owner-only - log in "
                 "from the sidebar first."
            if not is_admin else
            "As Admin/Owner, you can also record a payment against a supplier's balance or send "
            "stock back to one."
        )
        self.pay_btn.configure(state="normal" if is_admin else "disabled")
        self.return_btn.configure(state="normal" if is_admin else "disabled")
        tree_clear(self.tree)
        for s in suppliers_module.list_suppliers(self.conn):
            tree_insert(
                self.tree,
                (s["name"], s["phone"] or "", s["address"] or "", f"{s['payable_balance']:,.2f}"),
                iid=str(s["id"]),
            )

    def _selected_supplier_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def open_add_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Add Supplier")
        fit_dialog(dialog, 380, 280)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        # Fields go in a scrollable body, packed BEFORE the Save button -
        # guarantees the button its own space regardless of Windows display
        # scaling; see open_login_dialog in app.py for the full reasoning.
        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        fields = {}
        for label, key in [("Name:", "name"), ("Phone:", "phone"), ("Address:", "address")]:
            ctk.CTkLabel(body, text=label).pack(anchor="w", padx=16, pady=(12, 0))
            v = tk.StringVar(value="")
            ctk.CTkEntry(body, textvariable=v, width=320).pack(padx=16)
            fields[key] = v

        def save():
            name = fields["name"].get().strip()
            if not name:
                messagebox.showerror("Missing name", "Supplier name is required.")
                return
            suppliers_module.add_supplier(self.conn, name, fields["phone"].get(), fields["address"].get())
            dialog.destroy()
            self.refresh()

        dialog.bind("<Return>", lambda e: save())
        styled_button(dialog, "Save", save, kind="primary", width=140).pack(pady=18)

    def open_pay_dialog(self):
        if not self._is_admin():
            messagebox.showwarning("Admin required", "Log in as Admin or Owner (sidebar) to pay a supplier.")
            return
        supplier_id = self._selected_supplier_id()
        if supplier_id is None:
            messagebox.showwarning("Select a supplier", "Select a supplier in the list first.")
            return
        s = suppliers_module.get_supplier(self.conn, supplier_id)
        if s is None or s["payable_balance"] <= 0:
            messagebox.showinfo("Nothing owed", f"{s['name'] if s else 'This supplier'} has no outstanding balance.")
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Pay Supplier")
        fit_dialog(dialog, 360, 340)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(body, text=f"You owe {s['name']} LKR {s['payable_balance']:,.2f}",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color=theme.NAVY_DARK).pack(pady=(16, 10))

        ctk.CTkLabel(body, text="Amount paying:").pack(anchor="w", padx=20)
        amount_var = tk.StringVar(value=f"{s['payable_balance']:.2f}")
        ctk.CTkEntry(body, textvariable=amount_var, width=300).pack(padx=20)

        ctk.CTkLabel(body, text="Method:").pack(anchor="w", padx=20, pady=(10, 0))
        method_var = tk.StringVar(value="cash")
        ctk.CTkOptionMenu(body, values=["cash", "cheque", "bank_transfer"], variable=method_var, width=300).pack(padx=20)

        def save():
            try:
                amount = float(amount_var.get())
            except ValueError:
                messagebox.showerror("Invalid amount", "Enter a number.")
                return
            if amount <= 0 or amount > s["payable_balance"] + 0.01:
                messagebox.showerror("Invalid amount", f"Enter an amount up to LKR {s['payable_balance']:,.2f}.")
                return
            suppliers_module.pay_supplier(self.conn, supplier_id, amount, method_var.get())
            dialog.destroy()
            self.refresh()

        dialog.bind("<Return>", lambda e: save())
        styled_button(dialog, "Record Payment", save, kind="success", width=160).pack(pady=18)

    def open_statement_dialog(self):
        supplier_id = self._selected_supplier_id()
        if supplier_id is None:
            messagebox.showwarning("Select a supplier", "Select a supplier in the list first.")
            return
        s = suppliers_module.get_supplier(self.conn, supplier_id)
        rows = suppliers_module.supplier_statement(self.conn, supplier_id)

        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Supplier Statement — {s['name'] if s else ''}")
        fit_dialog(dialog, 560, 420)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        stmt_tree = make_treeview(dialog, ["Date", "Description", "Charge", "Payment", "Balance"],
                                   {"Date": 130, "Description": 200, "Charge": 80, "Payment": 80, "Balance": 90})
        stmt_tree.master_frame.pack(fill="both", expand=True, padx=16, pady=16)
        for r in rows:
            tree_insert(stmt_tree, (r["ts"], r["description"], f"{r['charge']:.2f}" if r["charge"] else "",
                                     f"{r['payment']:.2f}" if r["payment"] else "", f"{r['balance']:.2f}"))
        if not rows:
            ctk.CTkLabel(dialog, text="No purchase/payment activity yet.", text_color=theme.TEXT_MUTED).pack(pady=10)

    def open_cheques_due_dialog(self):
        rows = suppliers_module.list_pending_cheques(self.conn)

        dialog = ctk.CTkToplevel(self)
        dialog.title("Cheques Due")
        fit_dialog(dialog, 560, 420)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog, text="Cheques given to suppliers that haven't been marked cleared yet - make "
                         "sure the amount is in the bank account by the date shown.",
            text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=11), wraplength=520, justify="left",
        ).pack(anchor="w", padx=16, pady=(16, 8))

        chq_tree = make_treeview(
            dialog, ["Supplier", "Cheque No.", "Due Date", "Amount"],
            {"Supplier": 180, "Cheque No.": 120, "Due Date": 110, "Amount": 100},
        )
        chq_tree.master_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        for r in rows:
            tree_insert(
                chq_tree,
                (r["supplier_name"], r["cheque_number"] or "-", r["cheque_date"] or "-", f"{r['amount']:,.2f}"),
                iid=str(r["id"]),
            )
        if not rows:
            ctk.CTkLabel(dialog, text="No cheques pending - nothing to remind you about.",
                         text_color=theme.TEXT_MUTED).pack(pady=10)
            return

        def mark_cleared():
            sel = chq_tree.selection()
            if not sel:
                messagebox.showwarning("Select a cheque", "Select a cheque in the list first.")
                return
            suppliers_module.mark_cheque_cleared(self.conn, int(sel[0]))
            dialog.destroy()
            self.open_cheques_due_dialog()

        styled_button(dialog, "Mark Selected as Cleared", mark_cleared, kind="secondary").pack(pady=(0, 16))

    def open_return_dialog(self):
        if not self._is_admin():
            messagebox.showwarning("Admin required", "Log in as Admin or Owner (sidebar) to return stock.")
            return
        supplier_id = self._selected_supplier_id()
        if supplier_id is None:
            messagebox.showwarning("Select a supplier", "Select a supplier in the list first.")
            return
        s = suppliers_module.get_supplier(self.conn, supplier_id)
        batches = self.conn.execute(
            """SELECT b.id, b.batch_no, b.qty_remaining, b.cost_price, p.name_en
               FROM stock_batches b JOIN products p ON p.id = b.product_id
               WHERE b.supplier_id=? AND b.qty_remaining > 0 ORDER BY b.received_date DESC""",
            (supplier_id,),
        ).fetchall()
        if not batches:
            messagebox.showinfo(
                "Nothing to return",
                f"No stock still on hand from {s['name'] if s else 'this supplier'} to send back.")
            return
        labels = [f"{b['batch_no']} - {b['name_en']} ({b['qty_remaining']:g} left)" for b in batches]

        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Return to Supplier — {s['name'] if s else ''}")
        fit_dialog(dialog, 420, 420)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(body, text="Batch:").pack(anchor="w", padx=16, pady=(16, 0))
        batch_var = tk.StringVar(value=labels[0])
        SearchableDropdown(body, values=labels, variable=batch_var, width=360, label="Batch").pack(padx=16)

        ctk.CTkLabel(body, text="Quantity returning:").pack(anchor="w", padx=16, pady=(12, 0))
        qty_var = tk.StringVar(value="1")
        ctk.CTkEntry(body, textvariable=qty_var, width=360).pack(padx=16)

        ctk.CTkLabel(body, text="Credit amount (blank = qty × cost price):").pack(anchor="w", padx=16, pady=(12, 0))
        credit_var = tk.StringVar()
        ctk.CTkEntry(body, textvariable=credit_var, width=360).pack(padx=16)

        ctk.CTkLabel(body, text="Reason:").pack(anchor="w", padx=16, pady=(12, 0))
        reason_var = tk.StringVar()
        ctk.CTkEntry(body, textvariable=reason_var, width=360).pack(padx=16)

        def save():
            try:
                idx = labels.index(batch_var.get())
                batch = batches[idx]
                qty = float(qty_var.get())
                credit_amount = float(credit_var.get()) if credit_var.get().strip() else None
                suppliers_module.return_to_supplier(
                    self.conn, batch["id"], qty, credit_amount=credit_amount, reason=reason_var.get().strip())
                dialog.destroy()
                self.refresh()
            except ValueError as e:
                messagebox.showerror("Invalid input", str(e))

        dialog.bind("<Return>", lambda e: save())
        styled_button(dialog, "Confirm Return", save, kind="danger", width=160).pack(pady=18)


# --------------------------------------------------------------------------- #
# Staff / cashiers
# --------------------------------------------------------------------------- #

class StaffScreen(BaseScreen):
    """Add cashiers/admins and set them up, without needing a developer to
    reach into the database by hand every time someone new joins the till.
    Everyone can see the list, since a cashier glancing at who's on shift is
    harmless. Adding staff or editing someone's saved contact info needs
    Admin or Owner. Activating/deactivating someone (this app's version of
    "removing" a staff member - see staff.py's docstring for why it's
    deactivate-not-delete) is Owner-only, same as removing an item in
    Inventory - a bigger call than Admin's day-to-day account upkeep.

    Nobody manages anyone else's PIN here anymore - not even the Owner.
    Every account only ever changes its OWN PIN, either directly ("Change
    My PIN" in the sidebar) or via a random one emailed/texted to itself if
    forgotten ("Forgot PIN?" on the Login screen) - see staff.py's module
    docstring for why a "reset someone else's PIN" feature was removed
    (an Admin/Owner being able to silently hand themselves access to any
    account was exactly the kind of thing worth not having at all). What
    THIS screen's "Edit Contact Info" is for: making sure someone actually
    HAS an email/phone on file, so Forgot PIN has somewhere to send their
    reset to if they ever need it.

    There is exactly one Owner account, always - db.py seeds it
    automatically, staff.add_staff refuses to create a second one, and
    staff.set_active refuses to deactivate it, so it can never be created,
    duplicated, or removed from here.

    Every sensitive change made here (adding staff, editing contact info,
    activating/deactivating) is written to the Activity Log (audit.py) with
    who did it, and a best-effort notification (notifications.py) is sent
    to the Owner so nothing has to be discovered after the fact."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("👤 Staff & Access Control", subtitle="Manage cashier accounts, role permissions, and access PIN credentials")

        self.hint = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, wraplength=700,
        )
        self.hint.pack(anchor="w", padx=12, pady=(0, 8))

        # grid, not pack/side="left" - four buttons shrink to fit a narrower
        # window or a scaled-up-DPI screen instead of the last one or two
        # running off the edge.
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12)
        for col in range(4):
            top.grid_columnconfigure(col, weight=1, uniform="staff_top")
        styled_button(top, "Refresh", self.refresh, kind="secondary").grid(
            row=0, column=0, sticky="ew", padx=(0, 4))
        self.add_btn = styled_button(top, "Add Staff", self.open_add_dialog, kind="primary")
        self.add_btn.grid(row=0, column=1, sticky="ew", padx=4)
        self.contact_btn = styled_button(top, "Edit Contact Info", self.open_edit_contact_dialog, kind="secondary")
        self.contact_btn.grid(row=0, column=2, sticky="ew", padx=4)
        self.toggle_btn = styled_button(top, "Activate / Deactivate", self.toggle_active, kind="danger")
        self.toggle_btn.grid(row=0, column=3, sticky="ew", padx=(4, 0))

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=12, pady=10)
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
        """Removing (deactivating) a staff member is Owner-only, same as
        removing an item in Inventory - Admin can add staff and fix their
        contact info, but taking someone off the roster is a bigger call
        reserved for the Owner."""
        return self._current_role() == "owner"

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
        self.hint.configure(
            text="Logged in as " + ("Owner" if is_owner else "Admin")
            + (" - you can add staff, edit contact info, or activate/deactivate someone below."
               if is_owner else
               " - you can add staff or edit contact info. Only the Owner can activate/deactivate a staff member.")
            if is_admin else
            "Only Admin/Owner can add staff or edit contact info, and only the Owner can "
            "activate/deactivate someone - log in from the sidebar first. Anyone can view this list."
        )
        for btn in (self.add_btn, self.contact_btn):
            btn.configure(state="normal" if is_admin else "disabled")
        self.toggle_btn.configure(state="normal" if is_owner else "disabled")
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

    def open_add_dialog(self):
        if not self._require_admin():
            return
        # "owner" is never offered here - there is exactly one Owner
        # account for the whole shop, seeded automatically (db.py), and
        # staff.add_staff refuses to create a second one even if this
        # dropdown were somehow bypassed.
        dialog = ctk.CTkToplevel(self)
        dialog.title("Add Staff")
        fit_dialog(dialog, 360, 460)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        # Fields go in a scrollable body, packed BEFORE the Save button -
        # guarantees the button its own space regardless of Windows display
        # scaling; see open_login_dialog in app.py for the full reasoning.
        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(body, text="Name:").pack(anchor="w", padx=16, pady=(16, 0))
        name_var = tk.StringVar()
        ctk.CTkEntry(body, textvariable=name_var, width=300).pack(padx=16)

        ctk.CTkLabel(body, text="Role:").pack(anchor="w", padx=16, pady=(12, 0))
        role_var = tk.StringVar(value="cashier")
        ctk.CTkOptionMenu(body, values=["cashier", "admin"], variable=role_var, width=300).pack(padx=16)

        ctk.CTkLabel(body, text="PIN (numbers, used to log in at the till):").pack(
            anchor="w", padx=16, pady=(12, 0))
        pin_var = tk.StringVar()
        ctk.CTkEntry(body, textvariable=pin_var, width=300).pack(padx=16)

        ctk.CTkLabel(body, text="Email (optional - needed for them to use \"Forgot PIN?\" later):").pack(
            anchor="w", padx=16, pady=(12, 0))
        email_var = tk.StringVar()
        ctk.CTkEntry(body, textvariable=email_var, width=300).pack(padx=16)

        ctk.CTkLabel(body, text="Phone (optional - for SMS/WhatsApp, needs Twilio set up):").pack(
            anchor="w", padx=16, pady=(12, 0))
        phone_var = tk.StringVar()
        ctk.CTkEntry(body, textvariable=phone_var, width=300).pack(padx=16)

        def save():
            role = role_var.get()
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
                    f"Your login PIN is: {pin_saved}\n\n"
                    "You can change this PIN yourself any time from the sidebar's \"Change My PIN\".",
                    to_email=target_email, to_phone=target_phone,
                )
            dialog.destroy()
            self.refresh()

        dialog.bind("<Return>", lambda e: save())
        styled_button(dialog, "Save", save, kind="primary", width=140).pack(pady=20)

    def open_edit_contact_dialog(self):
        """Lets an Admin/Owner add or correct the email/phone saved for
        someone else - never their PIN. The main reason to use this: a
        cashier who was added without an email/phone has no way to use
        "Forgot PIN?" if they're ever locked out - this gives them one for
        next time. It does not change anyone's PIN by itself."""
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

        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Edit Contact Info - {row['name']}")
        fit_dialog(dialog, 360, 320)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        # Fields go in a scrollable body, packed BEFORE the Save button -
        # guarantees the button its own space regardless of Windows display
        # scaling; see open_login_dialog in app.py for the full reasoning.
        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(body, text="Email:").pack(anchor="w", padx=16, pady=(16, 0))
        email_var = tk.StringVar(value=row["email"] or "")
        ctk.CTkEntry(body, textvariable=email_var, width=300).pack(padx=16)

        ctk.CTkLabel(body, text="Phone:").pack(anchor="w", padx=16, pady=(12, 0))
        phone_var = tk.StringVar(value=row["phone"] or "")
        ctk.CTkEntry(body, textvariable=phone_var, width=300).pack(padx=16)

        ctk.CTkLabel(
            body, text="This doesn't change their PIN - only they can do that, themselves, from "
                        "\"Change My PIN\" or \"Forgot PIN?\".",
            font=ctk.CTkFont(size=10), text_color=theme.TEXT_MUTED, wraplength=320, justify="left",
        ).pack(anchor="w", padx=16, pady=(12, 0))

        def save():
            new_email = email_var.get().strip() or None
            new_phone = phone_var.get().strip() or None
            try:
                staff_module.update_contact(self.conn, staff_id, email_var.get(), phone_var.get())
            except ValueError as e:
                messagebox.showerror("Cannot save", str(e))
                return
            audit.record(
                self.conn, actor_staff_id=self.app.current_staff_id, action="staff.edit_contact",
                target_staff_id=staff_id,
            )
            notifications.send(
                "SmartGrocer: contact info edited",
                f"{self._actor_label()} edited the saved contact info for {row['name']}.",
            )
            # Confirm it TO that person too, at whichever address/number is
            # now on file - so a change they didn't make or expect (not
            # just a PIN change) still reaches them directly, not only the
            # Owner's "who did what" copy above.
            if new_email or new_phone:
                notifications.send(
                    "SmartGrocer: your contact info was updated",
                    f"Your saved email/phone for SmartGrocer was updated by {self._actor_label()}.\n\n"
                    "If you didn't expect this, contact the shop owner.",
                    to_email=new_email, to_phone=new_phone,
                )
            dialog.destroy()
            self.refresh()

        dialog.bind("<Return>", lambda e: save())
        styled_button(dialog, "Save", save, kind="primary", width=140).pack(pady=18)

    def toggle_active(self):
        if not self._is_owner():
            messagebox.showwarning(
                "Owner required", "Only the Owner can activate or deactivate a staff member."
            )
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
        try:
            staff_module.set_active(self.conn, staff_id, new_active)
        except ValueError as e:
            # e.g. "The Owner account cannot be deactivated" - there is
            # exactly one Owner, always, and this is enforced in staff.py
            # itself (not just by disabling a button), so this is the real
            # backstop, not just UI dressing.
            messagebox.showerror("Cannot deactivate", str(e))
            return
        audit.record(
            self.conn, actor_staff_id=self.app.current_staff_id,
            action="staff.activate" if new_active else "staff.deactivate",
            target_staff_id=staff_id,
        )
        verb = "activated" if new_active else "deactivated"
        # Owner's copy: who did it, to whom. The affected person's own copy
        # (only if they have contact info on file): what changed to THEIR
        # account - matching how every other account-affecting action here
        # (add, contact edit) also messages the individual, not just the
        # Owner.
        notifications.send(f"SmartGrocer: staff {verb}", f"{self._actor_label()} {verb} {row['name']}.")
        target_email = row["email"] or None
        target_phone = row["phone"] or None
        if target_email or target_phone:
            notifications.send(
                f"SmartGrocer: your account was {verb}",
                f"Your SmartGrocer account was {verb} by {self._actor_label()}.\n\n"
                "If you didn't expect this, contact the shop owner.",
                to_email=target_email, to_phone=target_phone,
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
        self.header("🌐 Network & Multi-Till Sync", subtitle="Connect and synchronize multiple cash registers over local network")  # stays fixed at the top, outside the scrollable area below

        # Everything below goes in a scrollable frame, not straight into
        # `self` - this screen has no treeview or other expanding widget to
        # absorb extra space, so on a smaller screen or with Windows display
        # scaling above 100% its content could overflow with no way at all
        # to reach the rest (see DashboardScreen/POSScreen for the fuller
        # explanation of this same fix).
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)

        self.hint = ctk.CTkLabel(
            body, text="Connect more than one till to the same stock and sales data - wired Ethernet "
                       "or Wi-Fi both work, this only cares that the tills are on the same network.",
            font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, wraplength=760, justify="left",
        )
        self.hint.pack(anchor="w", padx=12, pady=(0, 10))

        self.status_card = ctk.CTkFrame(body, fg_color=theme.CARD_BG_ALT, corner_radius=10)
        self.status_card.pack(fill="x", padx=12, pady=(0, 14))
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

        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.pack(fill="x", padx=12)
        self.share_btn = styled_button(btn_row, "Share This Till's Data", self.start_sharing, kind="primary")
        self.share_btn.pack(side="left")
        self.stop_share_btn = styled_button(btn_row, "Stop Sharing", self.stop_sharing, kind="danger")
        self.stop_share_btn.pack(side="left", padx=8)
        self.connect_btn = styled_button(btn_row, "Connect to Another Till", self.open_connect_dialog,
                                          kind="secondary")
        self.connect_btn.pack(side="left", padx=8)
        self.sync_now_btn = styled_button(btn_row, "Sync Now", self.sync_now, kind="primary")
        self.sync_now_btn.pack(side="left", padx=8)
        self.disconnect_btn = styled_button(btn_row, "Disconnect (Use This PC's Own Data)",
                                             self.disconnect, kind="danger")
        self.disconnect_btn.pack(side="left", padx=8)

        warning_card = ctk.CTkFrame(body, fg_color=theme.CARD_BG_ALT, corner_radius=8)
        warning_card.pack(fill="x", padx=12, pady=(16, 0))
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
            is_offline_conn = isinstance(self.app.conn, offline_sync.OfflineConnection)
            pending = self.app.conn.pending_count if is_offline_conn else 0
            self.status_label.configure(
                text=f"Cashier Till - connected to {host}:{port}" if reachable else
                     f"Cashier Till - OFFLINE ({host}:{port} unreachable right now)"
            )
            if reachable:
                self.status_detail.configure(
                    text="This till is using the Main Till's shared data. Disconnect to go back to a "
                         "database that only this PC uses."
                )
            elif is_offline_conn:
                self.status_detail.configure(
                    text=(f"Working offline - {pending} transaction(s) queued and will sync automatically "
                          f"once the Main Till is reachable again (checked every 20s), or press \"Sync Now\". "
                          "Sales, stock and everything else keep working meanwhile from this till's own "
                          "local copy of the shop's data."
                          if pending else
                          "Working offline - nothing queued yet. Sales made now will sync automatically "
                          "once the Main Till is reachable again, or press \"Sync Now\".")
                )
            else:
                self.status_detail.configure(
                    text="Can't reach the Main Till right now - check it's turned on and both PCs are on the "
                         "same network."
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
        self.sync_now_btn.configure(
            state="normal" if (is_client and isinstance(self.app.conn, offline_sync.OfflineConnection)
                                and not self.app.conn.is_online) else "disabled"
        )

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
        fit_dialog(dialog, 380, 340)
        dialog.configure(fg_color=theme.BG_LIGHT)
        dialog.grab_set()

        # Fields go in a scrollable body, packed BEFORE the Connect button -
        # guarantees the button its own space regardless of Windows display
        # scaling; see open_login_dialog in app.py for the full reasoning.
        body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(
            body, text="Enter the address and pairing code shown on the Main Till's Network screen:",
            wraplength=340, justify="left",
        ).pack(anchor="w", padx=16, pady=(16, 10))

        ctk.CTkLabel(body, text="Main Till address (e.g. 192.168.1.5):").pack(anchor="w", padx=16)
        host_var = tk.StringVar()
        ctk.CTkEntry(body, textvariable=host_var).pack(fill="x", padx=16)

        ctk.CTkLabel(body, text="Port (leave as shown on the Main Till):").pack(anchor="w", padx=16, pady=(10, 0))
        port_var = tk.StringVar(value=str(netserver.DEFAULT_PORT))
        ctk.CTkEntry(body, textvariable=port_var).pack(fill="x", padx=16)

        ctk.CTkLabel(body, text="Pairing code:").pack(anchor="w", padx=16, pady=(10, 0))
        code_var = tk.StringVar()
        ctk.CTkEntry(body, textvariable=code_var).pack(fill="x", padx=16)

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

        dialog.bind("<Return>", lambda e: connect())
        styled_button(dialog, "Connect", connect, kind="primary", width=140).pack(pady=18)

    def sync_now(self):
        conn = self.app.conn
        if not isinstance(conn, offline_sync.OfflineConnection):
            return
        self.sync_now_btn.configure(state="disabled")

        def work():
            return conn.try_reconnect()

        def done(outcome):
            if outcome["still_offline"]:
                messagebox.showwarning(
                    "Still can't reach the Main Till",
                    "No luck this time" + (f" - {outcome['failed']} transaction(s) remain queued."
                                            if outcome["failed"] else ".") + " Will keep retrying automatically.",
                )
            else:
                messagebox.showinfo(
                    "Synced",
                    f"Reconnected - {outcome['synced']} queued transaction(s) synced to the Main Till."
                    if outcome["synced"] else "Reconnected to the Main Till.",
                )
            self.refresh()

        run_in_background(self, work, done, lambda e: (self.sync_now_btn.configure(state="normal"), self.refresh()))

    def disconnect(self):
        if not messagebox.askyesno(
            "Disconnect this till?",
            "This till will go back to using a database that only this PC can see, starting empty "
            "unless it had one before. Any sales made while connected (including anything still queued "
            "offline and not yet synced) stay on the Main Till/this till's own offline queue - they "
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
        "staff.pin_reset": "Reset PIN",  # historical - this action is no longer generated (see staff.py)
        "staff.pin_self_change": "Changed own PIN",
        "staff.pin_forgot_reset": "Reset PIN (forgot)",
        "staff.edit_contact": "Edited contact info",
        "staff.activate": "Activated staff",
        "staff.deactivate": "Deactivated staff",
        "product.edit": "Edited item",
        "product.deactivate": "Removed item",
        "product.reactivate": "Restored item",
    }

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("📋 Security & Audit Trail", subtitle="Inspect system events, staff authorizations, PIN updates, and item alterations")

        self.hint = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, wraplength=760, justify="left",
        )
        self.hint.pack(anchor="w", padx=12, pady=(0, 8))

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12)
        self.refresh_btn = styled_button(top, "Refresh", self.refresh, kind="secondary")
        self.refresh_btn.pack(side="left")
        self.notify_btn = styled_button(top, "Notification Settings", self.open_notification_settings, kind="secondary")
        self.notify_btn.pack(side="left", padx=8)

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=12, pady=10)
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
        fit_dialog(dialog, 460, 620)
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

        # On `dialog`, not `scroll` - same as btn_row below - so both the
        # buttons and the result text are always visible without having to
        # scroll all the way down through this (fairly long) settings form.
        status_label = ctk.CTkLabel(
            dialog, text="", font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED,
            wraplength=400, justify="left",
        )
        status_label.pack(anchor="w", padx=12, pady=(6, 0))

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
            sent, errors = notifications.send(
                "SmartGrocer: test notification", "This is a test notification from SmartGrocer.", debug=True,
            )
            if sent and not errors:
                status_label.configure(text=f"Saved and sent via: {', '.join(sent)}", text_color=theme.TEXT_MUTED)
            elif sent:
                status_label.configure(
                    text=f"Saved. Sent via: {', '.join(sent)}. Also failed: "
                         + "; ".join(f"{ch} - {why}" for ch, why in errors),
                    text_color=theme.TEXT_MUTED,
                )
            else:
                # Real reasons, not a bare "didn't work" - e.g. Gmail
                # rejecting the app password, so it's fixable without
                # guessing. Every other place in the app that sends a
                # notification deliberately hides this detail (a
                # notification failing must never block the action that
                # triggered it) - here, on this settings screen, showing it
                # is the whole point.
                reason = "; ".join(f"{ch}: {why}" for ch, why in errors) if errors else \
                    "Nothing is filled in above yet."
                status_label.configure(text=f"Saved, but nothing sent - {reason}", text_color=theme.TEXT_MUTED)

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(4, 12))
        styled_button(btn_row, "Save", save, kind="primary").pack(side="left")
        styled_button(btn_row, "Save & Send Test", send_test, kind="secondary").pack(side="left", padx=8)
