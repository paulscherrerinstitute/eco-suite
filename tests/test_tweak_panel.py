"""Tweak panels (eco.widgets.tweak_panel[_qt|_ipy]): key bindings, controller,
the soft (Tweak) axis, and both panels driven by key events."""

import os
import time

import pytest

from eco.widgets import tweak_panel as tp


class FakeAxis:
    def __init__(self, name, value=0.0, step=1.0):
        self.name = name
        self.value = value
        self.step = step
        self.start_value = value
        self.log = []

    def get_value(self):
        return self.value

    def move(self, sign):
        self.value += sign * self.step
        self.log.append(("move", sign))

    def scale_step(self, factor):
        self.step *= factor

    def set_step(self, step):
        self.step = step

    def go(self, value):
        self.value = value
        self.log.append(("go", value))

    def back_to_start(self):
        self.go(self.start_value)

    def reset_current_value_to(self, value):
        self.value = value
        self.log.append(("reset", value))

    def stop(self):
        self.log.append(("stop",))


def axes(n):
    return [FakeAxis(f"a{i}") for i in range(n)]


# --- bindings ---


def test_single_axis_uses_arrows_like_the_terminal():
    b = tp.key_bindings(1)
    assert b["Left"] == ("move", 0, -1)
    assert b["Up"] == ("step", 0, 2.0)
    assert b["Down"] == ("step", 0, 0.5)
    assert b["Right"] == ("move", 0, +1)
    assert b["s"] == ("start",) and b["q"] == ("release",)


def test_two_axes_use_xy_arrows_and_ctrl_arrows_for_steps():
    b = tp.key_bindings(2)
    assert b["Left"] == ("move", 0, -1) and b["Right"] == ("move", 0, +1)
    assert b["Down"] == ("move", 1, -1) and b["Up"] == ("move", 1, +1)
    assert b["Ctrl+Right"] == ("step", 0, 2.0) and b["Ctrl+Left"] == ("step", 0, 0.5)
    assert b["Ctrl+Up"] == ("step", 1, 2.0) and b["Ctrl+Down"] == ("step", 1, 0.5)


@pytest.mark.parametrize(
    "n, rows",
    [
        (1, ["jkl;"]),
        (2, ["jkl;", "uiop"]),
        (3, ["jkl;", "uiop", "7890"]),
        (4, ["m,./", "jkl;", "uiop", "7890"]),
    ],
)
def test_stacked_rows(n, rows):
    b = tp.key_bindings(n, stacked=True)
    for i, (neg, dbl, half, pos) in enumerate(rows):
        assert b[neg] == ("move", i, -1)
        assert b[dbl] == ("step", i, 2.0)
        assert b[half] == ("step", i, 0.5)
        assert b[pos] == ("move", i, +1)
    assert "Left" not in b


def test_stacked_is_automatic_above_two_axes_and_limited_to_four():
    assert not tp.use_stacked(1) and not tp.use_stacked(2)
    assert tp.use_stacked(3) and tp.use_stacked(4)
    with pytest.raises(ValueError):
        tp.key_bindings(5)


def test_manual_names_every_key_and_lists_the_top_row_first():
    manual = tp.key_manual(["x", "y", "z", "w"])
    keys = [k for k, _ in manual]
    assert keys[:4] == ["7 8 9 0", "u i o p", "j k l ;", "m , . /"]
    assert manual[0][1].startswith("w:")
    assert {"s", "Esc", "q"} <= set(keys)


# --- controller ---


def test_controller_applies_keys_to_the_right_axis():
    a = axes(3)
    c = tp.TweakController(a)
    c.handle_key(";")
    c.handle_key("i")
    c.handle_key("p")
    c.handle_key("7")
    assert a[0].value == 1 and a[1].step == 2 and a[1].value == 2 and a[2].value == -1
    assert c.handle_key("x") is None
    assert c.handle_key("q") == ("release",)
    c.handle_key("s")
    assert [x.value for x in a] == [0, 0, 0]
    c.handle_key("Escape")
    assert all(x.log[-1] == ("stop",) for x in a)


def test_controller_reports_errors_instead_of_raising():
    a = axes(1)
    a[0].move = lambda sign: 1 / 0
    c = tp.TweakController(a)
    messages = []
    c.notify = messages.append
    c.handle_key("Right")
    assert messages and "a0 move failed" in messages[0]


# --- soft axis on a real Tweak ---


class Changer:
    def is_alive(self):
        return False

    def wait(self):
        pass


class Adj:
    def __init__(self, name, value=0.0):
        self.name = name
        self.value = value
        self.sets = []

    def get_current_value(self):
        return self.value

    def set_target_value(self, value):
        self.sets.append(value)
        self.value = value
        return Changer()

    def reset_current_value_to(self, value):
        self.value = value


def test_soft_axis_steps_from_the_target_and_commands_only_its_adjustable():
    from eco.elements.adjustable import Tweak

    x, y = Adj("x"), Adj("y", 5.0)
    t = Tweak((x, 1.0), (y, 1.0))
    ax, ay = tp.axes_from_tweak(t)
    x.value = 0.4  # still moving: the next step goes from the target
    ax.move(+1)
    ax.move(+1)
    assert x.sets == [1.0, 2.0] and y.sets == []
    ax.scale_step(0.5)
    assert t.step_sizes == [0.5, 1.0]
    ax.move(-1)
    assert x.sets[-1] == 1.5
    ay.go(7.0)
    ax.reset_current_value_to(10.0)
    ax.move(+1)
    assert x.sets[-1] == 10.5  # relative to the redefined position
    ax.back_to_start()
    assert x.sets[-1] == 0.0 and y.sets == [7.0]


# --- front-end selection ---


def test_frontend(monkeypatch):
    import IPython

    class InProcessInteractiveShell:
        pass

    class ZMQInteractiveShell:
        pass

    monkeypatch.delenv("ECO_QTCONSOLE_KERNEL", raising=False)
    monkeypatch.setattr(IPython, "get_ipython", lambda: None)
    assert tp.frontend() == "terminal"
    monkeypatch.setattr(IPython, "get_ipython", lambda: InProcessInteractiveShell())
    assert tp.frontend() == "qt"
    monkeypatch.setattr(IPython, "get_ipython", lambda: ZMQInteractiveShell())
    assert tp.frontend() == "ipy"
    monkeypatch.setenv("ECO_QTCONSOLE_KERNEL", "1")
    assert tp.frontend() == "qt"


# --- Qt panel ---


@pytest.fixture
def qt_panel():
    pytest.importorskip("qtpy")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qtpy import QtWidgets

    from eco.widgets.tweak_panel_qt import TweakPanelQt

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    made = []

    def make(n, **kwargs):
        a = axes(n)
        panel = TweakPanelQt(tp.TweakController(a, **kwargs), poll_interval=0.05)
        made.append(panel)
        return panel, a

    yield make
    for panel in made:
        panel.close()
    app.processEvents()


def test_qt_keys_act_like_the_terminal(qt_panel):
    from qtpy import QtCore
    from qtpy.QtTest import QTest

    panel, a = qt_panel(1)
    assert panel.keypress_active() and not panel.manual.isHidden()
    QTest.keyClick(panel, QtCore.Qt.Key_Right)
    QTest.keyClick(panel, QtCore.Qt.Key_Up)
    QTest.keyClick(panel, QtCore.Qt.Key_Right)
    assert a[0].value == 3 and panel._step_edits[0].text() == "2"
    QTest.keyClick(panel, QtCore.Qt.Key_Q)
    assert not panel.keypress_active() and panel.manual.isHidden()
    QTest.keyClick(panel, QtCore.Qt.Key_Right)
    assert a[0].value == 3  # keypress control off


def test_qt_ctrl_arrows_and_stacked_keys(qt_panel):
    from qtpy import QtCore
    from qtpy.QtTest import QTest

    panel, a = qt_panel(2)
    QTest.keyClick(panel, QtCore.Qt.Key_Up, QtCore.Qt.ControlModifier)
    QTest.keyClick(panel, QtCore.Qt.Key_Up)
    assert a[1].step == 2 and a[1].value == 2 and a[0].value == 0

    panel, a = qt_panel(4)
    for key in (QtCore.Qt.Key_Slash, QtCore.Qt.Key_0, QtCore.Qt.Key_Semicolon):
        QTest.keyClick(panel, key)
    assert [x.value for x in a] == [1, 1, 0, 1]


def test_qt_buttons_and_fields(qt_panel):
    from qtpy import QtCore, QtWidgets
    from qtpy.QtTest import QTest

    panel, a = qt_panel(1)
    buttons = [b for b in panel.findChildren(QtWidgets.QPushButton) if b.text() == "▶"]
    assert buttons[0].focusPolicy() == QtCore.Qt.NoFocus
    assert "key: →" in buttons[0].toolTip()
    buttons[0].click()
    assert a[0].value == 1
    go = panel._go_edits[0]
    go.setText("4.5")
    QTest.keyClick(go, QtCore.Qt.Key_Return)
    assert a[0].log[-1] == ("go", 4.5)
    go.setText("nonsense")
    QTest.keyClick(go, QtCore.Qt.Key_Return)
    assert a[0].log[-1] == ("go", 4.5)
    step = panel._step_edits[0]
    step.setText("0.25")
    QTest.keyClick(step, QtCore.Qt.Key_Return)
    assert a[0].step == 0.25


def test_qt_value_labels_are_polled(qt_panel):
    from qtpy import QtWidgets

    panel, a = qt_panel(1)
    a[0].value = 1.25
    app = QtWidgets.QApplication.instance()
    for _ in range(50):
        app.processEvents()
        if panel._value_labels[0].text() == "1.25":
            break
        time.sleep(0.02)
    assert panel._value_labels[0].text() == "1.25"


# --- ipywidgets panel ---


def test_ipy_keys_and_buttons():
    pytest.importorskip("ipywidgets")
    from eco.widgets.tweak_panel_ipy import TweakPanelIpy, normalize_key_event

    assert normalize_key_event({"key": "ArrowUp", "ctrlKey": True}) == "Ctrl+Up"
    assert normalize_key_event({"key": "J"}) == "j"
    assert normalize_key_event({"key": "Shift"}) is None

    a = axes(3)
    panel = TweakPanelIpy(tp.TweakController(a), poll_interval=0.05)
    try:
        panel._on_key({"key": ";"})
        panel._on_key({"key": "i"})
        assert a[0].value == 1 and a[1].step == 2
        assert panel._step_inputs[1].value == 2
        panel._step_inputs[2].value = 0.5
        assert a[2].step == 0.5
        panel._on_key({"key": "q"})
        assert panel.keypress_box.value is False
        assert panel.key_area.layout.display == "none"
        panel._on_key({"key": ";"})
        assert a[0].value == 1
    finally:
        panel.close()


def test_ui_keyword_and_eco_no_x(monkeypatch):
    import IPython

    class ZMQInteractiveShell:
        pass

    monkeypatch.setattr(IPython, "get_ipython", lambda: ZMQInteractiveShell())
    monkeypatch.setenv("ECO_QTCONSOLE_KERNEL", "1")
    monkeypatch.delenv("ECO_NO_X", raising=False)
    assert tp.frontend() == "qt"
    with tp.forced_frontend("terminal"):
        assert tp.frontend() == "terminal"
        with tp.forced_frontend(None):  # None keeps the outer override
            assert tp.frontend() == "terminal"
    assert tp.frontend() == "qt"
    with pytest.raises(ValueError):
        with tp.forced_frontend("x11"):
            pass

    from eco.widgets.tweak_recorder import display_available

    monkeypatch.setenv("ECO_NO_X", "1")
    assert tp.frontend() == "terminal" and not display_available()
    with tp.forced_frontend("qt"):  # explicit ui= still wins
        assert tp.frontend() == "qt"
    monkeypatch.setenv("ECO_NO_X", "0")
    assert tp.frontend() == "qt"
    monkeypatch.delenv("ECO_QTCONSOLE_KERNEL")
    monkeypatch.setenv("ECO_NO_X", "1")
    assert tp.frontend() == "ipy"  # notebooks are not affected
