"""eco.ei_wait -- something to look at while %ei waits for the model.

A spinner with what %ei is doing ("updating the index", "searching",
"asking <model>") and the seconds waited, from the moment the question is
asked. Now and then (config "wait_animations", the share of questions) one of the
small animations below on top, picked at random and never the same twice
in a row, so it stays a surprise. Drawn in place under the question and
erased as soon as the answer starts, only in a real terminal; messages
printed meanwhile go through paused().
"""

import contextlib

import math
import random
import sys
import threading
import time

# -- the animations: (name, seconds per frame, frames) ---------------------------------


def _chicken_and_egg():
    head, body = "     __", "    ( ._> /"
    frames = [
        ("<(o )___", "", " `---'", "_/  \\_      "),
        ("<(O )___", "hmm...", " `---'", "_/  \\_      "),
        ("<(>< )___", "nnngh!", " `---'", "_/  \\_      "),
        ("<(>< )___", "NNNGH!!", " `-o-'", "_/  \\_      "),
        ("<(^ )___", "POP!", " `---'", "_/()\\_      "),
        ("<(^ )___", "bawk bawk!", " `---'", "_/  \\_   () "),
        ("<(o )___", "", " `---'", "_/  \\_   (/)"),
        ("<(O )___", "", " `---'", "_/  \\_   <')"),
        ("<(O )___", "so who came first?", " `---'", "_/  \\_   <')"),
    ]
    out = []
    for face, says, belly, feet in frames:
        out.append("\n".join([head, f"   {face:<10} {says}", body, f"    {belly}", f"    {feet}"]))
    return out


def _undulator(width=30):
    top = "   " + "N S " * (width // 4)
    bottom = "   " + "S N " * (width // 4)
    frames = []
    for x in range(0, width - 2, 2):
        upper = (x // 2) % 2 == 0
        path = "." * x + "o"
        rows = [top, "   " + (path if upper else " " * len(path)), "   " + (" " * len(path) if upper else path), bottom]
        frames.append("\n".join(rows))
    for n in range(1, 6):
        frames.append("\n".join([top, "   " + "." * (width - 2) + " " + "~" * n + ">", "", bottom]))
    frames.append("\n".join([top, "   " + "." * (width - 2) + " ~~~~~> X-rays!", "", bottom]))
    frames.append("\n".join([top, "   " + "." * (width - 2) + " ~~~~~> lasing :)", "", bottom]))
    return frames


def _coffee():
    cup = ["  .--------.", "  |{}|]", "  |{}|]", "  |{}|]", "  `--------'"]
    steam = ["", "    (  )", "     )(", "    (  )"]
    frames = []
    for level in range(0, 4):
        rows = ["        " if 3 - i > level else "~~~~~~~~" for i in range(3)]
        frames.append("\n".join(["", "", cup[0], cup[1].format(rows[0]), cup[2].format(rows[1]), cup[3].format(rows[2]), cup[4]]))
    for i in range(4):
        full = cup[1].format("~~~~~~~~")
        frames.append("\n".join([steam[i % 4], steam[(i + 1) % 4], cup[0], full, cup[2].format("~~~~~~~~"),
                                 cup[3].format("~~~~~~~~"), cup[4] + ("  coffee while it thinks" if i >= 2 else "")]))
    return frames


def _laser_cat(width=36):
    cat = [" /\\_/\\ ", "( o.o )", " > ^ < "]
    pounce = [" /\\_/\\ ", "( ^.^ )", " />*<\\ "]
    dots = [28, 6, 31, 15, 2, 24]
    frames, x = [], 0
    for dot in dots:
        while abs(x - dot) > 2:
            x += 3 if dot > x else -3
            floor = [" "] * (width + 8)
            floor[dot + 3] = "*"
            frames.append("\n".join([" " * x + c for c in cat] + ["".join(floor).rstrip()]))
        frames.append("\n".join([" " * x + c for c in pounce] + ["  " + " " * x + "got it!"]))
    return frames


def _peak_scan(points=24, height=6):
    noise = [0.0, 0.1, -0.05, 0.05, 0.12, -0.1, 0.0, 0.08]
    ys = [round(max(0.0, math.exp(-((i - 15) / 3.0) ** 2) + noise[i % len(noise)] * 0.6) * (height - 1)) for i in range(points)]
    frames = []
    for n in range(1, points + 1):
        rows = []
        for level in range(height - 1, -1, -1):
            rows.append(" |" + "".join("*" if y == level else " " for y in ys[:n]))
        rows.append(" +" + "-" * points)
        label = " scans.ascan(answer, ...)" if n < points else " peak found! ->"
        frames.append("\n".join(rows + [label + f"  step {n}/{points}"]))
    frames += [frames[-1]] * 3
    return frames


ANIMATIONS = {
    "chicken and egg": (0.7, _chicken_and_egg()),
    "undulator": (0.15, _undulator()),
    "coffee": (0.6, _coffee()),
    "laser cat": (0.18, _laser_cat()),
    "peak scan": (0.2, _peak_scan()),
}

def width(name):
    """Columns the widest frame of an animation needs."""
    return max(len(line) for frame in ANIMATIONS[name][1] for line in frame.split("\n"))


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_last_animation = None


def pick(share, rng=random):
    """Name of the animation for this question, or None (the spinner):
    with probability `share`, at random, not the previous one again."""
    global _last_animation
    if not share or rng.random() >= share:
        return None
    names = [n for n in ANIMATIONS if n != _last_animation] or list(ANIMATIONS)
    _last_animation = rng.choice(names)
    return _last_animation


# -- drawing ------------------------------------------------------------------------


class Waiting:
    """Draws the spinner or an animation on `out` from a thread until
    stop(); stop() erases it. Thread-safe: stop() waits for a frame being
    drawn, so the answer never lands in the middle of one."""

    def __init__(self, out, label="", animation=None):
        self.out = out
        self.label = label  # may be changed while it runs
        self.animation = animation
        self.preview = []  # lines shown under the status, e.g. the answer being written
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._height = 0  # lines of the frame on screen
        self._thread = None

    def start(self):
        import shutil

        self._t0 = time.time()
        if self.animation and shutil.get_terminal_size().columns <= width(self.animation):
            self.animation = None  # wrapped lines could not be erased
        self._thread = threading.Thread(target=self._run, name="ei-wait", daemon=True)
        self._thread.start()
        return self

    def _frames(self):
        import shutil

        delay, frames = ANIMATIONS[self.animation] if self.animation else (0.1, [""])
        tick, i, shown = 0.1, 0, 0.0
        while True:
            elapsed = time.time() - self._t0
            art = frames[int(elapsed / delay) % len(frames)] if self.animation else ""
            lines = (art.split("\n") if art else []) + [f"{SPINNER[i % len(SPINNER)]} {self.label} {elapsed:.0f} s"]
            preview = list(self.preview)
            lines += [f"  | {line}" for line in preview]
            # a wrapped line could not be erased: cut every line to the width
            columns = shutil.get_terminal_size().columns - 1
            yield "\n".join(line[:columns] for line in lines), tick
            i += 1

    @contextlib.contextmanager
    def paused(self):
        """Erase it while the block prints something (or asks), then draw
        it again below."""
        with self._lock:
            if self._height:
                self.out.write((f"\x1b[{self._height - 1}A" if self._height > 1 else "") + "\r\x1b[J\x1b[?25h")
                self.out.flush()
                self._height = 0
            yield

    def _run(self):
        for frame, delay in self._frames():
            with self._lock:
                if self._stop.is_set():
                    return
                self._draw(frame)
            if self._stop.wait(delay):
                return

    def _draw(self, frame):
        lines = frame.split("\n")
        # back to the frame's first line, clear to the end of the screen
        erase = (f"\x1b[{self._height - 1}A" if self._height > 1 else "") + "\r\x1b[J"
        self.out.write(("\x1b[?25l" if not self._height else erase) + "\n".join(lines))
        self.out.flush()
        self._height = len(lines)

    def stop(self):
        with self._lock:
            if self._stop.is_set():
                return
            self._stop.set()
            if self._height:
                self.out.write((f"\x1b[{self._height - 1}A" if self._height > 1 else "") + "\r\x1b[J\x1b[?25h")
                self.out.flush()
                self._height = 0


def in_terminal(out=None):
    """True when drawing in place works: an interactive terminal, not a
    notebook, qtconsole or a captured stream."""
    import os

    out = out or sys.stdout
    if os.environ.get("TERM", "dumb") == "dumb" or "ipykernel" in type(out).__module__:
        return False
    try:
        return out.isatty() and sys.__stdout__.isatty()
    except (AttributeError, ValueError):
        return False
