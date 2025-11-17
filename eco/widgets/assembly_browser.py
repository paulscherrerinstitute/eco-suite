import threading
import time
import inspect
from typing import Any, Set

import ipywidgets as widgets
from IPython.display import display

# try to import eco types used for identification
try:
    import eco
    from eco import Adjustable, Detector
    from eco.elements.assembly import Assembly
except Exception:
    # Fallback names if eco is not importable in test environment
    Adjustable = None
    Detector = None
    Assembly = None


def _safe_get_value(obj: Any):
    """Try a few common getters used in eco for adjustables/detectors."""
    for fn in ("get_current_value", "get", "__call__", "value", "get_value"):
        try:
            attr = getattr(obj, fn)
            if callable(attr):
                return attr()
            else:
                return attr
        except Exception:
            pass
    # try attribute 'current_value' or 'current' etc.
    for name in ("current_value", "current", "_value", "val"):
        if hasattr(obj, name):
            try:
                return getattr(obj, name)
            except Exception:
                pass
    return None


def _safe_set_value(obj: Any, val):
    """Try a few common setters used in eco for adjustables."""
    # try common setter names
    for fn in ("set_target_value", "mv", "mvr", "set", "set_value", "put", "write"):
        try:
            fnobj = getattr(obj, fn)
            if callable(fnobj):
                return fnobj(val)
        except Exception:
            pass
    # try attribute assignment if has property 'value'
    if hasattr(obj, "value"):
        try:
            setattr(obj, "value", val)
            return True
        except Exception:
            pass
    raise RuntimeError("No known setter found on object")


class _ItemWidget:
    """Internal container for one monitored item (adjustable/detector/assembly leaf)."""

    def __init__(self, name: str, obj: Any, base):
        self.name = name
        self.obj = obj
        self.base = base
        self.type_label = widgets.Label(
            value=type(obj).__name__, layout=widgets.Layout(width="160px")
        )
        self.name_label = widgets.Label(
            value=name, layout=widgets.Layout(width="260px")
        )
        self.value_label = widgets.Label(
            value="", layout=widgets.Layout(flex="1 1 auto")
        )
        self.refresh_btn = widgets.Button(
            description="Refresh", layout=widgets.Layout(width="70px")
        )
        self.refresh_btn.on_click(lambda _: self.refresh())
        # tweak controls for adjustables
        self.tweak_text = widgets.Text(
            placeholder="new value", layout=widgets.Layout(width="180px")
        )
        self.set_btn = widgets.Button(
            description="Set", button_style="info", layout=widgets.Layout(width="60px")
        )
        self.set_btn.on_click(lambda _: self._on_set())

        # assemble
        if _is_adjustable(obj):
            controls = [
                self.name_label,
                self.type_label,
                self.value_label,
                self.tweak_text,
                self.set_btn,
            ]
        elif _is_detector(obj):
            controls = [
                self.name_label,
                self.type_label,
                self.value_label,
                self.refresh_btn,
            ]
        else:
            controls = [
                self.name_label,
                self.type_label,
                self.value_label,
                self.refresh_btn,
            ]

        self.widget = widgets.HBox(
            controls, layout=widgets.Layout(align_items="center", width="100%")
        )
        self.refresh()

    def _on_set(self):
        txt = self.tweak_text.value
        if txt == "":
            return
        cur = _safe_get_value(self.obj)
        # try to coerce type based on current value
        try:
            if isinstance(cur, bool):
                v = txt.lower() in ("1", "true", "yes", "on")
            elif isinstance(cur, int):
                v = int(txt)
            elif isinstance(cur, float):
                v = float(txt)
            else:
                # fallback: try json-like parsing, else string
                import json

                try:
                    v = json.loads(txt)
                except Exception:
                    v = txt
        except Exception:
            v = txt
        try:
            _safe_set_value(self.obj, v)
            # auto refresh after set
            time.sleep(0.01)
            self.refresh()
        except Exception as e:
            self.value_label.value = f"Set failed: {e}"

    def refresh(self):
        try:
            val = _safe_get_value(self.obj)
            self.value_label.value = repr(val)
        except Exception as e:
            self.value_label.value = f"Err: {e}"


def _is_adjustable(obj):
    if Adjustable is not None and isinstance(obj, Adjustable):
        return True
    # heuristics
    return any(hasattr(obj, n) for n in ("mv", "set_target_value", "mvr", "set"))


def _is_detector(obj):
    if Detector is not None and isinstance(obj, Detector):
        return True
    return any(hasattr(obj, n) for n in ("get_current_value", "get"))


class AssemblyBrowser:
    def __init__(self, assembly, refresh_interval: float = 1.0, expand_level: int = 10):
        """
        assembly: an Assembly instance or namespace-like object that has .status_collection
        refresh_interval: seconds between auto-refresh cycles when enabled
        expand_level: max recursion depth
        """
        self.root = assembly
        self.refresh_interval = refresh_interval
        self.expand_level = expand_level
        self._widgets = []  # list of _ItemWidget
        self._visited = set()
        self._timer = None
        self._running = False

        # controls
        self.type_filter = widgets.Dropdown(
            options=["All", "Adjustable", "Detector", "Assembly"],
            value="All",
            description="Type:",
        )
        self.refresh_toggle = widgets.ToggleButton(
            value=False, description="Live", tooltip="Toggle live refresh"
        )
        self.refresh_btn = widgets.Button(description="Refresh now")
        self.refresh_btn.on_click(lambda _: self.refresh_all())
        self.refresh_toggle.observe(self._on_toggle, "value")
        self.header = widgets.HBox(
            [self.type_filter, self.refresh_toggle, self.refresh_btn]
        )

        # container
        self.content = widgets.VBox()
        self.top = widgets.VBox([self.header, self.content])

        # build UI
        self._build_tree()

    def _on_toggle(self, change):
        if change["new"]:
            self.start()
        else:
            self.stop()

    def start(self):
        if not self._running:
            self._running = True
            self._schedule()

    def stop(self):
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _schedule(self):
        if not self._running:
            return
        self.refresh_all()
        self._timer = threading.Timer(self.refresh_interval, self._schedule)
        self._timer.daemon = True
        self._timer.start()

    def _build_tree(self):
        self._visited = set()
        nodes = self._build_children(self.root, depth=0, base=self.root)
        self.content.children = nodes

    def _build_children(self, obj, depth: int, base) -> list:
        """Return list of widgets for items in obj.status_collection (non-blocking)."""
        if depth > self.expand_level:
            return [widgets.Label(value="... max depth reached ...")]

        out_widgets = []
        # try to access status_collection.get_list()
        try:
            sc = getattr(obj, "status_collection", None)
            if sc is None:
                return [widgets.Label(value=f"No status_collection on {obj}")]
            members = sc.get_list()
        except Exception:
            # fallback: try dir(obj) members
            members = []
            for n, v in inspect.getmembers(obj):
                if n.startswith("_"):
                    continue
                members.append(v)

        # convert weakref refs etc to concrete objects and names
        for member in members:
            try:
                # member might be weakref, or actual object
                if hasattr(member, "__call__") and not isinstance(member, type):
                    pass
                # get alias name if available
                name = getattr(member, "alias", None)
                if name is not None and hasattr(name, "get_full_name"):
                    item_name = name.get_full_name(base=base)
                else:
                    # fallback use object's __name__ or repr
                    item_name = (
                        getattr(member, "name", None)
                        or getattr(member, "__name__", None)
                        or repr(member)
                    )
            except Exception:
                item_name = repr(member)

            # detect assembly-like objects (heuristic by attribute)
            is_assembly = hasattr(member, "status_collection") and hasattr(
                member, "alias"
            )
            if is_assembly:
                # optionally show as Accordion with recursive children
                if self.type_filter.value in ("All", "Assembly"):
                    acc = widgets.Accordion(
                        children=[
                            widgets.VBox(
                                self._build_children(member, depth + 1, base=member)
                            )
                        ]
                    )
                    acc.set_title(0, item_name)
                    out_widgets.append(acc)
            else:
                # leaf item
                add = False
                if self.type_filter.value == "All":
                    add = True
                elif self.type_filter.value == "Adjustable" and _is_adjustable(member):
                    add = True
                elif self.type_filter.value == "Detector" and _is_detector(member):
                    add = True
                if add:
                    itw = _ItemWidget(item_name, member, base)
                    self._widgets.append(itw)
                    out_widgets.append(itw.widget)
        if not out_widgets:
            return [widgets.Label(value="(no items)")]
        return out_widgets

    def refresh_all(self):
        for it in list(self._widgets):
            try:
                it.refresh()
            except Exception:
                pass

    def widget(self):
        return self.top


def show_assembly_browser(
    assembly, refresh_interval: float = 1.0, expand_level: int = 10
):
    """Convenience function to create, start and return the browser widget."""
    br = AssemblyBrowser(
        assembly, refresh_interval=refresh_interval, expand_level=expand_level
    )
    return br.widget()
