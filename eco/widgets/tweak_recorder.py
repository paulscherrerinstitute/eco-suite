"""
Record detector values along a tweak, and plot them.

TweakRecorder samples the tweak axes and the detectors in a background thread.
Each plateau of constant axis positions becomes one point: detector values are
averaged only while the positions stay put, so readings taken during a move
never mix in. A new point starts as soon as the positions settle somewhere
else -- whatever moved them (panel, terminal keys, IOC tweak, someone else).

"Constant" means: all axes within `settle_fraction` x their current step size
of the previous reading, for `settle_samples` consecutive readings.

draw_tweak_plot(fig, recorder) draws the points into a matplotlib Figure:
- 1 axis: one x/y plot per detector, detector vs axis position
- 2+ axes: a vertical stack sharing "step" as x, detectors on top, then one
  plot per axis position

Used by the Qt/ipywidgets tweak panels (embedded plot) and by the terminal
tweaks (separate Qt window, see terminal_recording).
"""

import contextlib
import math
import threading
import time

import numpy as np

from eco.widgets.tweak_panel import _name_of


def _as_float(value):
    """Scalar -> float; array-like -> mean of its elements; else NaN."""
    if value is None:
        return math.nan
    try:
        return float(value)
    except Exception:
        pass
    try:
        arr = np.asarray(value, dtype=float)
        return float(np.nanmean(arr)) if np.isfinite(arr).any() else math.nan
    except Exception:
        return math.nan


def _as_detector_list(detectors):
    if detectors is None:
        return []
    if isinstance(detectors, (list, tuple)):
        return list(detectors)
    return [detectors]


class TweakRecorder:
    def __init__(
        self,
        axes,
        detectors,
        interval=0.1,
        settle_fraction=0.1,
        settle_samples=2,
    ):
        self.axes = list(axes)
        self.detectors = _as_detector_list(detectors)
        self.axis_names = [a.name for a in self.axes]
        self.detector_names = [_name_of(d) for d in self.detectors]
        self.interval = interval
        self.settle_fraction = settle_fraction
        self.settle_samples = settle_samples
        self.version = 0  # bumped on every change, for cheap redraw checks
        self._points = []  # dicts, see points
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._last_positions = None
        self._stable_count = 0

    # --- running ---
    def start(self):
        if self._thread is None:
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def stop(self):
        self._stop_event.set()
        self._thread = None

    def _run(self):
        while not self._stop_event.is_set():
            t0 = time.time()
            try:
                self.sample()
            except Exception:
                pass
            self._stop_event.wait(max(0.0, self.interval - (time.time() - t0)))

    # --- sampling ---
    def _tolerances(self):
        tols = []
        for axis in self.axes:
            try:
                step = axis.current_step()
            except Exception:
                step = getattr(axis, "step", 0.0)
            tols.append(self.settle_fraction * abs(step or 0.0))
        return tols

    @staticmethod
    def _same(a, b, tols):
        return all(abs(x - y) <= t for x, y, t in zip(a, b, tols))

    def sample(self):
        """Take one reading (called by the thread; public for tests)."""
        positions = [_as_float(a.get_value()) for a in self.axes]
        if any(math.isnan(p) for p in positions):
            return
        tols = self._tolerances()
        last, self._last_positions = self._last_positions, positions
        if last is not None and self._same(positions, last, tols):
            self._stable_count += 1
        else:
            self._stable_count = 0
        if self._stable_count < self.settle_samples - 1:
            return  # moving, or not yet settled

        values = []
        for det in self.detectors:
            try:
                values.append(_as_float(det.get_current_value()))
            except Exception:
                values.append(math.nan)

        with self._lock:
            current = self._points[-1] if self._points else None
            if current is None or not self._same(
                positions, current["_position_sums"] / current["n_samples"], tols
            ):
                current = {
                    "step": len(self._points),
                    "time": time.time(),
                    "n_samples": 0,
                    "_position_sums": np.zeros(len(positions)),
                    "_values": [[] for _ in self.detectors],
                }
                self._points.append(current)
            current["n_samples"] += 1
            current["_position_sums"] += positions
            for store, value in zip(current["_values"], values):
                if not math.isnan(value):
                    store.append(value)
            self.version += 1

    # --- results ---
    @property
    def points(self):
        """One dict per plateau: step, time, n_samples, positions (mean per
        axis), values (mean per detector), stds (per detector)."""
        out = []
        with self._lock:
            for p in self._points:
                vals = [np.asarray(v) for v in p["_values"]]
                out.append(
                    {
                        "step": p["step"],
                        "time": p["time"],
                        "n_samples": p["n_samples"],
                        "positions": [float(v) for v in p["_position_sums"] / p["n_samples"]],
                        "values": [float(v.mean()) if v.size else math.nan for v in vals],
                        "stds": [float(v.std()) if v.size else math.nan for v in vals],
                    }
                )
        return out

    def arrays(self):
        """(steps, positions[n_points, n_axes], values[n_points, n_detectors])"""
        pts = self.points
        steps = np.array([p["step"] for p in pts])
        pos = np.array([p["positions"] for p in pts]).reshape(len(pts), len(self.axes))
        val = np.array([p["values"] for p in pts]).reshape(len(pts), len(self.detectors))
        return steps, pos, val

    def to_dataframe(self):
        import pandas as pd

        rows = []
        for p in self.points:
            row = {"step": p["step"], "time": p["time"], "n_samples": p["n_samples"]}
            row.update(zip(self.axis_names, p["positions"]))
            row.update(zip(self.detector_names, p["values"]))
            row.update((f"{n}_std", s) for n, s in zip(self.detector_names, p["stds"]))
            rows.append(row)
        return pd.DataFrame(rows)


def plot_rows(recorder):
    return len(recorder.detectors) if len(recorder.axes) == 1 else (
        len(recorder.detectors) + len(recorder.axes)
    )


def draw_tweak_plot(fig, recorder):
    """(Re)draw the recorder's points into matplotlib Figure `fig`."""
    fig.clear()
    steps, pos, val = recorder.arrays()
    n_rows = max(plot_rows(recorder), 1)
    axs = fig.subplots(n_rows, 1, sharex=True, squeeze=False)[:, 0]
    style = dict(marker="o", markersize=4, linewidth=0.8, color="C0")
    last = dict(marker="o", markersize=7, linestyle="none", color="C3")

    if len(recorder.axes) == 1:
        x = pos[:, 0] if len(steps) else []
        for ax, i in zip(axs, range(len(recorder.detectors))):
            if len(steps):
                ax.plot(x, val[:, i], **style)
                ax.plot(x[-1:], val[-1:, i], **last)
            ax.set_ylabel(recorder.detector_names[i])
        axs[-1].set_xlabel(recorder.axis_names[0])
    else:
        series = [(n, val[:, i] if len(steps) else []) for i, n in enumerate(recorder.detector_names)]
        series += [(n, pos[:, i] if len(steps) else []) for i, n in enumerate(recorder.axis_names)]
        for ax, (name, y) in zip(axs, series):
            if len(steps):
                ax.plot(steps, y, **style)
                ax.plot(steps[-1:], y[-1:], **last)
            ax.set_ylabel(name)
        axs[-1].set_xlabel("step")
    for ax in axs:
        ax.grid(True, alpha=0.3)
        ax.yaxis.label.set_fontsize(8)
        ax.tick_params(labelsize=7)
    fig.tight_layout()


def figure_size(recorder):
    return (5.0, max(2.8, 1.7 * plot_rows(recorder)))


def no_x():
    """True if ECO_NO_X is set (to anything but '', '0', 'false', 'no'):
    tweaks then never open Qt windows, see eco.widgets.tweak_panel.frontend."""
    import os

    return os.environ.get("ECO_NO_X", "").strip().lower() not in ("", "0", "false", "no")


def display_available():
    """False where creating a QApplication would abort the process (no X /
    Wayland display, e.g. a plain ssh session), or where Qt windows are
    switched off with ECO_NO_X=1."""
    import os
    import sys

    if no_x():
        return False
    if os.environ.get("QT_QPA_PLATFORM"):
        return True
    if not sys.platform.startswith("linux"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


@contextlib.contextmanager
def terminal_recording(axes, detectors, owner=None, label=None):
    """Record (and, where a display is available, live-plot in a Qt window)
    `detectors` during a terminal tweak of `axes`. The recorder is left on
    `owner._tweak_recorder` (if given) for later access, `label` says where
    in the final message; the plot window stays open after the tweak ends."""
    detectors = _as_detector_list(detectors)
    if not detectors:
        yield None
        return
    recorder = TweakRecorder(axes, detectors).start()
    if owner is not None:
        try:
            owner._tweak_recorder = recorder
        except Exception:
            pass
    if display_available():
        try:
            from eco.widgets.tweak_panel_qt import show_tweak_plot_qt

            recorder._plot_window = show_tweak_plot_qt(recorder)
        except Exception as exc:
            print(f"tweak: no live plot ({exc}); still recording")
    else:
        print("tweak: no display for a live plot; still recording")
    try:
        yield recorder
    finally:
        recorder.stop()
        print(
            f"\ntweak recorded {len(recorder.points)} points "
            f"({label or 'recorder'}.to_dataframe() for the data)"
        )
