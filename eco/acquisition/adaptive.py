"""Adaptive step scans: a strategy from ``escape.adaptive_scan`` chooses the
next scan points from the live data while an ordinary ``StepScan`` runs.

User documentation: ``docs/adaptive_scans.md``. Entry point:
``Scans.adaptive_scan``. This module is the thin adapter between the two:

- ``AdaptiveDriver`` is put into the scan's counter list (last). It acquires
  nothing; its end-of-step callback reads the feedback counter, adds the
  point to the strategy, learns the motion model, and when the queued steps
  run out appends the strategy's next batch to the running scan
  (``scan._values_todo``/``pulses_per_step``, which ``StepScan`` consumes one
  per step). Not refilling ends the scan.
- The feedback is live data, not DAQ data: any counter with
  ``step_feedback(scan) -> (mean, standard error, n)`` (``CounterValue``,
  ``BsStreamCounter``), or a callable ``f(scan) -> (y, yerr)``.
- Points stay on a grid (the scan's axis specs), at the grid's resolution;
  the grid may be unevenly spaced. 2-D scans carry ``grid_specs`` whose
  ``index_plan`` grows with the scan and each step's ``grid_index``, which is
  what escape uses to place data on the grid.
- With ``mesh="free"`` there is no grid: the strategy picks from a lattice
  four times finer than the axis step (``free_candidates``), the axis step
  becomes the mesh ``resolution`` (targets closer than it are one node), and
  no ``grid_specs`` are written. ``eco.acquisition.optimize`` builds on the
  same driver with points anywhere.

Time units: the strategies plan in one time unit for exposure and motion.
Here that unit is one ``N_pulses`` unit (shots for the DAQ, seconds for
``CounterValue``); the seconds per unit are measured from the acquisitions,
and motion models given or learned in seconds are converted with it.
"""

import math
import re
import time
import weakref

import numpy as np

from eco.acquisition.utilities import Acquisition

MIN_ESCAPE_VERSION = "0.2.17"

STRATEGIES = {
    "uniform": "UniformScan",  # coarse-to-fine sweeps, no model
    "modelfree": "ModelFreeScan",  # refine where interpolation is worst
    "gradient": "GradientScan",  # refine where the signal changes
    "edge": "StepScan",  # an edge: position and width
    "oscillation": "OscillationScan",  # step + damped oscillation(s)
    "peak2d": "PeakScan2D",  # 2-D peak: centre, widths, tilt
}


def adaptive_scan_module():
    try:
        from escape import adaptive_scan
    except ImportError as exc:
        try:
            from importlib.metadata import version

            installed = version("escape-fel")
        except Exception:
            installed = "unknown"
        raise ImportError(
            f"adaptive scans need escape-fel >= {MIN_ESCAPE_VERSION} "
            f"(escape.adaptive_scan); installed: {installed}"
        ) from exc
    return adaptive_scan


def parse_duration(budget):
    """Seconds from a number or a string like "90s", "10min", "1.5h"."""
    if budget is None or isinstance(budget, (int, float)):
        return budget
    m = re.fullmatch(r"\s*([\d.]+)\s*(s|sec|m|min|h)?\s*", str(budget))
    if not m:
        raise ValueError(f"budget {budget!r}: give seconds or e.g. '90s', '10min', '1h'")
    factor = {None: 1, "s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600}[m.group(2)]
    return float(m.group(1)) * factor


def is_detector_like(obj):
    """A counter with live values or a detector (not a plain function)."""
    return hasattr(obj, "step_feedback") or hasattr(obj, "get_current_value")


def resolve_feedback(feedback, counters):
    """(counter to add to the scan or None, function scan -> (y, yerr, n)).

    `feedback`: a counter with ``step_feedback``, a ``(counter, channel)``
    pair, a detector (turned into its counter by ``as_counter``: a bs
    detector's live bs counter, otherwise a ``CounterValue``), a
    callable ``f(scan) -> (y, yerr)``, or None to use the first counter in
    `counters` that has ``step_feedback``."""
    channel = None
    if isinstance(feedback, tuple):
        feedback, channel = feedback
    if feedback is None:
        for ctr in counters:
            if hasattr(ctr, "step_feedback"):
                return None, lambda scan: ctr.step_feedback(scan)
        raise ValueError(
            "adaptive scans steer by live (non-DAQ) data: pass "
            "feedback=<detector or counter>"
        )
    if is_detector_like(feedback):
        from eco.acquisition.counters import as_counters, find_counter

        # the counter already reading it (adaptive_scan puts a feedback
        # detector among the counters first), else a new one to add
        found = find_counter(counters, feedback)
        new = None
        if found is None:
            (new,) = as_counters([feedback])
            found = (new, None)
        counter, own_channel = found
        if not hasattr(counter, "step_feedback"):
            raise TypeError(f"feedback {feedback!r}: its counter gives no live values")
        if channel is None:
            channel = own_channel
        return new, lambda scan: counter.step_feedback(scan, channel=channel)
    if callable(feedback):
        def call(scan):
            out = feedback(scan)
            return (*out, 1) if len(out) == 2 else out

        return None, call
    raise TypeError(f"feedback {feedback!r}: expected a detector, counter or callable")


def _to_planner_units(motion, sec_per_unit, ndim):
    """A Motion in planner time units from Axis/list/Motion in seconds."""
    ads = adaptive_scan_module()
    motion = ads.Motion.coerce(motion, ndim)
    axes = [
        ads.Axis(
            speed=a.speed * sec_per_unit,
            acceleration=a.acceleration * sec_per_unit**2,
            overhead=a.overhead / sec_per_unit,
            backlash=a.backlash,
            direction=a.direction,
            reverse_penalty=a.reverse_penalty / sec_per_unit,
        )
        for a in motion.axes
    ]
    return ads.Motion(axes, parallel=motion.parallel, overhead=motion.overhead / sec_per_unit)


def _fmt_axis(axis):
    parts = [f"speed={axis.speed:.4g}" if math.isfinite(axis.speed) else "speed=inf"]
    parts.append(f"overhead={axis.overhead:.3g}")
    return f"Axis({', '.join(parts)})"


class _MotionLearner:
    """Fits one Axis per adjustable (in seconds) to the scan's own moves:
    `duration = overhead + distance / speed`, from the steps in which only
    that adjustable moved (judged by the targets, so readback noise does not
    count as a move). Until two clearly different distances were moved, or
    when the adjustable never moves alone (the slow axis of a 2-D survey),
    its own `speed` setting (``MotorRecord.speed``, if it has one) is used,
    with the overhead from its moves or from the other axes."""

    MIN_MOVES = 3

    def __init__(self, adjustables, tolerances):
        self.adjustables = adjustables
        self.tolerances = tolerances
        self.moves = [[] for _ in adjustables]  # (start, end, seconds)
        self.axes = [None] * len(adjustables)
        self.sources = [""] * len(adjustables)

    def record(self, start, end, duration):
        moved = [abs(e - s) > tol for s, e, tol in zip(start, end, self.tolerances)]
        if sum(moved) != 1:
            return  # nothing, or several axes together: not attributable
        i = moved.index(True)
        self.moves[i].append((start[i], end[i], duration))

    def _speed_setting(self, adj):
        speed = getattr(adj, "speed", None)
        try:
            value = speed.get_current_value() if hasattr(speed, "get_current_value") else speed
            value = float(value)
        except Exception:
            return None
        return value if value > 0 else None

    def update(self):
        """Refit; returns the indices whose model changed noticeably."""
        ads = adaptive_scan_module()
        changed = []
        candidates = {}
        for i, moves in enumerate(self.moves):
            if len(moves) < self.MIN_MOVES:
                continue
            s, e, t = (np.array(v, float) for v in zip(*moves))
            d = np.abs(e - s)
            if d.max() > 1.5 * d.min():
                candidates[i] = (
                    ads.Axis.calibrate(s, e, t, direction=0),
                    f"fitted to {len(moves)} moves",
                )
                continue
            speed = self._speed_setting(self.adjustables[i])
            if speed is not None:  # equal distances only: speed from the setting
                overhead = max(float(np.median(t - d / speed)), 0.0)
                candidates[i] = (
                    ads.Axis(speed=speed, overhead=overhead),
                    "speed setting of the adjustable, overhead from its moves",
                )
        known_overheads = [a.overhead for a, _ in candidates.values()]
        for i, adj in enumerate(self.adjustables):
            if i in candidates or not known_overheads:
                continue  # wait until some axis showed the controller's overhead
            speed = self._speed_setting(adj)
            if speed is not None:
                candidates[i] = (
                    ads.Axis(speed=speed, overhead=float(np.median(known_overheads))),
                    "speed setting of the adjustable, overhead from the other axes",
                )
        for i, (axis, source) in candidates.items():
            old = self.axes[i]
            if old is None or not np.isclose(
                [axis.speed, axis.overhead], [old.speed, old.overhead], rtol=0.25, atol=0.02
            ).all():
                changed.append(i)
                self.axes[i] = axis
                self.sources[i] = source
        return changed

    def motion(self):
        """Motion of all axes; an axis without a model yet moves for free."""
        ads = adaptive_scan_module()
        if all(a is None for a in self.axes):
            return None
        return ads.Motion([a if a is not None else ads.Axis() for a in self.axes], parallel=True)


class AdaptiveDriver:
    """Counter-shaped (``eco.acquisition.counter_protocol.Counter``) driver of
    an adaptive scan; see the module docstring. Built by
    ``Scans.adaptive_scan``; after the scan, ``scan.adaptive`` is this object
    and ``scan.adaptive.strategy`` the escape strategy with all data."""

    def __init__(
        self, strategy, adjustables, axis_positions, feedback, n_pulses,
        budget=None, max_points=None, batch=5, motion="auto", plot=True,
        avoid_revisits=False, resolution=None, name="adaptive",
    ):
        self.name = name
        self.strategy = strategy
        self.adjustables = adjustables
        self.axis_positions = [np.asarray(p, float) for p in axis_positions]
        self._feedback = feedback
        self.n_pulses = n_pulses
        self.budget = budget
        self.max_points = max_points
        self.batch = batch
        self.motion_spec = motion
        self.plot = plot
        self.avoid_revisits = avoid_revisits
        # None: points on the axis grid; else the mesh resolution of a
        # scan without a grid (one value per adjustable)
        self.resolution = resolution
        # a target change below a quarter of the finest grid spacing is no move
        tolerances = [
            0.25 * np.diff(np.unique(p)).min() if len(np.unique(p)) > 1 else 0.0
            for p in self.axis_positions
        ]
        self.learner = _MotionLearner(adjustables, tolerances) if motion == "auto" else None
        self.sec_per_unit = None
        self.n_skipped = 0
        self._fig = None
        self._scan = None
        self._t0 = None
        self._previous = None
        self._done = False
        self.callbacks_start_scan = [self._on_start]
        self.callbacks_start_step = []
        self.callbacks_step_counting = []
        self.callbacks_end_step = [self._on_step_end]
        self.callbacks_end_scan = [self._on_end]
        self.description = f"adaptive ({type(strategy).__name__})"

    # -- counter protocol: nothing to acquire ------------------------------
    follows = True  # takes no exposure; StepScan only start()s/stop()s it

    def acquire(self, scan=None, Npulses=None, **kwargs):
        return Acquisition(acquire=lambda: None, hold=False)

    def start(self, scan=None, **kwargs):
        pass

    def stop(self, scan=None, **kwargs):
        return {"files": []}

    # -- planning helpers ---------------------------------------------------
    def initial_values(self):
        """First points: the strategy's survey (or first batch), within
        `max_points`."""
        n = getattr(self.strategy, "n_initial", self.batch)
        if self.max_points:
            n = min(n, self.max_points)
        plan = self.strategy.plan(n)
        return self._steps_from_plan(plan)

    def _steps_from_plan(self, plan):
        if not len(plan):
            return []
        positions = np.asarray(plan.positions, float).reshape(len(plan), -1)
        steps = []
        for pos, exposure in zip(positions, plan.exposures):
            if isinstance(self.n_pulses, int):
                exposure = max(int(round(exposure)), 1)
            else:
                exposure = float(exposure)
            steps.append(([float(p) for p in pos], exposure))
        return steps

    def grid_index(self, values):
        return [int(np.argmin(np.abs(ax - v))) for ax, v in zip(self.axis_positions, values)]

    # -- scan callbacks -------------------------------------------------------
    def _on_start(self, scan, **kwargs):
        self._scan = weakref.ref(scan)
        self._t0 = time.time()
        self._previous = list(scan.initial_values())
        scan.adaptive = self
        mesh = getattr(scan, "mesh", None)
        if mesh is not None and self.resolution is not None:
            # points anywhere ("line"/"path" as for any scan without a grid)
            mesh.resolution = list(self.resolution)
        elif mesh is not None:
            # a refined subset of the axis grid, nodes at its spacing
            mesh.kind = "adapted"
            mesh.resolution = [
                float(np.min(np.diff(np.unique(p)))) if len(np.unique(p)) > 1 else 0.0
                for p in self.axis_positions
            ]
        scan.set_scan_parameter("adaptive", {
            "strategy": type(self.strategy).__name__,
            "budget_s": self.budget,
            "max_points": self.max_points,
            "batch": self.batch,
        })
        self._update_expected(scan)

    def _on_step_end(self, scan, **kwargs):
        if not getattr(scan, "_current_step_ok", True):
            return  # the step will be repeated
        info = scan.scan_info["scan_step_info"][-1]
        times = info.get("times", {})
        x = list(scan.values_current_step)
        exposure = scan.pulses_per_step[0]
        self._add_point(x, exposure)
        self._learn(scan, times, exposure)
        self._previous = x  # targets: readback noise is no move
        if len(scan._values_todo) <= 1 and not self._done:  # [0] is this step
            self._refill(scan)
        self._update_expected(scan)

    def _add_point(self, x, exposure):
        try:
            out = self._feedback(self._scan())
        except Exception as exc:
            out = None
            print(f"adaptive: no feedback for this step ({type(exc).__name__}: {exc})")
        if out is None or not np.isfinite(out[0]):
            self.n_skipped += 1
            return
        y, yerr, n = out
        if not np.isfinite(yerr) or yerr <= 0:
            # one sample or a noise-free signal: the strategy drops points
            # without a positive error, so take the typical error so far
            known = self.strategy.yerr
            yerr = float(np.median(known)) if len(known) else max(abs(y) * 1e-3, 1e-12)
        # model strategies refit on every add; that can take seconds, so it
        # is done once per batch instead, in _fit (before planning)
        self.strategy.add(
            [x] if self.strategy.ndim > 1 else x, [y], [yerr], exposure=exposure, refit=False
        )

    def _fit(self):
        fit = getattr(self.strategy, "fit", None)  # model strategies only
        if fit is not None and self.strategy.n_points:
            fit()

    def _learn(self, scan, times, exposure):
        t_ctr = times.get("counters")
        if t_ctr and exposure:
            spu = t_ctr / float(exposure)
            self.sec_per_unit = spu if self.sec_per_unit is None else 0.7 * self.sec_per_unit + 0.3 * spu
        if not self.sec_per_unit:
            return
        timing = scan.timing
        if timing.n_steps:
            dead = (timing.totals["settle"] + timing.totals["callbacks"]) / timing.n_steps
            self.strategy.exposure_model.overhead = dead / self.sec_per_unit
        if self.learner is not None:
            if self._previous is not None and times.get("adjustables") is not None:
                self.learner.record(self._previous, scan.values_current_step, times["adjustables"])
            changed = self.learner.update()
            motion = self.learner.motion()
            if motion is not None:
                self._set_motion(motion)
            for i in changed:
                name = getattr(self.adjustables[i], "name", f"axis {i}")
                print(
                    f"adaptive: motion model for {name} ({self.learner.sources[i]}): "
                    f"{_fmt_axis(self.learner.axes[i])} in seconds -- "
                    f"to fix it, pass motion=[...] with such Axis objects"
                )
        elif self.motion_spec is not None and not getattr(self, "_motion_set", False):
            self._set_motion(self.motion_spec)
            self._motion_set = True

    def _set_motion(self, motion_seconds):
        self.strategy.motion = _to_planner_units(motion_seconds, self.sec_per_unit, self.strategy.ndim)
        # escape only reorders batches for the fastest route when a motion
        # model was given at construction; ours arrives during the scan
        self.strategy._motion_given = True

    def _remaining_points(self, scan):
        """How many more points the budget allows (None: unlimited)."""
        limits = []
        if self.max_points:
            limits.append(self.max_points - scan.next_step - len(scan._values_todo))
        if self.budget:
            step_time = scan.timing.mean_step_time()
            if step_time:
                left = self.budget - (time.time() - self._t0)
                limits.append(int(left // step_time) - (len(scan._values_todo) - 1))
        return None if not limits else max(min(limits), 0)

    def _refill(self, scan):
        n = self.batch
        remaining = self._remaining_points(scan)
        if remaining is not None:
            n = min(n, remaining)
        if n <= 0:
            self._done = True
            return
        t0 = time.time()
        try:
            self._fit()
            position = np.asarray(scan.readbacks_current_step, float)
            plan = self.strategy.plan(n, position=position if self.strategy.ndim > 1 else position[0])
            steps = self._steps_from_plan(plan)
            if self.avoid_revisits:
                measured = {tuple(self.grid_index(v)) for v in scan._values_done}
                measured.add(tuple(self.grid_index(scan.values_current_step)))
                steps = [s for s in steps if tuple(self.grid_index(s[0])) not in measured]
        except Exception as exc:
            print(f"adaptive: planning failed, ending the scan ({type(exc).__name__}: {exc})")
            self._done = True
            return
        finally:
            scan.timing.book("planning", time.time() - t0)
        if not steps:
            self._done = True
            return
        self._append_steps(scan, steps)

    def _append_steps(self, scan, steps):
        """Queue `steps` ((values, exposure) pairs) in the running scan."""
        gridspecs = scan.grid_specs.current_value
        for values, n_pulses in steps:
            scan._values_todo.append(values)
            scan.pulses_per_step.append(n_pulses)
            if gridspecs:
                gridspecs["index_plan"].append(tuple(self.grid_index(values)))
        if gridspecs:
            scan.scan_info["scan_parameters"]["grid_specs"]["index_plan"] = list(
                gridspecs["index_plan"]
            )
        if self.plot:
            self._draw_later()

    def _update_expected(self, scan):
        """Steps expected beyond the queued ones, for the progress bar and
        `number_of_steps`: what the budget still allows."""
        if self._done:
            scan.extra_expected_steps = 0
            return
        remaining = self._remaining_points(scan)
        # before the first step the step time (and so a time budget) is unknown
        scan.extra_expected_steps = self.batch if remaining is None else remaining

    def _on_end(self, scan, **kwargs):
        try:
            self._fit()
        except Exception as exc:
            print(f"adaptive: final fit failed ({type(exc).__name__}: {exc})")
        if self.plot:
            self._draw_later()
        s = self.strategy
        line = f"adaptive: {s.n_points} points measured"
        if self.n_skipped:
            line += f", {self.n_skipped} without feedback"
        print(line + ".")
        summary = getattr(s, "summary", None)
        if callable(summary):
            try:
                print(summary())
            except Exception:
                pass

    # -- plot ---------------------------------------------------------------
    def _draw_later(self):
        from eco.utilities import mainthread

        mainthread.on_main(self._draw)()

    def _draw(self):
        try:
            import matplotlib.pyplot as plt

            if self._fig is None or not plt.fignum_exists(self._fig.number):
                self._fig = plt.figure(num=f"adaptive scan ({type(self.strategy).__name__})")
            fig = self._fig
            fig.clf()
            s = self.strategy
            if hasattr(s, "plot") and s.n_points:
                if s.ndim == 1:
                    s.plot(fig.subplots(2, 1, sharex=True, height_ratios=(3, 1)))
                else:
                    s.plot(fig.add_subplot(111))
            else:
                ax = fig.add_subplot(111)
                ax.errorbar(s.x, s.y, s.yerr, fmt=".")
            fig.canvas.draw_idle()
            plt.show(block=False)
        except Exception as exc:
            if not getattr(self, "_plot_failed", False):
                self._plot_failed = True
                print(f"adaptive: plot failed ({type(exc).__name__}: {exc})")


FREE_SUBDIVISION = 4  # candidate lattice of a free mesh: this much finer than the step
MAX_FREE_CANDIDATES = 250_000


def axis_steps(axis_positions):
    """Finest spacing of each axis' positions (0 for a single position)."""
    out = []
    for p in axis_positions:
        u = np.unique(np.asarray(p, float))
        out.append(float(np.diff(u).min()) if len(u) > 1 else 0.0)
    return out


def free_candidates(axis_positions):
    """Positions per axis for a free mesh: from the axis' first to last
    position, `FREE_SUBDIVISION` times finer than its finest step (coarser
    if all axes together would exceed `MAX_FREE_CANDIDATES` points)."""
    steps = axis_steps(axis_positions)
    spans = [np.ptp(np.asarray(p, float)) for p in axis_positions]
    sub = FREE_SUBDIVISION
    while sub > 1 and np.prod([s / st * sub + 1 for s, st in zip(spans, steps) if st]) > MAX_FREE_CANDIDATES:
        sub -= 1
    out = []
    for p, st in zip(axis_positions, steps):
        p = np.asarray(p, float)
        if not st:
            out.append(np.unique(p))
            continue
        n = int(round(np.ptp(p) / st * sub))
        out.append(np.linspace(p.min(), p.max(), n + 1))
    return out


def build_strategy(strategy, axis_positions, n_pulses, exposure="fixed", strategy_options=None):
    """An escape strategy on the grid `axis_positions` (one array per axis)."""
    ads = adaptive_scan_module()
    if not isinstance(strategy, str):
        return strategy  # a ready strategy object
    try:
        cls = getattr(ads, STRATEGIES[strategy])
    except KeyError:
        raise ValueError(f"strategy {strategy!r}: one of {sorted(STRATEGIES)}") from None
    axes = [np.unique(np.asarray(p, float)) for p in axis_positions]
    bounds = [(a.min(), a.max()) for a in axes]
    mesh = np.meshgrid(*axes, indexing="ij")
    candidates = np.column_stack([m.ravel() for m in mesh])
    if exposure == "fixed":
        exposure_model = ads.ExposureModel(base=n_pulses)
    elif exposure == "auto":
        exposure_model = ads.ExposureModel("auto", base=n_pulses)
    else:
        exposure_model = ads.ExposureModel(list(exposure), base=n_pulses)
    options = dict(strategy_options or {})
    options.setdefault("exposure", exposure_model)
    return cls(bounds if len(bounds) > 1 else bounds[0], candidates=candidates, **options)
