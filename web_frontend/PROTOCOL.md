# Gateway ↔ client protocol (v0)

Implemented by `eco/web/client_session.py` (server) and `eco/web/static/app.js`
(client). Rows marked *planned* are not implemented yet.

JSON over one websocket per client (`/ws?token=…`), plus a few plain HTTP GETs for
static files and SVGs. Versioned from day one so client and gateway can be updated
independently (a phone may keep a cached old client).

## Envelope

```json
{"v": 0, "id": 17, "op": "subscribe", "args": {...}}      // client → gateway
{"v": 0, "re": 17, "ok": true, "data": {...}}             // reply to id 17
{"v": 0, "ev": "values", "data": {...}}                   // unsolicited event
```

`id` is chosen by the client; every request gets exactly one reply (`ok:false`
with `error: {type, message}` on failure — e.g. `MoveBlocked`, `AccessDenied`,
`NotInitialized`, `WritesDisabled`).

## Item addressing

Items are addressed by **dotted eco name** (`alias.get_full_name()`, e.g.
`"mono.energy"`). Every row a `describe` returned is remembered by the session
under that name, and `subscribe`/`set`/... only accept such names. `describe`
also accepts a top-level component or a public attribute path below one
(`"vacuum.valve1"`, nothing starting with `_`), so deep links survive a reload.
The client never sends expressions.

## Operations

| op | args | reply / effect |
|---|---|---|
| `hello` | `{client_version, client_id}` | `{protocol, label, mode: "session", writes: bool, flush_interval, jog_repeat}` |
| `ping` | `{}` | `{}` — the client sends it every 15 s; no reply in 8 s means the connection is dead |
| `names` | `{}` | `{initialized:[…], lazy:[…], failed:{name: reason}}` |
| `init` | `{name}` | *planned* — today `describe` of a lazy name initializes it |
| `describe` | `{name}` | `{name, title, kind, svg, writable, doc?, rows:[{name, label, kind: "assembly"|"adjustable"|"detector"|"trigger"|"other", writable, unit?, enum?, hidden?, svg?, button?, value?, jog?, own?}]}` — rows are the display collection, then the rest of the status collection as `hidden` (same split as `display_widget`/`display_qt`); initializes a lazy top-level name. An assembly that is itself an item (a motor record) has `value: "adjustable"|"detector"` on its row (shown with value and controls, like the grids), and its own view starts with an `own` row for it. `jog: {speed: <name of its jog speed item>}` marks a hardware jog |
| `subscribe` | `{names:[…]}` | added to this client's `LiveReader`; values arrive as `ev: "values"` |
| `unsubscribe` | `{names:[…]}` | |
| `set` | `{name, value}` | (mode B, writes on) `start_write`; state via `ev: "write"` (`moving`/`done`/`error`) |
| `step` | `{name, sign, step}` | relative move (▲/▼) |
| `value` | `{name}` | for an item whose value is a dict/list (`struct` in its values): `{data, read_only: [path…], type: "dict"|"list"|"tuple", writable}` — the whole value as JSON; a leaf that is not plain (array, object) comes as its repr and its path is listed in `read_only` |
| `set_value` | `{name, data}` | (writes on) writes the edited dict/list at once; the types of the current value are restored where the structure matches (an int into a float leaf, a tuple, numpy scalars; `read_only` leaves sent back as their repr stay unchanged — `eco.widgets.structured_values.coerce_like`); state via `ev: "write"` |
| `stop` | `{name}` | `stop_move`; always allowed when writes are on |
| `step_size` | `{name}` | `{step}` — the start value of the ▲/▼ step (the grids' `initial_step`) |
| `trigger` | `{name}` | (writes on) runs an `AdjustableTrigger` |
| `jog` | `{name, direction: 1|-1, start: bool}` | (writes on, items with `jog`) starts the hardware jog (`MotorRecord.jog`, JOGF/JOGR), after the access gate and queue claims; the client repeats `start: true` every `jog_repeat` s while the button is held, and the gateway stops the jog when that has not come for 1 s (`JOG_DEADMAN`), when the client goes away, on `stop`, or on `start: false`. Run in request order (one thread per client). State via `ev: "write"` (`moving` "jogging +", `idle`) |
| `ptz` | `{name, action: "center"|"rect"|"zoom_in"|"zoom_out"|"wide"|"home", x?, y?, x1?, y1?, w?, h?}` | (writes on, items whose `describe` has `camera.ptz`) `click_center`/`zoom_to_rectangle`/`area_zoom`/`set_zoom(1)`/`home`, coordinates in the displayed image of size w×h; access gate checked |
| — | — | a camera's live stream is plain HTTP: `GET /api/mjpeg?name=…&quality=low|medium|full` returns `multipart/x-mixed-replace` JPEGs for an `<img>`; `describe` of such an item has `camera: {stream: "mjpeg", ptz, rotate, qualities}` |
| `jog_speed` | `{name, value}` | sets the jog speed of `name` (its `jog_speed` item); allowed whenever writing `name` is |
| — | — | SVG panels are plain HTTP: `GET /api/svg?name=…` returns the SVG, header `X-Eco-Prefix` is what click markers are relative to; the client re-fetches every 3 s |
| `queues` | `{}` | `{queues: [{name, paused, running: task?, pending: [task], history: [task] (newest first, 10)}], now}`; task = `{id, source, description, state, started, finished, error?, scan?: {done, total, paused, stopping}}` — the client polls it every second |
| `queue_action` | `{queue, action, task?}` | (writes on, all items) `pause`, `resume`, `clear`, `stop`, `pause_running`, `resume_running`, `stop_running`, and for a waiting task `remove`, `move_up`, `move_top` (`eco.web.queues.ACTIONS`); nothing adds or repeats a task |
| `queue_output` | `{queue, task, since}` | `{text, offset, clipped, task}` — what the task printed after character `since` (at most the newest 20 000 characters) |
| `tweak` *planned* | `{session_id, action, args}` | `TweakController` methods by allow-list |

## Events

```json
{"ev": "values", "now": 1761390000.2, "data": {"mono.energy": {"text": "7.1", "num": 7.1, "t": 1761390000.12, "state": "live"|"subscribed"|"polled"}}}
{"ev": "values", "now": ..., "data": {"mono.gauge": {"error": "no value (not connected?)", "state": "error", "t": ...}}}
{"ev": "write",  "data": {"name": "...", "state": "busy"|"moving"|"idle"|"error", "message": "..."}}
{"ev": "queue_output", ...}   // planned
```

Value fields: `text` always; `num` for a finite real, `bool`, `enum` (labels), `cls`
`"green"`/`"red"` for open/closed, `struct` (`"dict"`/`"list"`/`"tuple"`) for a dict/list value (its whole value through `value`). Values are coalesced per item and flushed at most every 100 ms per client (latest
value wins), so a 100 Hz channel cannot flood a phone over a tunnel. `t` is the
read time on the gateway; the client shows age from it (clock skew: the gateway
sends its own `now` in every `values` batch).

Value encoding: numbers/str/bool as JSON; numpy scalars converted; arrays only
on explicit array subscriptions (later, binary frames); anything else
`format_value()` from `live_values` as a string.

## Not in v0

Binary frames (camera images, arrays), user input routing, multi-user presence,
saved layouts on the server.
