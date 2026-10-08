"""Detector recording along a tweak (eco.widgets.tweak_recorder) and its plots
in the Qt/ipywidgets tweak panels."""

import math
import os

import pytest

from eco.widgets import tweak_panel as tp
from eco.widgets.tweak_recorder import TweakRecorder, draw_tweak_plot, plot_rows


class Axis:
    def __init__(self, name, value=0.0, step=1.0):
        self.name = name
        self.value = value
        self.step = step
        self.start_value = value

    def get_value(self):
        return self.value

    def current_step(self):
        return self.step

    def move(self, sign):
        self.value += sign * self.step

    def scale_step(self, factor):
        self.step *= factor

    def set_step(self, step):
        self.step = step

    def go(self, value):
        self.value = value

    def back_to_start(self):
        self.value = self.start_value

    def reset_current_value_to(self, value):
        self.value = value

    def stop(self):
        pass


class Det:
    def __init__(self, name, values):
        self.name = name
        self.values = list(values)

    def get_current_value(self):
        return self.values.pop(0)


def test_values_are_averaged_per_plateau_and_moves_are_skipped():
    x = Axis("x")
    det = Det("diode", [1, 3, 99, 10, 20, 30])
    rec = TweakRecorder([x], det, settle_samples=2)
    rec.sample()  # first reading: not settled yet, detector not read
    rec.sample()  # settled -> 1
    rec.sample()  # -> 3
    x.value = 0.5  # moving
    rec.sample()  # position changed: not read
    x.value = 1.0
    rec.sample()  # moved again: not read
    rec.sample()  # settled at 1.0 -> 99
    rec.sample()  # -> 10
    pts = rec.points
    assert [p["positions"] for p in pts] == [[0.0], [1.0]]
    assert pts[0]["values"] == [2.0] and pts[0]["n_samples"] == 2
    assert pts[1]["values"] == [54.5]
    assert [p["step"] for p in pts] == [0, 1]


def test_jitter_within_the_settle_tolerance_stays_on_one_plateau():
    x = Axis("x", step=1.0)
    rec = TweakRecorder([x], Det("d", [1, 2, 3, 4]), settle_fraction=0.1)
    for v in (0.0, 0.03, -0.04, 0.02, 0.05):
        x.value = v
        rec.sample()
    assert len(rec.points) == 1 and rec.points[0]["n_samples"] == 4
    x.step = 0.1  # smaller step -> tighter tolerance: 0.05 is now a new spot
    x.value = 0.3
    rec.sample()
    rec.sample()
    assert len(rec.points) == 2


def test_failed_and_array_detector_reads():
    class Flaky:
        name = "flaky"

        def __init__(self):
            self.n = 0

        def get_current_value(self):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("timeout")
            return None if self.n == 2 else [1.0, 3.0]

    x = Axis("x")
    rec = TweakRecorder([x], [Flaky()], settle_samples=1)
    for _ in range(3):
        rec.sample()
    p = rec.points[0]
    assert p["n_samples"] == 3 and p["values"] == [2.0]  # array -> its mean


def test_multi_axis_points_and_arrays():
    x, y = Axis("x"), Axis("y", 5.0)
    rec = TweakRecorder([x, y], [Det("a", range(100)), Det("b", range(100))], settle_samples=1)
    rec.sample()
    y.value = 6.0
    rec.sample()
    steps, pos, val = rec.arrays()
    assert list(steps) == [0, 1]
    assert pos.tolist() == [[0.0, 5.0], [0.0, 6.0]]
    assert val.shape == (2, 2)


def test_dataframe():
    pytest.importorskip("pandas")
    x = Axis("x")
    rec = TweakRecorder([x], Det("d", [1.0, 2.0]), settle_samples=1)
    rec.sample()
    rec.sample()
    df = rec.to_dataframe()
    assert list(df.columns) == ["step", "time", "n_samples", "x", "d", "d_std"]
    assert df["d"].tolist() == [1.5]


def test_recorder_thread_starts_and_stops():
    import time

    x = Axis("x")
    rec = TweakRecorder([x], Det("d", [1.0] * 1000), interval=0.01).start()
    time.sleep(0.15)
    rec.stop()
    assert rec.points and rec.points[0]["n_samples"] > 3


# --- plots ---


def _figure():
    pytest.importorskip("matplotlib")
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure()
    FigureCanvasAgg(fig)
    return fig


def test_one_axis_plots_each_detector_against_the_position():
    fig = _figure()
    x = Axis("x")
    rec = TweakRecorder([x], [Det("a", range(9)), Det("b", range(9))], settle_samples=1)
    for v in (0.0, 1.0, 2.0):
        x.value = v
        rec.sample()
    draw_tweak_plot(fig, rec)
    axs = fig.axes
    assert plot_rows(rec) == 2 and len(axs) == 2
    assert [a.get_ylabel() for a in axs] == ["a", "b"]
    assert axs[-1].get_xlabel() == "x"
    assert list(axs[0].lines[0].get_xdata()) == [0.0, 1.0, 2.0]


def test_several_axes_stack_detectors_on_top_over_the_step_number():
    fig = _figure()
    axes = [Axis("x"), Axis("y"), Axis("z")]
    rec = TweakRecorder(axes, [Det("a", range(9))], settle_samples=1)
    for v in (0.0, 1.0):
        axes[1].value = v
        rec.sample()
    draw_tweak_plot(fig, rec)
    axs = fig.axes
    assert [a.get_ylabel() for a in axs] == ["a", "x", "y", "z"]
    assert axs[-1].get_xlabel() == "step"
    assert list(axs[2].lines[0].get_ydata()) == [0.0, 1.0]


def test_empty_recorder_draws_without_points():
    fig = _figure()
    draw_tweak_plot(fig, TweakRecorder([Axis("x")], Det("a", [])))
    assert len(fig.axes) == 1


# --- panels ---


def test_qt_panel_embeds_a_live_plot():
    pytest.importorskip("qtpy")
    pytest.importorskip("matplotlib")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qtpy import QtWidgets

    from eco.widgets.tweak_panel_qt import TweakPanelQt

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    x = Axis("x")
    rec = TweakRecorder([x], Det("d", [float(i) for i in range(10000)]), interval=0.01)
    panel = TweakPanelQt(tp.TweakController([x]), recorder=rec)
    try:
        assert panel.plot is not None and rec._thread is not None
        import time

        time.sleep(0.1)
        panel.plot.refresh()
        assert panel.plot._drawn_version == rec.version or rec.version > 0
        assert panel.plot.figure.axes[0].get_ylabel() == "d"
    finally:
        panel.close()
        app.processEvents()
    assert rec._stop_event.is_set()


def test_ipy_panel_renders_the_plot_as_png():
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from eco.widgets.tweak_panel_ipy import TweakPanelIpy

    x = Axis("x")
    rec = TweakRecorder([x], Det("d", [1.0, 2.0, 3.0]), settle_samples=1)
    rec.sample()
    panel = TweakPanelIpy(tp.TweakController([x]), recorder=rec, poll_interval=10)
    try:
        import time

        for _ in range(100):  # rendered by the panel's poll thread
            if len(panel.plot.value):
                break
            time.sleep(0.02)
        assert bytes(panel.plot.value[:4]) == b"\x89PNG"
    finally:
        panel.close()


def test_terminal_plot_text_has_the_panel_layout(monkeypatch):
    pytest.importorskip("uniplot")
    import os

    from eco.utilities import termplot
    from eco.widgets.tweak_recorder import terminal_plot_text

    monkeypatch.setattr(termplot, "terminal_size", lambda: os.terminal_size((80, 40)))
    x = Axis("mirror")
    rec = TweakRecorder([x], [Det("diode", range(9)), Det("cam", range(9))], settle_samples=1)
    rec.sample()
    text = terminal_plot_text(rec)
    assert "diode vs mirror" in text and "cam vs mirror" in text
    rec2 = TweakRecorder([Axis("x"), Axis("y")], Det("d", range(9)), settle_samples=1)
    rec2.sample()
    text = terminal_plot_text(rec2)
    titles = [l.strip() for l in text.split("\n") if l.strip() in ("d", "x", "y   (x: step)")]
    assert titles == ["d", "x", "y   (x: step)"]
    assert len(text.split("\n")) <= 40 - 12
