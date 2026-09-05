"""PC side: runs eco + a ManualControlBox, and exposes it to a remote thin
client over a LineTransport. Authoritative - the client only sends events
and renders what this streams back.

Traffic kept Bluetooth-friendly: a small MSG_STATE after each event (the
current level only, not the whole tree) and a low-rate MSG_VALUE stream
for the armed target. The bulky tree goes out once as MSG_SNAPSHOT on
request (or is preloaded on the Pi over USB).
"""

import threading

from ..box import ManualControlBox
from . import protocol as p
from .snapshot import export_tree


def _safe_value(box):
    if box.target is None:
        return None
    try:
        return box.target_value()
    except Exception as exc:
        return f"<error: {exc}>"


class RemoteControlServer:
    def __init__(self, root, transport, root_name=None, value_hz=5, token=None, **box_kwargs):
        self.closed_reason = None  # why the box let this session go
        self.root = root
        self.root_name = root_name
        self.box = ManualControlBox(root, root_name=root_name, **box_kwargs)
        self.tr = transport
        self.token = token
        # With a token set, nothing but a matching hello is accepted. On a
        # facility network this port can drive real motors, so an unknown
        # peer must not be able to just talk to it (a guardrail against
        # mistakes/strays, not a security boundary - it is plaintext).
        self._authenticated = token is None
        self._value_period = 1.0 / value_hz
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._ticker = threading.Thread(target=self._value_loop, daemon=True)

    def start(self):
        self._reader.start()
        self._ticker.start()
        if self._authenticated:
            self._send_state()
        return self

    def _read_loop(self):
        while not self._stop.is_set():
            line = self.tr.read_line()
            if line is None:
                break
            try:
                t, d = p.decode(line)
                if not self._authenticated:
                    if t != p.EV_HELLO or d.get("token") != self.token:
                        self._safe_write(p.encode(p.MSG_ERROR, error="bad or missing token"))
                        print("rejected client: bad or missing token")
                        break
                    self._authenticated = True
                    print("client authenticated")
                    self._send_state()
                    continue
                self._dispatch(t, d)
            except Exception as exc:
                self._safe_write(p.encode(p.MSG_ERROR, error=str(exc)))

    def _dispatch(self, t, d):
        b = self.box
        if t == p.EV_GET_SNAPSHOT:
            self._safe_write(
                p.encode(p.MSG_SNAPSHOT, tree=export_tree(self.root, self.root_name))
            )
            self._send_state()
            return
        if t == p.EV_ROTATE:
            b.encoder_rotate(d["dir"])
        elif t == p.EV_PRESS:
            b.encoder_short_press()
        elif t == p.EV_LONG_PRESS:
            b.encoder_long_press()
        elif t == p.EV_ACTIVATE:
            b.set_cursor(d["index"])
            b.activate_cursor()
        elif t == p.EV_BREADCRUMB:
            b.breadcrumb_jump(d["index"])
        elif t == p.EV_STEP:
            b.step_up() if d["dir"] > 0 else b.step_down()
        elif t == p.EV_JOG_START:
            b.jog_start(d["dir"])
        elif t == p.EV_JOG_STOP:
            b.jog_stop()
        elif t == p.EV_MENU:
            b.toggle_menu()
        elif t == p.MSG_BYE:
            # the box handed itself to someone else (or was shut down): stop
            # rather than sit here looking connected to a box that is gone
            self.closed_reason = d.get("reason", "closed by the box")
            print(f"the control box closed this session: {self.closed_reason}")
            self._stop.set()
            self.tr.close()
            return
        elif t in (p.EV_HELLO,):
            pass
        else:
            return
        self._send_state()

    def _send_state(self):
        b = self.box
        entries = [
            {
                "name": e.name,
                "marker": e.marker,
                "kind": e.kind,
                "armed": e._obj is not None and e._obj is b.target,
            }
            for e in b.entries
        ]
        self._safe_write(
            p.encode(
                p.MSG_STATE,
                path=b.path_names,
                entries=entries,
                cursor=b.cursor,
                target=b.target_name(),
                mode=b.mode,
                step=b.step_size,
                value=_safe_value(b),
                slots=[{"name": s.name, "step": s.step_size, "motion": s.motion}
                       for s in getattr(b, "slots", [])],
                active_slot=getattr(b, "active_slot", 0),
                in_menu=getattr(b, "in_menu", False),
                message=getattr(b, "message", None),
            )
        )

    def _value_loop(self):
        while not self._stop.wait(self._value_period):
            if self._authenticated and self.box.target is not None:
                self._safe_write(p.encode(p.MSG_VALUE, value=_safe_value(self.box)))

    def _safe_write(self, line):
        try:
            self.tr.write_line(line)
        except OSError:
            self._stop.set()

    def wait(self):
        """Block until the client disconnects (reader loop ends)."""
        self._reader.join()

    def stop(self):
        self._stop.set()
        self.tr.close()
