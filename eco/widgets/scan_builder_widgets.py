"""Structured, picker-driven building blocks for the per-method Scans forms
in eco.widgets.scan_widget.

StepSpecWidget covers the two most common forms interpret_step_specification
accepts (linear start/end/N, or an explicit position list). ScanAxisRow and
ScanBuilderWidget mirror Scans.scan()'s own *adj_specs convention: one row
per grid dimension, each either a single (adjustable, *step_spec) mesh axis
or -- when allowed -- a growable list of such specs that move together
(a2scan-like), exactly the nested-list-for-simultaneous / flat-tuple-for-mesh
split scan() itself parses.
"""
import json
from typing import Any, List, Optional

import ipywidgets as widgets

from eco.widgets.component_picker_widget import ComponentPickerWidget
from eco.widgets.component_selector import ComponentBookmarks, RecentComponents


def _parse_position_list(s: str) -> list:
    s = s.strip()
    if not s:
        return []
    try:
        val = json.loads(s)
        if isinstance(val, list):
            return val
    except Exception:
        pass
    return [float(p.strip()) for p in s.split(",") if p.strip()]


class StepSpecWidget(widgets.VBox):
    """One axis's step positions: linear start/end/N_intervals (the common
    case) or a raw position list, toggled by a dropdown. `get_spec()`
    returns the tuple to splice after the adjustable in a Scans.scan()/
    meshscan() axis spec, e.g. `(adj,) + spec.get_spec()`."""

    def __init__(self, start: float = 0.0, end: float = 1.0, n: int = 10):
        self.mode_dd = widgets.Dropdown(
            options=["start/end/N", "position list"],
            value="start/end/N",
            layout=widgets.Layout(width="130px"),
        )
        self.start_w = widgets.FloatText(
            value=start, description="start", layout=widgets.Layout(width="150px")
        )
        self.end_w = widgets.FloatText(
            value=end, description="end", layout=widgets.Layout(width="150px")
        )
        self.n_w = widgets.IntText(
            value=n, description="N", layout=widgets.Layout(width="110px")
        )
        self.list_w = widgets.Text(
            placeholder="e.g. 0,1,2,5 or [0,1,2,5]",
            layout=widgets.Layout(width="260px"),
        )
        self.linear_box = widgets.HBox([self.start_w, self.end_w, self.n_w])
        self.list_box = widgets.HBox([self.list_w])
        self.list_box.layout.display = "none"

        self.mode_dd.observe(self._on_mode, "value")
        super().__init__([self.mode_dd, self.linear_box, self.list_box])

    def _on_mode(self, change) -> None:
        linear = change["new"] == "start/end/N"
        self.linear_box.layout.display = "" if linear else "none"
        self.list_box.layout.display = "none" if linear else ""

    def get_spec(self) -> tuple:
        if self.mode_dd.value == "start/end/N":
            return (self.start_w.value, self.end_w.value, int(self.n_w.value))
        return (_parse_position_list(self.list_w.value),)


class ScanAxisRow(widgets.VBox):
    """One dimension of a Scans.scan()/meshscan() call.

    `allow_simultaneous=True` (scan()) exposes a mode toggle: "single axis"
    keeps exactly one adjustable+spec pair; "simultaneous (co-moving)" grows
    to N pairs sharing that one grid dimension, matching scan()'s nested
    `[(adjA, ...), (adjB, ...)]` convention. `allow_simultaneous=False`
    (meshscan()) never shows the toggle -- always a single mesh axis.
    """

    def __init__(
        self,
        root: Any,
        allow_simultaneous: bool = True,
        bookmarks: Optional[ComponentBookmarks] = None,
        recent: Optional[RecentComponents] = None,
    ):
        self.root = root
        self.allow_simultaneous = allow_simultaneous
        self._bookmarks = bookmarks
        self._recent = recent
        self._sub_rows: List[Any] = []  # (picker, spec_widget, hbox)

        if allow_simultaneous:
            self.mode_dd = widgets.Dropdown(
                options=["single axis", "simultaneous (co-moving)"],
                value="single axis",
                layout=widgets.Layout(width="230px"),
            )
            self.mode_dd.observe(lambda ch: self._on_mode(), "value")
        else:
            self.mode_dd = None

        self.sub_rows_box = widgets.VBox([])
        self.add_adj_btn = widgets.Button(
            description="+ adjustable", layout=widgets.Layout(width="110px")
        )
        self.add_adj_btn.on_click(lambda _: self._add_sub_row())
        self.add_adj_btn.layout.display = "none"

        header_children = [self.mode_dd] if self.mode_dd is not None else []
        super().__init__(header_children + [self.sub_rows_box, self.add_adj_btn])

        self._add_sub_row()

    def _add_sub_row(self) -> None:
        picker = ComponentPickerWidget(
            self.root,
            kind_filter="Adjustable",
            bookmarks=self._bookmarks,
            recent=self._recent,
        )
        spec = StepSpecWidget()
        remove_btn = widgets.Button(
            description="x", layout=widgets.Layout(width="30px")
        )
        row = widgets.HBox([picker, spec, remove_btn])

        def _remove(_btn=None):
            if len(self._sub_rows) <= 1:
                return
            self._sub_rows[:] = [r for r in self._sub_rows if r[2] is not row]
            self.sub_rows_box.children = [r[2] for r in self._sub_rows]

        remove_btn.on_click(_remove)
        self._sub_rows.append((picker, spec, row))
        self.sub_rows_box.children = [r[2] for r in self._sub_rows]

    def _on_mode(self) -> None:
        simultaneous = self.mode_dd.value.startswith("simultaneous")
        self.add_adj_btn.layout.display = "" if simultaneous else "none"
        if not simultaneous:
            while len(self._sub_rows) > 1:
                self._sub_rows.pop()
            self.sub_rows_box.children = [r[2] for r in self._sub_rows]

    def get_adj_spec(self):
        """One *adj_specs entry: `(adj, *spec)` for a single axis, or
        `[(adj, *spec), ...]` for a simultaneous one."""
        simultaneous = (
            self.mode_dd is not None and self.mode_dd.value.startswith("simultaneous")
        )
        if simultaneous and len(self._sub_rows) > 1:
            return [
                (picker.value,) + spec.get_spec()
                for picker, spec, _ in self._sub_rows
                if picker.value is not None
            ]
        picker, spec, _ = self._sub_rows[0]
        return (picker.value,) + spec.get_spec()


class ScanBuilderWidget(widgets.VBox):
    """A growable list of ScanAxisRow's -- the *adj_specs for
    Scans.scan()/meshscan(). `get_adj_specs()` returns the list to splat
    into the call."""

    def __init__(
        self,
        root: Any,
        allow_simultaneous: bool = True,
        bookmarks: Optional[ComponentBookmarks] = None,
        recent: Optional[RecentComponents] = None,
    ):
        self.root = root
        self.allow_simultaneous = allow_simultaneous
        self._bookmarks = bookmarks
        self._recent = recent
        self._axis_rows: List[Any] = []  # (ScanAxisRow, wrapper_vbox)

        self.axes_box = widgets.VBox([])
        self.add_axis_btn = widgets.Button(
            description="+ add axis",
            button_style="info",
            layout=widgets.Layout(width="120px"),
        )
        self.add_axis_btn.on_click(lambda _: self.add_axis())

        super().__init__(
            [widgets.HTML("<b>Scan axes</b>"), self.axes_box, self.add_axis_btn]
        )
        self.add_axis()

    def add_axis(self) -> ScanAxisRow:
        row = ScanAxisRow(
            self.root,
            allow_simultaneous=self.allow_simultaneous,
            bookmarks=self._bookmarks,
            recent=self._recent,
        )
        remove_btn = widgets.Button(
            description="remove axis", layout=widgets.Layout(width="100px")
        )
        wrapper = widgets.VBox([widgets.HBox([widgets.HTML("<hr>"), remove_btn]), row])

        def _remove(_btn=None):
            if len(self._axis_rows) <= 1:
                return
            self._axis_rows[:] = [r for r in self._axis_rows if r[1] is not wrapper]
            self.axes_box.children = [r[1] for r in self._axis_rows]

        remove_btn.on_click(_remove)
        self._axis_rows.append((row, wrapper))
        self.axes_box.children = [r[1] for r in self._axis_rows]
        return row

    def get_adj_specs(self) -> list:
        return [row.get_adj_spec() for row, _ in self._axis_rows]
