"""Pi side: a proxy that looks to the GUI just like a ManualControlBox, but
holds no eco - it forwards input events over the transport and renders the
state deltas streamed back. It also caches the full tree snapshot (the
bulky part, preloadable over USB) so labels/structure survive independent
of the light live traffic.

Because it exposes the same read attributes/methods the mock GUI already
uses (entries, path_names, cursor, target, target_name(), target_value(),
mode, step_size, and the encoder/jog/step/touch input methods), the
existing mock_gui.ManualControlApp can drive this unchanged.
"""

import threading

from . import protocol as p


class ClientEntry:
    def __init__(self, d):
        self.name = d["name"]
        self.marker = d["marker"]
        self.kind = d["kind"]
        self.armed = d.get("armed", False)
        self._obj = self  # so `entry._obj is target`-style checks stay false


class RemoteControlClient:
    def __init__(self, transport, on_update=None, token=None):
        self.tr = transport
        self.on_update = on_update
        self.token = token
        self.connected = True  # cleared when the link closes (server gone)
        self.tree = None  # cached full snapshot (from USB or MSG_SNAPSHOT)
        self.path_names = ["…"]
        self._entries = []
        self.cursor = 0
        self._target = None
        self.mode = "navigate"
        self.step_size = None
        self.slots = []
        self.active_slot = 0
        self.in_menu = False
        self.message = None
        self._value = None
        self._pending_index = 0
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)

    def start(self, request_snapshot=True):
        self._reader.start()
        if self.token is not None:
            self._send(p.EV_HELLO, token=self.token)
        if request_snapshot:
            self.request_snapshot()
        return self

    # --- incoming ---
    def _read_loop(self):
        while True:
            try:
                line = self.tr.read_line()
            except OSError:
                line = None
            if line is None:
                break
            try:
                t, d = p.decode(line)
            except Exception:
                continue
            with self._lock:
                if t == p.MSG_STATE:
                    self.path_names = d["path"]
                    self._entries = [ClientEntry(x) for x in d["entries"]]
                    self.cursor = d["cursor"]
                    self._target = d["target"]
                    self.mode = d["mode"]
                    self.step_size = d["step"]
                    self._value = d["value"]
                    self.slots = d.get("slots", [])
                    self.active_slot = d.get("active_slot", 0)
                    self.in_menu = d.get("in_menu", False)
                    self.message = d.get("message")
                elif t == p.MSG_VALUE:
                    self._value = d["value"]
                elif t == p.MSG_SNAPSHOT:
                    self.tree = d["tree"]
            if self.on_update:
                self.on_update()
        self.connected = False
        if self.on_update:
            self.on_update()

    # --- read interface used by the GUI ---
    @property
    def entries(self):
        return self._entries

    @property
    def target(self):
        return self._target

    def target_name(self):
        return self._target

    def target_value(self):
        return self._value

    def entry_is_armed(self, entry):
        return getattr(entry, "armed", False)

    # --- input forwarded over the link ---
    def _send(self, msg_type, **payload):
        try:
            self.tr.write_line(p.encode(msg_type, **payload))
        except OSError:
            pass

    def encoder_rotate(self, direction):
        self._send(p.EV_ROTATE, dir=direction)

    def move_cursor(self, direction):
        self._send(p.EV_ROTATE, dir=direction)

    def encoder_short_press(self):
        self._send(p.EV_PRESS)

    def encoder_long_press(self):
        self._send(p.EV_LONG_PRESS)

    def disarm(self):
        self._send(p.EV_LONG_PRESS)

    def set_cursor(self, index):
        self._pending_index = index

    def activate_cursor(self):
        self._send(p.EV_ACTIVATE, index=self._pending_index)

    def breadcrumb_jump(self, index):
        self._send(p.EV_BREADCRUMB, index=index)

    def step_up(self):
        self._send(p.EV_STEP, dir=1)

    def step_down(self):
        self._send(p.EV_STEP, dir=-1)

    def jog_start(self, direction):
        self._send(p.EV_JOG_START, dir=direction)

    def jog_stop(self):
        self._send(p.EV_JOG_STOP)

    def toggle_menu(self):
        self._send(p.EV_MENU)

    def request_snapshot(self):
        self._send(p.EV_GET_SNAPSHOT)
