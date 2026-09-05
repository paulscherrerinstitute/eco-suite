"""Newline-delimited JSON message protocol between the Pi (thin client)
and the PC (eco session). Deliberately tiny and transport-agnostic: every
message is one JSON object with a "t" (type) key, one line, so it runs
over anything that carries bytes - a socket for testing, or a Bluetooth
RFCOMM serial device (/dev/rfcomm0) in the field.

Design: the PC side is authoritative (it holds eco + the ManualControlBox).
The Pi sends small *events*; the PC replies with small *state* deltas and a
low-rate *value* stream for the armed target. The bulky tree structure is
sent rarely (snapshot), so the link only ever carries light traffic.

Who dials: the **box listens** and the eco session connects to it. That way
the box belongs to no particular console - any machine holding the shared
token may offer itself, and the operator standing at the box decides who
gets it. The connection handshake is therefore:

    PC  -> box : hello    {token, host, user, namespace, pid}
    box -> PC  : accepted {box}            (operator tapped Accept)
             or  rejected {reason}          (operator declined / bad token)
    ... normal event/state traffic ...
    box -> PC  : bye      {reason}          (e.g. taken over by another host)

`bye` is what makes a displaced session close itself instead of lingering.
"""

import json

# --- PC -> box : connection request ---
EV_HELLO = "hello"  # {"token", "host", "user", "namespace", "pid"}

# --- box -> PC : verdict on that request, and a parting reason ---
MSG_ACCEPTED = "accepted"  # {"box": "<box hostname>"}
MSG_REJECTED = "rejected"  # {"reason": "..."}
MSG_BYE = "bye"  # {"reason": "..."} sent before the box drops this session

# --- box -> PC : events (each ~30-80 bytes) ---
EV_ROTATE = "rotate"  # {"dir": +1|-1}  encoder detent
EV_PRESS = "press"  # encoder short press (OK)
EV_LONG_PRESS = "long_press"  # encoder long press (disarm)
EV_ACTIVATE = "activate"  # touch a row: {"index": i}
EV_BREADCRUMB = "breadcrumb"  # {"index": i}
EV_STEP = "step"  # {"dir": +1|-1}
EV_JOG_START = "jog_start"  # {"dir": +1|-1}
EV_JOG_STOP = "jog_stop"
EV_GET_SNAPSHOT = "get_snapshot"
EV_MENU = "menu"  # open/close the box menu (hardware button or touch)

# --- PC -> Pi : state ---
MSG_SNAPSHOT = "snapshot"  # {"tree": {...}}  full tree model (rare)
MSG_STATE = "state"  # small delta for the current view
MSG_VALUE = "value"  # {"value": ...}  live value of armed target
MSG_ERROR = "error"  # {"error": "..."}


def _default(obj):
    # numpy scalars/arrays and anything json can't handle -> plain python
    for attr in ("item", "tolist"):
        if hasattr(obj, attr):
            try:
                return getattr(obj, attr)()
            except Exception:
                pass
    return str(obj)


def encode(msg_type, **payload):
    return json.dumps({"t": msg_type, **payload}, default=_default) + "\n"


def decode(line):
    d = json.loads(line)
    return d.pop("t"), d
