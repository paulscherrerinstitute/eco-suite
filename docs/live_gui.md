# GUI windows stay live while a command runs

Qt windows (camera viewers, live plots, strip charts in-process, the desktop
workbench itself) can only be redrawn by the main Python thread. IPython
normally redraws them only while it waits at the prompt. So, traditionally, a
blocking statement froze every open window until it returned: `umv`, a scan,
`time.sleep`, a long loop.

eco now keeps those windows serviced *during* such statements:

```python
cam_north.widget()       # open a live camera viewer
umv(mot, 10)             # the viewer keeps updating while the motor moves
```

Measured on a 20 Hz camera viewer during `sleep`, a motor `wait()` and pure
Python loops: before, 0 frames/s; now, 19–20 frames/s. That holds both in a
terminal session and docked in the desktop. In the desktop it also keeps the
console itself responsive, so **Ctrl-C / Interrupt** reaches a running cell.

This is also why camera viewers now open in-process by default: they start
instantly, dock into the desktop, and share the very camera object your
session uses.

## How it works

A watchdog thread notices when Qt's own event loop has not run for 60 ms
while a cell is executing. It then briefly interrupts the main thread to
process pending GUI events, after which your code continues where it was.
When Qt's loop runs by itself (at the prompt), nothing happens at all.

## Checking and switching it off

```python
from eco.utilities import mainthread
mainthread.status()          # what it has been doing
```

```bash
ECO_NO_HEARTBEAT=1 eco        # start a session without it
```

## Limits

- A single long call into C code on the main thread (a large numpy
  operation, for example) cannot be interrupted. The windows resume when it
  returns.
- A Ctrl-C that arrives while the GUI is being serviced is held back for that
  moment and then raised in your code, as usual.
- For developers: an *untimed* wait on the main thread (`thread.join()`,
  `event.wait()` without a timeout) cannot be interrupted either. Use
  `mainthread.join(thread)` / `mainthread.wait_event(event)`, which wait in
  slices. eco's own waits (`Changer.wait()`, `Acquisition.wait()`, a paused
  scan) already do.
