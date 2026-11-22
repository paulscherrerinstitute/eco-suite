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

## Camera viewers do their image work in a separate process

Keeping a window *serviced* is one half; the other is how much work each
frame costs the main thread. Turning a large camera frame into a picture
(averaging, background, contrast, colormap, histogram) used to take
80–150 ms of main-thread time per frame for a 5-megapixel camera, at 10 Hz
most of the main thread, and in the desktop the console runs on it too.

A camera viewer now starts a small helper process for this. It receives the
camera stream, does all of that work plus the profiles and fits, and sends
back a picture already the size it is shown at. The viewer itself only
draws it. Measured on Bernina cameras, the main-thread time per frame went
from 31 to 5 ms (450×2048) and from 151 to 7 ms (2048×2448).

Nothing changes in use. If the helper process cannot start, or stops, the
viewer carries on doing the work itself, as before. To have it do that from
the start:

```python
cam_north.widget(process=False)
```

or, for every viewer of a session, start it with

```bash
ECO_CAMERA_PROCESS=0 eco
```

### Profiles and fits

**Profile** and **Fit** in the viewer's toolbar (or
`cam_north.widget(show_fit=True, show_profile=True)`) show what pshell's screen
panel shows: the two projections along the image edges, the fitted
Gaussians, and the fit's center and width. **Settings → Fit** chooses what
is fitted:

- *Pipeline*: the fit the camera server makes of every frame (the `_sp`
  pipeline, as in pshell).
- *Local*: a fit of what the viewer shows, after averaging, background
  subtraction and ROI.
- *Local, good region*: pshell's/the camera server's "good region": only
  the part around the peak, as far out as `threshold` of its height,
  widened by `scale`.
- *Auto* (default): the pipeline's fit while the viewer shows its image
  unchanged, else the local one.

The fit is available in the session too:

```python
v = cam_north.widget()
v.fit                                     # {"source", "unit", "x": {"mean", "sigma", ...}, "y": ...}
v.readout("fit_x_mean").get_current_value()   # a Detector, for scans and strip plots
s = v.streams("fit_x_mean", "fit_x_sigma")    # escape Streams, each value with its pulse id
s["fit_x_mean"].plot()
```

Local fits are in camera pixels; a pipeline fit is in the camera server's
own (possibly calibrated) units.

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
