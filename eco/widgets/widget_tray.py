"""Manage a set of opened eco widgets in a Jupyter / Voila dashboard.

The problem this solves: in a long-running Voila session every opened widget is
*live* -- it holds a comm to the kernel and (for eco assembly widgets) a
background refresh loop polling EPICS. Opening many sub-namespace widgets then
both clutters the page and leaks comms / grows EPICS traffic for the whole
shift. So closing must genuinely tear a widget down, not merely hide it.

Public API
----------
teardown_widget(w)
    Stop a widget's refresh loop and close its whole subtree (frees the comm).

WidgetTray(mode="panels", cap=6)
    A container that opens objects' ``.widget()`` views and lets the user close
    them, in one of two layouts:

    "panels" (default, mobile friendly)
        A vertical stack of collapsible panels, each with a close (X) button,
        plus a "Close all". Every open widget is visible/stacked.

    "detail" (master-detail)
        One live widget at a time in a detail pane; a "pin" action promotes it
        into a small, capped tray of closeable panels. The live-widget count
        can never run away.

make_namespace_dashboard(namespace, mode="panels", cap=6)
    Full dashboard: a member selector to open sub-components, a layout switch
    (panels / detail), and the tray. This is what the ``eco --ui voila``
    launcher renders.

Pure ipywidgets -- no dependencies beyond what eco's widgets already require.
"""

from collections import OrderedDict
from typing import Any, List, Optional, Tuple

import ipywidgets as widgets


# --------------------------------------------------------------------------- #
# teardown
# --------------------------------------------------------------------------- #
def _close_tree(w: Any) -> None:
    """Recursively close a widget and its children (releases their comms)."""
    for child in list(getattr(w, "children", ()) or ()):
        _close_tree(child)
    close = getattr(w, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def teardown_widget(widget: Any) -> None:
    """Stop refresh loops and fully close ``widget`` and its subtree.

    eco's assembly widgets expose ``stop()`` (and a ``_stop_event``); the
    assembly browser exposes ``stop()``. We call whatever is present before
    closing so no orphaned timer keeps polling EPICS after the view is gone.
    """
    if widget is None:
        return
    stop = getattr(widget, "stop", None)
    if callable(stop):
        try:
            stop()
        except Exception:
            pass
    ev = getattr(widget, "_stop_event", None)
    if ev is not None:
        try:
            ev.set()
        except Exception:
            pass
    _close_tree(widget)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _label_of(item: Any) -> str:
    try:
        alias = getattr(item, "alias", None)
        if alias is not None and hasattr(alias, "get_full_name"):
            return alias.get_full_name()
    except Exception:
        pass
    return str(getattr(item, "name", None) or repr(item))


def _build_view(obj: Any) -> widgets.Widget:
    """Build the widget view for ``obj`` via its ``.widget()`` method."""
    factory = getattr(obj, "widget", None)
    if callable(factory):
        try:
            return factory()
        except Exception as e:  # keep the dashboard alive on a single failure
            return widgets.HTML(value=f"<i>widget() failed for {_label_of(obj)}: {e}</i>")
    return widgets.HTML(value=f"<pre>{obj!r}</pre>")


_X = "✕"       # close
_DOWN = "▾"    # expanded marker
_RIGHT = "▸"   # collapsed marker
_PIN = "\U0001f4cc" # pushpin


def _panel(label: str, view: widgets.Widget, on_close,
           on_pin=None) -> widgets.VBox:
    """A collapsible panel wrapping ``view`` with a header (collapse, title,
    optional pin, close). ``on_close``/``on_pin`` are called with no args.
    Collapsing only hides the body (display:none); it does NOT tear it down.
    """
    toggle = widgets.Button(description=_DOWN, tooltip="Collapse / expand",
                            layout=widgets.Layout(width="34px"))
    title = widgets.HTML(value=f"<b>{label}</b>",
                         layout=widgets.Layout(flex="1 1 auto"))
    buttons = [toggle, title]
    if on_pin is not None:
        pin_btn = widgets.Button(description=_PIN, tooltip="Pin to keep while opening others",
                                 layout=widgets.Layout(width="40px"))
        pin_btn.on_click(lambda _b: on_pin())
        buttons.append(pin_btn)
    close_btn = widgets.Button(description=_X, button_style="danger",
                               tooltip="Close and free resources (stops refresh)",
                               layout=widgets.Layout(width="40px"))
    close_btn.on_click(lambda _b: on_close())
    buttons.append(close_btn)

    header = widgets.HBox(buttons)
    panel = widgets.VBox([header, view])

    def _toggle(_b):
        collapsed = view.layout.display == "none"
        view.layout.display = "" if collapsed else "none"
        toggle.description = _DOWN if collapsed else _RIGHT

    toggle.on_click(_toggle)
    return panel


# --------------------------------------------------------------------------- #
# tray
# --------------------------------------------------------------------------- #
class WidgetTray:
    """Container managing opened widgets. Display it directly (it implements
    ``_ipython_display_``) or read its ``.box`` VBox."""

    def __init__(self, mode: str = "panels", cap: int = 6):
        self.mode = mode if mode in ("panels", "detail") else "panels"
        self.cap = max(1, int(cap))

        # panels mode: key -> {"view", "panel"}
        self._panels: "OrderedDict[str, dict]" = OrderedDict()
        # detail mode: active record + pinned key -> {"view", "panel"}
        self._active: Optional[dict] = None
        self._pinned: "OrderedDict[str, dict]" = OrderedDict()

        # panels-mode toolbar
        self._count = widgets.HTML()
        close_all = widgets.Button(description="Close all",
                                   layout=widgets.Layout(width="auto"))
        close_all.on_click(lambda _b: self.close_all())
        self._panels_toolbar = widgets.HBox([close_all, self._count])
        self._panels_box = widgets.VBox()

        # detail-mode areas
        self._detail_box = widgets.VBox()
        self._pinned_hint = widgets.HTML()
        self._pinned_box = widgets.VBox()

        self.box = widgets.VBox()
        self._render()

    # -- display -----------------------------------------------------------
    def _ipython_display_(self):
        from IPython.display import display
        display(self.box)

    # -- public ------------------------------------------------------------
    def open(self, obj: Any, label: Optional[str] = None) -> None:
        label = label or _label_of(obj)
        if self.mode == "panels":
            self._open_panel(label, obj)
        else:
            self._open_detail(label, obj)

    def close_all(self) -> None:
        for rec in list(self._panels.values()):
            teardown_widget(rec["view"])
        self._panels.clear()
        if self._active is not None:
            teardown_widget(self._active["view"])
            self._active = None
        for rec in list(self._pinned.values()):
            teardown_widget(rec["view"])
        self._pinned.clear()
        self._render()

    def set_mode(self, mode: str) -> None:
        """Switch layout. Open widgets are torn down on switch (kept simple and
        leak-free rather than migrated between incompatible layouts)."""
        if mode == self.mode:
            return
        self.close_all()
        self.mode = mode if mode in ("panels", "detail") else "panels"
        self._render()

    # -- panels mode -------------------------------------------------------
    def _open_panel(self, label: str, obj: Any) -> None:
        if label in self._panels:  # already open -> just expand it
            view = self._panels[label]["view"]
            view.layout.display = ""
            return
        view = _build_view(obj)

        def _close(key=label):
            rec = self._panels.pop(key, None)
            if rec is not None:
                teardown_widget(rec["view"])
            self._render()

        panel = _panel(label, view, on_close=_close)
        self._panels[label] = {"view": view, "panel": panel}
        self._render()

    # -- detail mode -------------------------------------------------------
    def _open_detail(self, label: str, obj: Any) -> None:
        # replace the current (unpinned) active view, tearing it down
        if self._active is not None:
            teardown_widget(self._active["view"])
            self._active = None
        view = _build_view(obj)

        def _close():
            if self._active is not None:
                teardown_widget(self._active["view"])
                self._active = None
            self._render()

        def _pin(key=label):
            if self._active is None:
                return
            rec = self._active
            self._active = None
            # move into the pinned tray with a panel wrapper (capped)
            def _close_pin(k=key):
                r = self._pinned.pop(k, None)
                if r is not None:
                    teardown_widget(r["view"])
                self._render()
            rec["panel"] = _panel(key, rec["view"], on_close=_close_pin)
            self._pinned[key] = rec
            self._enforce_cap()
            self._render()

        panel = _panel(label, view, on_close=_close, on_pin=_pin)
        self._active = {"view": view, "panel": panel, "label": label}
        self._render()

    def _enforce_cap(self) -> None:
        while len(self._pinned) > self.cap:
            old_key, old_rec = self._pinned.popitem(last=False)  # LRU = oldest
            teardown_widget(old_rec["view"])

    # -- rendering ---------------------------------------------------------
    def _render(self) -> None:
        if self.mode == "panels":
            n = len(self._panels)
            self._count.value = f"&nbsp;<i>{n} open</i>"
            self._panels_box.children = tuple(r["panel"] for r in self._panels.values())
            self.box.children = (self._panels_toolbar, self._panels_box)
        else:
            active = (self._active["panel"],) if self._active else (
                widgets.HTML("<i>Select a component to open it here.</i>"),
            )
            self._detail_box.children = active
            self._pinned_hint.value = f"<b>Pinned</b> ({len(self._pinned)}/{self.cap})"
            self._pinned_box.children = tuple(r["panel"] for r in self._pinned.values())
            self.box.children = (self._detail_box, self._pinned_hint, self._pinned_box)


# --------------------------------------------------------------------------- #
# namespace launcher (Name / Required table -- ipywidgets analog of
# eco.widgets.desktop_app._NamespaceLauncher)
# --------------------------------------------------------------------------- #
def _sorted_namespace_entries(namespace: Any) -> List[Tuple[str, str]]:
    """(name, state) pairs for every name registered on `namespace`, state
    one of "initialized" | "lazy" | "failed", sorted alphabetically.
    Duplicated from eco.widgets.desktop_app's identical pure function
    (kept import-eco-widgets-free of Qt here) -- see that module's
    docstring for why Namespace tracks these as name -> state bookkeeping
    separately from where the actual objects live."""
    entries = []
    for name in getattr(namespace, "initialized_names", set()):
        entries.append((name, "initialized"))
    for name in getattr(namespace, "lazy_names", set()):
        entries.append((name, "lazy"))
    for name in getattr(namespace, "failed_names", set()):
        entries.append((name, "failed"))
    entries.sort(key=lambda t: t[0].lower())
    return entries


def _resolve_namespace_item(namespace: Any, name: str) -> Any:
    """The actual object registered as `name` on `namespace` -- see
    eco.utilities.config.Namespace.resolve_item's docstring for why a bare
    `getattr(namespace, name)` doesn't work (append_obj writes it onto the
    scope's root module, not the Namespace instance). Delegates to
    Namespace.resolve_item; falls back to the same dict chain directly for
    anything namespace-shaped that has the dicts but not the method."""
    resolve = getattr(namespace, "resolve_item", None)
    if callable(resolve):
        return resolve(name)
    return (
        getattr(namespace, "lazy_items", {}).get(name)
        or getattr(namespace, "failed_items", {}).get(name)
        or getattr(namespace, "initialized_items", {}).get(name)
    )


def _required_names(namespace: Any) -> set:
    try:
        return set(namespace.required_names())
    except Exception:
        return set()


def _set_required(namespace: Any, name: str, checked: bool) -> None:
    try:
        current = set(namespace.required_names())
        if checked:
            current.add(name)
        else:
            current.discard(name)
        namespace.required_names(sorted(current))
    except Exception:
        pass


class NamespaceLauncherWidget(widgets.VBox):
    """ipywidgets analog of eco.widgets.desktop_app._NamespaceLauncher: one
    row per registered namespace name, filterable, two columns (Name,
    Required). Initialized entries open immediately on click; lazy
    entries initialize (blocking -- see the note below) then open on
    click; failed entries are shown but not retried automatically, same
    as the desktop launcher. Required is a checkbox mirroring/editing
    Namespace.required_names(), same semantics as the desktop launcher's
    Required column.

    Unlike the desktop launcher, initializing a lazy entry here blocks
    this kernel for the duration (no background thread) -- Jupyter/Voila
    already shows a busy kernel indicator during that, and adding real
    threading would mean marshalling ipywidgets updates back onto the
    kernel's own execution thread for comparatively little benefit here.
    Also unlike the desktop launcher, there is no live-refresh timer --
    click Refresh after something else changes state (e.g. an
    initialization triggered from a separate console on the same
    namespace).
    """

    def __init__(self, namespace: Any, on_open, on_status=None):
        """`on_open(obj, label)` is called with the resolved object to
        open and its display name -- e.g. ``lambda obj, label:
        tray.open(obj, label=label)`` for a WidgetTray (make_namespace_
        dashboard, Voila), or ``eco.widgets.jupyter_sidecar.open_in_
        sidecar`` for a real JupyterLab Sidecar panel."""
        self.namespace = namespace
        self.on_open = on_open
        self._on_status = on_status or (lambda msg: None)

        self._filter = widgets.Text(placeholder="Filter...",
                                    layout=widgets.Layout(flex="1 1 auto"))
        self._filter.observe(
            lambda ch: self._render() if ch["name"] == "value" else None, "value"
        )
        refresh_btn = widgets.Button(description="Refresh",
                                     layout=widgets.Layout(width="auto"))
        refresh_btn.on_click(lambda _b: self._render())
        toolbar = widgets.HBox([self._filter, refresh_btn])

        self._grid = widgets.GridBox(
            layout=widgets.Layout(
                grid_template_columns="1fr 90px",
                grid_gap="2px 8px",
                align_items="center",
            )
        )

        super().__init__([toolbar, self._grid])
        self._render()

    def _render(self) -> None:
        query = self._filter.value.strip().lower()
        required = _required_names(self.namespace)
        children: List[widgets.Widget] = [
            widgets.HTML("<b>Name</b>"),
            widgets.HTML("<b>Required</b>"),
        ]
        for name, state in _sorted_namespace_entries(self.namespace):
            if query and query not in name.lower():
                continue
            children.append(self._name_button(name, state))
            children.append(self._required_checkbox(name, required))
        self._grid.children = tuple(children)

    def _name_button(self, name: str, state: str) -> widgets.Button:
        style = {"lazy": "info", "failed": "danger"}.get(state, "")
        btn = widgets.Button(
            description=f"{self._icon(name, state)} {name}",
            button_style=style,
            tooltip="failed -- not retried automatically" if state == "failed" else "",
            layout=widgets.Layout(width="auto"),
        )
        btn.on_click(lambda _b, name=name, state=state: self._on_click(name, state))
        return btn

    def _icon(self, name: str, state: str) -> str:
        from eco.widgets.component_selector import KIND_ICONS, classify

        if state in ("lazy", "failed"):
            return KIND_ICONS[state]
        try:
            obj = _resolve_namespace_item(self.namespace, name)
        except Exception:
            return KIND_ICONS["other"]
        return KIND_ICONS.get(classify(obj), KIND_ICONS["other"])

    def _required_checkbox(self, name: str, required: set) -> widgets.Checkbox:
        cb = widgets.Checkbox(
            value=name in required, indent=False,
            layout=widgets.Layout(width="auto"),
        )
        cb.observe(
            lambda ch, name=name: (
                _set_required(self.namespace, name, ch["new"])
                if ch["name"] == "value" else None
            ),
            "value",
        )
        return cb

    def _on_click(self, name: str, state: str) -> None:
        if state == "failed":
            self._on_status(f"{name}: previously failed, not retried automatically.")
            return
        if state == "lazy":
            self._on_status(f"Initializing {name}...")
            try:
                self.namespace.init_name(name, raise_errors=False)
            except Exception:
                pass
            self._render()
            new_state = dict(_sorted_namespace_entries(self.namespace)).get(name)
            if new_state != "initialized":
                self._on_status(f"{name} failed to initialize.")
                return
        obj = _resolve_namespace_item(self.namespace, name)
        if obj is None:
            self._on_status(f"{name}: nothing to open.")
            return
        self.on_open(obj, name)
        self._on_status("")


# --------------------------------------------------------------------------- #
# dashboard
# --------------------------------------------------------------------------- #
def make_namespace_dashboard(namespace: Any, mode: str = "panels",
                             cap: int = 6) -> widgets.VBox:
    """Build the full Voila dashboard for a namespace -- NOT for JupyterLab,
    which has a real Lumino shell to dock into instead (see
    eco.widgets.jupyter_sidecar.open_namespace_dashboard).

    Header: a NamespaceLauncherWidget (Name/Required table -- browse every
    registered name including still-lazy or failed ones, same as the
    desktop UI's launcher panel) and a layout toggle (panels / detail).
    Body: the WidgetTray, everything rendered inline on one flat page --
    Voila has no docking shell to put a launcher/opened-widgets split into.
    """
    tray = WidgetTray(mode=mode, cap=cap)
    status = widgets.HTML()
    launcher = NamespaceLauncherWidget(
        namespace,
        on_open=lambda obj, label: tray.open(obj, label=label),
        on_status=lambda msg: setattr(status, "value", msg),
    )

    layout_toggle = widgets.ToggleButtons(
        options=[("Panels", "panels"), ("Master-detail", "detail")],
        value=tray.mode,
        tooltips=[
            "Stack every open widget (mobile friendly)",
            "One live widget at a time, with a capped pin tray",
        ],
    )
    layout_toggle.observe(
        lambda ch: tray.set_mode(ch["new"]) if ch["name"] == "value" else None, "value"
    )

    header = widgets.VBox([launcher, status, layout_toggle])
    return widgets.VBox([header, tray.box])
