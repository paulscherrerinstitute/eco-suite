"""Simulated scan targets: in-memory adjustables and detectors with a known
signal behind them, for developing and testing scans (adaptive ones in
particular) without a beamline. Pure Python + numpy, no EPICS, no threads
except the one each move runs in.

    sim.peak.signal.scans.ascan(sim.peak.x, -2, 2, 41, 0.2)

``sim`` is a lazy component of the bernina namespace (or build one with
``SimulatedSignals()``); its children are built on first use.

``SimulatedSignals`` holds one sub-assembly per signal shape, each with its own
axis/axes and a ``signal`` detector, plus a shared ``beam`` with an ``i0``:

=================  =========  ==================================================
``step``           x          edge (erf): position and width of an edge
``peak``           x          Gaussian peak on a background
``gradient``       x          mostly linear, slight curvature
``peak2d``         x, y       tilted, asymmetric 2-D Gaussian
``oscillation``    delay      pump-probe: step at t0 + one damped oscillation
``oscillations``   delay      same with three overlaid modes (own frequency,
                              damping and grow-in each)
=================  =========  ==================================================

Every parameter is an ``AdjustableMemory`` child, read at the moment a value
is produced, so the signal can be changed while a scan runs (e.g. move the peak
to test tracking). ``truth(...)`` evaluates the noise-free model.

Timing and noise
----------------
Axes (``SimAxis``) and detectors (``SimDetector``) carry the parameters that
make a simulated device behave like a particular real one, collected in
``AXIS_PRESETS`` / ``DETECTOR_PRESETS`` (``obj.apply_preset("electrometer")``,
or ``SimulatedSignals(axis="stepper", detector="diode")`` for all at once).

Axis: ``overhead`` (command latency before motion starts), ``speed``,
``settle_time`` (after arrival, before the move reports done), ``backlash``
(play between motor and load: on reversal the load stays put until the play
is taken up; the readback is the motor side and does not show it),
``readback_noise`` and ``jitter`` (rms of the *physical* position as the
signal sees it, per detector sample -- e.g. pump-probe timing jitter).

Detector: ``update_rate`` (new values per second; between them the value is
held, as for a scanned EPICS record or a 100 Hz beam-synchronous channel),
``response_time`` (first-order low-pass: electrometer, lock-in, thermal
sensor), ``dead_time`` (the value describes the state ``dead_time`` seconds
ago: pipeline/transport delay), ``read_latency`` (each read blocks this long:
slow controller), ``noise`` (additive rms), ``noise_rel`` (rms relative to the
signal, shot-noise-like) and ``drift`` (additive, signal units per hour).

The beam (``SimBeam``) multiplies every signal by the same shot-to-shot
intensity factor that ``beam.i0`` reads (``fluctuation`` rms around
``intensity``, at ``rate`` shots/s), so ``signal / i0`` normalisation works as
on real data.

All noise is a pure function of (stream, tick): reading twice within one
detector tick gives the same value, and a detector's monitor
(``set_current_value_callback``, polled at ``update_rate``) makes the detector
usable with ``CounterValue`` (``.scans`` on every ``SimDetector``).
"""

import itertools
import math
import threading
import time

import numpy as np

from eco.devices_general.utilities import Changer
from eco.elements.adjustable import AdjustableMemory
from eco.elements.assembly import Assembly
from eco.elements.detector import CallbackTimedelta
from eco.elements.protocols import resolve_lazy
from eco.acquisition.decorators import scannable


AXIS_PRESETS = {
    "ideal": dict(
        speed=math.inf, overhead=0.0, settle_time=0.0, backlash=0.0,
        readback_noise=0.0, jitter=0.0,
    ),
    # EPICS motor record on a stepper stage
    "stepper": dict(
        speed=2.0, overhead=0.15, settle_time=0.2, backlash=0.01,
        readback_noise=0.0005, jitter=0.0,
    ),
    # closed-loop piezo: fast, settles quickly, no play
    "piezo": dict(
        speed=100.0, overhead=0.02, settle_time=0.05, backlash=0.0,
        readback_noise=0.0002, jitter=0.0,
    ),
    # laser delay stage in ps, with timing jitter between the two beams
    "delay_stage": dict(
        speed=50.0, overhead=0.1, settle_time=0.1, backlash=0.02,
        readback_noise=0.001, jitter=0.03,
    ),
    # large, slow rotation with heavy load
    "goniometer": dict(
        speed=0.5, overhead=0.3, settle_time=0.5, backlash=0.002,
        readback_noise=0.0001, jitter=0.0,
    ),
    # electronic delay (phase shifter / EVR): no motion, but a slow
    # set-and-lock cycle
    "electronic_delay": dict(
        speed=math.inf, overhead=0.5, settle_time=1.0, backlash=0.0,
        readback_noise=0.0, jitter=0.05,
    ),
}

DETECTOR_PRESETS = {
    "ideal": dict(
        update_rate=100.0, response_time=0.0, dead_time=0.0, read_latency=0.0,
        noise=0.0, noise_rel=0.0, drift=0.0,
    ),
    # beam-synchronous diode, every shot
    "diode": dict(
        update_rate=100.0, response_time=0.0, dead_time=0.0, read_latency=0.0,
        noise=0.02, noise_rel=0.05, drift=0.0,
    ),
    # an EPICS record scanned at 1 Hz
    "epics_slow": dict(
        update_rate=1.0, response_time=0.0, dead_time=0.0, read_latency=0.0,
        noise=0.01, noise_rel=0.0, drift=0.0,
    ),
    # integrating current amplifier
    "electrometer": dict(
        update_rate=10.0, response_time=0.5, dead_time=0.0, read_latency=0.02,
        noise=0.002, noise_rel=0.0, drift=0.0,
    ),
    "lockin": dict(
        update_rate=50.0, response_time=0.3, dead_time=0.0, read_latency=0.0,
        noise=0.001, noise_rel=0.0, drift=0.0,
    ),
    # ROI sum computed by a camera pipeline
    "camera_roi": dict(
        update_rate=10.0, response_time=0.0, dead_time=0.2, read_latency=0.01,
        noise=0.0, noise_rel=0.03, drift=0.0,
    ),
    # temperature-like: slow response and drift
    "thermal": dict(
        update_rate=1.0, response_time=5.0, dead_time=0.0, read_latency=0.0,
        noise=0.001, noise_rel=0.0, drift=0.05,
    ),
}

FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))

_seeds = itertools.count(1)
_erf = np.vectorize(math.erf, otypes=[float])
_GH_NODES, _GH_WEIGHTS = np.polynomial.hermite.hermgauss(20)


def _normal(stream, ticks):
    """Standard-normal values that depend only on (stream, tick): splitmix64
    of both, then Box-Muller. Vectorised, stateless, so any tick can be
    re-drawn at any time and two readers of the same tick agree."""
    k = np.atleast_1d(np.asarray(ticks)).astype(np.int64).view(np.uint64)
    gold = np.uint64(0x9E3779B97F4A7C15)

    def mix(z):
        z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        return z ^ (z >> np.uint64(31))

    with np.errstate(over="ignore"):
        a = mix(k + np.uint64(stream) * gold)
        b = mix(a + gold)
    u1 = ((a >> np.uint64(11)).astype(float) + 0.5) / 2.0**53
    u2 = (b >> np.uint64(11)).astype(float) / 2.0**53
    return np.sqrt(-2.0 * np.log(u1)) * np.cos(2.0 * np.pi * u2)


def _value(adj):
    return adj.get_current_value()


class _Segment:
    """One commanded move: motion starts at t_start from x0 towards x1 at
    `speed`; `p0` is the physical (load) position at t_start."""

    __slots__ = ("t_start", "x0", "x1", "p0", "speed")

    def __init__(self, t_start, x0, x1, p0, speed):
        self.t_start, self.x0, self.x1, self.p0, self.speed = t_start, x0, x1, p0, speed

    @property
    def t_arrive(self):
        if self.speed == math.inf or self.x0 == self.x1:
            return self.t_start
        return self.t_start + abs(self.x1 - self.x0) / self.speed

    def motor(self, t):
        if self.speed == math.inf:
            return np.full(np.shape(t), self.x1)
        d = self.x1 - self.x0
        travelled = np.clip(self.speed * (t - self.t_start), 0.0, abs(d))
        return self.x0 + math.copysign(1.0, d) * travelled

    def physical(self, t, backlash):
        # monotonic within a segment, so the play operator reduces to a clip
        m = self.motor(t)
        return np.clip(self.p0, m - backlash / 2, m + backlash / 2)


class SimAxis(Assembly):
    """Simulated positioner (an Adjustable). Motion is kept as a short list of
    analytic segments, so ``motor_position(t)`` / ``physical_position(t)``
    answer for any recent time without a simulation thread. See the module
    docstring for the parameters; ``apply_preset`` sets them from
    ``AXIS_PRESETS``."""

    MAX_SEGMENTS = 64

    def __init__(
        self, value=0.0, unit=None, limits=(-math.inf, math.inf), preset="ideal",
        seed=None, name=None, **params,
    ):
        super().__init__(name=name)
        self.status_collection.append(self, selection="settings", recursive=False)
        self.unit = unit
        self._seed = next(_seeds) if seed is None else seed
        self._lock = threading.Lock()
        self._segments = [_Segment(-math.inf, float(value), float(value), float(value), math.inf)]
        self._move_done = None
        p = dict(AXIS_PRESETS[preset], **params)
        for key in AXIS_PRESETS["ideal"]:
            self._append(AdjustableMemory, p[key], name=key, is_setting=True, is_display=False)
        self._append(AdjustableMemory, limits[0], name="limit_low", is_setting=True, is_display=False)
        self._append(AdjustableMemory, limits[1], name="limit_high", is_setting=True, is_display=False)

    def apply_preset(self, preset):
        for key, value in AXIS_PRESETS[preset].items():
            getattr(self, key).set_target_value(value).wait()

    # -- position as a function of time ------------------------------------
    def _evaluate(self, t, physical):
        t = np.asarray(t, float)
        with self._lock:
            segments = list(self._segments)
        backlash = _value(self.backlash)
        out = np.empty(t.shape)
        for i, seg in enumerate(segments):
            end = segments[i + 1].t_start if i + 1 < len(segments) else math.inf
            m = (t >= seg.t_start) & (t < end)
            if i == 0:
                m |= t < seg.t_start
            if m.any():
                tm = t[m]
                out[m] = seg.physical(tm, backlash) if physical else seg.motor(tm)
        return out

    def motor_position(self, t):
        return self._evaluate(t, physical=False)

    def physical_position(self, t, jitter_ticks=None):
        """Load position at times `t`, plus the axis' `jitter`, drawn per
        detector tick (`jitter_ticks`) when given."""
        x = self._evaluate(t, physical=True)
        jitter = _value(self.jitter)
        if jitter and jitter_ticks is not None:
            x = x + jitter * _normal(4 * self._seed + 1, jitter_ticks).reshape(x.shape)
        return x

    # -- Adjustable ----------------------------------------------------------
    def get_current_value(self):
        x = float(self.motor_position(time.time()))
        noise = _value(self.readback_noise)
        if noise:
            x += noise * float(_normal(4 * self._seed, int(time.time() * 1e3))[0])
        return x

    def get_limits(self):
        return (_value(self.limit_low), _value(self.limit_high))

    def set_limits(self, low, high):
        self.limit_low.set_target_value(low).wait()
        self.limit_high.set_target_value(high).wait()

    def _command(self, target, t_start):
        """Start a segment towards `target` at `t_start` (truncating the one
        in progress) and return when it is settled."""
        t_start = max(t_start, time.time())
        x0 = float(self.motor_position(t_start))
        p0 = float(self.physical_position(t_start))
        seg = _Segment(t_start, x0, float(target), p0, float(_value(self.speed)))
        done = threading.Event()
        with self._lock:
            self._segments = (self._segments + [seg])[-self.MAX_SEGMENTS :]
            if self._move_done is not None:
                self._move_done.set()  # supersede the previous move
            self._move_done = done
        return seg, done

    def set_target_value(self, value, hold=False):
        low, high = self.get_limits()
        if not low <= value <= high:
            raise ValueError(f"{self.name}: {value} outside limits {(low, high)}")

        def change(value):
            seg, done = self._command(value, time.time() + _value(self.overhead))
            t_done = seg.t_arrive + _value(self.settle_time)
            # sliced, so a stop or a newer move ends this wait
            while not done.wait(timeout=max(0.0, min(0.05, t_done - time.time()))):
                if time.time() >= t_done:
                    break

        return Changer(target=value, parent=self, changer=change, hold=hold, stopper=self.stop)

    def stop(self):
        x = float(self.motor_position(time.time()))
        self._command(x, time.time())[1].set()


@scannable
class SimDetector(Assembly):
    """Simulated detector: ``model(*positions) * beam + drift``, sampled on its
    own tick clock, filtered and delayed as described in the module
    docstring.

    model : callable
        ``model(*arrays) -> array``: the noise-free signal for physical
        positions of `axes` (one array per axis, all of the same shape).
    axes : list of SimAxis
    beam : SimBeam, optional
        Multiplies the signal by its shot-to-shot intensity factor.
    """

    def __init__(self, model, axes=(), beam=None, unit=None, preset="ideal", seed=None, name=None, **params):
        super().__init__(name=name)
        self.status_collection.append(self, selection="display", recursive=False)
        self._model = model
        self._axes = list(axes)
        self._beam = beam
        self.unit = unit
        self._seed = next(_seeds) if seed is None else seed
        self._t_created = time.time()
        self._lock = threading.Lock()
        self._filter_state = None  # (last tick, low-pass output at that tick)
        p = dict(DETECTOR_PRESETS[preset], **params)
        for key in DETECTOR_PRESETS["ideal"]:
            self._append(AdjustableMemory, p[key], name=key, is_setting=True, is_display=False)

    def apply_preset(self, preset):
        for key, value in DETECTOR_PRESETS[preset].items():
            getattr(self, key).set_target_value(value).wait()

    def truth(self, *positions):
        """Noise-free model (no beam, no drift, no filtering) at `positions`."""
        return self._model(*[np.asarray(p, float) for p in positions])

    def _clean(self, ticks, rate):
        """Signal before readout noise at the given ticks: what the device
        sees `dead_time` before each tick."""
        t = ticks / rate - _value(self.dead_time)
        positions = [ax.physical_position(t, jitter_ticks=ticks) for ax in self._axes]
        x = np.broadcast_to(np.asarray(self._model(*positions), float), t.shape)
        if self._beam is not None:
            x = x * self._beam.factor(t)
        drift = _value(self.drift)
        if drift:
            x = x + drift * (t - self._t_created) / 3600.0
        return x

    def get_current_value(self):
        latency = _value(self.read_latency)
        if latency:
            time.sleep(latency)
        rate = float(_value(self.update_rate))
        tick = math.floor(time.time() * rate)
        tau = _value(self.response_time)
        with self._lock:
            if tau <= 0:
                y = float(self._clean(np.array([tick]), rate)[0])
                self._filter_state = None
            else:
                y = self._low_pass(tick, rate, tau)
        noise = _value(self.noise) + _value(self.noise_rel) * abs(y)
        if noise:
            y += noise * float(_normal(4 * self._seed + 2, tick)[0])
        return y

    def _low_pass(self, tick, rate, tau):
        """First-order low-pass, caught up tick by tick since the last read
        (at most ~6 time constants back; older history no longer matters)."""
        a = 1.0 - math.exp(-1.0 / (rate * tau))
        n_max = int(math.ceil(6 * tau * rate)) + 1
        last, y = self._filter_state if self._filter_state else (None, None)
        if last is not None and last >= tick:
            return y
        first = tick - n_max + 1 if last is None or tick - last > n_max else last + 1
        x = self._clean(np.arange(first, tick + 1), rate)
        if y is None:
            y = x[0]
        for xi in x:
            y += a * (xi - y)
        self._filter_state = (tick, y)
        return float(y)

    def set_current_value_callback(self):
        return CallbackTimedelta(self, frequency=_value(self.update_rate))


class SimBeam(Assembly):
    """Shared incident intensity: ``intensity * (1 + fluctuation * N(0,1))``,
    a new value every 1/``rate`` s, seen by ``i0`` and by every detector built
    with this beam."""

    def __init__(self, intensity=1.0, fluctuation=0.05, rate=100.0, preset="diode", seed=None, name=None):
        super().__init__(name=name)
        self._seed = next(_seeds) if seed is None else seed
        self._append(AdjustableMemory, intensity, name="intensity", is_setting=True)
        self._append(AdjustableMemory, fluctuation, name="fluctuation", is_setting=True)
        self._append(AdjustableMemory, rate, name="rate", is_setting=True)
        self._append(SimDetector, lambda: 1.0, beam=self, preset=preset, name="i0", is_setting="recursive")

    def factor(self, t):
        t = np.asarray(t, float)
        shots = np.floor(t * _value(self.rate))
        fluct = _value(self.fluctuation)
        f = _value(self.intensity) * np.ones(t.shape)
        if fluct:
            f = f * (1.0 + fluct * _normal(4 * self._seed + 3, shots).reshape(t.shape))
        return f


class _SimSignal(Assembly):
    """Base for the signal shapes: model parameters as settings, the axes and
    a ``signal`` detector built by ``_build``."""

    PARAMS = {}

    def __init__(self, beam=None, axis="ideal", detector="diode", name=None, **params):
        super().__init__(name=name)
        for key, value in dict(self.PARAMS, **params).items():
            self._append(AdjustableMemory, value, name=key, is_setting=True, is_display=False)
        self._build(beam, axis, detector)

    def _p(self, name):
        return _value(getattr(self, name))

    def truth(self, *positions):
        return self.signal.truth(*positions)

    def apply_presets(self, axis=None, detector=None):
        for item in self.__dict__.values():
            if axis and isinstance(item, SimAxis):
                item.apply_preset(axis)
        if detector:
            self.signal.apply_preset(detector)


class _SimSignal1D(_SimSignal):
    AXIS_UNIT = "mm"
    AXIS_LIMITS = (-10.0, 10.0)

    def _build(self, beam, axis, detector):
        self._append(SimAxis, 0.0, unit=self.AXIS_UNIT, limits=self.AXIS_LIMITS, preset=axis, name="x", is_setting="recursive")
        self._append(SimDetector, self.model, axes=[self.x], beam=beam, preset=detector, name="signal", is_setting="recursive")


class SimStep(_SimSignal1D):
    """``background + amplitude * edge``; `width` is the FWHM of the edge's
    derivative (Gaussian), i.e. 10-90 % rise = 0.77 * width."""

    PARAMS = dict(center=0.5, width=0.3, amplitude=1.0, background=0.1)

    def model(self, x):
        s = self._p("width") / FWHM_PER_SIGMA
        edge = 0.5 * (1 + _erf((x - self._p("center")) / (math.sqrt(2) * s)))
        return self._p("background") + self._p("amplitude") * edge


class SimPeak(_SimSignal1D):
    """Gaussian ``background + amplitude * exp(-(x-center)^2 / 2s^2)``, `fwhm` = 2.355 s."""

    PARAMS = dict(center=0.7, fwhm=0.4, amplitude=1.0, background=0.05)

    def model(self, x):
        s = self._p("fwhm") / FWHM_PER_SIGMA
        return self._p("background") + self._p("amplitude") * np.exp(-0.5 * ((x - self._p("center")) / s) ** 2)


class SimGradient(_SimSignal1D):
    """``offset + slope * (x - x0) + curvature * (x - x0)^2``: a slope with a
    little non-linearity."""

    PARAMS = dict(x0=0.0, offset=1.0, slope=0.2, curvature=0.01)

    def model(self, x):
        d = x - self._p("x0")
        return self._p("offset") + self._p("slope") * d + self._p("curvature") * d**2


class SimPeak2D(_SimSignal):
    """Tilted, asymmetric 2-D Gaussian. Along the major axis (at `angle`,
    degrees from x) the width is ``fwhm_major * (1 + asymmetry)`` on the
    positive side and ``fwhm_major * (1 - asymmetry)`` on the negative side;
    ``fwhm_minor`` across it."""

    PARAMS = dict(
        center_x=0.4, center_y=-0.3, fwhm_major=1.0, fwhm_minor=0.4, angle=30.0,
        asymmetry=0.2, amplitude=1.0, background=0.05,
    )

    def _build(self, beam, axis, detector):
        for key in ("x", "y"):
            self._append(SimAxis, 0.0, unit="mm", limits=(-5.0, 5.0), preset=axis, name=key, is_setting="recursive")
        self._append(SimDetector, self.model, axes=[self.x, self.y], beam=beam, preset=detector, name="signal", is_setting="recursive")

    def model(self, x, y):
        a = math.radians(self._p("angle"))
        dx, dy = x - self._p("center_x"), y - self._p("center_y")
        u = dx * math.cos(a) + dy * math.sin(a)
        v = -dx * math.sin(a) + dy * math.cos(a)
        major = self._p("fwhm_major") / FWHM_PER_SIGMA
        su = major * (1 + np.sign(u) * self._p("asymmetry"))
        sv = self._p("fwhm_minor") / FWHM_PER_SIGMA
        return self._p("background") + self._p("amplitude") * np.exp(-0.5 * ((u / su) ** 2 + (v / sv) ** 2))


class SimOscillationMode(Assembly):
    """One damped oscillation ``amplitude * (1 - exp(-u/rise)) * exp(-u/damping)
    * cos(2 pi frequency u + phase)``, u = time after t0 (ps, THz, rad)."""

    def __init__(self, amplitude=0.3, frequency=1.0, damping=2.0, rise=0.0, phase=0.0, name=None):
        super().__init__(name=name)
        for key, value in dict(amplitude=amplitude, frequency=frequency, damping=damping, rise=rise, phase=phase).items():
            self._append(AdjustableMemory, value, name=key, is_setting=True, is_display=False)

    def response(self, u):
        p = {k: _value(getattr(self, k)) for k in ("amplitude", "frequency", "damping", "rise", "phase")}
        return p["amplitude"] * _rise_decay(u, p["rise"], p["damping"]) * np.cos(2 * np.pi * p["frequency"] * u + p["phase"])


def _rise_decay(u, rise, decay):
    """(1 - exp(-u/rise)) * exp(-u/decay) for u >= 0, 0 before; rise <= 0
    means instantaneous, decay <= 0 or inf means none."""
    up = np.maximum(u, 0.0)
    f = np.where(u >= 0, 1.0, 0.0)
    if rise > 0:
        f = f * (1 - np.exp(-up / rise))
    if 0 < decay < math.inf:
        f = f * np.exp(-up / decay)
    return f


class SimPumpProbe(_SimSignal1D):
    """Pump-probe trace vs. `delay` (ps): at t0 a step (``step_amplitude``,
    grow-in ``step_rise``, recovery ``step_decay``) plus damped oscillation
    modes (``mode1`` ...), everything convolved with a Gaussian time
    resolution of FWHM ``resolution``."""

    AXIS_UNIT = "ps"
    AXIS_LIMITS = (-20.0, 100.0)
    PARAMS = dict(t0=0.0, resolution=0.08, step_amplitude=-0.5, step_rise=0.0, step_decay=math.inf, background=1.0)
    MODES = [dict(amplitude=0.2, frequency=1.5, damping=1.5, rise=0.0, phase=0.0)]

    def __init__(self, beam=None, axis="ideal", detector="diode", modes=None, name=None, **params):
        self._modes = self.MODES if modes is None else modes
        super().__init__(beam=beam, axis=axis, detector=detector, name=name, **params)

    def _build(self, beam, axis, detector):
        for i, mode in enumerate(self._modes, start=1):
            self._append(SimOscillationMode, **mode, name=f"mode{i}", is_setting="recursive")
        self._append(SimAxis, 0.0, unit="ps", limits=self.AXIS_LIMITS, preset=axis, name="delay", is_setting="recursive")
        self._append(SimDetector, self.model, axes=[self.delay], beam=beam, preset=detector, name="signal", is_setting="recursive")

    def _unsmeared(self, u):
        f = self._p("step_amplitude") * _rise_decay(u, self._p("step_rise"), self._p("step_decay"))
        for i in range(1, len(self._modes) + 1):
            f = f + getattr(self, f"mode{i}").response(u)
        return f

    def model(self, t):
        u = np.asarray(t, float) - self._p("t0")
        s = self._p("resolution") / FWHM_PER_SIGMA
        if s > 0:  # Gauss-Hermite quadrature of the convolution
            nodes = u[..., None] + math.sqrt(2) * s * _GH_NODES
            f = self._unsmeared(nodes) @ _GH_WEIGHTS / math.sqrt(math.pi)
        else:
            f = self._unsmeared(u)
        return self._p("background") + f


class SimOscillations(SimPumpProbe):
    """`SimPumpProbe` with three overlaid modes, each with its own frequency,
    damping and grow-in, and a recovering step."""

    PARAMS = dict(SimPumpProbe.PARAMS, step_rise=0.15, step_decay=20.0)
    MODES = [
        dict(amplitude=0.15, frequency=0.8, damping=3.0, rise=0.0, phase=0.0),
        dict(amplitude=0.08, frequency=2.3, damping=1.0, rise=0.1, phase=0.5),
        dict(amplitude=0.05, frequency=5.1, damping=0.6, rise=0.3, phase=1.2),
    ]


class SimulatedSignals(Assembly):
    """All simulated signal shapes with a shared beam; `axis` / `detector`
    pick presets (``AXIS_PRESETS`` / ``DETECTOR_PRESETS``) for all of them;
    `delay_axis` (default: same as `axis`) for the two pump-probe ones.

    Every child is built on first use, like a lazy namespace component (an
    ``eco.utilities.config.Proxy`` holds its place until then), so only the
    shapes actually touched exist and show up in ``status()``."""

    SIGNALS = {
        "step": (SimStep, "axis"),
        "peak": (SimPeak, "axis"),
        "gradient": (SimGradient, "axis"),
        "peak2d": (SimPeak2D, "axis"),
        "oscillation": (SimPumpProbe, "delay_axis"),
        "oscillations": (SimOscillations, "delay_axis"),
    }

    def __init__(self, axis="ideal", detector="diode", delay_axis=None, name="sim"):
        super().__init__(name=name)
        self._presets = dict(axis=axis, delay_axis=delay_axis or axis, detector=detector)
        self._build_lock = threading.RLock()
        self._lazy("beam", lambda: self._append(SimBeam, name="beam", is_setting="recursive"))
        for key, (cls, axis_kind) in self.SIGNALS.items():
            self._lazy(key, self._signal_builder(key, cls, axis_kind))

    def _signal_builder(self, key, cls, axis_kind):
        def build():
            self._append(
                cls, beam=resolve_lazy(self.beam), axis=self._presets[axis_kind],
                detector=self._presets["detector"], name=key, is_setting="recursive",
            )

        return build

    def _lazy(self, key, build):
        from eco.utilities.config import Proxy

        def resolve():
            with self._build_lock:
                if isinstance(self.__dict__.get(key), Proxy):
                    del self.__dict__[key]
                    build()
                return self.__dict__[key]

        self.__dict__[key] = Proxy(resolve)

    def built(self):
        """Names of the children constructed so far."""
        from eco.utilities.config import Proxy

        return [k for k in ["beam", *self.SIGNALS] if not isinstance(self.__dict__[k], Proxy)]

    def apply_presets(self, axis=None, detector=None):
        """Same timing/noise preset for every axis and/or signal detector,
        including those not built yet."""
        if axis:
            self._presets.update(axis=axis, delay_axis=axis)
        if detector:
            self._presets["detector"] = detector
        for key in self.built():
            item = self.__dict__[key]
            if isinstance(item, _SimSignal):
                item.apply_presets(axis=axis, detector=detector)
