"""
Qt scan launcher: build and submit eco.acquisition.scan.Scans scans through
eco.acquisition.scan_queue.ScanQueue, from a Qt form with device pickers --
the desktop/dashboard counterpart of eco.widgets.scan_widget.ScanWidget
(its ipywidgets/Jupyter equivalent).

Structured, picker-driven forms exist for the same "known" Scans methods
scan_widget.py covers (acquire, ascan, dscan, ascan_position_list,
snakescan, a2scan, meshscan, scan); anything else falls back to a generic
signature-introspection form. Adjustables/counters are picked with
ComponentSelectorQt (eco.widgets.component_selector_qt) via a compact
"Pick.../Clear" wrapper (ComponentPickerQt) -- there was no Qt analog of
eco.widgets.component_picker_widget.ComponentPickerWidget's compact-field
trick yet, only the always-expanded browse panel.

Unlike ScanWidget, Run always submits through a named ScanQueue (default
"default") rather than calling the scan method directly -- every method
here is called with scan_queue=<name>, so the queue panel (pause/resume the
queue itself, pause/resume/stop the item currently running, save a paused
item's remaining work to disk and resume it later from a picked-counters
dialog) is always meaningful for whatever Run just launched, not a
sometimes-empty extra. Progress (steps done / total) and a live per-step
readback plot are read straight off the running StepScan (next_step,
_values_done, readbacks) via a polling QTimer -- there's no push/callback
channel for this today, so polling is the straightforward option (same
tradeoff eco.widgets.camserver_stream_qt's render-rate timer makes for
frame delivery). The plot shows adjustable *readback position* per step,
not detector signal: StepScan doesn't keep counter values in any general
in-memory per-step array (they're acquired via each counter's own
acquire()/start()/stop(), typically straight to file) -- readback position
is the one thing every scan always records live.

Usage:
    from eco.widgets.scan_launcher_qt import make_scan_launcher_qt_window
    gui = make_scan_launcher_qt_window(scans_instance, root=eco.bernina.bernina)
    # root is the namespace browsed by the adjustable/counter pickers.
"""
import inspect
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from qtpy import QtCore, QtGui, QtWidgets

from eco.widgets.component_selector import ComponentBookmarks, RecentComponents
from eco.widgets.component_selector_qt import ComponentSelectorQt

_app_ref = None  # keep a strong reference to any QApplication we create ourselves


# ---------------------------------------------------------------------------
# compact device pickers -- Qt analog of eco.widgets.component_picker_widget
# ---------------------------------------------------------------------------


class ComponentPickerQt(QtWidgets.QWidget):
    """Label + Pick.../Clear button that opens a ComponentSelectorQt in a
    dialog. `.value`/`.path` hold the current selection (None until
    something's picked); `changed` fires on pick or clear."""

    changed = QtCore.Signal()

    def __init__(
        self,
        root: Any,
        kind_filter: str = "Adjustable",
        placeholder: str = "(none selected)",
        bookmarks: Optional[ComponentBookmarks] = None,
        recent: Optional[RecentComponents] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.root = root
        self.kind_filter = kind_filter
        self.placeholder = placeholder
        self.bookmarks = bookmarks
        self.recent = recent
        self.value: Any = None
        self.path: Optional[str] = None

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QtWidgets.QLabel(placeholder)
        self.label.setStyleSheet("color: palette(mid);")
        pick_btn = QtWidgets.QPushButton("Pick...")
        pick_btn.clicked.connect(self._pick)
        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(self.clear)
        layout.addWidget(self.label, 1)
        layout.addWidget(pick_btn)
        layout.addWidget(clear_btn)

    def _pick(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"Pick {self.kind_filter}")
        dialog.resize(700, 560)
        dlayout = QtWidgets.QVBoxLayout(dialog)
        selector = ComponentSelectorQt(
            self.root,
            kind_filter=self.kind_filter,
            bookmarks=self.bookmarks,
            recent=self.recent,
        )
        dlayout.addWidget(selector, 1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        ok_btn = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        ok_btn.setEnabled(False)
        selector.on_select(lambda _name, _obj: ok_btn.setEnabled(True))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dlayout.addWidget(buttons)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            name, obj = selector.get_selected()
            if obj is not None:
                self.path = name
                self.value = obj
                self.label.setText(name)
                self.label.setStyleSheet("")
                self.changed.emit()

    def clear(self):
        self.value = None
        self.path = None
        self.label.setText(self.placeholder)
        self.label.setStyleSheet("color: palette(mid);")
        self.changed.emit()


class MultiComponentPickerQt(QtWidgets.QWidget):
    """Growable list of ComponentPickerQt rows -- e.g. an optional counters
    override. get_value() -> list of picked live objects (empty = none
    picked, "use the caller's defaults")."""

    def __init__(
        self,
        root: Any,
        kind_filter: str = "Detector",
        empty_hint: str = "(empty = use defaults)",
        bookmarks: Optional[ComponentBookmarks] = None,
        recent: Optional[RecentComponents] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.root = root
        self.kind_filter = kind_filter
        self.bookmarks = bookmarks
        self.recent = recent
        self._rows: List[Tuple[ComponentPickerQt, QtWidgets.QWidget]] = []

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.hint_label = QtWidgets.QLabel(empty_hint)
        self.hint_label.setStyleSheet("color: palette(mid); font-style: italic;")
        outer.addWidget(self.hint_label)
        self.rows_box = QtWidgets.QVBoxLayout()
        outer.addLayout(self.rows_box)
        add_btn = QtWidgets.QPushButton("+ add")
        add_btn.setMaximumWidth(110)
        add_btn.clicked.connect(self.add_row)
        outer.addWidget(add_btn, alignment=QtCore.Qt.AlignLeft)

    def add_row(self) -> ComponentPickerQt:
        picker = ComponentPickerQt(
            self.root,
            kind_filter=self.kind_filter,
            bookmarks=self.bookmarks,
            recent=self.recent,
        )
        remove_btn = QtWidgets.QPushButton("x")
        remove_btn.setMaximumWidth(24)
        row = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(picker, 1)
        row_layout.addWidget(remove_btn)

        def _remove():
            self._rows[:] = [r for r in self._rows if r[1] is not row]
            row.setParent(None)

        remove_btn.clicked.connect(_remove)
        self._rows.append((picker, row))
        self.rows_box.addWidget(row)
        return picker

    def get_value(self) -> list:
        return [p.value for p, _ in self._rows if p.value is not None]


# ---------------------------------------------------------------------------
# grid-axis builder -- Qt analog of eco.widgets.scan_builder_widgets
# ---------------------------------------------------------------------------


def _parse_position_list_qt(s: str) -> list:
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


class StepSpecQt(QtWidgets.QWidget):
    """One axis's step positions: linear start/end/N (the common case) or a
    raw position list, toggled by a dropdown. get_spec() returns the tuple
    to splice after the adjustable in a Scans.scan()/meshscan() axis spec."""

    def __init__(self, start: float = 0.0, end: float = 1.0, n: int = 10, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode_dd = QtWidgets.QComboBox()
        self.mode_dd.addItems(["start/end/N", "position list"])
        layout.addWidget(self.mode_dd)

        self.linear_box = QtWidgets.QWidget()
        linear_layout = QtWidgets.QHBoxLayout(self.linear_box)
        linear_layout.setContentsMargins(0, 0, 0, 0)
        self.start_w = QtWidgets.QDoubleSpinBox()
        self.start_w.setRange(-1e9, 1e9)
        self.start_w.setValue(start)
        self.end_w = QtWidgets.QDoubleSpinBox()
        self.end_w.setRange(-1e9, 1e9)
        self.end_w.setValue(end)
        self.n_w = QtWidgets.QSpinBox()
        self.n_w.setRange(1, 100000)
        self.n_w.setValue(n)
        for label, w in (("start", self.start_w), ("end", self.end_w), ("N", self.n_w)):
            linear_layout.addWidget(QtWidgets.QLabel(label))
            linear_layout.addWidget(w)
        layout.addWidget(self.linear_box)

        self.list_w = QtWidgets.QLineEdit()
        self.list_w.setPlaceholderText("e.g. 0,1,2,5 or [0,1,2,5]")
        self.list_w.setVisible(False)
        layout.addWidget(self.list_w)

        self.mode_dd.currentTextChanged.connect(self._on_mode)

    def _on_mode(self, text):
        linear = text == "start/end/N"
        self.linear_box.setVisible(linear)
        self.list_w.setVisible(not linear)

    def get_spec(self) -> tuple:
        if self.mode_dd.currentText() == "start/end/N":
            return (self.start_w.value(), self.end_w.value(), int(self.n_w.value()))
        return (_parse_position_list_qt(self.list_w.text()),)


class ScanAxisRowQt(QtWidgets.QWidget):
    """One dimension of a Scans.scan()/meshscan() call.

    allow_simultaneous=True (scan()) exposes a "single axis" / "simultaneous
    (co-moving)" toggle that grows to N adjustable+spec pairs sharing that
    one grid dimension, matching scan()'s nested `[(adjA, ...), (adjB,
    ...)]` convention. allow_simultaneous=False (meshscan()) never shows the
    toggle -- always a single mesh axis."""

    def __init__(
        self,
        root: Any,
        allow_simultaneous: bool = True,
        bookmarks: Optional[ComponentBookmarks] = None,
        recent: Optional[RecentComponents] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.root = root
        self.allow_simultaneous = allow_simultaneous
        self.bookmarks = bookmarks
        self.recent = recent
        self._sub_rows: List[Tuple[ComponentPickerQt, StepSpecQt, QtWidgets.QWidget]] = []

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.mode_dd = None
        if allow_simultaneous:
            self.mode_dd = QtWidgets.QComboBox()
            self.mode_dd.addItems(["single axis", "simultaneous (co-moving)"])
            self.mode_dd.currentTextChanged.connect(lambda _t: self._on_mode())
            layout.addWidget(self.mode_dd)

        self.sub_rows_box = QtWidgets.QVBoxLayout()
        layout.addLayout(self.sub_rows_box)

        self.add_adj_btn = QtWidgets.QPushButton("+ adjustable")
        self.add_adj_btn.clicked.connect(self._add_sub_row)
        self.add_adj_btn.setVisible(False)
        layout.addWidget(self.add_adj_btn, alignment=QtCore.Qt.AlignLeft)

        self._add_sub_row()

    def _add_sub_row(self):
        picker = ComponentPickerQt(
            self.root, kind_filter="Adjustable", bookmarks=self.bookmarks, recent=self.recent
        )
        spec = StepSpecQt()
        remove_btn = QtWidgets.QPushButton("x")
        remove_btn.setMaximumWidth(24)
        row = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(picker)
        row_layout.addWidget(spec)
        row_layout.addWidget(remove_btn)

        def _remove():
            if len(self._sub_rows) <= 1:
                return
            self._sub_rows[:] = [r for r in self._sub_rows if r[2] is not row]
            row.setParent(None)

        remove_btn.clicked.connect(_remove)
        self._sub_rows.append((picker, spec, row))
        self.sub_rows_box.addWidget(row)

    def _on_mode(self):
        simultaneous = self.mode_dd.currentText().startswith("simultaneous")
        self.add_adj_btn.setVisible(simultaneous)
        if not simultaneous:
            while len(self._sub_rows) > 1:
                _picker, _spec, row = self._sub_rows.pop()
                row.setParent(None)

    def get_adj_spec(self):
        """One *adj_specs entry: `(adj, *spec)` for a single axis, or
        `[(adj, *spec), ...]` for a simultaneous one."""
        simultaneous = self.mode_dd is not None and self.mode_dd.currentText().startswith(
            "simultaneous"
        )
        if simultaneous and len(self._sub_rows) > 1:
            return [
                (picker.value,) + spec.get_spec()
                for picker, spec, _ in self._sub_rows
                if picker.value is not None
            ]
        picker, spec, _ = self._sub_rows[0]
        return (picker.value,) + spec.get_spec()


class ScanBuilderQt(QtWidgets.QWidget):
    """Growable list of ScanAxisRowQt's -- the *adj_specs for
    Scans.scan()/meshscan(). get_adj_specs() returns the list to splat into
    the call."""

    def __init__(
        self,
        root: Any,
        allow_simultaneous: bool = True,
        bookmarks: Optional[ComponentBookmarks] = None,
        recent: Optional[RecentComponents] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.root = root
        self.allow_simultaneous = allow_simultaneous
        self.bookmarks = bookmarks
        self.recent = recent
        self._axis_rows: List[Tuple[ScanAxisRowQt, QtWidgets.QWidget]] = []

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QtWidgets.QLabel("<b>Scan axes</b>"))
        self.axes_box = QtWidgets.QVBoxLayout()
        layout.addLayout(self.axes_box)
        add_axis_btn = QtWidgets.QPushButton("+ add axis")
        add_axis_btn.clicked.connect(self.add_axis)
        layout.addWidget(add_axis_btn, alignment=QtCore.Qt.AlignLeft)

        self.add_axis()

    def add_axis(self) -> ScanAxisRowQt:
        row = ScanAxisRowQt(
            self.root,
            allow_simultaneous=self.allow_simultaneous,
            bookmarks=self.bookmarks,
            recent=self.recent,
        )
        remove_btn = QtWidgets.QPushButton("remove axis")
        wrapper = QtWidgets.QWidget()
        wlayout = QtWidgets.QVBoxLayout(wrapper)
        wlayout.setContentsMargins(0, 4, 0, 4)
        header = QtWidgets.QHBoxLayout()
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        header.addWidget(line, 1)
        header.addWidget(remove_btn)
        wlayout.addLayout(header)
        wlayout.addWidget(row)

        def _remove():
            if len(self._axis_rows) <= 1:
                return
            self._axis_rows[:] = [r for r in self._axis_rows if r[1] is not wrapper]
            wrapper.setParent(None)

        remove_btn.clicked.connect(_remove)
        self._axis_rows.append((row, wrapper))
        self.axes_box.addWidget(wrapper)
        return row

    def get_adj_specs(self) -> list:
        return [row.get_adj_spec() for row, _ in self._axis_rows]


# ---------------------------------------------------------------------------
# structured, picker-driven forms for the known Scans.* methods -- mirrors
# eco.widgets.scan_widget._STRUCTURED_FORM_BUILDERS one-for-one, in Qt.
# ---------------------------------------------------------------------------

_RETURN_AT_END_OPTIONS = [
    ("timeout (ask, auto-revert after 10s)", "timeout"),
    ("question (ask, wait for answer)", "question"),
    ("always revert to initial value", True),
    ("stay at final value", False),
]


def _build_common_extra_kwargs_qt(
    root: Any,
    bookmarks: Optional[ComponentBookmarks],
    recent: Optional[RecentComponents],
    return_at_end_default: Any = "timeout",
    include_repetitions: bool = True,
):
    """The parameter block shared by (almost) every Scans method: description,
    settling_time, return_at_end, optionally repetitions, and an optional
    counters override. Returns (widget, read()) where read() -> kwargs dict
    (only including "counters" if the user actually picked any)."""
    box = QtWidgets.QGroupBox("Common options")
    form = QtWidgets.QFormLayout(box)

    description_w = QtWidgets.QLineEdit()
    description_w.setPlaceholderText("description")
    form.addRow("description:", description_w)

    settling_w = QtWidgets.QDoubleSpinBox()
    settling_w.setRange(0, 1e6)
    settling_w.setDecimals(3)
    form.addRow("settling_time:", settling_w)

    rae_w = QtWidgets.QComboBox()
    for label, value in _RETURN_AT_END_OPTIONS:
        rae_w.addItem(label, value)
    # not QComboBox.findData(): Qt's QVariant comparison treats True/False
    # as loosely equal to the *first* string entry (findData(True) matches
    # index 0, "timeout", not index 2) -- guard with an exact type check too.
    for i, (_label, value) in enumerate(_RETURN_AT_END_OPTIONS):
        if type(value) is type(return_at_end_default) and value == return_at_end_default:
            rae_w.setCurrentIndex(i)
            break
    form.addRow("return_at_end:", rae_w)

    rep_w = None
    if include_repetitions:
        rep_w = QtWidgets.QSpinBox()
        rep_w.setRange(1, 1000000)
        rep_w.setValue(1)
        form.addRow("repetitions:", rep_w)

    counters_w = MultiComponentPickerQt(
        root,
        kind_filter="Detector",
        empty_hint="(empty = use scan's default counters)",
        bookmarks=bookmarks,
        recent=recent,
    )
    form.addRow("counters (optional):", counters_w)

    def read() -> Dict[str, Any]:
        kw: Dict[str, Any] = dict(
            description=description_w.text(),
            settling_time=settling_w.value(),
            return_at_end=rae_w.currentData(),
        )
        if rep_w is not None:
            kw["repetitions"] = rep_w.value()
        counters_val = counters_w.get_value()
        if counters_val:
            kw["counters"] = counters_val
        return kw

    return box, read


def _build_acquire_form_qt(scans_obj, root, bookmarks, recent):
    box = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(box)
    top = QtWidgets.QFormLayout()
    n_pulses_w = QtWidgets.QSpinBox()
    n_pulses_w.setRange(1, 10000000)
    n_pulses_w.setValue(100)
    n_rep_w = QtWidgets.QSpinBox()
    n_rep_w.setRange(1, 100000)
    n_rep_w.setValue(1)
    top.addRow("N_pulses:", n_pulses_w)
    top.addRow("N_repetitions:", n_rep_w)
    layout.addLayout(top)
    extra_box, read_extra = _build_common_extra_kwargs_qt(
        root, bookmarks, recent, return_at_end_default=True, include_repetitions=False
    )
    layout.addWidget(extra_box)
    layout.addStretch(1)

    def get_call():
        kwargs = read_extra()
        kwargs["N_repetitions"] = n_rep_w.value()
        return [n_pulses_w.value()], kwargs

    return box, get_call


def _build_ascan_like_form_qt(scans_obj, root, bookmarks, recent):
    """Shared by ascan and dscan: same shape, only the target method call
    differs (dscan interprets start/end relative to the current position)."""
    box = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(box)
    layout.addWidget(QtWidgets.QLabel("<b>Adjustable</b>"))
    picker = ComponentPickerQt(root, kind_filter="Adjustable", bookmarks=bookmarks, recent=recent)
    layout.addWidget(picker)
    grid = QtWidgets.QFormLayout()
    start_w = QtWidgets.QDoubleSpinBox()
    start_w.setRange(-1e9, 1e9)
    end_w = QtWidgets.QDoubleSpinBox()
    end_w.setRange(-1e9, 1e9)
    end_w.setValue(1.0)
    n_w = QtWidgets.QSpinBox()
    n_w.setRange(1, 100000)
    n_w.setValue(10)
    n_pulses_w = QtWidgets.QSpinBox()
    n_pulses_w.setRange(1, 10000000)
    n_pulses_w.setValue(100)
    grid.addRow("start:", start_w)
    grid.addRow("end:", end_w)
    grid.addRow("N_intervals:", n_w)
    grid.addRow("N_pulses:", n_pulses_w)
    layout.addLayout(grid)
    extra_box, read_extra = _build_common_extra_kwargs_qt(root, bookmarks, recent)
    layout.addWidget(extra_box)
    layout.addStretch(1)

    def get_call():
        args = [picker.value, start_w.value(), end_w.value(), n_w.value(), n_pulses_w.value()]
        return args, read_extra()

    return box, get_call


def _build_ascan_position_list_form_qt(scans_obj, root, bookmarks, recent):
    box = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(box)
    layout.addWidget(QtWidgets.QLabel("<b>Adjustable</b>"))
    picker = ComponentPickerQt(root, kind_filter="Adjustable", bookmarks=bookmarks, recent=recent)
    layout.addWidget(picker)
    positions_w = QtWidgets.QLineEdit()
    positions_w.setPlaceholderText("e.g. 0,0.5,1,2,5 or [0,0.5,1,2,5]")
    layout.addWidget(positions_w)
    form = QtWidgets.QFormLayout()
    n_pulses_w = QtWidgets.QSpinBox()
    n_pulses_w.setRange(1, 10000000)
    n_pulses_w.setValue(100)
    form.addRow("N_pulses:", n_pulses_w)
    layout.addLayout(form)
    extra_box, read_extra = _build_common_extra_kwargs_qt(root, bookmarks, recent)
    layout.addWidget(extra_box)
    layout.addStretch(1)

    def get_call():
        args = [picker.value, _parse_position_list_qt(positions_w.text()), n_pulses_w.value()]
        return args, read_extra()

    return box, get_call


def _build_snakescan_form_qt(scans_obj, root, bookmarks, recent):
    box = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(box)
    layout.addWidget(QtWidgets.QLabel("<b>Slow adjustable</b>"))
    picker_slow = ComponentPickerQt(
        root, kind_filter="Adjustable", placeholder="(slow axis)", bookmarks=bookmarks, recent=recent
    )
    layout.addWidget(picker_slow)
    form1 = QtWidgets.QFormLayout()
    step_interval_w = QtWidgets.QDoubleSpinBox()
    step_interval_w.setRange(-1e9, 1e9)
    step_interval_w.setValue(1.0)
    nrows_w = QtWidgets.QSpinBox()
    nrows_w.setRange(1, 100000)
    nrows_w.setValue(5)
    form1.addRow("step_interval:", step_interval_w)
    form1.addRow("Nrows:", nrows_w)
    layout.addLayout(form1)
    layout.addWidget(QtWidgets.QLabel("<b>Fast adjustable</b>"))
    picker_fast = ComponentPickerQt(
        root, kind_filter="Adjustable", placeholder="(fast axis)", bookmarks=bookmarks, recent=recent
    )
    layout.addWidget(picker_fast)
    form2 = QtWidgets.QFormLayout()
    interval_w = QtWidgets.QDoubleSpinBox()
    interval_w.setRange(-1e9, 1e9)
    interval_w.setValue(1.0)
    form2.addRow("interval:", interval_w)
    layout.addLayout(form2)
    # snakescan hardcodes Npulses=1 internally -- no N_pulses field here
    extra_box, read_extra = _build_common_extra_kwargs_qt(root, bookmarks, recent)
    layout.addWidget(extra_box)
    layout.addStretch(1)

    def get_call():
        args = [
            picker_slow.value,
            step_interval_w.value(),
            nrows_w.value(),
            picker_fast.value,
            interval_w.value(),
        ]
        return args, read_extra()

    return box, get_call


def _build_a2scan_form_qt(scans_obj, root, bookmarks, recent):
    box = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(box)
    layout.addWidget(QtWidgets.QLabel("<b>Adjustable 0</b>"))
    picker0 = ComponentPickerQt(
        root, kind_filter="Adjustable", placeholder="(adjustable 0)", bookmarks=bookmarks, recent=recent
    )
    layout.addWidget(picker0)
    form0 = QtWidgets.QFormLayout()
    start0_w = QtWidgets.QDoubleSpinBox()
    start0_w.setRange(-1e9, 1e9)
    end0_w = QtWidgets.QDoubleSpinBox()
    end0_w.setRange(-1e9, 1e9)
    end0_w.setValue(1.0)
    form0.addRow("start0:", start0_w)
    form0.addRow("end0:", end0_w)
    layout.addLayout(form0)
    layout.addWidget(QtWidgets.QLabel("<b>Adjustable 1</b>"))
    picker1 = ComponentPickerQt(
        root, kind_filter="Adjustable", placeholder="(adjustable 1)", bookmarks=bookmarks, recent=recent
    )
    layout.addWidget(picker1)
    form1 = QtWidgets.QFormLayout()
    start1_w = QtWidgets.QDoubleSpinBox()
    start1_w.setRange(-1e9, 1e9)
    end1_w = QtWidgets.QDoubleSpinBox()
    end1_w.setRange(-1e9, 1e9)
    end1_w.setValue(1.0)
    form1.addRow("start1:", start1_w)
    form1.addRow("end1:", end1_w)
    layout.addLayout(form1)
    form2 = QtWidgets.QFormLayout()
    n_w = QtWidgets.QSpinBox()
    n_w.setRange(1, 100000)
    n_w.setValue(10)
    n_pulses_w = QtWidgets.QSpinBox()
    n_pulses_w.setRange(1, 10000000)
    n_pulses_w.setValue(100)
    form2.addRow("N_intervals:", n_w)
    form2.addRow("N_pulses:", n_pulses_w)
    layout.addLayout(form2)
    extra_box, read_extra = _build_common_extra_kwargs_qt(root, bookmarks, recent)
    layout.addWidget(extra_box)
    layout.addStretch(1)

    def get_call():
        args = [
            picker0.value,
            start0_w.value(),
            end0_w.value(),
            picker1.value,
            start1_w.value(),
            end1_w.value(),
            n_w.value(),
            n_pulses_w.value(),
        ]
        return args, read_extra()

    return box, get_call


def _build_grid_form_qt(allow_simultaneous: bool):
    """Shared by meshscan (allow_simultaneous=False) and scan
    (allow_simultaneous=True): a growable list of grid axes via
    ScanBuilderQt, the picker-driven version of *adj_specs."""

    def build(scans_obj, root, bookmarks, recent):
        box = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(box)
        builder = ScanBuilderQt(
            root, allow_simultaneous=allow_simultaneous, bookmarks=bookmarks, recent=recent
        )
        layout.addWidget(builder)
        form = QtWidgets.QFormLayout()
        n_pulses_w = QtWidgets.QSpinBox()
        n_pulses_w.setRange(1, 10000000)
        n_pulses_w.setValue(100)
        order_w = QtWidgets.QComboBox()
        order_w.addItems(["last_fastest", "first_fastest"])
        form.addRow("N_pulses:", n_pulses_w)
        form.addRow("scanning_order:", order_w)
        layout.addLayout(form)
        extra_box, read_extra = _build_common_extra_kwargs_qt(root, bookmarks, recent)
        layout.addWidget(extra_box)
        layout.addStretch(1)

        def get_call():
            kwargs = read_extra()
            kwargs["N_pulses"] = n_pulses_w.value()
            kwargs["scanning_order"] = order_w.currentText()
            return builder.get_adj_specs(), kwargs

        return box, get_call

    return build


_STRUCTURED_FORM_BUILDERS_QT: Dict[str, Callable] = {
    "acquire": _build_acquire_form_qt,
    "ascan": _build_ascan_like_form_qt,
    "dscan": _build_ascan_like_form_qt,
    "ascan_position_list": _build_ascan_position_list_form_qt,
    "snakescan": _build_snakescan_form_qt,
    "a2scan": _build_a2scan_form_qt,
    "meshscan": _build_grid_form_qt(allow_simultaneous=False),
    "scan": _build_grid_form_qt(allow_simultaneous=True),
}


# ---------------------------------------------------------------------------
# generic reflection-based fallback, for any Scans method not covered above
# ---------------------------------------------------------------------------


def _coerce_value(s: Any) -> Any:
    if s is None:
        return None
    s = s.strip()
    if s == "":
        return ""
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        return int(s)
    except Exception:
        pass
    try:
        return float(s)
    except Exception:
        pass
    try:
        return json.loads(s)
    except Exception:
        pass
    return s


def _default_text(default: Any) -> str:
    if isinstance(default, (list, dict, tuple)):
        return json.dumps(default)
    return str(default)


def _build_generic_form_qt(method):
    """Signature-introspection fallback: one free-text (JSON-coerced) field
    per parameter -- same idea as scan_widget.ScanWidget.build_for_method's
    fallback path, simplified to plain QLineEdits."""
    box = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(box)
    try:
        sig = inspect.signature(method) if callable(method) else None
    except (TypeError, ValueError):
        sig = None

    pos_readers: List[Tuple[str, QtWidgets.QWidget, Any]] = []
    kw_readers: Dict[str, QtWidgets.QWidget] = {}

    if sig is not None:
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)
        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            kind = param.kind
            default = param.default if param.default is not inspect._empty else None
            if kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL,
            ):
                w = QtWidgets.QLineEdit()
                if default is not None:
                    w.setText(_default_text(default))
                prefix = "*" if kind == inspect.Parameter.VAR_POSITIONAL else ""
                form.addRow(f"{prefix}{pname}:", w)
                pos_readers.append((pname, w, kind))
            elif kind == inspect.Parameter.KEYWORD_ONLY:
                w = QtWidgets.QLineEdit()
                if default is not None:
                    w.setText(_default_text(default))
                form.addRow(f"{pname}:", w)
                kw_readers[pname] = w
            elif kind == inspect.Parameter.VAR_KEYWORD:
                w = QtWidgets.QLineEdit()
                w.setPlaceholderText('JSON object, e.g. {"key": 1}')
                form.addRow(f"**{pname}:", w)
                kw_readers[f"**{pname}"] = w
    else:
        layout.addWidget(QtWidgets.QLabel("(signature unavailable -- enter JSON args/kwargs)"))
        args_w = QtWidgets.QLineEdit()
        args_w.setPlaceholderText("JSON list of positional args")
        kwargs_w = QtWidgets.QLineEdit()
        kwargs_w.setPlaceholderText("JSON kwargs dict")
        layout.addWidget(args_w)
        layout.addWidget(kwargs_w)
        pos_readers.append(("args", args_w, "json_list"))
        kw_readers["__json_kwargs__"] = kwargs_w

    layout.addStretch(1)

    def get_call():
        args = []
        for _pname, w, kind in pos_readers:
            text = w.text()
            if kind in ("json_list", inspect.Parameter.VAR_POSITIONAL):
                val = _coerce_value(text)
                if isinstance(val, list):
                    args.extend(val)
                elif text:
                    args.append(val)
                continue
            args.append(_coerce_value(text))
        kwargs = {}
        for key, w in kw_readers.items():
            text = w.text()
            if key == "__json_kwargs__":
                val = _coerce_value(text)
                if isinstance(val, dict):
                    kwargs.update(val)
                continue
            if key.startswith("**"):
                val = _coerce_value(text)
                if isinstance(val, dict):
                    kwargs.update(val)
                continue
            kwargs[key] = _coerce_value(text)
        return args, kwargs

    return box, get_call


# ---------------------------------------------------------------------------
# queue panel
# ---------------------------------------------------------------------------


class ScanQueuePanel(QtWidgets.QWidget):
    """Status + controls for one ScanQueue: pause/resume the queue itself
    (don't start the next item), pause/resume/stop whatever's running right
    now, save a paused item's remaining work to disk and resume it later,
    and a live progress bar + per-step readback plot for whatever this
    panel most recently submitted. Polls queue.status() (and, for anything
    finer-grained than status()'s repr strings, the tracked QueueItems
    themselves) on a QTimer -- ScanQueue has no push/callback channel for
    this today."""

    POLL_INTERVAL_MS = 500

    def __init__(self, queue, root, bookmarks=None, recent=None, parent=None):
        super().__init__(parent)
        self.queue = queue
        self.root = root
        self.bookmarks = bookmarks
        self.recent = recent
        self._tracked: List[Any] = []  # QueueItems submitted via this panel

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(f"<b>Queue: {queue.name}</b>"))

        self.current_label = QtWidgets.QLabel("Current: (idle)")
        self.pending_label = QtWidgets.QLabel("Pending: 0")
        layout.addWidget(self.current_label)
        layout.addWidget(self.pending_label)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("no active scan")
        layout.addWidget(self.progress)

        self.pause_queue_btn = QtWidgets.QPushButton("Pause queue")
        self.pause_queue_btn.setCheckable(True)
        self.pause_queue_btn.toggled.connect(self._on_pause_queue_toggled)
        layout.addWidget(self.pause_queue_btn)

        # one button per row, not side by side -- qt-material's bold
        # uppercase button styling makes even "Resume current" too wide for
        # two-across in a ~300px side panel without clipping (see the same
        # lesson learned the hard way in camserver_stream_qt.py's toolbar,
        # which had the analogous bug)
        pause_current_btn = QtWidgets.QPushButton("Pause current")
        pause_current_btn.clicked.connect(self.queue.pause_current)
        layout.addWidget(pause_current_btn)
        resume_current_btn = QtWidgets.QPushButton("Resume current")
        resume_current_btn.clicked.connect(self.queue.resume_current)
        layout.addWidget(resume_current_btn)
        stop_current_btn = QtWidgets.QPushButton("Stop current")
        stop_current_btn.clicked.connect(self._on_stop_current)
        layout.addWidget(stop_current_btn)

        save_btn = QtWidgets.QPushButton("Save paused...")
        save_btn.clicked.connect(self._on_save_paused)
        layout.addWidget(save_btn)
        resume_file_btn = QtWidgets.QPushButton("Resume from file...")
        resume_file_btn.clicked.connect(self._on_resume_from_file)
        layout.addWidget(resume_file_btn)

        layout.addWidget(QtWidgets.QLabel("Pending:"))
        self.pending_list = QtWidgets.QListWidget()
        self.pending_list.setMaximumHeight(80)
        layout.addWidget(self.pending_list)

        # live per-step readback plot -- see module docstring for why this
        # is readback position, not detector signal
        self._figure = None
        self._canvas = None
        self._axes = None
        try:
            from matplotlib.figure import Figure

            try:
                from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            except ImportError:
                from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg

            self._figure = Figure(figsize=(4, 2.4))
            self._axes = self._figure.add_subplot(111)
            self._canvas = FigureCanvasQTAgg(self._figure)
            self._canvas.setMinimumHeight(180)
            self._style_plot_for_palette()
            layout.addWidget(self._canvas)
        except Exception:
            layout.addWidget(QtWidgets.QLabel("(matplotlib unavailable -- no live plot)"))

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.POLL_INTERVAL_MS)
        self._poll()

    def track_item(self, item):
        """Register a QueueItem this panel submitted, so progress/plot/save
        can find it -- ScanQueue.status() only exposes repr strings, not the
        item objects themselves."""
        self._tracked.append(item)
        del self._tracked[:-20]  # bounded history

    def _current_tracked_item(self):
        """The most recently submitted tracked item that's actually running
        (or paused mid-run) right now, if any."""
        for item in reversed(self._tracked):
            if item.state == "running" or (
                item.scan is not None and item.scan.is_paused()
            ):
                return item
        return None

    def _on_pause_queue_toggled(self, checked):
        if checked:
            self.queue.pause()
            self.pause_queue_btn.setText("Resume queue")
        else:
            self.queue.resume()
            self.pause_queue_btn.setText("Pause queue")

    def _on_stop_current(self):
        reply = QtWidgets.QMessageBox.question(
            self,
            "Stop current scan",
            "Abandon the scan currently running? Its remaining steps will not run.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self.queue.stop_current()

    def _paused_item_for_save(self):
        for item in reversed(self._tracked):
            if item.scan is not None and item.scan.is_paused() and item.state == "running":
                return item
        return None

    def _on_save_paused(self):
        item = self._paused_item_for_save()
        if item is None:
            QtWidgets.QMessageBox.information(
                self,
                "Save paused",
                "No paused scan (from this panel) to save -- pause the currently running scan first.",
            )
            return
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save paused scan", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            self.queue.save_paused(item, path)
            QtWidgets.QMessageBox.information(self, "Save paused", f"Saved to {path}")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Save paused", f"Failed: {exc}")

    def _on_resume_from_file(self):
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self, "Resume scan from file", "", "JSON (*.json)"
        )
        if not path:
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Pick counters to resume with")
        dlayout = QtWidgets.QVBoxLayout(dialog)
        dlayout.addWidget(
            QtWidgets.QLabel(
                "Ad hoc counters aren't addressable by name -- pick the live\n"
                "counter(s) to use for the resumed scan:"
            )
        )
        counters_w = MultiComponentPickerQt(
            self.root,
            kind_filter="Detector",
            empty_hint="(pick at least one)",
            bookmarks=self.bookmarks,
            recent=self.recent,
        )
        dlayout.addWidget(counters_w)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dlayout.addWidget(buttons)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        counters = counters_w.get_value()
        if not counters:
            QtWidgets.QMessageBox.warning(self, "Resume from file", "Pick at least one counter.")
            return
        try:
            item = self.queue.resume_from_file(path, self.root, counters)
            self.track_item(item)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Resume from file", f"Failed: {exc}")

    def _poll(self):
        status = self.queue.status()
        self.current_label.setText(f"Current: {status['current'] or '(idle)'}")
        pending = status["pending"]
        self.pending_label.setText(f"Pending: {len(pending)}")
        self.pending_list.clear()
        self.pending_list.addItems(pending)

        item = self._current_tracked_item()
        if item is None or item.scan is None:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress.setFormat("no active scan")
            return

        scan = item.scan
        total = len(scan.scan_info.get("scan_values_all", []))
        done = len(getattr(scan, "_values_done", []))
        paused = scan.is_paused()
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(min(done, total))
        self.progress.setFormat(f"step {done}/{total}" + (" (paused)" if paused else " (%p%)"))

        self._update_plot(scan)

    def _style_plot_for_palette(self):
        """Match the embedded plot's colors to whatever Qt theme is active
        (see eco.widgets.qt_theme) -- matplotlib defaults to a plain white
        figure regardless, which looks jarring inside a dark-themed dock."""
        window_color = self.palette().color(QtGui.QPalette.Window)
        text_color = self.palette().color(QtGui.QPalette.WindowText)
        face = window_color.name()
        text = text_color.name()
        self._figure.patch.set_facecolor(face)
        self._axes.set_facecolor(face)
        for spine in self._axes.spines.values():
            spine.set_color(text)
        self._axes.tick_params(colors=text)
        self._axes.xaxis.label.set_color(text)
        self._axes.yaxis.label.set_color(text)

    def _update_plot(self, scan):
        if self._canvas is None:
            return
        readbacks = getattr(scan, "readbacks", None)
        adj_names = [getattr(a, "name", str(a)) for a in getattr(scan, "adjustables", [])]
        if not readbacks or not adj_names:
            return
        self._axes.clear()
        self._style_plot_for_palette()
        for i, name in enumerate(adj_names):
            ys = [step[i] for step in readbacks if len(step) > i]
            self._axes.plot(range(len(ys)), ys, marker="o", markersize=3, label=name)
        self._axes.set_xlabel("step")
        self._axes.set_ylabel("readback")
        if len(adj_names) > 1:
            self._axes.legend(fontsize=7)
        self._figure.tight_layout()
        self._canvas.draw_idle()

    def stop(self):
        self._timer.stop()


class _CurrentPageStackedWidget(QtWidgets.QStackedWidget):
    """QStackedWidget.sizeHint()/minimumSizeHint() default to the max over
    *every* page it holds, not just the visible one -- so the compact
    "ascan" form still reserved space for whatever the widest page is (e.g.
    a2scan, with two device pickers side by side), which made the
    surrounding QScrollArea think it needed a horizontal scrollbar and
    pushed the Pick.../Clear buttons off-screen even on the common, narrow
    case. Reporting only the current page's hint fixes that; call
    updateGeometry() after switching pages so the scroll area re-measures."""

    def sizeHint(self):
        w = self.currentWidget()
        return w.sizeHint() if w is not None else super().sizeHint()

    def minimumSizeHint(self):
        w = self.currentWidget()
        return w.minimumSizeHint() if w is not None else super().minimumSizeHint()


# ---------------------------------------------------------------------------
# main widget
# ---------------------------------------------------------------------------


class ScanLauncherQt(QtWidgets.QWidget):
    """Build-and-submit form for eco.acquisition.scan.Scans, plus a
    ScanQueuePanel for the queue it submits into. Every Run goes through
    scan_queue=<queue_name> (default "default") -- never a direct
    synchronous call -- so the queue panel's pause/resume/stop/save/resume
    controls are always meaningful for whatever was just launched here."""

    def __init__(
        self,
        scans_obj: Any,
        root: Any = None,
        methods: Optional[List[str]] = None,
        queue_name: str = "default",
        parent=None,
    ):
        super().__init__(parent)
        self.scans = scans_obj
        self.root = root if root is not None else scans_obj
        self.queue_name = queue_name
        self.queue = scans_obj.queues[queue_name]
        self._bookmarks = ComponentBookmarks(namespace_name=getattr(self.root, "name", None))
        self._recent = RecentComponents(namespace_name=getattr(self.root, "name", None))
        self._get_call: Optional[Callable[[], Tuple[List[Any], Dict[str, Any]]]] = None

        if methods is None:
            cand = [
                "ascan",
                "dscan",
                "ascan_position_list",
                "snakescan",
                "a2scan",
                "meshscan",
                "scan",
                "acquire",
            ]
            methods = [m for m in cand if hasattr(scans_obj, m)]
        self.methods = methods

        layout = QtWidgets.QVBoxLayout(self)

        top_row = QtWidgets.QHBoxLayout()
        self.method_combo = QtWidgets.QComboBox()
        self.method_combo.addItems(self.methods)
        self.run_btn = QtWidgets.QPushButton("Run (submit to queue)")
        self.run_btn.setStyleSheet("font-weight: bold;")
        self.status_label = QtWidgets.QLabel("")
        top_row.addWidget(QtWidgets.QLabel("Method:"))
        top_row.addWidget(self.method_combo)
        top_row.addWidget(self.run_btn)
        top_row.addWidget(self.status_label, 1)
        layout.addLayout(top_row)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(splitter, 1)

        form_scroll = QtWidgets.QScrollArea()
        form_scroll.setWidgetResizable(True)
        self._stack = _CurrentPageStackedWidget()
        form_scroll.setWidget(self._stack)
        splitter.addWidget(form_scroll)

        self._pages: Dict[str, Tuple[QtWidgets.QWidget, Callable]] = {}
        for name in self.methods:
            self._build_page(name)

        self.queue_panel = ScanQueuePanel(
            self.queue, self.root, bookmarks=self._bookmarks, recent=self._recent
        )
        self.queue_panel.setMinimumWidth(280)
        splitter.addWidget(self.queue_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        # setStretchFactor alone only governs how *extra* space is shared on
        # a later resize -- the splitter's initial split comes from each
        # side's sizeHint(), and the queue panel's button labels ("Resume
        # from file...") make it want far more than its fair share, so the
        # form side was squeezed down to a sliver on first show. setSizes()
        # fixes the initial split explicitly.
        splitter.setSizes([650, 330])

        self.method_combo.currentTextChanged.connect(self._on_method_changed)
        self.run_btn.clicked.connect(self._on_run)
        if self.methods:
            self._on_method_changed(self.method_combo.currentText())

    def _build_page(self, name):
        method = getattr(self.scans, name, None)
        builder = _STRUCTURED_FORM_BUILDERS_QT.get(name)
        if builder is not None:
            widget, get_call = builder(self.scans, self.root, self._bookmarks, self._recent)
        else:
            widget, get_call = _build_generic_form_qt(method)
        self._pages[name] = (widget, get_call)
        self._stack.addWidget(widget)

    def _on_method_changed(self, name):
        if not name or name not in self._pages:
            return
        widget, get_call = self._pages[name]
        self._stack.setCurrentWidget(widget)
        self._stack.updateGeometry()
        self._get_call = get_call
        self.status_label.setText("")

    def _on_run(self):
        method_name = self.method_combo.currentText()
        method = getattr(self.scans, method_name, None)
        if method is None or self._get_call is None:
            self.status_label.setText("method not available")
            return
        try:
            args, kwargs = self._get_call()
            kwargs["scan_queue"] = self.queue_name
            item = method(*args, **kwargs)
        except Exception as exc:
            self.status_label.setText(f"Error: {exc}")
            return
        self.queue_panel.track_item(item)
        self.status_label.setText(f"Submitted: {item!r}")

    def stop(self):
        self.queue_panel.stop()


class ScanLauncherQtWindow:
    """Wraps ScanLauncherQt in its own top-level window -- same
    non-blocking-inside-IPython / blocking-otherwise start()/run()/stop()
    convention as eco.widgets.component_selector_qt.ComponentSelectorQtWindow."""

    def __init__(
        self,
        scans_obj: Any,
        root: Any = None,
        methods: Optional[List[str]] = None,
        queue_name: str = "default",
        auto_start: bool = True,
    ):
        self._init_kwargs = dict(
            scans_obj=scans_obj, root=root, methods=methods, queue_name=queue_name
        )
        self.window: Optional[QtWidgets.QMainWindow] = None
        self.launcher: Optional[ScanLauncherQt] = None
        if auto_start:
            self.start()

    def _build_window(self) -> None:
        global _app_ref
        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication([])
            _app_ref = app
        self.launcher = ScanLauncherQt(**self._init_kwargs)
        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle("Scan launcher")
        self.window.setCentralWidget(self.launcher)
        # close_calls_stop: closing via the window's own native close (X)
        # button should stop the queue panel's polling the same as an
        # explicit stop() does -- see eco.widgets.qt_lifecycle's module
        # docstring for the fuller why.
        from eco.widgets.qt_lifecycle import close_calls_stop

        close_calls_stop(self.window, self.stop)
        self.window.resize(980, 700)
        self.window.show()

    def run(self) -> None:
        app = QtWidgets.QApplication.instance()
        created_app = app is None
        if created_app:
            app = QtWidgets.QApplication([])
        if self.window is None:
            self._build_window()
        if created_app:
            app.exec_()

    def start(self) -> None:
        if self.window is not None:
            return
        ip = None
        try:
            from IPython import get_ipython

            ip = get_ipython()
        except Exception:
            ip = None

        if ip is None:
            self.run()
            return

        active = getattr(ip, "active_eventloop", None)
        if active is None:
            try:
                ip.enable_gui("qt")
            except Exception:
                pass
        elif active not in ("qt", "qt4", "qt5", "qt6"):
            print(
                f"eco scan launcher: a different GUI event loop ('{active}') is "
                "already active in this IPython session, so the window can't be "
                "pumped non-blockingly alongside it. Showing it in blocking mode "
                "instead (closing the window returns control)."
            )
            self.run()
            return

        self._build_window()

    def stop(self) -> None:
        if self.launcher is not None:
            self.launcher.stop()
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None
            self.launcher = None


def make_scan_launcher_qt(
    scans_obj: Any,
    root: Any = None,
    methods: Optional[List[str]] = None,
    queue_name: str = "default",
) -> ScanLauncherQt:
    """Build just the embeddable QWidget (no window) -- e.g. to add to
    eco.widgets.dashboard_qt.Dashboard via add_widget()."""
    return ScanLauncherQt(scans_obj, root=root, methods=methods, queue_name=queue_name)


def make_scan_launcher_qt_window(
    scans_obj: Any,
    root: Any = None,
    methods: Optional[List[str]] = None,
    queue_name: str = "default",
    auto_start: bool = True,
) -> ScanLauncherQtWindow:
    return ScanLauncherQtWindow(
        scans_obj, root=root, methods=methods, queue_name=queue_name, auto_start=auto_start
    )
