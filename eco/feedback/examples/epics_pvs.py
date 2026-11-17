"""Setup script: one feedback loop on raw EPICS channels, no namespace.

    python -m eco.feedback serve eco/feedback/examples/epics_pvs.py --port 8010

The PV names below are PLACEHOLDERS -- replace them before running. The
loop is created disabled; enable it from the panel or with
``FeedbackClient(url).set("intensity", enabled=True)``.
"""

from eco.epics_utils.adjustable import AdjustablePv
from eco.epics_utils.detector import DetectorPvData
from eco.feedback import PID, FeedbackManager

DETECTOR_PV = "SAROP21-PBPS133:INTENSITY"      # placeholder
ACTUATOR_PV = "SAROP21-OPPI113:MOTOR_X1"        # placeholder (setpoint PV)
ACTUATOR_RBV = ACTUATOR_PV + ".RBV"             # placeholder (readback PV)


def build_manager():
    det = DetectorPvData(DETECTOR_PV, name="intensity_monitor")
    act = AdjustablePv(ACTUATOR_PV, ACTUATOR_RBV, name="steering_actuator")
    mgr = FeedbackManager()
    # Keep the output clamp small until the sign and size of the gains are known:
    # out_min/out_max limit the correction *rate* (actuator units per second);
    # the output is a rate, so kp alone already integrates -- start with ki=0.
    mgr.add("intensity", det, act, setpoint=1.0, rate_hz=1,
            pid=PID(kp=0.05, out_min=-0.01, out_max=0.01))
    return mgr
