"""Scans.optimize / eco.acquisition.optimize: the strategy alone on plain
functions (fast), and whole scans on the simulated THz focusing chamber
(sim.thz_focus): signal found, delay calibrated and following, every point
recorded on a mesh without a grid, the scan ending at the best point."""

import math

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pytest

from eco.acquisition import optimize
from eco.acquisition.optimize import Optimizer, fit_quadratic, parse_setup
from eco.acquisition.scan import Scans
from eco.devices_general.simulated_signals import SimThzFocus, SimulatedSignals

C = SimThzFocus.C_MM_PER_PS
NAMES = ["par_x", "par_z", "par_rx", "mir_rz", "mir_rx", "delay"]
STEPS = np.array([0.03, 0.03, 0.1, 0.05, 0.05, 0.08])


@pytest.fixture
def sim(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the monitor counter stores its data here
    return SimulatedSignals(axis="ideal")


def drive(opt, f, noise, seed=1):
    """Run `opt` to its end on `f(x) -> value` with Gaussian noise."""
    rng = np.random.default_rng(seed)
    n = 0
    while True:
        plan = opt.plan(10**6)
        if not len(plan):
            return n
        for x in plan.positions:
            opt.add([x], [f(x) + noise * rng.normal()], [noise / 2])
            n += 1


def thz_optimizer(**kwargs):
    start = np.zeros(6)
    bounds = np.column_stack([start - 10 * STEPS, start + 10 * STEPS])
    return Optimizer(start, STEPS, bounds, names=NAMES, verbose=False, **kwargs)


def test_quadratic_fit_recovers_coupling():
    rng = np.random.default_rng(0)
    U = rng.uniform(-2, 2, (60, 2))
    H = np.array([[-1.0, 0.6], [0.6, -0.5]])
    y = 3 + U @ [0.2, -0.1] + 0.5 * np.einsum("ni,ij,nj->n", U, H, U)
    m = fit_quadratic(U, y, np.full(60, 0.01), np.zeros(2), [0, 1])
    assert m["full"] and m["H"] == pytest.approx(H, abs=1e-6)
    assert m["g"] == pytest.approx([0.2, -0.1], abs=1e-6)


def test_finds_thz_focus_from_the_model(sim):
    model = sim.thz_focus.truth
    opt = thz_optimizer()
    n = drive(opt, lambda x: float(model(*x)), 0.01)
    assert 120 < n < 400
    best = opt.absolute(opt.best_u)
    assert float(model(*best)) > 0.95  # the maximum is 1.02
    assert opt.finished and opt.phase[-1] == "best"
    assert {"probe", "line", "best"} <= set(opt.phase)
    assert len(opt.directions) == 6 and "directions" in opt.parameters()


def test_calibrated_delay_follows_the_parabola(sim):
    model = sim.thz_focus.truth
    opt = thz_optimizer(calibrate=[5])
    drive(opt, lambda x: float(model(*x)), 0.01)
    k = opt.follow_coefficients["delay"]
    # path length: 1 mm of parabola x or z is 1/c = 3.34 ps; the mirror
    # tilts change no path
    assert k["par_x"] == pytest.approx(1 / C, rel=0.2)
    assert k["par_z"] == pytest.approx(1 / C, rel=0.2)
    assert abs(k["mir_rz"]) < 0.5 and abs(k["mir_rx"]) < 0.5
    assert len(opt.directions) == 5  # the delay is no longer searched
    best = opt.absolute(opt.best_u)
    assert float(model(*best)) > 0.95
    # after the calibration every point keeps the delay on the relation
    u = opt.complete(opt.best_u + np.array([1.0, 0, 0, 0, 0, 0]))
    assert (u[5] - opt.best_u[5]) * STEPS[5] == pytest.approx(k["par_x"] * STEPS[0], rel=1e-6)


def test_check_phase_restarts_at_a_better_maximum():
    # a small peak at the start, a higher one 7 steps away
    def f(x):
        a = math.exp(-((x[0]) ** 2 + x[1] ** 2) / 8)
        b = math.exp(-((x[0] - 7) ** 2 + (x[1] - 6) ** 2) / 8)
        return 0.5 * a + 1.0 * b

    opt = Optimizer([0, 0], [1, 1], [(-10, 10), (-10, 10)], verbose=False, check=30, restarts=1, seed=0)
    drive(opt, f, 0.005)
    assert f(opt.absolute(opt.best_u)) > 0.9
    assert any("better maximum found" in line for line in opt.log)


def test_check_phase_on_a_single_peak_says_so():
    opt = Optimizer([0], [1], [(-10, 10)], verbose=False, check=5)
    drive(opt, lambda x: math.exp(-((x[0] - 2) ** 2) / 8), 0.005)
    assert opt.absolute(opt.best_u)[0] == pytest.approx(2, abs=0.5)
    assert "none better" in opt.check_result


def test_fixed_follower_moves_with_the_axes():
    opt = Optimizer(
        [1.0, 2.0], [0.1, 0.1], [(0, 2), (1, 3)], followers=[(5.0, np.array([2.0, 0.0]), (-math.inf, math.inf))],
        verbose=False,
    )
    assert opt.ndim == 3
    assert opt.absolute([3.0, -1.0]) == pytest.approx([1.3, 1.9, 5.6])


def test_lost_points_and_repeats_are_handled():
    calls = []

    def f(x):
        calls.append(x)
        return -((x[0] - 0.3) ** 2)

    opt = Optimizer([0.0], [0.1], [(-1, 1)], verbose=False, check=0)
    rng = np.random.default_rng(0)
    while True:
        plan = opt.plan(10**6)
        if not len(plan):
            break
        for x in plan.positions:
            if rng.random() < 0.1:
                continue  # the driver got no feedback for this step
            opt.add([x], [f(x)], [0.001])
    assert opt.absolute(opt.best_u)[0] == pytest.approx(0.3, abs=0.05)


def test_suspicious_point_is_measured_again():
    seen = []

    def errors(x):
        seen.append(float(x[0]))
        # the first time at +1 step the value is mixed up with the move
        return (5.0, 1.0) if seen.count(float(x[0])) == 1 and x[0] == pytest.approx(0.1) else (-(x[0] ** 2), 0.001)

    opt = Optimizer([0.0], [0.1], [(-1, 1)], verbose=False, check=0)
    while True:
        plan = opt.plan(10**6)
        if not len(plan):
            break
        for x in plan.positions:
            y, e = errors(x)
            opt.add([x], [y], [e])
    assert seen.count(pytest.approx(0.1)) >= 2
    assert opt.absolute(opt.best_u)[0] == pytest.approx(0.0, abs=0.05)


def test_parse_setup(sim):
    t = sim.thz_focus
    t.par_x.set_target_value(0.5).wait()
    setup = parse_setup([[t.par_x, 0.1], [t.delay, 0.05, (-1, 1)]], follow={t.par_z: {t.par_x: 2.0}}, limits=3)
    assert setup.start == pytest.approx([0.5, 0.0])
    assert np.allclose(setup.bounds, [[0.2, 0.8], [-1, 1]])
    assert setup.adjustables[-1] is t.par_z
    assert setup.followers[0][1] == pytest.approx([2.0, 0.0])
    with pytest.raises(ValueError, match="not one of the axes"):
        parse_setup([[t.par_x, 0.1]], follow={t.delay: "calibrate"})
    with pytest.raises(ValueError, match="is an axis"):
        parse_setup([[t.par_x, 0.1], [t.delay, 0.1]], follow={t.delay: {t.par_x: 1.0}})
    with pytest.raises(ValueError, match="step"):
        parse_setup([[t.par_x, 0.0]])
    with pytest.raises(ValueError, match="outside"):
        parse_setup([[t.par_x, 0.1, (1.0, 2.0)]])


def _quiet(sim):
    t = sim.thz_focus
    t.signal.noise.set_target_value(0.005).wait()
    t.signal.noise_rel.set_target_value(0.01).wait()
    sim.beam.fluctuation.set_target_value(0.01).wait()
    return t


def test_scan_optimizes_and_records(sim):
    t = _quiet(sim)
    s = Scans().optimize(
        [t.par_x, 0.03], [t.mir_rz, 0.05], [t.delay, 0.08],
        exposure_def=0.05, feedback=t.signal, follow={t.delay: "calibrate"},
        settling_time=0.02, check=3, plot=False,
    )
    opt = s.optimizer
    values = [t.par_x.get_current_value(), 0, 0, t.mir_rz.get_current_value(), 0, t.delay.get_current_value()]
    assert float(t.truth(*values)) > 0.9  # from 0.22
    # ends at the best point, measured last
    assert s._values_done[-1] == pytest.approx(list(opt.best().values()))
    assert opt.strategy.phase[-1] == "best"
    params = s.scan_info["scan_parameters"]
    assert params["optimize"]["follow_coefficients"]["sim.thz_focus.delay"]["sim.thz_focus.par_x"] == pytest.approx(
        1 / C, rel=0.3
    )
    mesh = params["mesh_specs"]
    assert mesh["kind"] == "path" and params.get("grid_specs") is None
    assert mesh["resolution"] == pytest.approx([0.003, 0.005, 0.008])
    assert len(s.scan_info["scan_step_info"]) == len(s._values_done) == opt.strategy.n_points
    # the eigen-axes move all adjustables, the delay along
    axis = opt.virtual_axes()[0]
    assert axis.get_current_value() == pytest.approx(0.0, abs=1e-9)
    d0 = t.delay.get_current_value()
    axis.set_target_value(1.0).wait()
    assert axis.get_current_value() == pytest.approx(1.0)
    assert t.delay.get_current_value() != pytest.approx(d0)
    opt.move_to_best()
    assert t.delay.get_current_value() == pytest.approx(d0)
    fig = opt.landscape()
    assert len(fig.axes) >= 4


def test_scan_with_fixed_follower_and_point_budget(sim, capsys):
    t = _quiet(sim)
    s = Scans().optimize(
        [t.par_x, 0.03], exposure_def=0.05, feedback=t.signal,
        follow={t.delay: {t.par_x: 1 / C}}, max_points=8, plot=False,
    )
    # the budget ends it; the best point is measured once more
    assert len(s._values_done) == 9
    assert "budget used up" in capsys.readouterr().out
    for x, d in s._values_done:
        assert d == pytest.approx(x / C)
    assert s._values_done[-1] == pytest.approx(list(s.optimizer.best().values()))


def test_build_without_start_does_not_run(sim):
    t = sim.thz_focus
    s = Scans().optimize([t.par_x, 0.03], exposure_def=0.05, feedback=t.signal, start_immediately=False, plot=False)
    assert s.next_step == 0 and len(s._values_todo) == 3 + 2  # the probe
    assert t.par_x.get_current_value() == 0.0
