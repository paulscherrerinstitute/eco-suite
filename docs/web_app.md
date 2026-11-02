# The web app: eco on a phone or tablet

The web app shows a running eco session in a browser, made for phones and
tablets and fine on a laptop: the namespace, live values, moves, tweak and
jog, live strip charts and the devices' schematic panels. It is served from
*inside* the session, so the browser and the terminal work on the same
objects. It can be installed like an app (it is a PWA, a Progressive Web App)
and needs nothing on the phone but a browser.

## Start it

```bash
eco web -s bernina              # console session + web app, read-only
eco web -s bernina --writes     # moves allowed from the browser
```

or from a session that is already running:

```python
eco.start_web()                 # read-only
eco.start_web(writes=True)
eco.stop_web()
```

It prints a link like `http://localhost:8097/?token=…`. Open it once with the
token; the browser keeps it afterwards. The token is per account
(`~/.eco/web_token`).

The web app only listens on the console itself (`127.0.0.1`). From your
laptop or phone, forward the port over SSH first, the same way as any other
tunnel to the consoles:

```bash
ssh -L 8097:localhost:8097 <console>      # plus your usual jump host
```

and open `http://localhost:8097/` there. Always use the same local port:
the browser keeps pinned items and the installed app per address.
If 8097 is taken on the console (another account's session), use
`--port 8098` / `start_web(port=8098)`.

Without any beamline, a simulated namespace shows what it does:

```bash
python -m eco.web --demo --writes
```

## Use it

- **Namespace**: initialized, not-yet-initialized (tap to initialize) and
  failed components, with a filter.
- **Device view**: the rows a device's widget shows, with live values, a
  status dot and the age of a value that has not changed for a while. A
  sub-assembly unfolds in place (▸, fold again with ▾) or opens as its own
  view (›). Motor records show their value and controls on their row.
- **Moves** (only with writes on): ▼/▲ with a step, tap the value to move to a
  number, a select for enums, ■ to stop.
- **⋯ menu** on every row: pin a live strip chart, tweak, jog, details, copy
  the name.
- **Pinned** (bottom): strip charts that keep running while you go elsewhere,
  with time window and running average. **+ Add** finds any item of the
  initialized components by name, for example a detector to watch while
  tweaking a motor.
- **Tweak**: big step buttons, step ×2/÷2, go to, back to start, Stop. On a
  laptop the arrow keys step.
- **Jog** (motor records): the motor moves while a button is held, at the jog
  speed, which ÷2/×2/Set change. It stops on release, at a limit, with Stop,
  and also when the button stops being held for any other reason: the web app
  re-confirms a held button four times a second, and the session stops the
  jog when that has not come for one second (phone locked, network gone,
  page closed), and when the session ends.
- **Live view** (network PTZ cameras such as `cam_west`): the camera's video
  in the page, in low (default, for a phone on a tunnel), medium or full
  quality. With writes on, switch **Control** on to tap where the view should
  be centred or drag a box to zoom into it (the mouse wheel zooms on a
  laptop), or use Zoom +/−, Wide and Home. *Settings* shows pan, tilt, zoom,
  iris and focus as rows. All viewers share one connection to the camera; the
  stream pauses while the page is in the background.
- **Queues** (start page): the session's task queues ({doc}`queues`): the
  running task with the step its scan is at, the waiting tasks, the last
  finished ones with their errors, and each task's output, live. With writes
  on: pause/resume the queue, pause/resume the running scan at its next step,
  stop the running task, move a waiting task up or remove it, drop all
  waiting, stop everything. Nothing can be added to a queue from here.
- **Panel**: a device's schematic, refreshed every 3 s. Tapping a symbol
  opens that device; the panel's commands are not run from the web app.

## What a write goes through

Every move from the browser is an ordinary `set_target_value` in the session,
on a worker thread: the write-access gate, a running queue's claims
({doc}`queues`) and the device's own limits apply as they do at the prompt. A
jog writes the motor record's jog fields directly, so the web app checks the
access gate and queue claims itself before it starts one. Writes are off
unless asked for, and the session logs each one with the client that sent it.
