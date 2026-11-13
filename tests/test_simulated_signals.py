"""eco.devices_general.simulated_signals: the models are what their docstrings
say, and each timing/noise parameter has the effect it claims."""

import math
import time

import numpy as np
import pytest

from eco.devices_general import simulated_signals as ss


@pytest.fixture
def sim():
    s = ss.SimulatedSignals(detector="ideal")
    s.beam.fluctuation.set_target_value(0.0).wait()
    return s


def test_an_ordinary_assembly_showing_all_its_children():
    s = ss.SimulatedSignals()
    assert isinstance(s.peak, ss.SimPeak)
    assert s.peak.signal._beam is s.beam
    shown = repr(s)
    assert all(key in shown for key in ["beam", *s.SIGNALS])


def test_presets_reach_all_children():
    s = ss.SimulatedSignals(axis="stepper")
    s.apply_presets(axis="piezo", detector="lockin")
    assert s.step.x.speed.get_current_value() == ss.AXIS_PRESETS["piezo"]["speed"]
    assert s.oscillation.delay.speed.get_current_value() == ss.AXIS_PRESETS["piezo"]["speed"]
    assert s.peak2d.signal.response_time.get_current_value() == ss.DETECTOR_PRESETS["lockin"]["response_time"]


def test_models_at_known_points(sim):
    step = sim.step
    assert step.truth(-10.0) == pytest.approx(0.1)
    assert step.truth(0.5) == pytest.approx(0.6)
    assert step.truth(10.0) == pytest.approx(1.1)

    assert sim.peak.truth(0.7) == pytest.approx(1.05)
    assert sim.peak.truth(0.9) == pytest.approx(0.55)  # half maximum at fwhm/2

    assert sim.gradient.truth(2.0) == pytest.approx(1.0 + 0.4 + 0.04)

    assert sim.peak2d.truth(0.4, -0.3) == pytest.approx(1.05)

    osc = sim.oscillation
    assert osc.truth(-5.0) == pytest.approx(1.0)
    late = osc.truth(50.0)
    assert late == pytest.approx(0.5, abs=1e-6)  # step only, oscillation damped


def test_peak2d_asymmetry_along_major_axis(sim):
    p = sim.peak2d
    a = math.radians(30.0)
    d = 0.5
    plus = p.truth(0.4 + d * math.cos(a), -0.3 + d * math.sin(a))
    minus = p.truth(0.4 - d * math.cos(a), -0.3 - d * math.sin(a))
    assert plus > minus  # wider on the positive side


def test_resolution_smears_the_onset(sim):
    osc = sim.oscillation
    osc.mode1.amplitude.set_target_value(0.0).wait()
    assert osc.truth(0.0) == pytest.approx(0.75, abs=1e-3)  # half the step
    osc.resolution.set_target_value(0.0).wait()
    assert osc.truth(-1e-6) == pytest.approx(1.0)


def test_overlay_has_three_modes(sim):
    assert [hasattr(sim.oscillations, f"mode{i}") for i in (1, 2, 3, 4)] == [True, True, True, False]


def test_parameters_change_the_signal_live(sim):
    sim.peak.center.set_target_value(-1.0).wait()
    assert sim.peak.truth(-1.0) == pytest.approx(1.05)


def test_detector_reads_the_model_at_the_axis_position(sim):
    sim.peak.x.set_target_value(0.7).wait()
    time.sleep(0.02)  # next detector tick
    assert sim.peak.signal.get_current_value() == pytest.approx(1.05)


def test_value_is_held_within_a_tick():
    d = ss.SimDetector(lambda: 1.0, preset="diode", update_rate=0.5, name="d")
    assert d.get_current_value() == d.get_current_value()


def test_beam_fluctuation_is_shared_with_i0():
    beam = ss.SimBeam(fluctuation=0.2, rate=1.0, preset="ideal", name="beam")
    beam.i0.update_rate.set_target_value(1.0).wait()
    det = ss.SimDetector(lambda: 2.0, beam=beam, preset="ideal", update_rate=1.0, name="d")
    assert det.get_current_value() / beam.i0.get_current_value() == pytest.approx(2.0)


def test_noise_is_reproducible_per_tick_and_roughly_normal():
    x = ss._normal(7, np.arange(20000))
    assert np.array_equal(x, ss._normal(7, np.arange(20000)))
    assert abs(x.mean()) < 0.03 and x.std() == pytest.approx(1.0, abs=0.03)
    assert not np.array_equal(x, ss._normal(8, np.arange(20000)))


def test_axis_timing_and_readback():
    ax = ss.SimAxis(preset="ideal", speed=10.0, overhead=0.05, settle_time=0.05, name="ax")
    t = time.time()
    ax.set_target_value(1.0).wait()
    assert time.time() - t == pytest.approx(0.2, abs=0.05)
    assert ax.get_current_value() == pytest.approx(1.0)


def test_backlash_shows_on_the_load_not_the_readback():
    ax = ss.SimAxis(preset="ideal", backlash=0.1, name="ax")
    ax.set_target_value(1.0).wait()
    now = time.time()
    assert ax.physical_position(now) == pytest.approx(0.95)
    ax.set_target_value(0.5).wait()
    now = time.time()
    assert ax.motor_position(now) == pytest.approx(0.5)
    assert ax.physical_position(now) == pytest.approx(0.55)
    assert ax.get_current_value() == pytest.approx(0.5)


def test_limits_are_enforced():
    ax = ss.SimAxis(limits=(-1, 1), name="ax")
    assert ax.get_limits() == (-1, 1)
    with pytest.raises(ValueError):
        ax.set_target_value(2.0)


def test_stop_ends_a_move_early():
    ax = ss.SimAxis(speed=1.0, name="ax")
    ch = ax.set_target_value(10.0)
    time.sleep(0.2)
    ax.stop()
    ch.wait(timeout=1)
    assert 0.1 < ax.get_current_value() < 0.5


def test_response_time_low_passes():
    ax = ss.SimAxis(name="ax")
    det = ss.SimDetector(lambda x: x, axes=[ax], preset="ideal", update_rate=100.0, response_time=0.2, name="d")
    det.get_current_value()
    ax.set_target_value(1.0).wait()
    time.sleep(0.2)
    assert det.get_current_value() == pytest.approx(1 - math.exp(-1), abs=0.1)
    time.sleep(0.8)
    assert det.get_current_value() == pytest.approx(1.0, abs=0.02)


def test_dead_time_delays_the_signal():
    ax = ss.SimAxis(name="ax")
    det = ss.SimDetector(lambda x: x, axes=[ax], preset="ideal", dead_time=0.3, name="d")
    ax.set_target_value(1.0).wait()
    assert det.get_current_value() == pytest.approx(0.0)
    time.sleep(0.35)
    assert det.get_current_value() == pytest.approx(1.0)


def test_jitter_blurs_the_physical_position():
    ax = ss.SimAxis(jitter=0.1, name="ax")
    x = ax.physical_position(np.zeros(5000), jitter_ticks=np.arange(5000))
    assert x.std() == pytest.approx(0.1, abs=0.01)
    assert ax.physical_position(0.0) == pytest.approx(0.0)  # no ticks, no jitter


@pytest.mark.parametrize("preset", sorted(ss.DETECTOR_PRESETS))
def test_detector_presets_apply(preset):
    det = ss.SimDetector(lambda: 1.0, name="d")
    det.apply_preset(preset)
    for key, value in ss.DETECTOR_PRESETS[preset].items():
        assert getattr(det, key).get_current_value() == value


@pytest.mark.parametrize("preset", sorted(ss.AXIS_PRESETS))
def test_axis_presets_apply(preset):
    ax = ss.SimAxis(name="ax")
    ax.apply_preset(preset)
    for key, value in ss.AXIS_PRESETS[preset].items():
        assert getattr(ax, key).get_current_value() == value


def test_monitor_callback_collects_values():
    det = ss.SimDetector(lambda: 1.0, preset="ideal", update_rate=50.0, name="d")
    mon = det.set_current_value_callback()
    mon.start()
    time.sleep(0.2)
    mon.stop()
    assert len(mon.data["values"]) >= 5


# -- oscillation_tt: arrival-time jitter and a timetool ---------------------------
def test_oscillation_tt_units_and_period():
    o = ss.SimulatedSignals().oscillation_tt
    assert (o.delay.unit, o.tt.unit, o.arrival.unit) == ("s", "fs", "fs")
    t = np.arange(0, 600, 0.5)
    y = o.signal.truth(t * 1e-15, np.zeros_like(t))
    peaks = t[1:-1][(y[1:-1] > y[:-2]) & (y[1:-1] > y[2:])]
    assert np.diff(peaks) == pytest.approx(100.0, abs=1.0)  # 100 fs period


def test_oscillation_tt_timetool_explains_the_jitter(sim):
    # sim: no detector noise, no beam fluctuation -- only the timing remains
    o = sim.oscillation_tt
    o.delay.set_target_value(25e-15).wait()  # on the oscillation's slope
    shots = o.shots(3000)
    assert (shots.tt - shots.arrival).std() == pytest.approx(5.0, rel=0.15)
    assert shots.arrival.std() == pytest.approx(50.0, rel=0.1)
    nominal = o.signal.truth(shots.delay, np.zeros(len(shots)))
    corrected = o.signal.truth(shots.delay, shots.tt)
    # the corrected delay explains the signal, the stage delay alone does not
    assert (shots.signal - corrected).std() < 0.3 * (shots.signal - nominal).std()


def test_oscillation_tt_presets_in_seconds():
    sim = ss.SimulatedSignals()
    o = sim.oscillation_tt
    sim.apply_presets(axis="delay_stage", detector="epics_slow")
    assert o.delay.speed.get_current_value() == pytest.approx(50e-12)
    assert o.delay.jitter.get_current_value() == 0  # the arrival jitters, not the stage
    assert o.arrival.jitter.get_current_value() == 50.0
    assert o.tt.update_rate.get_current_value() == o.signal.update_rate.get_current_value() == 1.0
