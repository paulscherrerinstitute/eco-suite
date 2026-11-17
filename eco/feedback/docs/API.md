# API guide

This is a narrative companion to the live, interactive reference at
`/docs` (Swagger UI) and `/redoc` on a running server — those are always
authoritative for exact request/response schemas; this page explains what
the routes are *for*.

The server is a single `FeedbackManager` exposed over HTTP by FastAPI.
Every route calls one method of `eco.feedback.service.FeedbackService`
(`manager.service` in a session), which has the same names and return
values as the routes' `FeedbackClient` counterparts — so everything below
also works in-process, without HTTP. Every
route below is relative to wherever you ran `serve(mgr, host=..., port=...)`.

The example commands below are copy-pasteable as-is against
the matrix demo (`python -m eco.feedback demo matrix`, serves on
`:8001`, requires the API key `demo-key` — see "Auth" below) — its loops are
`loop_1`/`loop_2`, actuators `cx1`/`cx2`, detectors `bpm1`/`bpm2`.
`python -m eco.feedback demo busy` (`:8000`, no key required) has a different set —
`pointing_x`/`pointing_y` plus `steering_x1`/`cx2`/`cx3`/`bpm1`-`bpm3`; see
the "Demos" section of `docs/GUI.md` for the full layout.

## Auth

Opt-in: `serve(mgr, api_key="...")`. When set, every mutating route (PATCH/
POST/DELETE) requires a matching `X-API-Key` header — 401 without one or with
the wrong one. GET routes are **always** open, key or no key, so read-only
monitoring/dashboards never need credentials. Omitting `api_key` entirely
(the default) keeps the server fully open, as before.

```bash
curl -s -X PATCH http://127.0.0.1:8001/loops/loop_1 \
     -H 'content-type: application/json' -H 'X-API-Key: demo-key' \
     -d '{"enabled": true}'
```

## Loops

A *loop* is one detector driving one adjustable through a PID
(`FeedbackManager.add(name, detector, adjustable, ...)`).

- `GET /loops` — status of every loop, keyed by name.
- `GET /loops/{name}` — status of one loop: `enabled`, `setpoint`, `rate_hz`,
  `last_value`, `last_output`, `actual_rate_hz`, `kp`, `ki`, `kd`.
- `PATCH /loops/{name}` — update any subset of `setpoint`, `rate_hz`, `kp`,
  `ki`, `kd`, `enabled`, `alarm_after_s`, `auto_disable_on_alarm`. Fields
  left out are left unchanged. Setting `enabled: true` resets the PID's
  integral term (a clean start, not a jump).
- `GET /loops/{name}/history?n=200` — the last `n` `(t, value, output)`
  samples as parallel arrays `{t, value, output}`. `output` entries are
  `null` while the loop is disabled or held by an interlock. Add
  `&archive=true` to read from the persistent SQLite store instead of the
  in-memory window (needs a server started with a store — see
  "Persistent history/logs" below); not limited by the in-memory deque size.

```bash
curl -s http://127.0.0.1:8001/loops/loop_1
curl -s -X PATCH http://127.0.0.1:8001/loops/loop_1 \
     -H 'content-type: application/json' -H 'X-API-Key: demo-key' \
     -d '{"enabled": true, "setpoint": 0.1}'
```

Loop status also reports `held` (true while an interlock is blocking
correction) and `alarmed` (true once output has stayed saturated past
`alarm_after_s`) — see "Interlocks" and "Alarms" below.

## Actuators and detectors

Every adjustable/detector that's part of a loop is automatically registered
under its own `.name` (falling back to `{loop}_adjustable`/`{loop}_detector`
if it doesn't have one). Free-standing ones — not driven by any loop, only
used for response-matrix acquisition — are added explicitly with
`manager.add_actuator(name, obj)` / `manager.add_detector(name, obj)`.

- `GET /actuators` — all known adjustable names.
- `GET /detectors` — all known detector names.

## Response-matrix acquisition

A response matrix records how much each detector moves per unit change of
each actuator (`matrix[i, j]` = d(detector_i)/d(actuator_j)) — the standard
calibration step before any multi-actuator correction. This is a
**measurement**, not a running controller: it steps actuators, records the
result, and reports a matrix. It runs in a background thread on the server,
so starting one returns immediately; poll for progress.

If an actuator being stepped currently has an active `FeedbackLoop`, that
loop is disabled for the duration of *its* step and re-enabled immediately
after, so it doesn't fight the measurement.

- `POST /response-matrix/{name}` — start a measurement named `name`. Body:
  - `adjustable_names`, `detector_names` — lists of names from `/actuators`/`/detectors`.
  - `step` — delta applied to each actuator; a single number or a
    `{name: delta}` dict for per-actuator step sizes. Default `0.1`.
  - `settle_s` — settle time after each write before reading detectors. Default `0.2`.
  - `repeats` — samples averaged per read (reduces noise). Default `3`.
  - `bidirectional` — step `+step` and `-step` and take the central
    difference (more accurate, roughly 2x slower) vs. a single one-sided
    step from baseline. Default `true`.
  - 409 if a measurement with that name is already running.
- `GET /response-matrix` — all measurements (past and in-progress), by name.
- `GET /response-matrix/{name}` — one measurement's status:
  `state` (`pending`/`running`/`done`/`error`), `current_actuator`,
  `step_index`/`total_steps` for a progress bar, and once `done`: `matrix`
  (rows=detectors, columns=actuators) and `condition_number` (large values
  mean the matrix is close to singular — some actuator combination barely
  affects the detectors, or two actuators do nearly the same thing).
- `DELETE /response-matrix/{name}` — discard a stored result.

On completion, the result is also stored under `manager.matrices[name]` —
the registry `POST /matrix-loops/{name}` reads from (see "Closed-loop matrix
correctors" below).

```bash
curl -s -X POST http://127.0.0.1:8001/response-matrix/orbit \
     -H 'content-type: application/json' -H 'X-API-Key: demo-key' \
     -d '{"adjustable_names": ["cx1", "cx2"], "detector_names": ["bpm1", "bpm2"], "step": 0.1}'
curl -s http://127.0.0.1:8001/response-matrix/orbit
```

### Scheduled re-measurement

Response matrices drift as machine conditions change. Instead of purely
manual one-off runs, a measurement can be re-triggered on a fixed interval:

- `POST /response-matrix/{name}/schedule` — body: `adjustable_names`,
  `detector_names`, `interval_s`, plus the same `step`/`settle_s`/`repeats`/
  `bidirectional` fields as above. Re-runs the measurement every
  `interval_s` until stopped. `GET /response-matrix/{name}` gains a
  `schedule: {interval_s, next_run_in_s}` field once one is running — before
  the first run completes, the response is a minimal `{state: "scheduled",
  schedule: {...}}` instead of 404, since the schedule itself is real even
  before it's produced a matrix yet.
- `DELETE /response-matrix/{name}/schedule` — stop it.

```bash
curl -s -X POST http://127.0.0.1:8000/response-matrix/steering/schedule \
     -H 'content-type: application/json' \
     -d '{"adjustable_names": ["cx1","cx2","cx3"], "detector_names": ["bpm1","bpm2","bpm3"], "interval_s": 60}'
```

## Closed-loop matrix correctors

Where response-matrix acquisition only *measures*, a matrix corrector
*continuously* drives every actuator in a named matrix from every detector's
error: each cycle, `gain * pinv(matrix, rcond) @ (reference - measured)`.
Same rate-based semantics as a single loop's PID output.

- `POST /matrix-loops/{name}` — body: `matrix_name` (a name from
  `GET /response-matrix` with `state: done`, or one restored via config
  load), `gain` (default `0.3`), `rcond` (SVD cutoff, default `1e-3`),
  `rate_hz`, `out_min`/`out_max` (scalar or `{actuator: limit}` dict, clamps
  each actuator's correction), `alarm_after_s`, `auto_disable_on_alarm`,
  `interlocks` (list of interlock names). **Created disabled** — enable
  explicitly once you're ready to close the loop.
- `GET /matrix-loops` / `GET /matrix-loops/{name}` — status: `enabled`,
  `reference`, `weights`, `gain`, `rcond`, `last_values`/`last_outputs`,
  `held`, `alarmed`, `condition_number`.
- `PATCH /matrix-loops/{name}` — update `gain`, `rcond`, `enabled`, or merge
  into `reference`/`weights` (per-key merge, not a wholesale replace — send
  only the detectors you want to change).
- `POST /matrix-loops/{name}/save-reference` — capture current detector
  readings as the new reference (the "save golden orbit" action): the
  corrector subsequently targets whatever the detectors read right now.
- `DELETE /matrix-loops/{name}` — stop and remove it.
- `GET /matrix-loops/{name}/history?n=200` — `{t, values, outputs}`, one
  vector per timestamp.

`weights` (default `1.0` per detector) row-scales the matrix and error
before pseudo-inversion — set a detector's weight to `0` to mask it out of
the correction (e.g. a misbehaving BPM) without re-measuring the matrix.

```bash
curl -s -X POST http://127.0.0.1:8001/matrix-loops/steering \
     -H 'content-type: application/json' -H 'X-API-Key: demo-key' \
     -d '{"matrix_name": "orbit", "gain": 0.3, "out_min": -1, "out_max": 1}'
curl -s -X PATCH http://127.0.0.1:8001/matrix-loops/steering \
     -H 'content-type: application/json' -H 'X-API-Key: demo-key' -d '{"enabled": true}'
```

## Interlocks

A named permit condition a loop can subscribe to (`interlocks=["name"]` on
`manager.add`/`add_matrix_loop`). While any subscribed interlock isn't OK,
the loop **holds** — skips applying corrections without disabling — distinct
from a user-initiated disable, and resumes automatically once the interlock
clears. The condition itself is set in the deployment script (duck-typed
like a detector, e.g. a real EPICS "beam permit" PV); the API can only
force/clear an override on top of it.

- `GET /interlocks` — `{name: {ok, forced}}` for every registered interlock.
- `POST /interlocks/{name}/force` — body `{"value": true|false}`. Bypasses
  the live condition with a fixed value — commissioning or emergency
  override.
- `DELETE /interlocks/{name}/force` — clear the override, resume reading the
  live condition.

```bash
curl -s -X POST http://127.0.0.1:8001/interlocks/beam_permit/force \
     -H 'content-type: application/json' -H 'X-API-Key: demo-key' -d '{"value": false}'
```

## Alarms

`GET /alarms` — every loop/matrix-loop currently alarmed, i.e. its output
has stayed saturated past its `alarm_after_s` (default `5.0`; transient
saturation only logs a warning). Derived from loop status each call, not a
separate store. If `auto_disable_on_alarm` was set when the loop was
created, alarming also disables it.

## Health

`GET /health` — `status` (`"ok"` or `"degraded"`), `uptime_s`, and per-loop
`{alive, last_cycle_age_s}` for every loop and matrix loop. Unlike a bare
HTTP response (which only proves the *process* is up), this checks each
loop's last-cycle timestamp against its own rate, so a hung control thread
shows up as `degraded` even though the API keeps answering requests.

## Config persistence

Explicit, operator-triggered save/restore (like a DOOCS save/restore panel —
nothing autosaves). Saves loop settings (setpoint/rate/PID gains/enabled/
alarm config), matrix-loop settings (gain/rcond/reference/weights/enabled/
which matrix), every named response matrix, and any forced interlock
overrides, as YAML.

- `POST /config/save` — body `{"path": "..."}` optional; falls back to the
  server's configured `config_path` if omitted. 400 if neither is set.
- `POST /config/load` — same. Only updates settings on already-registered
  loops/actuators/detectors (created by the deployment script — hardware
  objects aren't serializable); restores response matrices and forced
  interlock overrides too. Pass `config_path=...` / `autoload=True` to
  `serve()` to load automatically at startup.

```bash
curl -s -X POST http://127.0.0.1:8001/config/save \
     -H 'content-type: application/json' -H 'X-API-Key: demo-key' -d '{}'
```

## Persistent history/logs

If the server was started with a `PersistentStore` (SQLite, batched
background writer — see `eco.feedback.PersistentStore`), history and log
entries are archived beyond the in-memory window and survive a restart.
Read them with `archive=true` on the existing routes:

- `GET /loops/{name}/history?archive=true&n=200`
- `GET /logs?archive=true&n=200`

400 if the server wasn't started with a store.

## Logs

`GET /logs?n=200` — the last `n` event-log entries (loop enable/disable/
hold, output saturation/alarm, response-matrix measurement progress), each
`{ts, level, source, message}`, oldest first. Add `&archive=true` for the
persistent store (see above).

## Docs

`GET /docs/api`, `GET /docs/gui` — this guide and the GUI guide, rendered as
HTML (what the GUI panels link to). `GET /` — a landing page linking to all
of the above plus the interactive Swagger/ReDoc references.
