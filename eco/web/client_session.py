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
        try:
            self._pool.submit(self._run, handler, request_id, args)
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
        with self._lock:
            obj = self._objects.get(name)
        if obj is None:
            raise NotFound(f"{name!r} is not open in this client (describe it first)")
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

    def _op_step_size(self, name):
        """The tweak step a ▲/▼ pair starts with (the grids' initial_step:
        the adjustable's own tweak step, else 1 / 0.1)."""
        obj = self._object(name)
        value = self._last.get(name)
        if value is None:
            value = obj.get_current_value()
        return {"step": live_values.initial_step(obj, value)}
