"""One connected client: the protocol of web_frontend/PROTOCOL.md, without
any transport. The gateway feeds it decoded messages (`handle`) and gives it
a thread-safe `send(message)`; it never blocks the caller -- every request
runs in the session's own small pool, values come from a live_values
LiveReader (monitored where possible, polled otherwise, exactly as in the
Qt and ipywidgets grids), writes go through live_values.start_write.

Values are coalesced: `flush()` returns at most one "values" event with the
latest value per item since the last flush; the gateway calls it on a timer
(FLUSH_INTERVAL), so a fast channel cannot flood a phone on a tunnel.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from eco.web import viewmodels
from eco.web.backends import NotFound
from eco.widgets import live_values

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 0
FLUSH_INTERVAL = 0.1  # s
REQUEST_WORKERS = 4
# A jog runs only while the client keeps confirming it (the held button
# re-sends "jog" every JOG_REPEAT s); without a confirmation for JOG_DEADMAN
# s -- the phone locked, the tunnel died, the tab was closed -- it is
# stopped here, since a jog itself never ends on its own.
JOG_DEADMAN = 1.0  # s
JOG_REPEAT = 0.25  # s, what the client is told in hello


class ProtocolError(Exception):
    """A request the client got wrong; reported back as error type ProtocolError."""


class WritesDisabled(PermissionError):
    pass


def _error(exc):
    return {"type": type(exc).__name__, "message": live_values.describe(exc)}


class ClientSession:
    """`writes`: False (read-only, the default), True, or a collection of
    item names that may be written (anything else is read-only)."""

    def __init__(self, backend, send, writes=False, poll_interval=1.0, client_id=None):
        self.backend = backend
        self.send = send
        self.writes = writes
        self.client_id = client_id
        self._pool = ThreadPoolExecutor(max_workers=REQUEST_WORKERS, thread_name_prefix="eco-web")
        self._lock = threading.Lock()
        self._objects = {}  # name -> object, every row/view handed to this client
        self._index = {}  # name -> LiveReader index
        self._names = []  # LiveReader index -> name
        self._last = {}  # name -> last raw value (types the next write)
        self._pending = {}  # name -> encoded value, not yet flushed
        self._holders = {}  # name -> {"changer": ...} of the latest write
        self._closed = False
        # jogs: one thread in request order (a quick press-release must not
        # stop before it starts), plus the dead-man watchdog
        self._jog_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eco-web-jog")
        self._jogs = {}  # name -> {"obj", "direction", "alive"}
        self._jog_watch = None
        self._reader = live_values.LiveReader(self._on_result, poll_interval=poll_interval)
        self._reader.start()

    # -- transport side ----------------------------------------------------------------

    def handle(self, message):
        """A decoded client message. Returns at once; the reply is sent later."""
        if not isinstance(message, dict) or "op" not in message:
            self.send({"v": PROTOCOL_VERSION, "re": None, "ok": False,
                       "error": {"type": "ProtocolError", "message": "not a request"}})
            return
        request_id = message.get("id")
        op = message.get("op")
        args = message.get("args") or {}
        handler = getattr(self, f"_op_{op}", None) if isinstance(op, str) else None
        if handler is None:
            self._reply(request_id, error=ProtocolError(f"unknown op {op!r}"))
            return
        pool = self._jog_pool if op == "jog" else self._pool
        try:
            pool.submit(self._run, handler, request_id, args)
        except RuntimeError:  # closed
            pass

    def flush(self):
        """The coalesced "values" event since the last flush, or None."""
        with self._lock:
            if not self._pending:
                return None
            data, self._pending = self._pending, {}
        return {"v": PROTOCOL_VERSION, "ev": "values", "now": time.time(), "data": data}

    def close(self):
        self._closed = True
        for name in list(self._jogs):
            self._end_jog(name, "client gone")
        self._jog_pool.shutdown(wait=False, cancel_futures=True)
        self._reader.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- plumbing ------------------------------------------------------------------------

    def _run(self, handler, request_id, args):
        try:
            data = handler(**args)
        except TypeError as exc:
            self._reply(request_id, error=ProtocolError(f"bad arguments: {exc}"))
        except Exception as exc:
            self._reply(request_id, error=exc)
        else:
            self._reply(request_id, data=data)

    def _reply(self, request_id, data=None, error=None):
        if self._closed:
            return
        message = {"v": PROTOCOL_VERSION, "re": request_id, "ok": error is None}
        if error is None:
            message["data"] = data
        else:
            message["error"] = _error(error)
        self.send(message)

    def _event(self, name, data):
        if not self._closed:
            self.send({"v": PROTOCOL_VERSION, "ev": name, "data": data})

    def _object(self, name):
        """An item this client has seen (a described row, a search hit), or
        else one the backend resolves by name -- e.g. a pinned item after a
        reload of the page."""
        with self._lock:
            obj = self._objects.get(name)
        if obj is None:
            obj = self.backend.resolve(name)
            with self._lock:
                self._objects[name] = obj
        return obj

    def _may_write(self, name):
        if self.writes is True:
            return True
        if not self.writes:
            return False
        return name in self.writes

    def _check_write(self, name):
        if not self._may_write(name):
            raise WritesDisabled(f"writing {name} is not enabled for this web session")
        return self._object(name)

    def _on_result(self, index, ok, payload):  # LiveReader pool threads
        name = self._names[index]
        if ok:
            self._last[name] = payload
            encoded = viewmodels.encode_value(payload)
            encoded["state"] = self._reader.monitor_state(index) or "polled"
        else:
            encoded = {"error": str(payload), "state": "error"}
        encoded["t"] = time.time()
        with self._lock:
            self._pending[name] = encoded

    # -- operations ------------------------------------------------------------------------

    def _op_hello(self, client_version=None, client_id=None):
        if client_id:
            self.client_id = client_id
        return {
            "protocol": PROTOCOL_VERSION,
            "label": self.backend.label,
            "mode": "session",
            "writes": bool(self.writes),
            "flush_interval": FLUSH_INTERVAL,
            "jog_repeat": JOG_REPEAT,
        }

    def _op_ping(self):
        """The client's liveness check: a network drop without a close
        frame (phone changing networks, tunnel dying) is only noticed by a
        reply that does not come."""
        return {}

    def _op_names(self):
        return self.backend.names()

    def _op_describe(self, name):
        with self._lock:
            obj = self._objects.get(name)
        if obj is None:
            obj = self.backend.resolve(name)  # top-level: may initialize a lazy one
        view, objects = viewmodels.describe(obj, name)
        objects.setdefault(name, obj)
        with self._lock:
            self._objects.update(objects)
        for row in view["rows"]:
            row["writable"] = row["writable"] and self._may_write(row["name"])
        view["writable"] = self._may_write(name)
        return view

    def _op_subscribe(self, names):
        for name in names:
            obj = self._object(name)
            with self._lock:
                index = self._index.get(name)
                if index is None:
                    self._names.append(name)
                    index = self._index[name] = self._reader.add(obj)
                    continue
            self._reader.set_active(index, True)
        return {"subscribed": list(names)}

    def _op_unsubscribe(self, names):
        for name in names:
            with self._lock:
                index = self._index.get(name)
                self._pending.pop(name, None)
            if index is not None:
                self._reader.set_active(index, False)
        return {"unsubscribed": list(names)}

    def _start_write(self, name, target_fn):
        obj = self._check_write(name)
        holder = self._holders.setdefault(name, {})

        def on_state(state, message):
            self._event("write", {"name": name, "state": state, "message": message})

        def on_finished():
            index = self._index.get(name)
            if index is not None:
                self._reader.request_read(index)

        self._event("write", {"name": name, "state": "busy", "message": ""})
        live_values.start_write(obj, target_fn, on_state, on_finished=on_finished, holder=holder)
        logger.info("web client %s writes %s", self.client_id, name)
        return {"started": name}

    def _op_set(self, name, value):
        last = self._last.get(name)
        target = viewmodels.coerce_target(value, last) if last is not None else value
        return self._start_write(name, lambda: target)

    def _op_step(self, name, sign, step):
        sign = 1 if float(sign) > 0 else -1
        step = abs(float(step))
        obj = self._check_write(name)
        return self._start_write(name, lambda: obj.get_current_value() + sign * step)

    def _op_stop(self, name):
        obj = self._check_write(name)
        if name in self._jogs:
            self._end_jog(name, "stopped")
        holder = self._holders.get(name, {})

        def on_state(state, message):
            self._event("write", {"name": name, "state": state, "message": message})

        live_values.stop_move(obj, holder.get("changer"), on_state)
        return {"stopping": name}

    def _op_trigger(self, name):
        obj = self._check_write(name)

        def run():
            try:
                obj.trigger()
                self._event("write", {"name": name, "state": "idle", "message": "triggered"})
            except Exception as exc:
                self._event("write", {"name": name, "state": "error", "message": live_values.describe(exc)})

        threading.Thread(target=run, daemon=True).start()
        return {"triggered": name}

    def _op_search(self, query, limit=40):
        """Readable items of the initialized components whose name contains
        every word of `query` -- for picking something to watch next to the
        current view. Lazy components are not searched (that would
        initialize them)."""
        words = [w for w in str(query).lower().split() if w]
        hits = []
        for name, kind, obj in self.backend.search_index():
            low = name.lower()
            if all(w in low for w in words):
                hits.append((not low.startswith(words[0]) if words else False, len(name), name, kind, obj))
        hits.sort(key=lambda h: h[:3])
        hits = hits[: max(1, min(int(limit), 200))]
        with self._lock:
            for _, _, name, _, obj in hits:
                self._objects.setdefault(name, obj)
        return {"hits": [{"name": name, "kind": kind} for _, _, name, kind, _ in hits],
                "searched": "initialized components"}

    def _op_details(self, name):
        """The Details of the grids' "⋯" menu (eco.widgets.item_menu)."""
        from eco.widgets.item_menu import describe_item

        rows = describe_item(self._object(name))
        return {"rows": [[field, viewmodels._clip(str(text))] for field, text in rows]}

    def _op_step_size(self, name):
        """The tweak step a ▲/▼ pair starts with (the grids' initial_step:
        the adjustable's own tweak step, else 1 / 0.1)."""
        obj = self._object(name)
        value = self._last.get(name)
        if value is None:
            value = obj.get_current_value()
        return {"step": live_values.initial_step(obj, value)}

    # -- jog: move while a button is held ------------------------------------------------------

    def _op_jog(self, name, direction, start=True):
        """Start (or keep going: the client repeats it every JOG_REPEAT s
        while the button is held) or stop a hardware jog. Only for items
        with one (viewmodels.jog_speed_item). The motor record jog writes
        JOGF/JOGR directly, past the Changer every other write goes through,
        so the access gate and queue claims are checked here."""
        obj = self._check_write(name)
        if viewmodels.jog_speed_item(obj) is None:
            raise ProtocolError(f"{name} has no hardware jog")
        direction = 1 if float(direction) > 0 else -1
        if not start:
            self._end_jog(name, "")
            return {"jogging": None}
        with self._lock:
            active = self._jogs.get(name)
            if active is not None and active["direction"] == direction:
                active["alive"] = time.monotonic()
                return {"jogging": direction}
        if active is not None:
            self._end_jog(name, "")
        from eco.devices_general.utilities import _access_gate, _claim_gate

        _access_gate(obj)
        _claim_gate(obj)
        obj.jog(direction, start=True)
        with self._lock:
            self._jogs[name] = {"obj": obj, "direction": direction, "alive": time.monotonic()}
            if self._jog_watch is None or not self._jog_watch.is_alive():
                self._jog_watch = threading.Thread(target=self._watch_jogs, daemon=True,
                                                   name="eco-web-jog-deadman")
                self._jog_watch.start()
        logger.info("web client %s jogs %s %+d", self.client_id, name, direction)
        self._event("write", {"name": name, "state": "moving", "message": f"jogging {'+' if direction > 0 else '−'}"})
        return {"jogging": direction}

    def _op_jog_speed(self, name, value):
        """Set the jog speed of `name` (its own jog_speed item); allowed
        whenever writing `name` is."""
        obj = self._check_write(name)
        speed = viewmodels.jog_speed_item(obj)
        if speed is None:
            raise ProtocolError(f"{name} has no hardware jog")
        value = float(value)
        if not value > 0:
            raise ValueError("the jog speed must be positive")
        speed_name = viewmodels.full_name(speed)
        with self._lock:
            self._objects.setdefault(speed_name, speed)
        holder = self._holders.setdefault(speed_name, {})

        def on_state(state, message):
            self._event("write", {"name": speed_name, "state": state, "message": message})

        def on_finished():
            index = self._index.get(speed_name)
            if index is not None:
                self._reader.request_read(index)

        live_values.start_write(speed, lambda: value, on_state, on_finished=on_finished, holder=holder)
        return {"started": speed_name}

    def _end_jog(self, name, message):
        with self._lock:
            active = self._jogs.pop(name, None)
        if active is None:
            return
        try:
            active["obj"].jog_stop()
        except Exception as exc:
            logger.warning("web client %s: stopping the jog of %s failed: %s", self.client_id, name, exc)
            self._event("write", {"name": name, "state": "error",
                                  "message": f"stopping the jog failed: {live_values.describe(exc)}"})
            return
        index = self._index.get(name)
        if index is not None:
            self._reader.request_read(index)
        self._event("write", {"name": name, "state": "idle", "message": message})

    def _watch_jogs(self):
        while True:
            time.sleep(0.1)
            now = time.monotonic()
            with self._lock:
                if not self._jogs:
                    self._jog_watch = None
                    return
                late = [name for name, j in self._jogs.items() if now - j["alive"] > JOG_DEADMAN]
            for name in late:
                logger.warning("web client %s: jog of %s stopped, no signal from the client", self.client_id, name)
                self._end_jog(name, "jog stopped: no signal from the client")
