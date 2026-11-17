# GUI guide

`FeedbackPanel(url, api_key=...)` (in `eco.feedback.gui`) builds a control
panel with six tabs, all sharing one refresh timer (default every second).
Each panel below has a matching "📖 docs" link in the running GUI that opens
straight to its section here. Pass `api_key` if the server was started with
one (see "Auth" in `docs/API.md`) — required for anything the GUI writes
(toggles, gains, force/save/measure buttons); read-only panels work without it.

```python
from eco.feedback.gui import FeedbackPanel
FeedbackPanel("http://127.0.0.1:8001", api_key="demo-key").widget
```

or served standalone with Voilà:

```bash
python -m eco.feedback panel http://127.0.0.1:8001 --api-key demo-key   # Voila on :8866
```

In an eco session the panel can also run in-process, without any server
(`fb.panel()` on a `FeedbackManager`); the "📖 docs" links are then
replaced by plain headings, since there is no server to serve this guide.

## Settings

One sub-tab per feedback loop. Each has:

- **enabled** — toggles the loop on/off. Turning it on resets the PID's
  integral term, so it never jumps on enable.
- **setpoint** — the target detector value.
- **rate [Hz]** — how often the loop cycles.
- **kp / ki / kd** — PID gains.
- a live readback line: current detector value, the loop's actual cycle
  rate, and `HELD`/`ALARM` flags in red when an interlock is blocking
  correction or output has stayed saturated too long (see System > Alarms /
  Interlocks).

Every field edit is sent to the server immediately (`PATCH /loops/{name}`).

## Plots

One sub-tab per loop: detector value and setpoint (dashed line) on the left
axis, PID output on the right axis, over the last 500 samples
(`GET /loops/{name}/history`). Redrawn each refresh tick.

## Matrix

Measures a response matrix: how much each detector moves per unit change of
each actuator. This is the calibration step before any multi-actuator
correction — it doesn't run a closed loop itself.

1. Pick one or more **actuators** and **detectors** from the lists (populated
   from `GET /actuators` / `GET /detectors` — includes both loop-driven and
   free-standing actuators).
2. Set **step** (how far to move each actuator), **settle [s]** (wait time
   before reading detectors), **repeats** (samples averaged per read), and
   **bidirectional** (step both directions and take the central difference —
   more accurate, about twice as slow as a one-sided step).
3. Click **Measure**. A progress bar tracks which actuator is currently being
   stepped. If a loop is actively running on that actuator, it's
   automatically disabled for its step and re-enabled right after, so it
   doesn't fight the measurement.
4. On completion, the matrix is drawn as a heatmap (rows = detectors,
   columns = actuators, values = d(detector)/d(actuator)) with a condition
   number underneath — a high condition number means the matrix is close to
   singular (some actuator barely affects anything, or two actuators do
   nearly the same thing), which is useful to know before trusting the
   matrix for anything downstream.

### Closed-loop correctors

Below the acquisition controls: once a measurement finishes, name it (in the
**corrector name** field) and click **Use for closed-loop correction** to
create a `MatrixFeedbackLoop` from that matrix — it appears **disabled** in
the "Closed-loop correctors" list underneath, one collapsible section per
corrector:

- **enabled** — closes the loop: continuously drives every actuator toward
  the reference vector from every detector's error. Off by default; you turn
  it on deliberately, same as any single loop.
- **gain** — proportional gain applied every cycle (standard SVD
  orbit-feedback scheme). **rcond** — SVD cutoff for the pseudo-inverse;
  raise it if the matrix is ill-conditioned and corrections look noisy.
- **Save reference** — captures the detectors' current readings as the new
  target (the "save golden orbit" action); the corrector subsequently drives
  toward whatever they read at that moment.
- one weight field per detector (default `1.0`) — set to `0` to mask a
  detector out of the correction (e.g. it's misbehaving) without
  re-measuring the whole matrix.
- the readback line shows each detector's live error from its reference,
  plus `HELD`/`ALARM` flags exactly like a single loop.

## Logs

A scrolling read-out of the server's event log (`GET /logs`): loop
enable/disable/hold, PID output saturation/alarm, and response-matrix
measurement progress, newest at the bottom.

## System

Everything about the server's operating state that isn't a specific loop:

- **Interlocks** — one row per registered interlock: green **OK** or red
  **TRIPPED**, plus **Force OK** / **Force trip** / **Clear** buttons.
  Forcing overrides the live condition (commissioning/emergency bypass);
  Clear goes back to reading it live. Any loop subscribed to a tripped
  interlock shows `HELD` in Settings/Matrix instead of disabling.
- **Alarms** — currently-alarmed loops/correctors (output saturated past
  `alarm_after_s`). Empty in normal operation.
- **Health** — server status (`ok`/`degraded`) and uptime, plus each loop's
  liveness (`alive`/`STALE` and time since its last cycle) — catches a hung
  control thread even though the API itself keeps responding.
- **Config** — **Save config** / **Load config** buttons (optional path
  field; blank uses the server's configured default). Explicit and
  operator-triggered, like a DOOCS save/restore panel — nothing autosaves.

## Help

Links out to this guide, the API guide, and the live Swagger/ReDoc API
reference — useful when you're scripting against the server directly instead
of using the GUI (see `docs/API.md` / `/docs/api`).

## Demos

Three demo servers (`eco/feedback/demo.py`), none needs hardware.
`python -m eco.feedback demo matrix` is the fastest way to see every panel
and every feature; run one, then `FeedbackPanel("http://127.0.0.1:<port>",
api_key=...).widget`. `python -m eco.feedback demo eco` (port `8010`, no
key) runs two loops on eco's own `AdjustableMemory`/`DetectorGet` objects
instead of the plain simulators, so writes take eco's real `Changer` path.

**`demo matrix`** (port `8001`, API key `demo-key`) — a minimal
2-actuator/2-detector system, purpose-built for trying the GUI end to end:

- Loops: `loop_1` (actuator `cx1` → detector `bpm1`), `loop_2` (`cx2` → `bpm2`).
  Both are enabled/disabled and tuned from **Settings**, and plot in **Plots**.
- `cx1`/`cx2` and `bpm1`/`bpm2` are real cross-coupled (hidden matrix
  `[[1.0, 0.3], [0.2, 1.0]]`): in **Matrix**, select both actuators and both
  detectors and click Measure — the result should come out close to that
  matrix, and off-diagonal terms (`0.3`, `0.2`) confirm the coupling is real,
  not just diagonal self-response.
- A closed-loop corrector named `steering` is already built (from a
  measurement of the real matrix taken at startup) and sits **disabled** in
  Matrix's "Closed-loop correctors" list — enable it there to watch it null
  out the cross-coupling between the two loops.
- `loop_1` has a tight output clamp and a short `alarm_after_s` (2s) — push
  its setpoint well away from 0 in Settings and watch System > Alarms (and
  the red `ALARM` flag in Settings itself) light up within a couple seconds.
- An interlock named `beam_permit` is normally OK; force it to trip from
  System > Interlocks and watch both loops show `HELD` instead of disabling
  — clear the force and they resume on their own.
- Every write (toggles, gains, Measure, force, save/load config) needs the
  `demo-key` API key — pass it to `FeedbackPanel`/`FeedbackClient`, or GET
  routes still work without one for read-only inspection.
- System > Config's Save/Load buttons read/write
  `~/.eco/feedback_demo/demo_matrix_config.yaml`; history/logs archive to
  `~/.eco/feedback_demo/demo_matrix.sqlite` and survive a restart
  (`?archive=true`). Delete that folder to reset to a clean state.

**`demo busy`** (port `8000`, no API key) — a busier setup:

- `pointing_x`, `pointing_y` — two independent, uncoupled loops (their own
  motor/BPM pair each, no shared matrix).
- A 3-actuator/3-detector coupled steering system: `cx1`/`bpm1` are wired
  into loop `steering_x1`; `cx2`/`cx3` are free-standing actuators (visible
  in Matrix's actuator picker, but with no Settings/Plots tab of their own,
  since no loop drives them) — good for checking that response-matrix
  acquisition works across a mix of loop-bound and free-standing actuators.
  Hidden matrix (rows/cols in `cx1, cx2, cx3` / `bpm1, bpm2, bpm3` order):
  `[[1.00, 0.25, 0.05], [0.10, 1.00, 0.20], [0.05, 0.15, 1.00]]`.
- The same matrix is also kept fresh automatically: a schedule re-measures it
  every 60s (`GET /response-matrix/steering` shows `next_run_in_s` ticking
  down). No GUI control for schedules yet — inspect/manage via the API.
