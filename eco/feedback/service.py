"""Transport-free API of a :class:`~eco.feedback.core.FeedbackManager`.

Every HTTP route of :mod:`eco.feedback.server` is a thin wrapper around one
method here, and :class:`FeedbackService` has the same method names,
arguments and (JSON-compatible) return values as the HTTP
:class:`~eco.feedback.client.FeedbackClient`. So the control panel and any
script can drive a manager either in-process (``manager.service``, no
fastapi needed) or remotely (``FeedbackClient(url)``) without changes.

Errors are :class:`FeedbackError` subclasses carrying the HTTP status code
the server answers with.
"""

from __future__ import annotations

import time
from dataclasses import asdict

LOOP_SETTINGS = ("setpoint", "rate_hz", "kp", "ki", "kd", "enabled",
                 "alarm_after_s", "auto_disable_on_alarm")
MATRIX_LOOP_SETTINGS = ("gain", "rcond", "enabled", "reference", "weights")
MEASUREMENT_SETTINGS = ("step", "settle_s", "repeats", "bidirectional")
MATRIX_LOOP_CREATE = ("gain", "rcond", "rate_hz", "out_min", "out_max",
                      "alarm_after_s", "auto_disable_on_alarm", "interlocks")


class FeedbackError(Exception):
    status_code = 400


class BadRequest(FeedbackError):
    status_code = 400


class NotFound(FeedbackError):
    status_code = 404


class Conflict(FeedbackError):
    status_code = 409


def _check_keys(settings, allowed, what):
    unknown = sorted(set(settings) - set(allowed))
    if unknown:
        raise BadRequest(f"unknown {what} setting(s) {unknown}; "
                         f"allowed: {list(allowed)}")


def _drop_none(settings):
    return {k: v for k, v in settings.items() if v is not None}


class FeedbackService:
    """In-process API of one manager (``manager.service``)."""

    url = None  # no HTTP server behind this one

    def __init__(self, manager):
        self.manager = manager

    # -- lookups -----------------------------------------------------------
    def _loop(self, name):
        try:
            return self.manager.loops[name]
        except KeyError:
            raise NotFound(f"no loop {name!r}") from None

    def _measurement(self, name):
        try:
            return self.manager.measurements[name]
        except KeyError:
            raise NotFound(f"no response-matrix measurement {name!r}") from None

    def _matrix_loop(self, name):
        try:
            return self.manager.matrix_loops[name]
        except KeyError:
            raise NotFound(f"no matrix loop {name!r}") from None

    def _interlock(self, name):
        try:
            return self.manager.interlocks[name]
        except KeyError:
            raise NotFound(f"no interlock {name!r}") from None

    def _check_names(self, adjustable_names, detector_names):
        unknown_adj = [n for n in adjustable_names if n not in self.manager.adjustables]
        unknown_det = [n for n in detector_names if n not in self.manager.detectors]
        if unknown_adj or unknown_det:
            raise NotFound(f"unknown actuators {unknown_adj}, "
                           f"unknown detectors {unknown_det}")

    # -- loops ---------------------------------------------------------------
    def loops(self) -> dict:
        return {n: asdict(l.status()) for n, l in self.manager.loops.items()}

    def status(self, name) -> dict:
        return asdict(self._loop(name).status())

    def set(self, name, **settings) -> dict:
        """e.g. ``set("pointing_x", setpoint=1.2, kp=0.5, enabled=True)``.
        Setting ``enabled`` resets the PID's integral term."""
        settings = _drop_none(settings)
        _check_keys(settings, LOOP_SETTINGS, "loop")
        loop = self._loop(name)
        if "setpoint" in settings:
            loop.setpoint = float(settings["setpoint"])
        if "rate_hz" in settings:
            rate = float(settings["rate_hz"])
            if rate <= 0:
                raise BadRequest("rate_hz must be > 0")
            loop.rate_hz = rate
        for k in ("kp", "ki", "kd"):
            if k in settings:
                setattr(loop.pid, k, float(settings[k]))
        if "alarm_after_s" in settings:
            loop.alarm_after_s = float(settings["alarm_after_s"])
        if "auto_disable_on_alarm" in settings:
            loop.auto_disable_on_alarm = bool(settings["auto_disable_on_alarm"])
        if "enabled" in settings:
            loop.enable() if settings["enabled"] else loop.disable()
        return asdict(loop.status())

    def history(self, name, n=200, archive=False) -> dict:
        self._loop(name)
        if archive:
            if self.manager.store is None:
                raise BadRequest("no persistent store configured")
            return self.manager.store.history(name, limit=n)
        hist = list(self._loop(name).history)[-n:]
        return {
            "t": [h[0] for h in hist],
            "value": [h[1] for h in hist],
            "output": [h[2] for h in hist],
        }

    # -- registries --------------------------------------------------------
    def actuators(self) -> list:
        return sorted(self.manager.adjustables)

    def detectors(self) -> list:
        return sorted(self.manager.detectors)

    # -- response matrices -------------------------------------------------
    def start_response_matrix(self, name, adjustable_names, detector_names,
                              **settings) -> dict:
        _check_keys(settings, MEASUREMENT_SETTINGS, "measurement")
        meas = self.manager.measurements.get(name)
        if meas is not None and meas.state == "running":
            raise Conflict(f"measurement {name!r} already running")
        self._check_names(adjustable_names, detector_names)
        meas = self.manager.measure_response_matrix(
            name, list(adjustable_names), list(detector_names), **settings)
        return asdict(meas.status())

    def response_matrices(self) -> dict:
        return {n: asdict(m.status()) for n, m in self.manager.measurements.items()}

    def response_matrix_status(self, name) -> dict:
        schedules = self.manager.schedules
        if name not in self.manager.measurements:
            if name in schedules:
                return {"name": name, "state": "scheduled",
                        "schedule": schedules[name].status()}
            raise NotFound(f"no response-matrix measurement {name!r}")
        result = asdict(self._measurement(name).status())
        if name in schedules:
            result["schedule"] = schedules[name].status()
        return result

    def delete_response_matrix(self, name) -> dict:
        self._measurement(name)
        del self.manager.measurements[name]
        return {"deleted": name}

    def schedule_response_matrix(self, name, adjustable_names, detector_names,
                                 interval_s, **settings) -> dict:
        _check_keys(settings, MEASUREMENT_SETTINGS, "measurement")
        if name in self.manager.schedules:
            raise Conflict(f"schedule {name!r} already running")
        self._check_names(adjustable_names, detector_names)
        self.manager.schedule_response_matrix(
            name, list(adjustable_names), list(detector_names), float(interval_s),
            **settings)
        return self.manager.schedules[name].status()

    def unschedule_response_matrix(self, name) -> dict:
        if name not in self.manager.schedules:
            raise NotFound(f"no schedule {name!r}")
        self.manager.unschedule_response_matrix(name)
        return {"stopped": name}

    # -- matrix loops --------------------------------------------------------
    def matrix_loops(self) -> dict:
        return {n: asdict(l.status()) for n, l in self.manager.matrix_loops.items()}

    def create_matrix_loop(self, name, matrix_name, **settings) -> dict:
        _check_keys(settings, MATRIX_LOOP_CREATE, "matrix loop")
        if name in self.manager.matrix_loops:
            raise Conflict(f"matrix loop {name!r} already exists")
        if matrix_name not in self.manager.matrices:
            raise NotFound(f"no response matrix {matrix_name!r}")
        unknown = [n for n in settings.get("interlocks", ())
                   if n not in self.manager.interlocks]
        if unknown:
            raise NotFound(f"unknown interlocks {unknown}")
        loop = self.manager.add_matrix_loop(name, matrix_name, **settings)
        return asdict(loop.status())

    def matrix_loop_status(self, name) -> dict:
        return asdict(self._matrix_loop(name).status())

    def set_matrix_loop(self, name, **settings) -> dict:
        """``reference``/``weights`` are merged per key, not replaced."""
        settings = _drop_none(settings)
        _check_keys(settings, MATRIX_LOOP_SETTINGS, "matrix loop")
        loop = self._matrix_loop(name)
        if "gain" in settings:
            loop.gain = float(settings["gain"])
        if "rcond" in settings:
            loop.rcond = float(settings["rcond"])
        if settings.get("reference"):
            loop.reference.update(settings["reference"])
        if settings.get("weights"):
            loop.weights.update(settings["weights"])
        if "enabled" in settings:
            loop.enable() if settings["enabled"] else loop.disable()
        return asdict(loop.status())

    def delete_matrix_loop(self, name) -> dict:
        loop = self.manager.matrix_loops.pop(name, None)
        if loop is None:
            raise NotFound(f"no matrix loop {name!r}")
        loop.stop()
        return {"deleted": name}

    def matrix_loop_history(self, name, n=200) -> dict:
        hist = list(self._matrix_loop(name).history)[-n:]
        return {
            "t": [h[0] for h in hist],
            "values": [h[1] for h in hist],
            "outputs": [h[2] for h in hist],
        }

    def save_reference(self, name) -> dict:
        loop = self._matrix_loop(name)
        loop.save_reference()
        return asdict(loop.status())

    # -- interlocks ----------------------------------------------------------
    def interlocks(self) -> dict:
        return {n: il.status() for n, il in self.manager.interlocks.items()}

    def force_interlock(self, name, value) -> dict:
        il = self._interlock(name)
        il.force(value)
        return il.status()

    def clear_interlock(self, name) -> dict:
        il = self._interlock(name)
        il.clear_force()
        return il.status()

    # -- alarms / health -----------------------------------------------------
    def alarms(self) -> dict:
        out = {}
        for loops in (self.manager.loops, self.manager.matrix_loops):
            for n, l in loops.items():
                s = l.status()
                if s.alarmed:
                    out[n] = asdict(s)
        return out

    def health(self) -> dict:
        now = time.time()

        def loop_health(loop):
            grace = max(2.0 / loop.rate_hz, 1.0)
            # a write that is still waiting for a slow move is not a hang
            grace = max(grace, getattr(loop, "write_timeout", 0) or 0)
            age = now - loop._last_cycle_ts
            return {"alive": age < grace and loop._thread.is_alive(),
                    "last_cycle_age_s": age}

        loops = {n: loop_health(l) for n, l in self.manager.loops.items()}
        matrix_loops = {n: loop_health(l) for n, l in self.manager.matrix_loops.items()}
        all_alive = all(v["alive"] for v in {**loops, **matrix_loops}.values())
        return {
            "status": "ok" if all_alive else "degraded",
            "uptime_s": now - self.manager._started_at,
            "loops": loops,
            "matrix_loops": matrix_loops,
        }

    # -- config ----------------------------------------------------------------
    def save_config(self, path=None) -> dict:
        path = path or self.manager.config_path
        if not path:
            raise BadRequest("no config path configured or given")
        from . import persistence

        persistence.save_config(self.manager, path)
        return {"saved": str(path)}

    def load_config(self, path=None) -> dict:
        path = path or self.manager.config_path
        if not path:
            raise BadRequest("no config path configured or given")
        from . import persistence

        persistence.load_config(self.manager, path)
        return {"loaded": str(path)}

    # -- logs ------------------------------------------------------------------
    def logs(self, n=200, archive=False) -> list:
        if archive:
            if self.manager.store is None:
                raise BadRequest("no persistent store configured")
            return self.manager.store.logs(n)
        return self.manager.log.entries(n)
