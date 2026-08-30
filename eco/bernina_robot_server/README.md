# `eco.bernina_robot_server` — the Bernina robot server

Runs the Stäubli TX200 detector arm as a service. It owns the **single** TCP
connection the VAL3 controller offers, polls it, and re-exports the arm to
clients over HTTP + server-sent events and over EPICS channel access.

Replaces the PSI-pshell deployment in
`/sf/bernina/config/src/python/bernina_robot` (a Java/Jython application with
Swing GUI plugins). The hardware driver underneath is
[`eco.robots`](../robots/README.md).

---

## 1. Running it

```bash
# no hardware at all — full stack against a simulated controller
python -m eco.bernina_robot_server --simulated --no-epics --port 8099

# production
python -m eco.bernina_robot_server \
    --config /sf/bernina/config/src/python/bernina_robot/robot_server.json
```

Under systemd: see `systemd/eco-bernina-robot-server.service`. Config
reference: `example_config/robot_server.json`, fields documented in
`config.py`.

> **Run one instance only.** The VAL3 dispatcher accepts a single client, and
> two servers would also both publish `SARES20-ROB:` PVs.

Useful flags: `--simulated`, `--no-epics`, `--port`, `--robot-host`,
`--polling-interval`, `--log-level`, and `--override-remote-safety` (see §6).

`kill -USR1 <pid>` dumps every thread's stack — the fastest way to see which
thread is stuck on a controller round trip.

---

## 2. Architecture

```
   eco session (bernina.rob)        caqtdm panel        archiver
            │  HTTP + SSE                │ CA               │ CA
            ▼                            ▼                  ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  server.py   Flask: pshell-compatible REST  +  /api/*  + SSE │
   ├──────────────────────────────────────────────────────────────┤
   │  app.py      RobotServerApp — owns everything below          │
   │    ├── commands.py   foreground slot + background pool       │
   │    ├── events.py     SSE fan-out, one bounded queue/client   │
   │    └── epics_pvs.py  pcaspy CA server (SARES20-ROB:)         │
   ├──────────────────────────────────────────────────────────────┤
   │  eco.robots  BerninaRobot ── poller thread ── Val3Link(lock) │
   └──────────────────────────────┬───────────────────────────────┘
                                  │ one TCP socket, line protocol
                                  ▼
                   Stäubli VAL3 controller  129.129.243.106:1234
```

Four thread groups, all daemons:

| Thread | Owner | What it does |
|---|---|---|
| poller | `BerninaRobot` | `update()` every `polling_interval`, then broadcasts |
| command workers | `CommandExecutor` | one foreground + a small background pool |
| CA server | pcaspy | serves the `SARES20-ROB:` PVs |
| HTTP | werkzeug | one per request; SSE requests hold theirs for the connection |

The only shared mutable state is the robot, and every controller access
funnels through the one lock in `Val3Link`.

### Two poll rates

`update_fast` (positions, 13 numbers) runs at the full rate; `update_env`
(modes, power, frames, tools, task status) is an order of magnitude more
expensive and runs at `env_polling_interval`. Splitting them is what makes a
5 Hz position feed affordable.

---

## 3. Client interface

### 3.1 The contract eco depends on

`eco.endstations.bernina_robots.StaeubliTx200` talks to this through
`eco.pshell.client.PShellClient`, written against the pshell REST interface.
Every endpoint that client can reach is implemented with the **same paths,
field names and semantics**, so the eco side needs no change.

| Endpoint | Returns | Notes |
|---|---|---|
| `GET /state` | `"Ready"` \| `"Busy"` \| `"Fault"` \| `"Initializing"` \| `"Closing"` | JSON string. **`Busy` means a foreground command holds the server** — clients refuse to issue moves then. |
| `GET /evalAsync/<statement>` | integer command id | Returns immediately. |
| `GET /result/<id>` | `{"id", "status", "return", "exception", …}` | `status` ∈ `running` / `completed` / `failed` / `aborted`. |
| `GET /eval/<statement>` | text | Blocking. Also carries the control words `:abort` and `:restart`. |
| `GET /eval-json/<statement>` | JSON | |
| `GET /events` | SSE stream | §3.3 |
| `GET /version`, `/config`, `/logs`, `/devices` | | `/devices` returns pshell's `[name, type, state, value, age]` rows. |
| `GET /abort`, `/abort/<id>` | bool | |
| `GET /stop`, `/resume`, `/pause`, `/update`, `/reinit` | bool | |
| `PUT /set-var` | bool | `{"name":…, "value":…}` |
| `GET /history/<index>` | text | 0 is most recent. |

### 3.2 Foreground vs background — the `&` convention

A statement ending in `&` runs in the **background pool** and leaves the
server `Ready`. Anything else takes the **foreground slot**, puts the server
in `Busy`, and any second foreground command is refused with an explanatory
`exception` rather than being queued.

This is the gate the whole client design rests on:

```python
rob.get_eval_result("robot.doUpdate()")            # background → "…&"  → never blocks anyone
rob.move(gamma=20)                                 # foreground → Busy  → exclusive
rob.record_motion(gamma=70)                        # foreground → Busy  → exclusive, minutes long
```

so one operator recording a trajectory does not silently interleave with
another's scan, while everybody's value reads keep working.

### 3.3 Server-sent events

`GET /events`, optionally filtered with `?events=polling,state`.

| Event | Payload | Meaning |
|---|---|---|
| `polling` | `{"pos": {…}, "mode", "status", "frame", "tool", "powered", "speed", …}` | Every poll. This is eco's `self._cache`. |
| `state` | `"Ready"` / `"Busy"` / … | **Server** state, on change. |
| `robot_state` | `{"state", "previous"}` | The **arm's** state. Distinct on purpose. |
| `motion` | text | Combined moves, coordinate-system switches, safety refusals. |
| `reset_motion` | text | Queued motions were discarded, with the reason. |
| `stop` | text | Motion stopped. |
| `shell` | text | Driver messages, e.g. `"Update error: …"` — eco treats this as a disconnect signal. |

Each subscriber gets its own **bounded** queue (256 frames). A client that
stops reading has its oldest frames dropped and a counter bumped; it can never
block the poller. Dropping is correct here — these are periodic snapshots, so
the next one supersedes what was lost. On connect, the last value of each
subscribed event is replayed so a fresh client renders immediately instead of
waiting a poll period. Inspect with `GET /api/subscribers`.

### 3.4 The eval namespace

`/eval` evaluates against a namespace holding:

* `robot` — the `BerninaRobot`
* every pseudo-motor under its **bare axis name**: `gamma`, `delta`, `r`, `x`,
  `y`, `z`, `rx`, `ry`, `rz`, `j1`…`j6`, `z_lin`
  (`PshellMotor` sends `gamma.moveAsync(20.0)` and `gamma.stop()`)
* the motion functions ported from `script/motion/*.py`: `move_home`,
  `move_park`, `tweak_x`, `tweak_y`, `enable_motion`, `wait_end_of_move`

> ⚠️ **`/eval` is remote code execution by design**, exactly as pshell's was.
> The builtins are restricted (no `__import__`, no `open`), which raises the
> bar but is **not a security boundary** — anything reachable from `robot`
> moves a two-tonne arm. Bind to a trusted network, as the pshell server was.

### 3.5 Structured API — prefer this for anything new

No eval, plain JSON:

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | Everything: server, robot, controller, motors, EPICS, subscribers |
| `GET /api/health` | `200` healthy / `503` not — for systemd and monitoring |
| `GET /api/positions` | Every axis, all coordinate systems |
| `GET /api/poll` | Exactly the `polling` SSE payload |
| `GET /api/config` | Which broadcast fields are read-only vs settable |
| `POST /api/move` | `{"gamma": 20, "delta": 3, "wait": true}` |
| `POST /api/stop` | Abort + stop + reset queue + resume |
| `GET /api/subscribers`, `/api/commands` | Introspection |

---

## 4. EPICS

The axes are published as fake motor records under `SARES20-ROB:` — the point
was never a real motor record, but to look enough like one that the PSI
archiver logs them (`.RTYP`, `.EGU`, `.ADEL`, `.MDEL`, `.PREC`) and the
existing caqtdm panel drives them (`$(P):$(M).VAL` / `.RBV`).

Names are **upper-case**: `SARES20-ROB:J1.RBV`, `SARES20-ROB:GAMMA.VAL`,
`SARES20-ROB:Z_LIN.RBV`, … Writing `.VAL` commands a motion; every other field
is a readback or a constant. `.EGU` now carries real units (`mm`/`deg`), which
the pshell version left blank.

pcaspy is optional — without it the server logs and carries on. eco clients do
not use these PVs; they read positions from the SSE `polling` payload.

---

## 5. Failure behaviour

**A controller that is unreachable at startup is not fatal.** The server comes
up in `Fault`, the poller keeps retrying, and a background recovery loop
re-runs `setup()` once the link returns — re-creating the motors, re-applying
the frame and tool, rebuilding the eval namespace, without dropping a single
client connection.

This is a deliberate change. pshell ran the device setup from a startup
script, so a controller that was down left the whole context in `Fault` with
the `robot` device simply **absent from the device pool** — which is exactly
the state the live pshell server is in right now: `GET /state` returns
`"Fault"` and `GET /devices` lists only the three cameras. That failure mode
looks like a missing feature rather than a down link, and its recovery path
was `get_context().restart()`, i.e. restarting the whole application.

A timed-out read closes the socket rather than reusing it, because the unread
reply would otherwise be handed to the *next* call.

---

## 6. Safety

* **The remote-motion whitelist** (§"safety" in the driver README) is the main
  interlock: in `remote` mode only motions bracketed by a hand-recorded
  trajectory execute. Refusals are broadcast as a `motion` event telling the
  operator to run `rob.record_motion(...)` first.
* **`--override-remote-safety` / `robot.set_override_remote_safety(True)`**
  disables it. Logged loudly, and visible in the status payload.
* **Manual-mode jogging** — the moment the operator switches the pendant to a
  jog mode, every queued motion is discarded and a `reset_motion` event is
  emitted.
* **Working-mode changes** switch the controller user profile automatically
  (`remote` → `remote`, anything else → `default`).
* `POST /api/stop` and `:abort` are the two panic paths; abort latency is
  bounded by one controller timeout, measured at ~30 ms.

---

## 7. Testing

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=/sf/bernina/config/personal/lemke_h/eco \
  python -m pytest tests/test_robots.py tests/test_bernina_robot_server.py -q
```

125 tests, no hardware needed — `eco.robots.simulation.SimulatedController`
answers the real line protocol. The framing assertions are transcribed from
captured live traffic, so they pin the wire format rather than the port's
self-consistency.

---

## 8. What was *not* ported

* **`plugins/RobotBernina.java` and `RobotPanel.java`** (1 900 lines) are pure
  Swing GUI — a camera view, jog buttons and spinners that emit the same
  `evalCmd("robot.…")` strings this server accepts. No server logic lives in
  them. The equivalent UI is the existing caqtdm panel plus eco's own widgets.
* **The pshell `www/` browser client** — pshell's stock UI, not robot-specific.
* **The three Axis MJPEG cameras** (`cam_n`/`cam_s`/`cam_w`) were pshell
  `MjpegSource` devices used by the Swing panel. eco already has camera
  support (`eco.devices_general.cameras`); they are not the robot server's job.
* **pshell's scan/data-acquisition machinery** — eco has its own.
