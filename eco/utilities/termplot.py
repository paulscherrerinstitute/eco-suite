"""
Text plots in the terminal, for where no Qt window can be shown (plain ssh,
ECO_NO_X=1). A thin eco-shaped layer over uniplot -- callers use this module,
not uniplot, so the renderer can be replaced later. See docs/terminal_plots.md
for the decision and the list of uniplot's shortcomings.

- panel_stack(): several plots stacked vertically over a shared x range
- LiveRegion: keeps a block of text (a plot) just above the current terminal
  line and redraws it in place, without disturbing a status line that other
  code keeps rewriting with '\\r' (as the terminal tweaks do).
"""

import shutil
import sys
import threading

import numpy as np


def available():
    try:
        import uniplot  # noqa: F401

        return True
    except Exception:
        return False


def terminal_size():
    return shutil.get_terminal_size((100, 40))


def panel_stack(panels, width=None, height=4, x_range=None):
    """Render panels stacked over a shared x range; x tick labels only under
    the last one.

    panels: list of dicts with "title", "xs", "ys" and optionally
    "highlight_last" (draw the last point as a separate marker series).
    Returns the text, lines joined with '\\n'."""
    from uniplot import plot_to_string

    if width is None:
        width = max(20, terminal_size().columns - 14)
    finite_x = [
        np.asarray(p["xs"], dtype=float)[np.isfinite(np.asarray(p["xs"], dtype=float))]
        for p in panels
    ]
    if x_range is None:
        allx = np.concatenate(finite_x) if finite_x else np.array([])
        x_range = (allx.min(), allx.max()) if allx.size else (0.0, 1.0)
    x_min, x_max = x_range
    if x_max <= x_min:
        x_min, x_max = x_min - 0.5, x_max + 0.5

    blocks = []
    for k, p in enumerate(panels):
        xs = np.asarray(p["xs"], dtype=float)
        ys = np.asarray(p["ys"], dtype=float)
        ok = np.isfinite(xs) & np.isfinite(ys)
        xs, ys = xs[ok], ys[ok]
        if xs.size == 0:  # nothing to show yet: an empty frame of the same size
            xs, ys = np.array([np.nan]), np.array([np.nan])
        series_x, series_y, lines = [xs], [ys], [True]
        if p.get("highlight_last") and np.isfinite(xs[-1]):
            series_x.append(xs[-1:])
            series_y.append(ys[-1:])
            lines.append(False)
        kwargs = dict(
            width=width,
            height=height,
            title=p["title"],
            lines=lines,
            x_min=x_min,
            x_max=x_max,
            x_labels=(k == len(panels) - 1),
            color=len(series_y) > 1,
        )
        try:
            text = plot_to_string(series_y if len(series_y) > 1 else series_y[0],
                                  series_x if len(series_x) > 1 else series_x[0],
                                  **kwargs)
        except Exception:
            kwargs["color"] = False
            text = plot_to_string([0.0], [x_min], **dict(kwargs, lines=[False]))
        blocks.append(text.rstrip("\n"))
    return "\n".join(blocks)


class LiveRegion:
    """Proxy for sys.stdout that keeps a text block just above the line the
    cursor is on, redrawing it in place.

    While installed, all output goes through it (so it knows whether lines
    were added since the block was drawn, and what the current line shows):
    - nothing printed a newline since the last draw -> the block is redrawn
      in place (cursor up, rewrite, back down), the current line untouched
    - otherwise (help text, prompts, other output scrolled in between) ->
      a fresh copy is printed, and the current line (e.g. a status line
      written with '\\r') is restored under it."""

    def __init__(self, stream=None):
        self._stream = stream if stream is not None else sys.stdout
        self._lock = threading.RLock()
        self._newlines = 0
        self._tail = ""
        self._height = 0
        self._installed = False

    # --- stdout proxy ---
    def write(self, s):
        with self._lock:
            n = self._stream.write(s)
            if "\n" in s:
                self._newlines += s.count("\n")
                self._tail = s.rsplit("\n", 1)[1]
            else:
                self._tail += s
            segments = [seg for seg in self._tail.split("\r") if seg]
            self._tail = segments[-1] if segments else ""
            return n

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def install(self):
        if not self._installed:
            sys.stdout = self
            self._installed = True
        return self

    def uninstall(self):
        if self._installed and sys.stdout is self:
            sys.stdout = self._stream
        self._installed = False

    # --- drawing ---
    def show(self, text):
        lines = text.split("\n")
        with self._lock:
            if self._height and self._height == len(lines) and self._newlines == 0:
                seq = f"\r\x1b[{len(lines)}A" + "".join(
                    "\x1b[2K" + line + "\n" for line in lines
                )
            else:
                seq = (
                    "\r\x1b[2K"
                    + "".join("\x1b[2K" + line + "\n" for line in lines)
                    + self._tail
                    + "\r"
                )
            self._stream.write(seq)
            self._stream.flush()
            self._height = len(lines)
            self._newlines = 0


class LivePlotter:
    """Redraws `render()` into a LiveRegion whenever `version()` changes."""

    def __init__(self, render, version, interval=0.3, stream=None):
        self._render = render
        self._version = version
        self._interval = interval
        self.region = LiveRegion(stream)
        self._stop_event = threading.Event()
        self._drawn = None
        self._thread = None

    def start(self):
        self.region.install()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def refresh(self):
        version = self._version()
        if version == self._drawn:
            return
        self._drawn = version
        self.region.show(self._render())

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self.refresh()
            except Exception:
                pass
            self._stop_event.wait(self._interval)

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        try:
            self.refresh()  # final state
        except Exception:
            pass
        self.region.uninstall()
