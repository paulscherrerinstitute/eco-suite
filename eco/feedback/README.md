# eco.feedback — feedback loops on eco objects (under development)

PID feedback loops between eco detectors and adjustables, response-matrix
measurement and SVD matrix correction, interlocks, saturation alarms and
config save/restore. It follows the DOOCS/Java feedback server: a server
that holds the loops, with a tabbed control panel on top. Not yet tested
against beamline hardware; interfaces may still change.

This is unrelated to the older `eco.utilities.feedback` (one `simple_pid`
Assembly, used by `eco.xoptics.xopt_feedback`).

## In an eco session (no extra dependencies)

```python
from eco.feedback import FeedbackManager, PID

fb = FeedbackManager()
fb.add("mono_hor", "mon_opt_new.xpos", "mono.roll1_piezo",   # alias names or objects
       setpoint=0.0, rate_hz=1, pid=PID(kp=-0.1, out_min=-1, out_max=1))
fb                                  # one line per loop: off / ON / HELD / ALARM / ERROR
fb.service.set("mono_hor", enabled=True)
fb.service.history("mono_hor", n=20)
fb.qt_panel()                       # Qt window (terminal / desktop sessions)
fb.panel()                          # ipywidgets panel (JupyterLab / Voila / notebook UIs)
fb.disable_all()                    # open every loop; threads keep monitoring
```

The loops run in threads of the session. `fb.service` has the same methods
and return values as the HTTP client below, so a script written against
one works with the other.

The Qt panel is one window: loop table, settings and trend plot of the
selected loop, Enable / Disable / Disable all, interlocks, alarms and the
log. Matrix loops are listed there but set up from the ipywidgets panel or
`fb.service`. To reach the loops from elsewhere, serve them:

```python
srv = fb.serve(port=8010)           # background thread, 127.0.0.1 only
# shell: python -m eco.feedback panel http://127.0.0.1:8010 --qt   -> Qt window
# shell: python -m eco.feedback panel http://127.0.0.1:8010        -> Voila on :8866 (ssh -L)
srv.stop()
```

## As a server process

```bash
python -m eco.feedback serve eco/feedback/examples/epics_pvs.py --port 8010
python -m eco.feedback demo eco         # no hardware: AdjustableMemory/DetectorGet loops, :8010
python -m eco.feedback demo matrix      # no hardware: the full GUI walkthrough, :8001, key demo-key
python -m eco.feedback panel http://127.0.0.1:8010
```

A setup script defines `manager` or `build_manager()`; see `examples/`
(raw PVs; Bernina namespace components). Clients:
`eco.feedback.FeedbackClient(url, api_key=...)`, `FeedbackPanelQt(url, ...)`,
`FeedbackPanel(url, ...)`,
the Swagger UI at `<url>/docs`. Needs `fastapi` and `uvicorn`
(`pip install eco-suite[feedback]`); bpy312 has uvicorn but not fastapi.

Full references: [docs/API.md](docs/API.md) (HTTP routes),
[docs/GUI.md](docs/GUI.md) (panel and demos).

## Semantics worth knowing

- **The PID output is a correction rate.** Each cycle writes
  `current + pid(setpoint - value) * dt`. So `kp` already integrates (it
  behaves like an I controller on the plant), and `ki` alone makes a double
  integrator that oscillates. Start with `kp` only, with its sign set by the
  sign of d(detector)/d(adjustable), and keep `out_min`/`out_max` (limits
  on the rate, in adjustable units per second) small until the gain is
  known.
- **The thread runs from `add()` on**: it reads the detector and records
  history while disabled, and writes only while enabled.
- **A failing cycle is skipped, never fatal.** A read returning `None`
  (pyepics on a timed-out get), NaN or more than one value, a limit
  violation, or a `MoveBlocked` from a queue task's claim: nothing is
  written for that cycle. The message appears in the loop status (`error`,
  red in the panel) and is logged once, as is the recovery. Array detectors
  need a wrapper that reduces them to one number.
- **Writes wait for eco's `Changer`** (`write_timeout`, 30 s), so a motor
  never gets a new target before it has arrived, and response-matrix steps
  are read after the move. A slow actuator therefore caps the loop rate.
  `/health` allows for that.
- **Writes are ordinary eco writes.** They go through the access gate and
  the queue claims like any `mv`, and they are not claims themselves, so a
  queue task can move a loop's adjustable. Disable the loop around scans of
  that adjustable.
- **Interlocks hold, they do not disable**: the loop resumes when the
  permit returns. An unreadable permit counts as tripped.
- **At interpreter exit** every manager disables and stops its loops, and
  panels stop refreshing. A panel thread still rendering during
  finalization segfaulted the process.
- The Qt panel never calls the client in a slot: a worker thread polls
  (the in-process service reads hardware, the HTTP client blocks) and a
  QTimer shows the latest snapshot; each write runs in its own thread.
- The ipywidgets panel refreshes from a background thread, so it uses neither pyplot
  nor `with output:`: plots are Agg-rendered PNGs, text is assigned to
  `Output.outputs`. Keep it that way: pyplot there would create Qt objects
  off the main thread in an eco session.

## Tests

`tests/test_feedback.py` (no hardware): loops on `AdjustableMemory`/
`DetectorGet`, failure paths, interlocks, response matrix plus matrix
loop, config round trip, server/client parity and both panels. The HTTP tests
skip without fastapi; a venv on top of bpy312 with fastapi added runs all
of them.

## Open

- The Qt panel cannot create or edit matrix loops / response matrices yet,
  and is not dockable in eco desktop yet.
- The panel builds its loop tabs once; loops added later need a new panel.
- Running a loop on real hardware: start in a session with small gains
  and clamps, then a server process.
