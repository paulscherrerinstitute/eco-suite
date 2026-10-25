"""eco.detector.stream_plots: named, refresh-in-place windows for escape
Stream live plots. Runs against escape.stream's synthetic local test stream
and an offscreen Qt backend -- no beamline/dispatcher/display needed."""

import os
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("escape.stream")
matplotlib = pytest.importorskip("matplotlib")
try:
    matplotlib.use("QtAgg", force=True)
except Exception:  # no Qt binding available
    pytest.skip("needs a Qt matplotlib backend", allow_module_level=True)
import matplotlib.pyplot as plt

from escape.stream.escape_stream import EventWorker, Stream, TestStream
from escape.stream.es_wrappers import LocalEventHandler

from eco.detector import stream_plots

PORT = 9897


@pytest.fixture()
def ew():
    ts = TestStream(port=PORT, interval=0.005)
    ts.start()
    time.sleep(0.5)
    worker = EventWorker(LocalEventHandler(host="localhost", port=PORT), make_default=False)
    stream_plots.install_stream_plot_windows()
    yield worker
    plt.close("all")
    stream_plots.uninstall_stream_plot_windows()
    ts.stop()


def _names():
    return sorted(plt.get_figlabels())


def test_window_is_named_after_the_data(ew):
    s = Stream("i0", ew)
    p = s.plot_hist()
    assert _names() == ["hist: i0"]
    assert p.fig.canvas.manager.get_window_title() == "hist: i0"
    assert len(s) >= 2  # data really flows into it


def test_same_name_refreshes_the_same_window_in_place(ew):
    s = Stream("i0", ew)
    p1 = s.plot_hist()
    fig = p1.fig
    p2 = s.plot_hist(n_bins=20)

    assert p2 is not p1
    assert p2.fig is fig  # same window, not closed and reopened
    assert _names() == ["hist: i0"]
    assert len(fig.axes) == 1  # old axes cleared, not stacked
    assert not p1.isUpdating and p2.isUpdating  # old live plot stopped, new one running
    assert s._is_accumulating()  # the reopen must not switch the stream off


def test_closing_the_window_still_stops_accumulation_then_replot_opens_a_new_one(ew):
    s = Stream("i0", ew)
    p = s.plot_hist()
    plt.close(p.fig)
    assert not s._is_accumulating()
    s.plot_hist()
    assert _names() == ["hist: i0"] and s._is_accumulating()


def test_different_kinds_and_streams_get_separate_windows(ew):
    i0, i = Stream("i0", ew), Stream("i", ew)
    i0.plot_hist()
    i0.plot_med()
    i.plot_hist()
    i0.plot_corr(i)
    assert _names() == ["corr: i0 vs i", "hist: i", "hist: i0", "median: i0"]


def test_default_x_of_plot_corr_is_part_of_the_name(ew):
    s = Stream("i0", ew)
    s.plot_corr()
    s.plot_corr(default_x="pulse_id")
    assert _names() == ["corr: i0 vs lab_time", "corr: i0 vs pulse_id"]


def test_eco_alias_gives_the_name_and_channel_the_disambiguation(ew):
    # eco's DetectorBsStream names every stream "<short name>_stream", so two
    # monitors' ".intensity" streams only differ by alias path / channel.
    a, b = Stream("i0", ew), Stream("i", ew)  # channels the synthetic sender really publishes
    a.name = b.name = "intensity_stream"
    a.alias = SimpleNamespace(get_full_name=lambda: "mon_mono.intensity.stream")
    b.alias = SimpleNamespace(get_full_name=lambda: "mon_opt.intensity.stream")
    a.plot_hist()
    b.plot_hist()
    assert _names() == [
        "hist: mon_mono.intensity.stream [i0]",
        "hist: mon_opt.intensity.stream [i]",
    ]


def test_foreign_figure_with_the_same_label_is_never_reused(ew):
    mine = plt.figure("hist: i0")
    mine.add_subplot().plot([1, 2, 3])
    s = Stream("i0", ew)
    p = s.plot_hist()
    assert p.fig is not mine
    assert _names() == ["hist: i0", "hist: i0 (2)"]
    assert len(mine.axes[0].lines) == 1  # untouched


def test_explicit_axes_is_left_to_escape(ew):
    fig, ax = plt.subplots()
    n = len(plt.get_figlabels())
    p = Stream("i0", ew).plot_hist(axes=ax)
    assert p.fig is fig and len(plt.get_figlabels()) == n


def test_failed_plot_does_not_leave_an_empty_window(ew):
    s = Stream("nonexistent_channel_xyz", ew)  # the local sender never sends it
    with pytest.raises(TimeoutError):
        s.plot_hist(timeout=0.3)
    assert _names() == []


def test_disabled_switch_restores_escape_behaviour(ew, monkeypatch):
    monkeypatch.setattr(stream_plots, "ENABLED", False)
    Stream("i0", ew).plot_hist()
    assert len(_names()) == 1 and not _names()[0].startswith("hist:")  # anonymous escape figure
