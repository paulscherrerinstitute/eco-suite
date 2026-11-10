"""Text charts in the terminal (eco.utilities.text_charts): the history plot
and the live strip chart drawn as text, where they go (plot= / ECO_PLOT /
no display), the keys' effect on what is shown, and the archiver table's
time axis."""

import os
import time

import numpy as np
import pandas as pd
import pytest

from eco.utilities import text_charts as tc


@pytest.fixture
def no_display(monkeypatch):
    monkeypatch.setattr(tc, "display_available", lambda: False)
    monkeypatch.delenv("ECO_PLOT", raising=False)


@pytest.fixture
def display(monkeypatch):
    monkeypatch.setattr(tc, "display_available", lambda: True)
    monkeypatch.delenv("ECO_PLOT", raising=False)


def test_where_a_chart_goes(display, monkeypatch, capsys):
    assert tc.plot_mode("auto") == "window" and tc.plot_mode(True) == "window"
    assert tc.plot_mode("text") == "text"
    assert tc.plot_mode(False) == "none" and tc.plot_mode(None) == "none"
    monkeypatch.setenv("ECO_PLOT", "text")
    assert tc.plot_mode("auto") == "text" and tc.plot_mode("window") == "window"
    monkeypatch.setattr(tc, "display_available", lambda: False)
    monkeypatch.delenv("ECO_PLOT")
    assert tc.plot_mode("auto") == "text"
    assert tc.plot_mode("window") == "text" and "drawing as text" in capsys.readouterr().out
    with pytest.raises(ValueError):
        tc.plot_mode("sideways")


def test_render_panels_with_a_shared_time_axis(monkeypatch):
    monkeypatch.setattr(
        "eco.utilities.termplot.terminal_size", lambda: os.terminal_size((90, 30))
    )
    t0 = 1_800_000_000.0
    series = [("a", t0 + np.arange(10.0), np.arange(10.0)), ("b", [], [])]
    text = tc.render(series, t0, t0 + 10, footer="foot")
    assert "a   9" in text and "b   (no data in this span)" in text
    assert text.rstrip().endswith("foot")
    assert len(text.splitlines()) <= 30


def test_steps_hold_each_value_until_the_next():
    t, v = tc._steps([0.0, 1.0, 2.0], [5.0, 6.0, 7.0], t_hold=3.0)
    assert list(t) == [0, 1, 1, 2, 2, 3] and list(v) == [5, 5, 6, 6, 7, 7]
    t, v = tc._visible([0, 1, 2, 3, 4], [0, 1, 2, 3, 4], 1.5, 3.0)
    assert list(t) == [1, 2, 3]  # the sample before the start included


def test_keys_history():
    view = tc.TimeView(100, t_end=1000)
    view.handle("zoom_in", 0)
    assert view.window == 50 and view.span(0) == (925, 975)  # around the middle
    view.handle("left", 0)
    assert view.span(0) == (912.5, 962.5)
    view.handle("up", 0)
    assert view.y_zoom == 0.5
    view.handle("autoscale", 0)
    assert view.y_zoom == 1
    view.handle("follow", 0)  # history: everything again
    assert (view.window, view.t_end) == (100, 1000)


def test_keys_live():
    view = tc.TimeView(60, live=True)
    now = 1000.0
    assert view.span(now) == (940, 1000) and view.following
    view.handle("left", now)
    assert not view.following and view.span(now) == (925, 985)
    view.handle("right", now)
    assert view.following  # back at now: follows again
    view.handle("pause", now)
    assert view.paused and not view.following and view.span(now + 30)[1] == now
    view.handle("pause", now)
    assert view.following
    view.handle("zoom_out", now)
    assert view.window == 120 and view.span(now) == (880, 1000)


def test_history_series_reads_the_archiver_table():
    class State:  # like datahub.Enum, what the archiver returns for enum channels
        def __init__(self, id, desc):
            self.id, self.desc = id, desc

    for unit in ("s", "us", "ns"):
        index = pd.DatetimeIndex(
            pd.date_range("2026-11-10 08:00", periods=3, freq="1min", tz="UTC")
        ).as_unit(unit)
        data = pd.DataFrame({"A": [1.0, np.nan, 3.0], "B": [State(1, "OPEN")] * 3}, index=index)
        series = tc.history_series(data, ["A", "B"], ["a", "b"])
        (label, t, v), (_, _, vb) = series
        assert label == "a" and list(v) == [1, 3] and list(vb) == [1, 1, 1]
        assert t[0] == pd.Timestamp("2026-11-10 08:00", tz="UTC").timestamp()


def test_history_plot_as_text(no_display, capsys):
    from eco.dbase.archiver import _plot_dataframe

    index = pd.date_range("2026-11-10 08:00", periods=60, freq="1min", tz="UTC")
    data = pd.DataFrame({"A": np.arange(60.0)}, index=index)
    _plot_dataframe(data, ["A"], ["mirror"], plot=True)  # no display: text
    out = capsys.readouterr().out
    assert "mirror   59" in out and "history" in out and "q quit" in out
    _plot_dataframe(data, ["A"], ["mirror"], plot=False)
    assert capsys.readouterr().out == ""


def test_live_strip_chart_as_text(no_display, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from eco.devices_general.simulated_signals import SimulatedSignals
    from eco.utilities.strip_plot import strip_plot

    sim = SimulatedSignals(axis="ideal")
    data = strip_plot(sim.peak.signal, window=1, poll_interval=0.05)
    out = capsys.readouterr().out
    assert "sim.peak.signal" in out and "strip chart" in out
    times, values = data["sim.peak.signal"]
    assert len(times) >= 5 and len(times) == len(values)
