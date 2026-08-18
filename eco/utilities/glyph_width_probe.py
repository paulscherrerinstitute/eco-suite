#!/usr/bin/env python3
"""Measure how many terminal cells a glyph actually occupies, by printing it
and asking the terminal for the resulting cursor column (DSR / CPR).

Run this INSIDE the terminal whose rendering you want to check:

    python glyph_width_probe.py

It compares each glyph's real width (what your terminal did) against what
rich thinks, so we can see exactly which characters disagree.
"""
import re
import sys
import termios
import tty

try:
    from rich.cells import cell_len as rich_len
except Exception:  # rich optional
    rich_len = None

try:
    from wcwidth import wcswidth
except Exception:
    wcswidth = None


def measured_width(s):
    """Real rendered width of `s`: print at column 1, ask where the cursor
    ended up (columns are 1-based, so width = final_col - 1)."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdout.write("\r")            # cursor to column 1
        sys.stdout.write(s)               # draw the glyph(s)
        sys.stdout.write("\x1b[6n")       # DSR: please report cursor position
        sys.stdout.flush()
        buf = ""
        while not buf.endswith("R"):
            buf += sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\r\x1b[2K")     # clear the probe line
        sys.stdout.flush()
    m = re.search(r"\x1b\[\d+;(\d+)R", buf)
    return int(m.group(1)) - 1 if m else None


SAMPLES = [
    ("pencil    ✏️", "✏️"),
    ("eye       👁️", "\U0001f441️"),
    ("warning   ⚠️", "⚠️"),
    ("arrow     ↳", "↳"),
    ("pencil+arrow ✏️ ↳", "✏️ ↳"),
    ("eye+arrow    👁️ ↳", "\U0001f441️ ↳"),
]

print(f"{'label':22} {'terminal':9} {'rich':5} {'wcwidth':8}")
print("-" * 48)
for label, s in SAMPLES:
    term = measured_width(s)
    r = rich_len(s) if rich_len else "?"
    w = wcswidth(s) if wcswidth else "?"
    flag = ""
    if isinstance(term, int) and isinstance(r, int) and term != r:
        flag = "  <-- rich disagrees with terminal"
    print(f"{label:22} {str(term):9} {str(r):5} {str(w):8}{flag}")
