"""SmaractRecord.startup_stage / calibrate_sensor / home against a simulated MSTA,
and SmaractController.startup running several stages in parallel."""

import threading
import time

import pytest

from eco.devices_general.motors import SmaractRecord
from eco.elements.adjustable import AdjustableError, AdjustableTrigger
from eco.motion.smaract import SmaractController

DONE, PROBLEM, HOMED = 1 << 1, 1 << 9, 1 << 14


class FakeChanger:
    def wait(self, timeout=None):
        pass


class FakeValue:
    def __init__(self, value, on_set=None):
        self.value = value
        self.writes = []
        self.on_set = on_set

    def get_current_value(self):
        return self.value

    def set_target_value(self, value):
        self.writes.append(value)
        self.value = value
        return FakeChanger()

    def __call__(self, value):
        self.writes.append(value)
        if self.on_set:
            self.on_set()


class FakeAlias:
    def __init__(self, name):
        self.name = name

    def get_full_name(self):
        return self.name


class FakeMotorParameters:
    def __init__(self):
        self.sensor_type_num = FakeValue(1)
        self.max_frequency = FakeValue(200)


def make_stage(name="stage1", action_time=0.3, responsive=True, homes=True):
    stg = object.__new__(SmaractRecord)
    stg.pvname = f"TEST:{name}"
    stg.name = name
    stg.alias = FakeAlias(f"smaract.{name}")
    stg.motor_parameters = FakeMotorParameters()
    stg.preferred_home_direction = FakeValue("forward")
    msta = FakeValue(DONE)
    stg._flags = msta
    stg.get_current_value = lambda: 0.0
    stg.order = []

    def run(label, final):
        stg.order.append(label)
        if not responsive:
            return

        def worker():
            time.sleep(0.05)
            msta.value = msta.value & ~DONE
            time.sleep(action_time)
            msta.value = final(msta.value) | DONE

        threading.Thread(target=worker, daemon=True).start()

    stg._pv_calibrate_sensor = FakeValue(
        0, on_set=lambda: run("calibrate", lambda m: m)
    )
    stg.home_forward = FakeValue(
        0, on_set=lambda: run("home", lambda m: m | HOMED if homes else m)
    )
    stg.home_reverse = FakeValue(0, on_set=lambda: run("home_reverse", lambda m: m))
    # as in SmaractRecord.__init__: the triggers shadow the methods (and
    # drop any arguments)
    stg.home = AdjustableTrigger(stg.home, name="home")
    stg.calibrate_sensor = AdjustableTrigger(
        stg.calibrate_sensor, name="calibrate_sensor"
    )
    return stg


def test_startup_order_and_defaults():
    stg = make_stage()
    result = stg.startup_stage(verbose=False)
    assert stg.order == ["calibrate", "home"]
    # defaults: the present values are written again
    assert stg.motor_parameters.sensor_type_num.writes == [1]
    assert stg.motor_parameters.max_frequency.writes == [200]
    assert result == {
        "sensor_type": 1,
        "max_frequency": 200,
        "calibrated": True,
        "homed": True,
    }


def test_startup_explicit_values():
    stg = make_stage()
    stg.startup_stage(sensor_type=9, max_frequency=1000, home=False, verbose=False)
    assert stg.motor_parameters.sensor_type_num.writes == [9]
    assert stg.motor_parameters.max_frequency.writes == [1000]
    assert stg.order == ["calibrate"]


def test_home_waits_even_if_already_homed():
    # the old home() returned at once when the homed bit was already set
    stg = make_stage(action_time=0.5)
    stg._flags.value = DONE | HOMED
    t0 = time.time()
    stg.home()  # the trigger, as from the GUI button
    assert time.time() - t0 > 0.4


def test_unresponsive_channel_times_out():
    # an axis stuck "not done" (never finishes) must not hang forever
    stg = make_stage(responsive=False)
    stg._flags.value = 0
    with pytest.raises(TimeoutError):
        stg._calibrate_sensor(timeout=0.5, start_timeout=0.2)


def test_home_not_referenced_raises():
    stg = make_stage(homes=False)
    with pytest.raises(AdjustableError, match="not referenced"):
        stg._home(timeout=5)


def test_problem_bit_raises():
    stg = make_stage()
    stg._flags.value = DONE | PROBLEM
    with pytest.raises(AdjustableError, match="problem"):
        stg._calibrate_sensor(timeout=5)


def test_background_thread_propagates():
    stg = make_stage(homes=False)
    thread = stg.startup_stage(wait=False, verbose=False)
    assert isinstance(thread, threading.Thread)
    with pytest.raises(AdjustableError):
        thread.join()


def test_controller_runs_stages_in_parallel():
    ctrl = object.__new__(SmaractController)
    stages = [make_stage(f"stage{n}", action_time=0.5) for n in (1, 2, 3)]
    for stg in stages:
        ctrl.__dict__[stg.name] = stg
    t0 = time.time()
    results = ctrl.startup([1, 2, 3], verbose=False)
    elapsed = time.time() - t0
    assert set(results) == {"stage1", "stage2", "stage3"}
    # calibration + homing, 0.5 s each: serially ~3 s, in parallel ~1 s
    assert elapsed < 2.0


def test_controller_reports_failures_after_all_finished():
    ctrl = object.__new__(SmaractController)
    good = make_stage("stage1")
    bad = make_stage("stage2", homes=False)
    ctrl.__dict__.update(stage1=good, stage2=bad)
    with pytest.raises(RuntimeError, match="stage2"):
        ctrl.startup([1, 2], verbose=False)
    assert good.order == ["calibrate", "home"]


def test_startup_stage_timeouts_reach_the_waits():
    # the triggers shadowing home/calibrate_sensor drop arguments; the
    # timeouts given to startup_stage must still apply
    stg = make_stage(action_time=3)
    t0 = time.time()
    with pytest.raises(TimeoutError):
        stg.startup_stage(calibration_timeout=0.5, verbose=False)
    assert time.time() - t0 < 2
