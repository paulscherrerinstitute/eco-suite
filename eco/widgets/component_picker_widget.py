"""Compact, embeddable "pick one component" building blocks on top of
eco.widgets.component_selector_widget (the ipywidgets component picker).

Where component_selector_widget.ComponentSelectorWidget is a whole
browse-and-pick panel meant to be the main content of a cell,
ComponentPickerWidget is a single form-field: a label + "Pick.../Clear"
button that expands the full picker inline on demand and collapses back to
just the label once something is chosen. Meant to be dropped into a form
row next to the parameter widgets it controls -- see eco.widgets.scan_widget
for the scan-building forms that use it.
"""
from typing import Any, Callable, List, Optional

import ipywidgets as widgets

from eco.widgets.component_selector import ComponentBookmarks, RecentComponents
from eco.widgets.component_selector_widget import make_component_selector_widget


class ComponentPickerWidget(widgets.VBox):
    """One picker slot. `.value`/`.path` hold the picked live object and its
    dotted name (both `None` until something is picked)."""

    def __init__(
        self,
        root: Any,
        kind_filter: str = "Adjustable",
        placeholder: str = "(none selected)",
        bookmarks: Optional[ComponentBookmarks] = None,
        recent: Optional[RecentComponents] = None,
        on_change: Optional[Callable[["ComponentPickerWidget"], None]] = None,
    ):
        self.root = root
        self.kind_filter = kind_filter
        self.value: Any = None
        self.path: Optional[str] = None
        self._bookmarks = bookmarks
        self._recent = recent
        self._selector = None
        self._on_change_cbs: List[Callable[["ComponentPickerWidget"], None]] = (
            [on_change] if callable(on_change) else []
        )

        self.label = widgets.HTML(f"<i>{placeholder}</i>")
        self.pick_btn = widgets.Button(
            description="Pick...", layout=widgets.Layout(width="80px")
        )
        self.clear_btn = widgets.Button(
            description="Clear", layout=widgets.Layout(width="60px")
        )
        header = widgets.HBox([self.label, self.pick_btn, self.clear_btn])
        self._picker_holder = widgets.VBox([])

        # Escape hatch for anything not (yet) in the namespace tree: type a
        # raw EPICS PV name directly instead of browsing. Builds a bare
        # AdjustablePv on the fly -- not a namespace component, so it's
        # recorded in "recent" under a "pv:" path rather than a dotted one,
        # since there's nothing to resolve_path() it back through later.
        self.pv_box = widgets.Text(
            placeholder="...or type a raw PV name",
            layout=widgets.Layout(width="220px"),
        )
        self.pv_btn = widgets.Button(
            description="Use PV", layout=widgets.Layout(width="70px")
        )
        pv_row = widgets.HBox([self.pv_box, self.pv_btn])

        self.pick_btn.on_click(self._toggle_picker)
        self.clear_btn.on_click(self._clear)
        self.pv_btn.on_click(self._use_pv)

        super().__init__([header, pv_row, self._picker_holder])

    def _use_pv(self, _btn=None) -> None:
        pvname = self.pv_box.value.strip()
        if not pvname:
            return
        try:
            from eco.epics_utils.adjustable import AdjustablePv

            adj = AdjustablePv(pvname, name=pvname)
        except Exception as e:
            self.label.value = f"<span style='color:red'>Could not connect to PV {pvname!r}: {e}</span>"
            return
        path = f"pv:{pvname}"
        self.value = adj
        self.path = path
        self.label.value = f"<b>{pvname}</b> <i>(raw PV)</i>"
        self.pv_box.value = ""
        if self._recent is None:
            self._recent = RecentComponents()
        self._recent.touch(path)
        self._notify()

    def _toggle_picker(self, _btn=None) -> None:
        if self._picker_holder.children:
            self._picker_holder.children = []
            return
        if self._selector is None:
            self._selector = make_component_selector_widget(
                self.root,
                kind_filter=self.kind_filter,
                bookmarks=self._bookmarks,
                recent=self._recent,
                on_select=self._on_pick,
            )
        else:
            # picked up any bookmarks/recent-items saved via a sibling picker
            # since this selector was first built
            self._selector._refresh_bookmark_options()
            self._selector._refresh_recent_options()
        self._picker_holder.children = [self._selector]

    def _on_pick(self, name: str, obj: Any) -> None:
        self.value = obj
        self.path = name
        self.label.value = f"<b>{name}</b>"
        self._picker_holder.children = []
        self._notify()

    def _clear(self, _btn=None) -> None:
        self.value = None
        self.path = None
        self.label.value = "<i>(none selected)</i>"
        self._notify()

    def _notify(self) -> None:
        for cb in list(self._on_change_cbs):
            try:
                cb(self)
            except Exception as e:
                print(f"component picker on_change callback failed: {e}")

    def on_change(self, cb: Callable[["ComponentPickerWidget"], None]) -> None:
        if callable(cb):
            self._on_change_cbs.append(cb)


class MultiComponentPickerWidget(widgets.VBox):
    """A growable list of ComponentPickerWidget rows, e.g. for optionally
    overriding a scan's counters -- empty by default, meaning "use the
    caller's defaults"."""

    def __init__(
        self,
        root: Any,
        kind_filter: str = "Detector",
        empty_hint: str = "(empty = use defaults)",
        bookmarks: Optional[ComponentBookmarks] = None,
        recent: Optional[RecentComponents] = None,
    ):
        self.root = root
        self.kind_filter = kind_filter
        self._bookmarks = bookmarks
        self._recent = recent
        self._rows: List[Any] = []  # list of (picker, row_hbox)

        self.rows_box = widgets.VBox([])
        self.add_btn = widgets.Button(
            description="+ add", layout=widgets.Layout(width="70px")
        )
        self.add_btn.on_click(lambda _: self.add_row())

        super().__init__(
            [widgets.HTML(f"<i>{empty_hint}</i>"), self.rows_box, self.add_btn]
        )

    def add_row(self) -> ComponentPickerWidget:
        picker = ComponentPickerWidget(
            self.root,
            kind_filter=self.kind_filter,
            bookmarks=self._bookmarks,
            recent=self._recent,
        )
        remove_btn = widgets.Button(
            description="x", layout=widgets.Layout(width="30px")
        )
        row = widgets.HBox([picker, remove_btn])

        def _remove(_btn=None):
            self._rows = [r for r in self._rows if r[1] is not row]
            self.rows_box.children = [r[1] for r in self._rows]

        remove_btn.on_click(_remove)
        self._rows.append((picker, row))
        self.rows_box.children = [r[1] for r in self._rows]
        return picker

    def get_value(self) -> list:
        return [p.value for p, _ in self._rows if p.value is not None]
