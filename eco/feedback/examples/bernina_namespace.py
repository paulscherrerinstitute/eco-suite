"""Setup script: loops on components of the Bernina namespace, in a server
process of their own.

    python -m eco.feedback serve eco/feedback/examples/bernina_namespace.py --port 8010

Only the named components are initialized (the namespace is lazy). The
component names below are examples -- adapt them. Inside a running eco
session none of this is needed: there ``fb.add("x", "mon_opt.xpos",
"mono.roll1_piezo")`` resolves the names directly.
"""

import importlib

from eco.feedback import PID, FeedbackManager

LOOPS = {
    # loop name: (detector, adjustable, setpoint, PID)
    "mono_hor": ("mon_opt_new.xpos", "mono.roll1_piezo", 0.0,
                 PID(kp=-0.1, out_min=-1, out_max=1)),  # output = correction rate
}


def component(dotted, module=None):
    module = module or importlib.import_module("eco.bernina.bernina")
    head, *rest = dotted.split(".")
    obj = getattr(module, head)
    for part in rest:
        obj = getattr(obj, part)
    return obj


def build_manager():
    module = importlib.import_module("eco.bernina.bernina")
    mgr = FeedbackManager()
    for name, (det, adj, setpoint, pid) in LOOPS.items():
        mgr.add(name, component(det, module), component(adj, module),
                setpoint=setpoint, rate_hz=1, pid=pid)
    return mgr
