"""Peak/step analysis of a running scan (eco/acquisition/peak_analysis.py), and
its use by CounterValue. The bs-stream counter's side is in
test_bs_counter_mockup.py, next to the synthetic stream it needs.

Needs escape-fel >= 0.2.14 (escape._peak_analysis.find_peak); skipped without.
"""

import json
import math
import types

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pytest

pytest.importorskip("escape._peak_analysis")

from escape import ArrayTimestamps

from eco.acquisition import peak_analysis as pa


def gaussian(x, center=0.2, sigma=0.25, height=5.0, offset=0.3, gradient=0.0):
    x = np.asarray(x, dtype=float)
    return offset + gradient * x + height * np.exp(-((x - center) ** 2) / (2 * sigma**2))


class FakeScan:
    """What `attach` reads off a StepScan: scan_info, counter_scratch, and
    adjustables for the unit (no `_append`, so no scan.peak_analysis)."""

    def __init__(self, names=("motor",), grid_specs=None, unit="mm"):
        self.scan_info = {
            "scan_parameters": {"name": list(names), "grid_specs": grid_specs},
            "scan_values": [],
        }
        self.counter_state = {}
        self.adjustables = [types.SimpleNamespace(unit=unit)]

    def counter_scratch(self, counter_name):
        return self.counter_state.setdefault(counter_name, {})


# ---------------------------------------------------------------- analyze_trace
def test_peak_fields_against_the_truth():
    # wide enough that the edge points, which fix the baseline, are off the peak
    x = np.linspace(-1.5, 1.5, 25)
    y = gaussian(x, center=0.2, sigma=0.25, height=5.0, offset=0.3, gradient=0.5)
    r = pa.analyze_trace(x, y, parameter="motor")

    assert r["valid"] and r["is_peak"]
    assert r["center"] == pytest.approx(0.2, abs=0.05)
    assert r["fwhm"] == pytest.approx(2.3548 * 0.25, rel=0.1)
    assert r["peak_x"] == pytest.approx(0.2, abs=0.07)
    # baseline: a linear background under the peak, read at the center
    assert r["gradient"] == pytest.approx(0.5, abs=0.1)
    assert r["offset"] == pytest.approx(0.3 + 0.5 * 0.2, abs=0.2)
    assert r["height"] == pytest.approx(5.0, rel=0.1)
    assert r["n_points"] == 25 and r["parameter"] == "motor"
    assert (r["n_bg"], r["bg_model"], r["mode"]) == (3, "linear", "auto")
    assert np.isinf(r["snr"]) or r["snr"] > 10  # noise-free, or at least clearly there


def test_dip_has_negative_height():
    x = np.linspace(-1, 1, 17)
    r = pa.analyze_trace(x, 10 - gaussian(x))
    assert r["valid"] and r["is_peak"]
    assert r["height"] < 0


def test_step_fields_against_the_truth():
    from math import erf

    rng = np.random.default_rng(2)
    x = np.linspace(-1, 1, 25)
    y = np.array([1.0 + 4.0 * 0.5 * (1 + erf((xi - 0.1) / (math.sqrt(2) * 0.12))) for xi in x])
    r = pa.analyze_trace(x, y + rng.normal(0, 0.05, x.size))

    assert r["valid"] and not r["is_peak"]
    assert r["center"] == pytest.approx(0.1, abs=0.05)
    assert r["offset"] == pytest.approx(1.0, abs=0.1)  # level before
    assert r["height"] == pytest.approx(4.0, abs=0.2)  # level after - before
    assert np.isnan(r["gradient"])
    assert r["peak_x"] == pytest.approx(r["center"])  # a step has no extremum
    assert r["snr"] > 20


@pytest.mark.parametrize(
    "x, y",
    [
        (np.arange(5.0), np.array([0, 1, 3, 1, 0.0])),  # fewer than 2 * n_bg + 3
        (np.arange(20.0), np.ones(20)),  # nothing varies
        (np.arange(20.0), np.full(20, np.nan)),
        ([], []),
    ],
)
def test_nothing_to_find_is_an_empty_result_not_an_error(x, y):
    r = pa.analyze_trace(x, y, parameter="motor")
    assert r["valid"] is False and r["is_peak"] is False
    assert all(np.isnan(r[k]) for k in ("center", "fwhm", "height", "offset", "snr"))
    assert r["parameter"] == "motor"


def test_pure_scatter_reads_a_low_snr():
    x = np.linspace(-1, 1, 17)
    found = 0
    for seed in range(40):
        r = pa.analyze_trace(x, np.random.default_rng(seed).normal(0, 1.0, 17))
        if r["valid"]:  # find_peak finds *something* in a good share of them...
            found += 1
            assert r["snr"] < 3  # ...which the S/N is there to call scatter
    assert found >= 3


def test_unusable_input_is_an_empty_result_too():
    assert pa.analyze_trace(["a", "b"], [1, 2])["valid"] is False


def test_n_points_counts_only_finite_steps():
    x = np.linspace(-1, 1, 17)
    y = gaussian(x)
    y[4] = np.nan
    r = pa.analyze_trace(x, y)
    assert r["valid"] and r["n_points"] == 16


def test_settings_reach_find_peak():
    x = np.linspace(-1, 1, 21)
    y = np.where(x > 0, 4.0, 1.0)  # a step, but...
    forced = pa.analyze_trace(x, y, pa.parse_settings({"mode": "peak"}))
    auto = pa.analyze_trace(x, y)
    assert auto["is_peak"] is False
    assert forced["mode"] == "peak"
    fixed = pa.analyze_trace(x, gaussian(x), pa.parse_settings({"fixed_offset": 0.3}))
    assert fixed["bg_model"] == "fixed"
    assert fixed["offset"] == pytest.approx(0.3)


# ---------------------------------------------------------------- settings
def test_parse_settings():
    assert pa.parse_settings(False) is None and pa.parse_settings(None) is None
    assert pa.parse_settings(True) == pa.DEFAULT_SETTINGS
    assert pa.parse_settings({"n_bg": 2})["n_bg"] == 2
    assert pa.parse_settings({"n_bg": 2})["mode"] == "auto"
    for bad in ({"n_bgg": 2}, {"mode": "dip"}, {"bg_model": "cubic"}, {"n_bg": 0}):
        with pytest.raises((TypeError, ValueError)):
            pa.parse_settings(bad)
    with pytest.raises(TypeError):
        pa.parse_settings("yes")


# ---------------------------------------------------------------- step_medians
def _monitor_array():
    """A monitor like the real ones: seeded before the first step, with
    samples exactly on interval borders."""
    rng = np.random.default_rng(0)
    intervals, ts, vals = [], [99.0], [0.1]
    t = 100.0
    for k in range(8):
        t0 = t
        for _ in range(7):
            t += 0.5
            ts.append(t)
            vals.append(float(k) + rng.normal(0, 0.1))
        intervals.append((t0, t))
        t += 0.4
    ts.insert(1, 100.0)  # exactly at step 0's start
    vals.insert(1, 9.0)
    return (
        np.asarray(ts),
        np.asarray(vals),
        intervals,
        ArrayTimestamps(
            data=np.asarray(vals),
            timestamps=np.asarray(ts),
            timestamp_intervals=intervals,
            parameter={"motor": {"values": list(range(8))}},
            name="mon",
        ),
    )


def test_step_medians_are_the_numbers_scan_plot_draws():
    ts, vals, intervals, arr = _monitor_array()
    expected = [np.median(arr.scan[i].data) for i in range(len(intervals))]
    assert pa.step_medians(ts, vals, intervals) == pytest.approx(expected)


def test_step_medians_without_a_seed_sample_and_for_empty_steps():
    ts = np.array([10.5, 11.0, 12.5])
    vals = np.array([1.0, 3.0, 5.0])
    out = pa.step_medians(ts, vals, [(10.0, 11.0), (11.5, 12.0), (12.0, 13.0)])
    assert out[0] == 2.0  # no sample before the first step: take what is there
    assert out[1] == 3.0  # nothing new: the value the monitor is holding
    assert out[2] == 4.0
    assert np.isnan(pa.step_medians(ts, vals, [(0.0, 1.0)])[0])  # nothing at all yet


def test_step_medians_ignore_waveforms_and_nans():
    ts = np.arange(6.0)
    assert np.isnan(pa.step_medians(ts, np.ones((6, 3)), [(0.0, 5.0)])).all()
    vals = np.array([1.0, np.nan, 3.0, np.nan, 5.0, 7.0])
    assert pa.step_medians(ts, vals, [(0.0, 5.0)])[0] == 4.0


# ---------------------------------------------------------------- json / summary
def test_json_clean_and_summary():
    r = pa.analyze_trace(np.linspace(-1, 1, 17), gaussian(np.linspace(-1, 1, 17)), parameter="motor")
    r["gradient"] = float("nan")
    clean = pa.json_clean(r)
    json.dumps(clean, allow_nan=False)  # strict JSON
    assert clean["gradient"] is None and clean["valid"] is True
    assert pa.json_clean({"a": np.float64("inf"), "b": np.int64(3), "c": np.bool_(True)}) == {
        "a": None,
        "b": 3,
        "c": True,
    }

    line = pa.format_summary(r, unit="mm")
    assert line.startswith("Peak analysis: peak at motor = ") and " mm" in line and "FWHM" in line
    assert pa.format_summary(pa.empty_result()) == ""
    step = dict(r, is_peak=False)
    assert "step at motor" in pa.format_summary(step)


# ---------------------------------------------------------------- attach
def test_attach_gives_one_analysis_per_scan_and_updates_scan_info():
    scan = FakeScan()
    a = pa.attach(scan, "ctr", pa.DEFAULT_SETTINGS)
    assert a is not None and a.parameter == "motor" and a.unit == "mm"
    assert pa.attach(scan, "ctr", pa.DEFAULT_SETTINGS) is a
    assert pa.get_attached(scan, "ctr") is a

    x = np.linspace(-1, 1, 17)
    a.update(x, gaussian(x))
    assert a.values["valid"] and "Peak analysis" in a.summary()
    json.dumps(scan.scan_info["peak_analysis"], allow_nan=False)
    assert scan.scan_info["peak_analysis"]["center"] == pytest.approx(0.2, abs=0.05)
    assert a.get_values()["gradient"] is not None  # a peak has one
    assert pa.analyze_trace(x, np.where(x > 0, 4.0, 1.0))["is_peak"] is False


@pytest.mark.parametrize(
    "scan, n_channels, settings",
    [
        (FakeScan(names=("a", "b")), 1, pa.DEFAULT_SETTINGS),  # several adjustables
        (FakeScan(grid_specs={"shape": (3, 4)}), 1, pa.DEFAULT_SETTINGS),  # grid
        (FakeScan(), 2, pa.DEFAULT_SETTINGS),  # two channels in one counter
        (FakeScan(), 1, None),  # switched off
        (types.SimpleNamespace(), 1, pa.DEFAULT_SETTINGS),  # not a StepScan at all
    ],
)
def test_attach_leaves_everything_else_alone(scan, n_channels, settings):
    assert pa.attach(scan, "ctr", settings, n_channels=n_channels) is None
    assert pa.get_attached(scan, "ctr") is None
    assert "peak_analysis" not in getattr(scan, "scan_info", {})


def test_a_second_analysing_counter_does_not_take_over(capsys):
    class Exposed(FakeScan):
        def _append(self, factory, *args, name=None, **kwargs):  # what Assembly does
            setattr(self, name, factory(*args, name=name))

    scan = Exposed()
    first = pa.attach(scan, "first", pa.DEFAULT_SETTINGS)
    assert first is not None and hasattr(scan, "peak_analysis")
    assert pa.attach(scan, "second", pa.DEFAULT_SETTINGS) is None
    assert "already belongs to another counter" in capsys.readouterr().out
    assert pa.get_attached(scan, "first") is first
    assert pa.attach(scan, "first", pa.DEFAULT_SETTINGS) is first


# ---------------------------------------------------------------- CounterValue, real scans
def _scan_with_gaussian(tmp_path, **counter_kwargs):
    """A real StepScan through Scans.ascan: a DummyAdjustable, a Gaussian
    detector watched by a monitor, 17 short steps."""
    from eco.acquisition.counters import CounterValue
    from eco.acquisition.scan import Scans
    from eco.elements.adjustable import DummyAdjustable
    from eco.elements.detector import DetectorGet

    adj = DummyAdjustable(name="motor")
    adj.unit = types.SimpleNamespace(get_current_value=lambda: "mm")
    rng = np.random.default_rng(3)
    det = DetectorGet(
        lambda: float(gaussian(adj.get_current_value()) + rng.normal(0, 0.03)),
        monitor_frequency=50,
        name="sig",
    )
    counter = CounterValue(det, name="sig", **counter_kwargs)
    scans = Scans(default_counters=[counter])
    scan = scans.ascan(
        adj, -1, 1, 16, 0.3, description="peak test", return_at_end=False,
        directory=str(tmp_path), filename="peak_test",
    )
    return scans, scan, counter


def test_counter_value_scan_has_peak_analysis(tmp_path, capsys):
    scans, scan, counter = _scan_with_gaussian(tmp_path)
    pa_ = scan.peak_analysis

    # a DetectorObject with one sub-detector per field, in scan units
    assert pa_.valid() is True and pa_.is_peak() is True
    assert pa_.center() == pytest.approx(0.2, abs=0.06)
    assert pa_.fwhm() == pytest.approx(2.3548 * 0.25, rel=0.15)
    assert pa_.height() == pytest.approx(5.0, rel=0.1)
    assert pa_.n_points() == 17 and pa_.parameter() == "motor"
    assert pa_.snr() > 15  # clean data; see the snr note in peak_analysis
    # still reachable from the Scans container, and in the scan's display
    assert scans.acquiring_scan.peak_analysis.center() == pa_.center()
    assert "peak_analysis.center" in scan.get_display_str(tablefmt="plain")

    # a copy in scan_info (written with the scan, and sent along by Daq)
    info = scan.scan_info["peak_analysis"]
    json.dumps(info, allow_nan=False)
    assert info["center"] == pytest.approx(pa_.center())

    # the one-line result, and a stored dataset next to it
    out = capsys.readouterr().out
    assert "Peak analysis: peak at motor = " in out and " mm" in out
    assert (tmp_path / "peak_test.esc.h5").exists()


def test_counter_value_analysis_follows_the_scan_step_by_step(tmp_path):
    from eco.acquisition.counters import CounterValue

    counter = CounterValue(peak_analysis=True)
    names = [f.__name__ for f in counter.callbacks_end_step]
    # the analysis must not depend on the figure, so it comes before plot_arrays
    assert names.index("update_peak_analysis") < names.index("plot_arrays")
    assert "finish_peak_analysis" in [f.__name__ for f in counter.callbacks_end_scan]


def test_counter_value_peak_analysis_off(tmp_path):
    scans, scan, counter = _scan_with_gaussian(tmp_path, peak_analysis=False)
    assert "peak_analysis" not in vars(scan)
    assert "peak_analysis" not in scan.scan_info
    assert (tmp_path / "peak_test.esc.h5").exists()  # the rest is as before


def test_counter_value_peak_analysis_settings_are_validated():
    from eco.acquisition.counters import CounterValue

    with pytest.raises(TypeError):
        CounterValue(peak_analysis={"nope": 1})
    assert CounterValue(peak_analysis={"n_bg": 2})._peak_settings["n_bg"] == 2
