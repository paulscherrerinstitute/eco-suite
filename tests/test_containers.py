"""eco.widgets.containers -- backend-agnostic stack()/aligned() and the
four widget builders (assembly_widget/adjustable_control/
detector_indicator/viewer), plus the extraction those builders rest on
(eco.widgets.display_qt._build_item_controls_qt/
eco.widgets.display_widget._build_item_controls_widget and their
standalone build_adjustable_control_*/_apply_*_update siblings) -- none of
which had dedicated test coverage before this module existed."""
import time

import pytest

pytest.importorskip("qtpy")

from qtpy import QtWidgets

from eco.widgets import containers


class _FakeDetector:
    def __init__(self, name, value=1.0):
        self.name = name
        self._value = value

    def get_current_value(self):
        return self._value


class _FakeAdjustable(_FakeDetector):
    def __init__(self, name, value=1.0):
        super().__init__(name, value)
        self.set_calls = []

    def set_target_value(self, value):
        self.set_calls.append(value)
        self._value = value
        return None  # no Changer -- exercises the hasattr(r, "wait") guards


class _FakeBuiltWidget:
    """A minimal already-built .window/.stop() Qt-convention widget, for
    stack() tests that don't want to go through a real builder."""

    def __init__(self):
        self.window = QtWidgets.QLabel("fake")
        self.stop_calls = 0

    def stop(self):
        self.stop_calls += 1


# --------------------------------------------------------------------------- #
# stack() / aligned() -- Qt
# --------------------------------------------------------------------------- #
def test_stack_qt_builds_a_window_with_every_child():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    a, b = _FakeBuiltWidget(), _FakeBuiltWidget()
    result = containers._build_qt([a, b], "vertical", "start")
    try:
        assert isinstance(result.window, QtWidgets.QWidget)
        assert result.window.layout().count() == 2
    finally:
        result.stop()


def test_stack_qt_stop_stops_every_child_with_a_stop_method():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    a, b = _FakeBuiltWidget(), _FakeBuiltWidget()
    result = containers._build_qt([a, b], "vertical", "start")
    result.stop()
    assert a.stop_calls == 1
    assert b.stop_calls == 1


def test_stack_qt_builder_children_are_called_lazily():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    calls = []

    def _builder():
        calls.append("built")
        return _FakeBuiltWidget()

    assert calls == []
    result = containers._build_qt([_builder], "vertical", "start")
    try:
        assert calls == ["built"]
    finally:
        result.stop()


def test_stack_qt_per_child_alignment_override():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from qtpy import QtCore

    plain = _FakeBuiltWidget()
    centered = _FakeBuiltWidget()
    result = containers._build_qt(
        [plain, containers.aligned(centered, "center")], "vertical", "left"
    )
    try:
        layout = result.window.layout()
        assert layout.itemAt(0).alignment() == QtCore.Qt.AlignLeft
        assert layout.itemAt(1).alignment() == QtCore.Qt.AlignHCenter
    finally:
        result.stop()


def test_stack_qt_skips_a_child_that_fits_neither_convention():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    result = containers._build_qt([object()], "vertical", "start")
    try:
        assert result.window.layout().count() == 0
    finally:
        result.stop()


def test_stack_rejects_bad_direction():
    with pytest.raises(ValueError):
        containers.stack(direction="diagonal")


# --------------------------------------------------------------------------- #
# stack() / aligned() -- notebook (ipywidgets), forced via is_notebook()
# --------------------------------------------------------------------------- #
def test_stack_notebook_builds_a_box_with_every_child(monkeypatch):
    import ipywidgets as widgets

    monkeypatch.setattr("eco.utilities.utilities.is_notebook", lambda: True, raising=False)
    a, b = widgets.Label("a"), widgets.Label("b")
    box = containers.stack(a, b, direction="vertical", align="center")
    assert isinstance(box, widgets.VBox)
    assert list(box.children) == [a, b]
    assert box.layout.align_items == "center"


def test_stack_notebook_horizontal_uses_hbox(monkeypatch):
    import ipywidgets as widgets

    monkeypatch.setattr("eco.utilities.utilities.is_notebook", lambda: True, raising=False)
    box = containers.stack(widgets.Label("a"), direction="horizontal")
    assert isinstance(box, widgets.HBox)


def test_stack_notebook_per_child_alignment_override(monkeypatch):
    import ipywidgets as widgets

    monkeypatch.setattr("eco.utilities.utilities.is_notebook", lambda: True, raising=False)
    centered = widgets.Label("c")
    box = containers.stack(
        widgets.Label("a"), containers.aligned(centered, "right"),
        direction="vertical", align="left",
    )
    assert box.layout.align_items == "flex-start"
    assert centered.layout.align_self == "flex-end"


def test_stack_notebook_stop_stops_every_child_with_a_stop_method(monkeypatch):
    import ipywidgets as widgets

    monkeypatch.setattr("eco.utilities.utilities.is_notebook", lambda: True, raising=False)
    child = widgets.Label("a")
    stopped = []
    child.stop = lambda: stopped.append(True)
    box = containers.stack(child)
    box.stop()
    assert stopped == [True]


# --------------------------------------------------------------------------- #
# the four builders -- dispatch/delegation behaviour
# --------------------------------------------------------------------------- #
def test_assembly_widget_uses_widget_assembly_when_present():
    calls = []

    class Fake:
        def _widget_assembly(self, **kwargs):
            calls.append(kwargs)
            return "the assembly widget"

        def widget(self, **kwargs):
            raise AssertionError("should not be called when _widget_assembly exists")

    result = containers.assembly_widget(Fake(), show_hidden=True)()
    assert result == "the assembly widget"
    assert calls == [{"show_hidden": True}]


def test_assembly_widget_falls_back_to_widget_normal_true():
    calls = []

    class Fake:
        def widget(self, **kwargs):
            calls.append(kwargs)
            return "generic widget"

    result = containers.assembly_widget(Fake())()
    assert result == "generic widget"
    assert calls == [{"normal": True}]


def test_viewer_calls_widget_method_if_present():
    class Fake:
        def widget(self, **kwargs):
            return "custom viewer"

    assert containers.viewer(Fake())() == "custom viewer"


def test_viewer_returns_object_as_is_without_a_widget_method():
    obj = object()
    assert containers.viewer(obj)() is obj


def test_adjustable_control_dispatches_to_qt_builder(monkeypatch):
    monkeypatch.setattr("eco.utilities.utilities.is_notebook", lambda: False, raising=False)
    calls = []
    monkeypatch.setattr(
        "eco.widgets.display_qt.build_adjustable_control_qt",
        lambda item, poll_interval=1.0, title=None: calls.append(
            (item, poll_interval, title)
        )
        or "qt control",
        raising=False,
    )
    item = _FakeAdjustable("x")
    result = containers.adjustable_control(item, poll_interval=2.0, title="X")()
    assert result == "qt control"
    assert calls == [(item, 2.0, "X")]


def test_adjustable_control_dispatches_to_notebook_builder(monkeypatch):
    monkeypatch.setattr("eco.utilities.utilities.is_notebook", lambda: True, raising=False)
    calls = []
    monkeypatch.setattr(
        "eco.widgets.display_widget.build_adjustable_control_widget",
        lambda item, poll_interval=1.0, title=None: calls.append(
            (item, poll_interval, title)
        )
        or "notebook control",
        raising=False,
    )
    item = _FakeAdjustable("x")
    result = containers.adjustable_control(item)()
    assert result == "notebook control"
    assert calls == [(item, 1.0, None)]


def test_detector_indicator_is_the_same_dispatch_as_adjustable_control(monkeypatch):
    monkeypatch.setattr("eco.utilities.utilities.is_notebook", lambda: True, raising=False)
    calls = []
    monkeypatch.setattr(
        "eco.widgets.display_widget.build_adjustable_control_widget",
        lambda item, poll_interval=1.0, title=None: calls.append(item) or "w",
        raising=False,
    )
    item = _FakeDetector("d")
    assert containers.detector_indicator(item)() == "w"
    assert calls == [item]


# --------------------------------------------------------------------------- #
# the extraction itself: eco.widgets.display_qt
# --------------------------------------------------------------------------- #
from eco.widgets import display_qt


def test_build_item_controls_qt_read_only_detector():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    item = _FakeDetector("d", value=3.0)
    value_label = QtWidgets.QLabel("3.0")
    controls, combo = display_qt._build_item_controls_qt(item, value_label, 3.0)
    assert combo is None
    assert isinstance(controls, QtWidgets.QWidget)


def test_build_item_controls_qt_adjustable_stop_reset():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # an int value (not float) so the tweak-step arithmetic is exact --
    # _default_step_for gives ints a step of 1
    item = _FakeAdjustable("a", value=1)
    value_label = QtWidgets.QLabel("1")
    controls, combo = display_qt._build_item_controls_qt(item, value_label, 1)
    assert combo is None  # not enum-like -- tweakable/plain branch

    buttons = controls.findChildren(QtWidgets.QPushButton)
    up_btn = next(b for b in buttons if b.text() == "▲")
    stop_btn = next(b for b in buttons if b.text() == "🛑")
    reset_btn = next(b for b in buttons if b.text() == "↺")

    up_btn.click()
    assert item.set_calls == [2]  # base 1 + step 1
    reset_btn.click()
    assert item.set_calls[-1] == 1  # back to the value the control opened with
    stop_btn.click()  # no changer with .stop() attached here -- just shouldn't raise


def test_apply_value_label_update_syncs_combo_selection():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    combo = QtWidgets.QComboBox()
    combo.addItems(["OPEN", "CLOSED"])
    combo.setCurrentText("OPEN")
    value_label = QtWidgets.QLabel("OPEN")
    display_qt._apply_value_label_update(value_label, combo, "CLOSED")
    assert value_label.text() == "CLOSED"
    assert combo.currentText() == "CLOSED"


def test_build_adjustable_control_qt_polls_and_updates():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    item = _FakeDetector("d", value=1.0)
    control = display_qt.build_adjustable_control_qt(item, poll_interval=0.05)
    try:
        item._value = 42.0
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            app.processEvents()
            labels = control.window.findChildren(QtWidgets.QLabel)
            if any(l.text() == "42.0" for l in labels):
                break
            time.sleep(0.02)
        else:
            pytest.fail("value label never picked up the polled update")
    finally:
        control.stop()
        assert control.window is None


# --------------------------------------------------------------------------- #
# the extraction itself: eco.widgets.display_widget
# --------------------------------------------------------------------------- #
from eco.widgets import display_widget


def test_build_item_controls_widget_read_only_detector():
    import ipywidgets as widgets

    item = _FakeDetector("d", value=3.0)
    value_w = widgets.Label("3.0")
    control_box, poll_state = display_widget._build_item_controls_widget(item, value_w, 3.0)
    assert poll_state["enum_opts"] is None
    assert poll_state["dd_suppress"] is None


def test_apply_item_update_reads_value_and_updates_widget():
    import ipywidgets as widgets

    item = _FakeDetector("d", value=1.0)
    value_w = widgets.Label("1.0")
    entry = {"item": item, "value_widget": value_w, "state_class": None}
    item._value = 7.0
    duration = display_widget._apply_item_update(entry)
    assert duration >= 0
    assert value_w.value == "7.0"


def test_build_adjustable_control_widget_polls_and_updates():
    item = _FakeDetector("d", value=1.0)
    control = display_widget.build_adjustable_control_widget(item, poll_interval=0.05)
    try:
        item._value = 9.0
        deadline = time.monotonic() + 2.0
        value_w = control.children[1]
        while time.monotonic() < deadline and value_w.value != "9.0":
            time.sleep(0.02)
        assert value_w.value == "9.0"
    finally:
        control.stop()
