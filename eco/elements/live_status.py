"""Blocking, live-refreshing terminal display -- `Assembly.live_status()`.

A single-threaded, blocking loop: while it runs, nothing else is reading the
terminal (unlike the earlier `__repr__`-based prototype, which ran a
background refresh thread *while IPython's own prompt was simultaneously
reading your next keystrokes* -- a race that turned out to lose keystrokes
outright and was abandoned; see git history for that attempt). Here there is
only ever one reader of stdin at a time: this loop, for as long as the call
blocks. `select.select(..., timeout=interval)` both throttles the refresh
rate and responsively notices a keypress the moment one arrives, so no
separate thread is needed at all.
"""

import select
import sys
import time

from rich.live import Live
from rich.text import Text

#: keys that end the loop: q/Q, Escape, Ctrl-C (as a raw byte -- see below
#: for why Ctrl-C is also caught as a real KeyboardInterrupt).
_STOP_KEYS = {"q", "Q", "\x1b", "\x03"}


def _to_renderable(text):
    """Turn a `get_display_str()`-style string (raw ANSI colour codes, from
    colorama or already rich-rendered -- see `eco.utilities.tables`) into
    something `rich.live.Live` can size and repaint correctly, with a
    trailing newline forced so the terminal's cursor ends up on a fresh
    line below the table instead of stuck after its last character."""
    if not text.endswith("\n"):
        text += "\n"
    return Text.from_ansi(text)


def run_live_status(render, interval=0.5):
    """Block, repainting `render()` (a zero-arg callable returning the
    current display string) every `interval` seconds, until ``q``, Escape,
    or Ctrl-C is pressed.

    Falls back to a plain `time.sleep` loop (Ctrl-C only, via
    `KeyboardInterrupt`) when stdin isn't a real tty -- a notebook, a piped
    session, or a non-interactive test run can't do raw single-key reads.
    """
    if not _isatty():
        _run_without_raw_input(render, interval)
        return

    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        with Live(_to_renderable(render()), refresh_per_second=4, transient=False) as live:
            while True:
                ready, _, _ = select.select([sys.stdin], [], [], interval)
                if ready:
                    ch = sys.stdin.read(1)
                    if ch in _STOP_KEYS:
                        break
                live.update(_to_renderable(render()))
    except (KeyboardInterrupt, SystemExit):
        # Ctrl-C should just stop the loop, not propagate out of
        # live_status() and be mistaken by IPython for an attempt to exit
        # the whole session -- confirmed live: an uncaught SystemExit here
        # otherwise surfaces as IPython's "use 'exit', 'quit', or Ctrl-D"
        # warning once control returns to the prompt.
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _run_without_raw_input(render, interval):
    try:
        with Live(_to_renderable(render()), refresh_per_second=4, transient=False) as live:
            while True:
                time.sleep(interval)
                live.update(_to_renderable(render()))
    except KeyboardInterrupt:
        pass


def _isatty():
    try:
        return sys.stdin.isatty()
    except Exception:
        return False
