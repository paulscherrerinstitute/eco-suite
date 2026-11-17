"""Feedback core.

Works directly with eco conventions: a *detector* is anything with
``get_current_value()`` (or ``get()``, or a plain zero-argument callable),
an *adjustable* is anything with ``get_current_value()``/``set_target_value()``
(or ``get()``/``set()``). No wrapping required. Inside an eco session both
can also be given as dotted alias names (``"mono.roll1_piezo"``), resolved
in the session's namespace when the loop is created.

Differences to a plain-Python feedback that matter with real hardware:

- a cycle that fails (a read returning ``None`` -- pyepics does that on a
  timed-out get --, a ``MoveBlocked`` from a queue task's claim, a limit
  violation) is logged and skipped; the loop thread survives and keeps
  trying, and the failure shows in the loop's status (``error``);
- a write waits for the returned eco ``Changer`` (``write_timeout``), so a
  motor is never sent a new target while it is still moving and a
  response-matrix step is measured after the move, not during it;
- every worker thread attaches to pyepics' initial CA context.
"""

from __future__ import annotations

import atexit
import math
import threading
import time
import weakref
from collections import deque
from dataclasses import dataclass, field

import numpy as np

#: Default time a write may take before the cycle is counted as failed.
WRITE_TIMEOUT_S = 30.0

_MANAGERS = weakref.WeakSet()


@atexit.register
def _stop_managers():
    """At interpreter exit: open every loop and stop its thread, so nothing
    writes to hardware -- or sits in C code -- while Python finalizes."""
    for manager in list(_MANAGERS):
        try:
            manager.stop_all()
        except Exception:
            pass


def _thread_init():
    """Attach a worker thread to pyepics' CA context (if pyepics is loaded).

    Threads that touch channel access without this get their own context,
    which pyepics does not support well. Harmless when epics was never
    imported."""
    import sys

    if "epics" not in sys.modules:
        return
    try:
        from epics import ca

        ca.use_initial_context()
    except Exception:
        pass


def _read(obj):
    """Read a value from an eco-like object (or call a plain callable)."""
    for name in ("get_current_value", "get"):
        f = getattr(obj, name, None)
        if callable(f):
            return f()
    if callable(obj):
        return obj()
    raise TypeError(f"{obj!r} has no get_current_value()/get()")


def _read_scalar(obj, what="detector"):
    """Read a value and insist on one finite number.

    ``None`` (failed CA get), arrays with more than one element and NaN are
    errors here -- feeding any of them into a PID would move hardware on
    garbage. Wrap array detectors in a function that reduces them."""
    value = _read(obj)
    if value is None:
        raise ValueError(f"{what} {object_name(obj)!r} returned None")
    arr = np.asarray(value, dtype=float)
    if arr.size != 1:
        raise ValueError(
            f"{what} {object_name(obj)!r} returned {arr.size} values, "
            f"a feedback needs one number (reduce it in a wrapper function)")
    value = float(arr.reshape(-1)[0])
    if not math.isfinite(value):
        raise ValueError(f"{what} {object_name(obj)!r} returned {value}")
    return value


def _write(obj, value, timeout=WRITE_TIMEOUT_S):
    """Write a value to an eco-like adjustable and wait for it to finish.

    eco's ``set_target_value`` returns a ``Changer``; waiting for it keeps
    the loop from stacking moves on a motor that has not arrived yet."""
    for name in ("set_target_value", "set"):
        f = getattr(obj, name, None)
        if callable(f):
            ret = f(value)
            wait = getattr(ret, "wait", None)
            if callable(wait) and timeout != 0:
                try:
                    wait(timeout=timeout)
                except TypeError:  # a wait() without timeout argument
                    wait()
            return ret
    raise TypeError(f"{obj!r} has no set_target_value()/set()")


def object_name(obj):
    """The best name for an eco object: its full alias name if it has one."""
    alias = getattr(obj, "alias", None)
    if alias is not None:
        try:
            return alias.get_full_name()
        except Exception:
            pass
    name = getattr(obj, "name", None)
    if isinstance(name, str) and name:
        return name
    return getattr(obj, "__name__", None) or repr(obj)


def resolve(obj):
    """Resolve a dotted alias name (``"mono.roll1_piezo"``) in the session.

    Looks up the first part in the IPython user namespace (where an eco
    session puts its top-level components), then in ``__main__``; anything
    that is not a string is returned unchanged."""
    if not isinstance(obj, str):
        return obj
    head, *rest = obj.split(".")
    namespaces = []
    try:
        from IPython import get_ipython

        ip = get_ipython()
        if ip is not None:
            namespaces.append(ip.user_ns)
    except Exception:
        pass
    import __main__

    namespaces.append(vars(__main__))
    for ns in namespaces:
        if head in ns:
            value = ns[head]
            break
    else:
        raise NameError(f"{head!r} (from {obj!r}) is not defined in this session")
    for part in rest:
        value = getattr(value, part)
    return value


class EventLog:
    """Bounded, thread-safe log shared by feedback loops and measurements.

    ``on_log``, if given, is called with each entry after it's appended --
    used to additionally forward entries to a :class:`persistence.PersistentStore`.
    """

    def __init__(self, maxlen=2000, on_log=None):
        self._entries = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.on_log = on_log

    def log(self, level, source, message):
        entry = {"ts": time.time(), "level": level,
                 "source": source, "message": message}
        with self._lock:
            self._entries.append(entry)
        if self.on_log is not None:
            self.on_log(entry)

    def entries(self, n=200) -> list[dict]:
        with self._lock:
            return list(self._entries)[-n:]


class Interlock:
    """A named permit condition. ``condition`` is duck-typed like a detector
    (``get_current_value()``/``get()``) or a plain zero-arg callable; truthy
    means "ok to correct". A real EPICS "beam permit" PV plugs in directly.

    ``force()`` overrides the live condition (commissioning/emergency
    bypass); ``clear_force()`` goes back to reading the live condition.
    """

    def __init__(self, name, condition):
        self.name = name
        self.condition = condition
        self.forced: bool | None = None

    def ok(self) -> bool:
        if self.forced is not None:
            return self.forced
        try:
            return bool(_read(self.condition))
        except Exception:
            return False  # fail safe: an unreadable permit is not a permit

    def force(self, value: bool):
        self.forced = bool(value)

    def clear_force(self):
        self.forced = None

    def status(self) -> dict:
        return {"name": self.name, "ok": self.ok(), "forced": self.forced}


class PID:
    """Textbook PID with output limits and anti-windup."""

    def __init__(self, kp=1.0, ki=0.0, kd=0.0, out_min=None, out_max=None):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.out_min, self.out_max = out_min, out_max
        self.reset()

    def reset(self):
        self._integral = 0.0
        self._prev_error = None

    def __call__(self, error, dt):
        p = self.kp * error
        d = 0.0
        if self._prev_error is not None and dt > 0:
            d = self.kd * (error - self._prev_error) / dt
        self._prev_error = error

        self._integral += error * dt
        out = p + self.ki * self._integral + d

        if self.out_max is not None and out > self.out_max:
            out = self.out_max
            self._integral -= error * dt  # anti-windup
        if self.out_min is not None and out < self.out_min:
            out = self.out_min
            self._integral -= error * dt
        return out


@dataclass
class LoopStatus:
    name: str
    enabled: bool
    setpoint: float
    rate_hz: float
    last_value: float | None
    last_output: float | None
    actual_rate_hz: float | None
    kp: float
    ki: float
    kd: float
    held: bool
    alarmed: bool
    error: str | None = None
    detector_name: str | None = None
    adjustable_name: str | None = None


class _CycleErrors:
    """Logs a repeating cycle failure once, and its recovery once."""

    def __init__(self, log):
        self._log = log
        self.message = None
        self.count = 0

    def failed(self, exc):
        msg = f"{type(exc).__name__}: {exc}"
        self.count += 1
        if msg != self.message:
            self._log("error", f"cycle failed, skipped ({msg})")
        self.message = msg

    def ok(self):
        if self.message is not None:
            self._log("info", f"cycles working again after {self.count} failure(s)")
        self.message = None
        self.count = 0


class FeedbackLoop:
    """One detector -> PID -> one adjustable, running in its own thread.

    The adjustable is driven *relatively*: each cycle applies
    ``current + pid(setpoint - measured)`` scaled by the loop period,
    i.e. the PID output is interpreted as a correction rate.

    The thread runs (and records the detector) from creation on; it only
    writes while ``enabled``. A failing cycle is skipped, never fatal.
    """

    def __init__(self, name, detector, adjustable, setpoint=0.0,
                 rate_hz=10.0, pid=None, history=1000, event_log=None,
                 store=None, interlocks=(), alarm_after_s=5.0,
                 auto_disable_on_alarm=False, write_timeout=WRITE_TIMEOUT_S):
        self.name = name
        self.detector = resolve(detector)
        self.adjustable = resolve(adjustable)
        self.write_timeout = write_timeout
        self._errors = _CycleErrors(self._log)
        self.setpoint = setpoint
        self.rate_hz = rate_hz
        self.pid = pid or PID()
        self.history = deque(maxlen=history)  # (t, value, output)
        self.enabled = False
        self.event_log = event_log
        self.store = store
        self.interlocks = list(interlocks)
        self.alarm_after_s = alarm_after_s
        self.auto_disable_on_alarm = auto_disable_on_alarm
        self._last_value = None
        self._last_output = None
        self._actual_rate = None
        self._saturated = False
        self._saturated_since = None
        self.alarmed = False
        self._held = False
        self._last_cycle_ts = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"feedback-{name}")
        self._thread.start()

    # -- control -----------------------------------------------------------
    def enable(self):
        self.pid.reset()
        self.enabled = True
        self._log("info", "enabled")

    def disable(self):
        self.enabled = False
        self._log("info", "disabled")

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    def status(self) -> LoopStatus:
        return LoopStatus(
            name=self.name, enabled=self.enabled, setpoint=self.setpoint,
            rate_hz=self.rate_hz, last_value=self._last_value,
            last_output=self._last_output, actual_rate_hz=self._actual_rate,
            kp=self.pid.kp, ki=self.pid.ki, kd=self.pid.kd,
            held=self._held, alarmed=self.alarmed,
            error=self._errors.message,
            detector_name=object_name(self.detector),
            adjustable_name=object_name(self.adjustable),
        )

    def _log(self, level, message):
        if self.event_log is not None:
            self.event_log.log(level, self.name, message)

    def _record_history(self, t, value, output):
        self.history.append((t, value, output))
        if self.store is not None:
            self.store.record_history(self.name, t, value, output)

    # -- loop --------------------------------------------------------------
    def _run(self):
        _thread_init()
        t_prev = time.perf_counter()
        while not self._stop.is_set():
            period = 1.0 / self.rate_hz
            t0 = time.perf_counter()
            try:
                self._cycle(t0 - t_prev)
                self._errors.ok()
            except Exception as exc:
                self._errors.failed(exc)
            t_prev = t0
            elapsed = time.perf_counter() - t0
            self._actual_rate = 1.0 / max(elapsed, period)
            self._last_cycle_ts = time.time()
            self._stop.wait(max(0.0, period - elapsed))

    def _cycle(self, dt):
        value = _read_scalar(self.detector)
        self._last_value = value

        if self.enabled and not all(il.ok() for il in self.interlocks):
            if not self._held:
                self._held = True
                self._log("warning", "held by interlock")
            self._record_history(time.time(), value, None)
        elif self.enabled:
            if self._held:
                self._held = False
                self._log("info", "interlock cleared, resuming")
            correction = self.pid(self.setpoint - value, dt)
            target = _read_scalar(self.adjustable, "adjustable") + correction * dt
            _write(self.adjustable, target, self.write_timeout)
            self._last_output = target
            self._record_history(time.time(), value, target)

            saturated = (
                (self.pid.out_max is not None and correction >= self.pid.out_max)
                or (self.pid.out_min is not None and correction <= self.pid.out_min)
            )
            now = time.time()
            if saturated:
                if self._saturated_since is None:
                    self._saturated_since = now
                    self._log("warning", "output saturated")
                elif not self.alarmed and now - self._saturated_since >= self.alarm_after_s:
                    self.alarmed = True
                    self._log("error", f"alarm: saturated for over {self.alarm_after_s:.0f}s")
                    if self.auto_disable_on_alarm:
                        self.disable()
            else:
                self._saturated_since = None
                if self.alarmed:
                    self.alarmed = False
                    self._log("info", "alarm cleared")
            self._saturated = saturated
        else:
            self._held = False
            self._record_history(time.time(), value, None)


@dataclass
class ResponseMatrix:
    """A measured actuator -> detector response matrix.

    ``matrix[i, j]`` is d(detector_i)/d(adjustable_j). Kept as a plain data
    object (not tied to any loop) so a closed-loop matrix corrector could
    consume it later without a data-model change.
    """

    adjustable_names: list[str]
    detector_names: list[str]
    matrix: np.ndarray
    step: dict
    timestamp: float = field(default_factory=time.time)

    def pinv(self, rcond=1e-15) -> np.ndarray:
        return np.linalg.pinv(self.matrix, rcond=rcond)

    def condition_number(self) -> float:
        return float(np.linalg.cond(self.matrix))

    def to_dict(self) -> dict:
        return {
            "adjustable_names": self.adjustable_names,
            "detector_names": self.detector_names,
            "matrix": self.matrix.tolist(),
            "step": self.step,
            "timestamp": self.timestamp,
            "condition_number": self.condition_number(),
        }


@dataclass
class MeasurementStatus:
    name: str
    state: str  # "pending" | "running" | "done" | "error"
    current_actuator: str | None
    step_index: int
    total_steps: int
    error: str | None
    adjustable_names: list[str]
    detector_names: list[str]
    matrix: list | None
    condition_number: float | None
    timestamp: float | None


class ResponseMatrixMeasurement:
    """Steps each actuator, records each detector's response, assembles a
    response matrix.

    Runs in its own background thread so it doesn't block the caller; poll
    ``.status()`` for progress. Any :class:`FeedbackLoop` currently driving
    an actuator being stepped is disabled for the duration of that
    actuator's step and re-enabled afterward.
    """

    def __init__(self, name, adjustable_names, adjustables, detector_names,
                 detectors, manager=None, event_log=None, step=0.1,
                 settle_s=0.2, repeats=3, bidirectional=True,
                 write_timeout=WRITE_TIMEOUT_S):
        self.name = name
        self.write_timeout = write_timeout
        self.adjustable_names = list(adjustable_names)
        self.adjustables = list(adjustables)
        self.detector_names = list(detector_names)
        self.detectors = list(detectors)
        self.manager = manager
        self.event_log = event_log
        self.step = step
        self.settle_s = settle_s
        self.repeats = max(1, repeats)
        self.bidirectional = bidirectional

        self.state = "pending"
        self.current_actuator = None
        self.step_index = 0
        self.error = None
        self.result: ResponseMatrix | None = None
        self._matrix = np.zeros((len(self.detectors), len(self.adjustables)))
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"response-matrix-{name}")

    def start(self):
        self.state = "running"
        self._thread.start()

    def _log(self, level, message):
        if self.event_log is not None:
            self.event_log.log(level, f"response-matrix:{self.name}", message)

    def _read_avg(self, obj):
        return sum(_read_scalar(obj) for _ in range(self.repeats)) / self.repeats

    def _step_delta(self, adj_name):
        return self.step[adj_name] if isinstance(self.step, dict) else self.step

    def _run(self):
        _thread_init()
        try:
            self._log("info", f"starting measurement over {len(self.adjustables)} "
                              f"actuator(s) x {len(self.detectors)} detector(s)")
            for j, (adj_name, adj) in enumerate(zip(self.adjustable_names, self.adjustables)):
                self.current_actuator = adj_name
                self.step_index = j
                delta = self._step_delta(adj_name)

                loop = self.manager.loop_for_adjustable(adj) if self.manager else None
                was_enabled = bool(loop and loop.enabled)
                if was_enabled:
                    loop.disable()
                    self._log("info", f"disabled loop on {adj_name!r} for measurement")

                try:
                    baseline = _read_scalar(adj, "adjustable")
                    if self.bidirectional:
                        _write(adj, baseline + delta, self.write_timeout)
                        time.sleep(self.settle_s)
                        after_plus = [self._read_avg(d) for d in self.detectors]

                        _write(adj, baseline - delta, self.write_timeout)
                        time.sleep(self.settle_s)
                        after_minus = [self._read_avg(d) for d in self.detectors]

                        for i in range(len(self.detectors)):
                            self._matrix[i, j] = (after_plus[i] - after_minus[i]) / (2 * delta)
                    else:
                        before = [self._read_avg(d) for d in self.detectors]
                        _write(adj, baseline + delta, self.write_timeout)
                        time.sleep(self.settle_s)
                        after = [self._read_avg(d) for d in self.detectors]

                        for i in range(len(self.detectors)):
                            self._matrix[i, j] = (after[i] - before[i]) / delta

                    _write(adj, baseline, self.write_timeout)
                    time.sleep(self.settle_s)
                finally:
                    if was_enabled:
                        loop.enable()
                        self._log("info", f"restored loop on {adj_name!r}")

                self._log("info", f"measured column {j + 1}/{len(self.adjustables)} "
                                  f"({adj_name!r})")

            self.result = ResponseMatrix(
                adjustable_names=list(self.adjustable_names),
                detector_names=list(self.detector_names),
                matrix=self._matrix.copy(),
                step={n: self._step_delta(n) for n in self.adjustable_names},
            )
            if self.manager is not None:
                self.manager.matrices[self.name] = self.result
            self.state = "done"
            self.current_actuator = None
            self._log("info", "measurement complete")
        except Exception as exc:
            self.state = "error"
            self.error = str(exc)
            self._log("error", f"measurement failed: {exc}")

    def status(self) -> MeasurementStatus:
        matrix = self._matrix.tolist() if self._matrix.size else None
        cond = None
        if self.result is not None:
            matrix = self.result.matrix.tolist()
            cond = self.result.condition_number()
        return MeasurementStatus(
            name=self.name, state=self.state, current_actuator=self.current_actuator,
            step_index=self.step_index, total_steps=len(self.adjustables),
            error=self.error, adjustable_names=self.adjustable_names,
            detector_names=self.detector_names, matrix=matrix,
            condition_number=cond,
            timestamp=self.result.timestamp if self.result else None,
        )


@dataclass
class MatrixLoopStatus:
    name: str
    matrix_name: str
    enabled: bool
    detector_names: list[str]
    adjustable_names: list[str]
    reference: dict
    weights: dict
    gain: float
    rcond: float
    rate_hz: float
    last_values: list | None
    last_outputs: list | None
    actual_rate_hz: float | None
    held: bool
    alarmed: bool
    condition_number: float
    error: str | None = None


class MatrixFeedbackLoop:
    """N detectors -> gain * pinv(response_matrix, rcond) @ error -> M
    actuators, continuously, in its own thread (same shape as
    :class:`FeedbackLoop`). A single scalar proportional gain applied every
    cycle is the standard SVD orbit-feedback scheme; it converges over
    repeated cycles the same way :class:`FeedbackLoop`'s rate-based output
    does (``current + correction * dt``).

    ``weights`` row-scales the matrix/error before pseudo-inversion, so
    setting a detector's weight to 0 masks it out of the correction without
    re-measuring. ``reference`` is the target detector vector (the "golden"
    values to correct toward), capturable live via :meth:`save_reference`.
    """

    def __init__(self, name, matrix_name, response_matrix: ResponseMatrix,
                 detectors, adjustables, gain=0.3, rcond=1e-3, rate_hz=10.0,
                 out_min=None, out_max=None, history=1000, event_log=None,
                 store=None, interlocks=(), alarm_after_s=5.0,
                 auto_disable_on_alarm=False, write_timeout=WRITE_TIMEOUT_S):
        self.name = name
        self.matrix_name = matrix_name
        self.write_timeout = write_timeout
        self._errors = _CycleErrors(self._log)
        self.response_matrix = response_matrix
        self.detector_names = list(response_matrix.detector_names)
        self.adjustable_names = list(response_matrix.adjustable_names)
        self.detectors = dict(zip(self.detector_names, detectors))
        self.adjustables = dict(zip(self.adjustable_names, adjustables))
        self.gain = gain
        self.rcond = rcond
        self.rate_hz = rate_hz
        self.out_min, self.out_max = out_min, out_max
        self.reference = {n: 0.0 for n in self.detector_names}
        self.weights = {n: 1.0 for n in self.detector_names}
        self.history = deque(maxlen=history)  # (t, values, outputs)
        self.enabled = False
        self.event_log = event_log
        self.store = store
        self.interlocks = list(interlocks)
        self.alarm_after_s = alarm_after_s
        self.auto_disable_on_alarm = auto_disable_on_alarm
        self._last_values = None
        self._last_outputs = None
        self._actual_rate = None
        self._saturated_since = None
        self.alarmed = False
        self._held = False
        self._last_cycle_ts = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"matrix-feedback-{name}")
        self._thread.start()

    # -- control -----------------------------------------------------------
    def enable(self):
        self.enabled = True
        self._log("info", "enabled")

    def disable(self):
        self.enabled = False
        self._log("info", "disabled")

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    def save_reference(self):
        """Capture the current detector readings as the new reference
        (the "save golden orbit" action)."""
        for name, det in self.detectors.items():
            self.reference[name] = _read_scalar(det)
        self._log("info", "reference saved from live detector values")

    def status(self) -> MatrixLoopStatus:
        return MatrixLoopStatus(
            name=self.name, matrix_name=self.matrix_name, enabled=self.enabled,
            detector_names=self.detector_names, adjustable_names=self.adjustable_names,
            reference=dict(self.reference), weights=dict(self.weights),
            gain=self.gain, rcond=self.rcond, rate_hz=self.rate_hz,
            last_values=self._last_values, last_outputs=self._last_outputs,
            actual_rate_hz=self._actual_rate, held=self._held, alarmed=self.alarmed,
            condition_number=self.response_matrix.condition_number(),
            error=self._errors.message,
        )

    def _log(self, level, message):
        if self.event_log is not None:
            self.event_log.log(level, f"matrix:{self.name}", message)

    def _clamp(self, adj_name, value):
        lo = self.out_min[adj_name] if isinstance(self.out_min, dict) else self.out_min
        hi = self.out_max[adj_name] if isinstance(self.out_max, dict) else self.out_max
        if hi is not None and value > hi:
            return hi, True
        if lo is not None and value < lo:
            return lo, True
        return value, False

    # -- loop --------------------------------------------------------------
    def _run(self):
        _thread_init()
        while not self._stop.is_set():
            period = 1.0 / self.rate_hz
            t0 = time.perf_counter()
            try:
                self._cycle()
                self._errors.ok()
            except Exception as exc:
                self._errors.failed(exc)
            elapsed = time.perf_counter() - t0
            self._actual_rate = 1.0 / max(elapsed, period)
            self._last_cycle_ts = time.time()
            self._stop.wait(max(0.0, period - elapsed))

    def _cycle(self):
        values = {n: _read_scalar(d) for n, d in self.detectors.items()}
        self._last_values = [values[n] for n in self.detector_names]

        permitted = all(il.ok() for il in self.interlocks)
        if self.enabled and not permitted:
            if not self._held:
                self._held = True
                self._log("warning", "held by interlock")
            self._record_history(time.time(), self._last_values, None)
        elif self.enabled:
            if self._held:
                self._held = False
                self._log("info", "interlock cleared, resuming")

            weight_vec = np.array([self.weights[n] for n in self.detector_names])
            error = np.array([self.reference[n] - values[n] for n in self.detector_names])
            r_weighted = self.response_matrix.matrix * weight_vec[:, None]
            pinv = np.linalg.pinv(r_weighted, rcond=self.rcond)
            corrections = self.gain * (pinv @ (error * weight_vec))

            outputs, saturated = [], False
            for adj_name, correction in zip(self.adjustable_names, corrections):
                adj = self.adjustables[adj_name]
                target = _read_scalar(adj, "adjustable") + float(correction)
                target, clamped = self._clamp(adj_name, target)
                _write(adj, target, self.write_timeout)
                outputs.append(target)
                saturated = saturated or clamped
            self._last_outputs = outputs
            self._record_history(time.time(), self._last_values, outputs)

            now = time.time()
            if saturated:
                if self._saturated_since is None:
                    self._saturated_since = now
                    self._log("warning", "output saturated")
                elif not self.alarmed and now - self._saturated_since >= self.alarm_after_s:
                    self.alarmed = True
                    self._log("error", f"alarm: saturated for over {self.alarm_after_s:.0f}s")
                    if self.auto_disable_on_alarm:
                        self.disable()
            else:
                self._saturated_since = None
                if self.alarmed:
                    self.alarmed = False
                    self._log("info", "alarm cleared")
        else:
            self._held = False
            self._record_history(time.time(), self._last_values, None)

    def _record_history(self, t, values, outputs):
        self.history.append((t, values, outputs))
        if self.store is None:
            return
        # The persistent store's schema is per-channel (loop, ts, value,
        # output); a matrix loop has no single detector<->actuator pairing,
        # so each detector and each actuator gets its own named series,
        # using the "value" column for both (uniform, still fully queryable).
        for name, v in zip(self.detector_names, values or ()):
            self.store.record_history(f"{self.name}:{name}", t, v, None)
        for name, o in zip(self.adjustable_names, outputs or ()):
            self.store.record_history(f"{self.name}:{name}", t, o, None)


class ScheduledMeasurement:
    """Re-runs a response-matrix measurement on a fixed interval until
    stopped, so the matrix stays current as machine conditions drift
    instead of relying purely on manual one-off runs."""

    def __init__(self, manager, name, adjustable_names, detector_names,
                 interval_s, **kwargs):
        self.manager = manager
        self.name = name
        self.adjustable_names = adjustable_names
        self.detector_names = detector_names
        self.interval_s = interval_s
        self.kwargs = kwargs
        self.next_run = time.time() + interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"scheduled-response-matrix-{name}")
        self._thread.start()

    def _run(self):
        _thread_init()
        while not self._stop.wait(max(0.0, self.next_run - time.time())):
            self.manager.measure_response_matrix(
                self.name, self.adjustable_names, self.detector_names, **self.kwargs)
            self.next_run = time.time() + self.interval_s

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    def status(self) -> dict:
        return {"name": self.name, "interval_s": self.interval_s,
                "next_run_in_s": max(0.0, self.next_run - time.time())}


class FeedbackManager:
    """Holds named loops plus actuator/detector registries and response-matrix
    measurements; the single object exposed by the server.

    In an eco session::

        from eco.feedback import FeedbackManager, PID
        fb = FeedbackManager()
        fb.add("xpos", "mon_opt.xpos", "mono.roll1_piezo", rate_hz=1,
               pid=PID(ki=0.1, out_min=-1, out_max=1))
        fb.service.set("xpos", setpoint=0.0, enabled=True)   # same calls as the HTTP client
        fb.panel()                    # ipywidgets control panel, no server needed
        srv = fb.serve(port=8010)     # optional: HTTP server in a background thread
    """

    def __init__(self, store=None, config_path=None):
        self.loops: dict[str, FeedbackLoop] = {}
        self.matrix_loops: dict[str, MatrixFeedbackLoop] = {}
        self.adjustables: dict[str, object] = {}
        self.detectors: dict[str, object] = {}
        self.measurements: dict[str, ResponseMatrixMeasurement] = {}
        self.matrices: dict[str, ResponseMatrix] = {}
        self.interlocks: dict[str, Interlock] = {}
        self.schedules: dict[str, ScheduledMeasurement] = {}
        self.store = store
        self.config_path = config_path
        self._started_at = time.time()
        self.log = EventLog(on_log=store.record_log if store else None)
        _MANAGERS.add(self)

    def add(self, name, detector, adjustable, interlocks=(), **kwargs) -> FeedbackLoop:
        if name in self.loops:
            raise ValueError(f"loop {name!r} already exists")
        resolved_interlocks = [self.interlocks[n] for n in interlocks]
        loop = FeedbackLoop(name, detector, adjustable, event_log=self.log,
                            store=self.store, interlocks=resolved_interlocks, **kwargs)
        self.loops[name] = loop
        self._register(self.detectors, loop.detector, f"{name}_detector")
        self._register(self.adjustables, loop.adjustable, f"{name}_adjustable")
        return loop

    @staticmethod
    def _register(registry, obj, fallback):
        for known in registry.values():
            if known is obj:
                return
        name = object_name(obj)
        if name.startswith("<") or name in registry:
            name = fallback
        registry[name] = obj

    def add_actuator(self, name, adjustable=None):
        """Register a free-standing adjustable for response-matrix acquisition
        (not driven by any FeedbackLoop). ``add_actuator("mono.roll1_piezo")``
        registers a session component under its alias name."""
        if adjustable is None:
            adjustable = name
        self.adjustables[name] = resolve(adjustable)

    def add_detector(self, name, detector=None):
        """Register a free-standing detector for response-matrix acquisition."""
        if detector is None:
            detector = name
        self.detectors[name] = resolve(detector)

    def add_interlock(self, name, condition) -> Interlock:
        interlock = Interlock(name, resolve(condition))
        self.interlocks[name] = interlock
        return interlock

    def remove(self, name):
        self.loops.pop(name).stop()

    def disable_all(self):
        """Open every loop (single and matrix); threads keep monitoring."""
        for loop in list(self.loops.values()) + list(self.matrix_loops.values()):
            if loop.enabled:
                loop.disable()

    # -- front-ends ----------------------------------------------------------
    @property
    def service(self):
        """The transport-free API: same methods and return values as
        :class:`eco.feedback.client.FeedbackClient`, called in-process."""
        if getattr(self, "_service", None) is None:
            from .service import FeedbackService

            self._service = FeedbackService(self)
        return self._service

    def panel(self, refresh_s=1.0):
        """The ipywidgets control panel on this manager, without a server
        (JupyterLab / Voila / notebook front-ends of eco)."""
        from .gui import FeedbackPanel

        panel = FeedbackPanel(client=self.service, refresh_s=refresh_s)
        self._panels = [p for p in getattr(self, "_panels", []) if not p._stopped.is_set()]
        self._panels.append(panel)
        return panel.widget

    def serve(self, host="127.0.0.1", port=8010, api_key=None):
        """Start the HTTP server for this manager in a background thread of
        this session; returns a handle with ``.url`` and ``.stop()``.
        Needs ``fastapi``/``uvicorn`` (``pip install eco[feedback]``)."""
        from .server import serve_in_background

        return serve_in_background(self, host=host, port=port, api_key=api_key)

    def __repr__(self):
        lines = [f"FeedbackManager: {len(self.loops)} loop(s), "
                 f"{len(self.matrix_loops)} matrix loop(s)"]
        for name, loop in {**self.loops, **self.matrix_loops}.items():
            st = loop.status()
            flags = [f for f, on in (("ON", st.enabled), ("HELD", st.held),
                                     ("ALARM", st.alarmed), ("ERROR", st.error)) if on]
            lines.append(f"  {name}: {' '.join(flags) or 'off'}"
                         + (f"  ({st.error})" if st.error else ""))
        return "\n".join(lines)

    def stop_all(self):
        """Disable and stop every loop, matrix loop, schedule and panel."""
        self.disable_all()
        for loop in self.loops.values():
            loop.stop()
        for loop in self.matrix_loops.values():
            loop.stop()
        for sched in self.schedules.values():
            sched.stop()
        for panel in getattr(self, "_panels", []):
            panel.stop(join=True)

    def loop_for_adjustable(self, obj) -> FeedbackLoop | None:
        """The FeedbackLoop currently driving this adjustable object, if any."""
        for loop in self.loops.values():
            if loop.adjustable is obj:
                return loop
        return None

    def measure_response_matrix(self, name, adjustable_names, detector_names,
                                 **kwargs) -> ResponseMatrixMeasurement:
        adjustables = [self.adjustables[n] for n in adjustable_names]
        detectors = [self.detectors[n] for n in detector_names]
        meas = ResponseMatrixMeasurement(
            name, adjustable_names, adjustables, detector_names, detectors,
            manager=self, event_log=self.log, **kwargs,
        )
        self.measurements[name] = meas
        meas.start()
        return meas

    def schedule_response_matrix(self, name, adjustable_names, detector_names,
                                 interval_s, **kwargs) -> ScheduledMeasurement:
        sched = ScheduledMeasurement(self, name, adjustable_names, detector_names,
                                     interval_s, **kwargs)
        self.schedules[name] = sched
        return sched

    def unschedule_response_matrix(self, name):
        self.schedules.pop(name).stop()

    def add_matrix_loop(self, name, matrix_name, interlocks=(),
                        **kwargs) -> MatrixFeedbackLoop:
        if name in self.matrix_loops:
            raise ValueError(f"matrix loop {name!r} already exists")
        rm = self.matrices[matrix_name]
        detectors = [self.detectors[n] for n in rm.detector_names]
        adjustables = [self.adjustables[n] for n in rm.adjustable_names]
        resolved_interlocks = [self.interlocks[n] for n in interlocks]
        loop = MatrixFeedbackLoop(name, matrix_name, rm, detectors, adjustables,
                                  event_log=self.log, store=self.store,
                                  interlocks=resolved_interlocks, **kwargs)
        self.matrix_loops[name] = loop
        return loop
