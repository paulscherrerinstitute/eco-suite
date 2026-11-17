"""No-hardware demo setups for :mod:`eco.feedback`.

Each ``build_*`` function returns a ready :class:`FeedbackManager`; run one
as a server with ``python -m eco.feedback demo <name>``, or build it in a
session and use it in-process (``fb = build_eco(); fb.panel()``).

- ``eco``    -- eco's own element classes (``AdjustableMemory`` actuators,
  ``DetectorGet`` detectors reading a simulated plant), so every write goes
  through the real ``Changer`` / access gate / queue-claim path. Default
  port 8010, no API key.
- ``matrix`` -- 2 actuators / 2 detectors with a coupling matrix, a
  closed-loop corrector built from a startup measurement, an interlock, a
  short alarm timeout and config/history persistence. Port 8001, API key
  ``demo-key`` (the walkthrough in ``docs/GUI.md`` uses this one).
- ``busy``   -- two independent loops plus a 3x3 coupled system with a
  scheduled re-measurement. Port 8000, no API key.

Persistence of the ``matrix`` demo goes to ``~/.eco/feedback_demo/``.
"""

from __future__ import annotations

import random
import time
from pathlib import Path

from .core import PID, FeedbackManager
from .sim import SimAdjustable, SimCoupledSystem, SimDetector

DEMO_DIR = Path.home() / ".eco" / "feedback_demo"


def build_eco(drift=0.01, noise=0.01):
    """Two loops on real eco element objects, plus a shared interlock.

    The plants: ``det = gain * act + offset(t)`` with a slowly drifting
    offset, so an enabled loop visibly works against the drift."""
    from ..elements.adjustable import AdjustableMemory
    from ..elements.detector import DetectorGet

    mgr = FeedbackManager()
    permit = AdjustableMemory(1, name="demo_beam_permit")
    mgr.add_interlock("beam_permit", permit)   # permit.set_target_value(0) trips it
    mgr.permit = permit

    for name, gain in (("x", 1.0), ("y", -0.5)):
        act = AdjustableMemory(0.0, name=f"demo_{name}_actuator")
        offset = [0.5]

        def read(act=act, gain=gain, offset=offset):
            offset[0] += drift * (random.random() - 0.4)
            return gain * act.get_current_value() + offset[0] + noise * random.gauss(0, 1)

        det = DetectorGet(read, name=f"demo_{name}_detector")
        mgr.add(name, det, act, setpoint=0.0, rate_hz=5,
                pid=PID(kp=0.5 * (1 if gain > 0 else -1), ki=2.0 * (1 if gain > 0 else -1),
                        out_min=-2, out_max=2),
                interlocks=["beam_permit"])
    return mgr


def build_matrix(var_dir=DEMO_DIR, measure=True):
    from .persistence import PersistentStore

    var_dir = Path(var_dir)
    var_dir.mkdir(parents=True, exist_ok=True)
    store = PersistentStore(str(var_dir / "demo_matrix.sqlite"))
    mgr = FeedbackManager(store=store, config_path=str(var_dir / "demo_matrix_config.yaml"))
    mgr.add_interlock("beam_permit", lambda: True)

    coupled = SimCoupledSystem(
        [[1.0, 0.3], [0.2, 1.0]], drift=0.01, noise=0.02,
        corrector_names=["cx1", "cx2"], detector_names=["bpm1", "bpm2"],
    )
    mgr.add("loop_1", coupled.detectors[0], coupled.adjustables[0], setpoint=0.0,
            rate_hz=50, pid=PID(kp=0.4, ki=3.0, out_min=-1, out_max=1),
            interlocks=["beam_permit"], alarm_after_s=2.0)
    mgr.add("loop_2", coupled.detectors[1], coupled.adjustables[1], setpoint=0.0,
            rate_hz=50, pid=PID(kp=0.4, ki=3.0, out_min=-5, out_max=5),
            interlocks=["beam_permit"])
    if measure:
        meas = mgr.measure_response_matrix("orbit", ["cx1", "cx2"], ["bpm1", "bpm2"],
                                           step=0.15, settle_s=0.1, repeats=3)
        while meas.state == "running":
            time.sleep(0.05)
        mgr.add_matrix_loop("steering", "orbit", gain=0.3, rcond=1e-3, rate_hz=20.0,
                            out_min=-1, out_max=1)
    return mgr


def build_busy():
    mgr = FeedbackManager()
    for name in ("pointing_x", "pointing_y"):
        adj = SimAdjustable(name=f"{name}_motor")
        det = SimDetector(adj, name=f"{name}_bpm")
        mgr.add(name, det, adj, setpoint=0.0, rate_hz=100,
                pid=PID(kp=0.5, ki=5.0, out_min=-10, out_max=10))
    steering = SimCoupledSystem(
        [[1.00, 0.25, 0.05], [0.10, 1.00, 0.20], [0.05, 0.15, 1.00]],
        drift=0.01, noise=0.02,
        corrector_names=["cx1", "cx2", "cx3"], detector_names=["bpm1", "bpm2", "bpm3"],
    )
    mgr.add("steering_x1", steering.detectors[0], steering.adjustables[0],
            setpoint=0.0, rate_hz=50, pid=PID(kp=0.3, ki=2.0, out_min=-5, out_max=5))
    mgr.add_actuator("cx2", steering.adjustables[1])
    mgr.add_actuator("cx3", steering.adjustables[2])
    for det in steering.detectors[1:]:
        mgr.add_detector(det.name, det)
    mgr.schedule_response_matrix(
        "steering", ["cx1", "cx2", "cx3"], ["bpm1", "bpm2", "bpm3"],
        interval_s=60, step=0.1, settle_s=0.15, repeats=3,
    )
    return mgr


DEMOS = {
    "eco": (build_eco, 8010, None),
    "matrix": (build_matrix, 8001, "demo-key"),
    "busy": (build_busy, 8000, None),
}
