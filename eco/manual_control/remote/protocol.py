"""Newline-delimited JSON message protocol between the Pi (thin client)
and the PC (eco session). Deliberately tiny and transport-agnostic: every
message is one JSON object with a "t" (type) key, one line, so it runs
over anything that carries bytes - a socket for testing, or a Bluetooth
RFCOMM serial device (/dev/rfcomm0) in the field.

Design: the PC side is authoritative (it holds eco + the ManualControlBox).
The Pi sends small *events*; the PC replies with small *state* deltas and a
low-rate *value* stream for the armed target. The bulky tree structure is
sent rarely (snapshot), ideally preloaded over USB, so the Bluetooth link
only ever carries the light traffic.
"""

import json

# --- Pi -> PC : events (each ~30-80 bytes) ---
EV_HELLO = "hello"  # {"token": "..."} when the server requires one
EV_ROTATE = "rotate"  # {"dir": +1|-1}  encoder detent
EV_PRESS = "press"  # encoder short press (OK)
EV_LONG_PRESS = "long_press"  # encoder long press (disarm)
EV_ACTIVATE = "activate"  # touch a row: {"index": i}
EV_BREADCRUMB = "breadcrumb"  # {"index": i}
EV_STEP = "step"  # {"dir": +1|-1}
EV_JOG_START = "jog_start"  # {"dir": +1|-1}
EV_JOG_STOP = "jog_stop"
EV_GET_SNAPSHOT = "get_snapshot"

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
