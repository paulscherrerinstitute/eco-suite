"""Scans.adaptive_scan (eco.acquisition.adaptive) against the simulated
signals: points stay on the grid, steering works, limits and budgets end
the scan, the step count follows, 2-D grids stay consistent, motion is
learned. Needs escape-fel with escape.adaptive_scan (>= 0.2.17)."""

import time

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pytest

pytest.importorskip("escape.adaptive_scan")

from eco.acquisition import adaptive
from eco.acquisition.scan import Scans
from eco.devices_general.simulated_signals import SimulatedSignals


@pytest.fixture
def sim(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the monitor counter stores its data here
    return SimulatedSignals(axis="ideal")


def _run(*specs, **kwargs):
    kwargs = dict(dict(N_pulses=0.05, return_at_end=False, plot=False), **kwargs)
    return Scans().adaptive_scan(*specs, **kwargs)


def test_parse_duration():
    assert adaptive.parse_duration(30) == 30
    assert adaptive.parse_duration("90s") == 90
    assert adaptive.parse_duration("10min") == 600
    assert adaptive.parse_duration("1.5h") == 5400
    with pytest.raises(ValueError):
        adaptive.parse_duration("soon")


def test_needs_a_limit_and_feedback(sim):
    with pytest.raises(ValueError, match="budget"):
        _run([sim.peak.x, -1.0, 2.0, 0.05], feedback=sim.peak.signal)
    class DaqLike:  # a counter, but no live values
        name = "daq_like"

        def acquire(self, scan=None, Npulses=None, **kwargs):
            raise AssertionError("not reached")

    with pytest.raises(ValueError, match="feedback"):
        _run([sim.peak.x, -1.0, 2.0, 0.05], max_points=5, counters=[DaqLike()])


def test_1d_points_on_grid_and_near_the_peak(sim):
    s = _run([sim.peak.x, -2.0, 3.0, 0.02], feedback=sim.peak.signal, max_points=30)
    x = np.array([v[0] for v in s._values_done])
    assert len(x) == 30 and s.number_of_steps() == 30
    grid = np.arange(-2.0, 3.0 + 0.02, 0.02)
    assert np.all(np.min(np.abs(x[:, None] - grid[None, :]), axis=1) < 1e-9)
    # after the 15-point survey most points go to the peak (0.7, fwhm 0.4)
    # and its flanks; evenly spread they would be ~5 of 15 in this range
    assert ((x[15:] > -0.1) & (x[15:] < 1.5)).sum() >= 9  # ~5 if spread evenly; it also explores
    assert s.adaptive.strategy.n_points == 30


def test_uneven_grid(sim):
    grid = [-2.0, -1.0, 0.0, 0.4, 0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.9, 1.0, 2.0]
    s = _run([sim.peak.x, grid], feedback=sim.peak.signal, max_points=12)
    assert {v[0] for v in s._values_done} <= set(grid)


def test_time_budget_ends_the_scan(sim):
    t0 = time.time()
    s = _run([sim.peak.x, -2.0, 3.0, 0.02], feedback=sim.peak.signal, budget=3)
    assert time.time() - t0 < 6
    assert s.next_step > 5


def test_expected_steps_follow_the_budget(sim):
    seen = []

    def record(scan, **kwargs):
        seen.append(scan.number_of_steps())

    s = Scans(callbacks_end_step=[record]).adaptive_scan(
        [sim.peak.x, -2.0, 3.0, 0.02], N_pulses=0.05, feedback=sim.peak.signal,
        max_points=25, batch=3, return_at_end=False, plot=False,
    )
    assert s.next_step == 25
    assert seen[-1] == 25 and all(n == 25 for n in seen)


def test_2d_stays_on_grid_with_growing_index_plan(sim):
    s = _run(
        [sim.peak2d.x, -2.0, 2.0, 0.2], [sim.peak2d.y, -2.0, 2.0, 0.2],
        feedback=sim.peak2d.signal, strategy="peak2d", max_points=40, batch=5,
    )
    gs = s.grid_specs.current_value
    assert gs["shape"] == [21, 21]
    assert len(gs["index_plan"]) == s.next_step == 40
    assert len(s.scan_info["scan_parameters"]["grid_specs"]["index_plan"]) == 40
    for step, values in zip(s.scan_info["scan_step_info"], s._values_done):
        i, j = step["grid_index"]
        assert (gs["positions"][0][i], gs["positions"][1][j]) == pytest.approx(values)


def test_2d_free_mesh_leaves_the_grid(sim):
    s = _run(
        [sim.peak2d.x, -2.0, 2.0, 0.2], [sim.peak2d.y, -2.0, 2.0, 0.2],
        feedback=sim.peak2d.signal, strategy="peak2d", max_points=40, batch=5, mesh="free",
    )
    assert s.grid_specs.current_value is None
    assert "grid_index" not in s.scan_info["scan_step_info"][-1]
    mesh = s.scan_info["scan_parameters"]["mesh_specs"]
    assert mesh["kind"] == "path" and mesh["resolution"] == pytest.approx([0.2, 0.2])
    assert len(s._values_done) == 40
    xy = np.array(s._values_done)
    assert np.all((xy >= -2.0 - 1e-9) & (xy <= 2.0 + 1e-9))
    # planned points are on the 4x finer lattice, not only on the 0.2 grid
    off_grid = np.abs(xy / 0.2 - np.round(xy / 0.2)) > 1e-6
    assert off_grid.any()
    assert np.allclose(xy / 0.05, np.round(xy / 0.05))


def test_free_candidates():
    x, y = adaptive.free_candidates([np.arange(0.0, 1.01, 0.1), [0.0, 0.5, 2.0]])
    assert len(x) == 41 and x[1] == pytest.approx(0.025)
    assert y[0] == 0.0 and y[-1] == 2.0 and y[1] == pytest.approx(0.125)
    assert adaptive.axis_steps([[0, 0.5, 2.0], [3.0]]) == [0.5, 0.0]


def test_mesh_must_be_grid_or_free(sim):
    with pytest.raises(ValueError, match="mesh"):
        _run([sim.peak.x, -1.0, 2.0, 0.05], feedback=sim.peak.signal, max_points=5, mesh="loose")


def test_2d_avoid_revisits(sim):
    strategy = adaptive.build_strategy("uniform", [np.arange(3.0), np.arange(3.0)], 0.05)
    driver = adaptive.AdaptiveDriver(
        strategy, [sim.peak2d.x, sim.peak2d.y], [np.arange(3.0), np.arange(3.0)],
        lambda scan: (1.0, 0.1, 5), 0.05, max_points=40, avoid_revisits=True,
    )
    assert driver.grid_index([1.9, 0.2]) == [2, 0]


def test_callable_feedback_and_learned_motion(sim, capsys):
    sim.peak.x.apply_preset("piezo")
    s = _run(
        [sim.peak.x, -2.0, 3.0, 0.02], max_points=20,
        feedback=lambda scan: (float(sim.peak.truth(scan.values_current_step[0])), 0.01),
    )
    assert s.adaptive.strategy.n_points == 20
    out = capsys.readouterr().out
    assert "adaptive: motion model for x" in out and "Axis(speed=" in out
    # no counter that acquires: the seconds per N_pulses unit are ~0 here
    assert s.adaptive.sec_per_unit is not None


def test_2d_motion_model_despite_readback_noise(sim, capsys):
    # stepper: readback noise, and in the survey x never moves alone
    sim.peak2d.x.apply_preset("stepper")
    sim.peak2d.y.apply_preset("stepper")
    for axis in (sim.peak2d.x, sim.peak2d.y):
        axis.speed.set_target_value(20.0).wait()
        axis.settle_time.set_target_value(0.0).wait()
    s = _run(
        [sim.peak2d.x, -2.0, 2.0, 0.5], [sim.peak2d.y, -2.0, 2.0, 0.5],
        feedback=sim.peak2d.signal, strategy="peak2d", max_points=27,
    )
    learner = s.adaptive.learner
    assert len(learner.moves[1]) >= 3  # the survey moves y alone along each row
    assert all(a is not None for a in learner.axes)
    # the simulated 0.15 s plus eco's own cost per move, which is what the
    # learner measures (~0.1 s more on a console than on a workstation)
    assert 0.07 < learner.axes[1].overhead < 0.5
    assert "overhead from the other axes" in capsys.readouterr().out


def test_queue_test_build_does_not_run(sim):
    s = _run(
        [sim.peak.x, -2.0, 3.0, 0.02], feedback=sim.peak.signal, max_points=10,
        start_immediately=False,
    )
    assert s.next_step == 0 and len(s._values_todo) == 10


def test_feedback_detector_joins_the_counters(sim):
    # the feedback detector and another monitored one: one CounterValue
    s = _run(
        [sim.peak.x, -2.0, 3.0, 0.02], feedback=sim.peak.signal, max_points=16,
        counters=[sim.step.signal],
    )
    ctr, driver = s.counters
    assert driver is s.adaptive
    assert set(ctr.get_monitorable_names()) == {
        sim.peak.signal.alias.get_full_name(), sim.step.signal.alias.get_full_name()
    }
    assert s.adaptive.strategy.n_points == 16


def test_two_axes_default_to_peak2d_and_refuse_1d_strategies(sim):
    s = _run([sim.peak2d.x, -2.0, 2.0, 0.5], [sim.peak2d.y, -2.0, 2.0, 0.5],
             feedback=sim.peak2d.signal, max_points=27)
    assert type(s.adaptive.strategy).__name__ == "PeakScan2D"
    with pytest.raises(ValueError, match="with 2 axes use one of"):
        _run([sim.peak2d.x, -2.0, 2.0, 0.5], [sim.peak2d.y, -2.0, 2.0, 0.5],
             feedback=sim.peak2d.signal, max_points=27, strategy="modelfree")
    with pytest.raises(ValueError, match="with 1 axes use one of"):
        _run([sim.peak.x, -2.0, 3.0, 0.1], feedback=sim.peak.signal, max_points=10,
             strategy="peak2d")


def test_motion_model_is_reported_only_when_it_changes(sim, capsys):
    s = _run([sim.peak.x, -2.0, 3.0, 0.02], feedback=sim.peak.signal, max_points=40)
    lines = [l for l in capsys.readouterr().out.splitlines() if "motion model for" in l]
    assert 1 <= len(lines) <= 3  # an ideal axis: once, not on every step
