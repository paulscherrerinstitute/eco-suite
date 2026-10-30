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
| `hello` | `{client_version, client_id}` | `{protocol, label, mode: "session", writes: bool, flush_interval}` |
| `ping` | `{}` | `{}` — the client sends it every 15 s; no reply in 8 s means the connection is dead |
| `names` | `{}` | `{initialized:[…], lazy:[…], failed:{name: reason}}` |
| `init` | `{name}` | *planned* — today `describe` of a lazy name initializes it |
| `describe` | `{name}` | `{name, title, kind, svg, writable, doc?, rows:[{name, label, kind: "assembly"|"adjustable"|"detector"|"trigger"|"other", writable, unit?, enum?, group?, hidden?, svg?, button?}]}` — rows are the display collection, then the rest of the status collection as `hidden` (same split as `display_widget`/`display_qt`); initializes a lazy top-level name |
| `subscribe` | `{names:[…]}` | added to this client's `LiveReader`; values arrive as `ev: "values"` |
| `unsubscribe` | `{names:[…]}` | |
| `set` | `{name, value}` | (mode B, writes on) `start_write`; state via `ev: "write"` (`moving`/`done`/`error`) |
| `step` | `{name, sign, step}` | relative move (▲/▼) |
| `stop` | `{name}` | `stop_move`; always allowed when writes are on |
| `step_size` | `{name}` | `{step}` — the start value of the ▲/▼ step (the grids' `initial_step`) |
| `trigger` | `{name}` | (writes on) runs an `AdjustableTrigger` |
| — | — | SVG panels are plain HTTP: `GET /api/svg?name=…` returns the SVG, header `X-Eco-Prefix` is what click markers are relative to; the client re-fetches every 3 s |
| `queues` *planned* | `{}` | queue list with tasks (mode B) |
| `queue_call` *planned* | `{queue, action: "pause"|"resume"|"abort_current"|…}` | allow-listed subset of `TaskQueue` methods |
| `queue_follow` *planned* | `{queue, from_task?}` | backlog + live `ev: "queue_output"` |
| `tweak` *planned* | `{session_id, action, args}` | `TweakController` methods by allow-list |

## Events

```json
{"ev": "values", "now": 1761390000.2, "data": {"mono.energy": {"text": "7.1", "num": 7.1, "t": 1761390000.12, "state": "live"|"subscribed"|"polled"}}}
{"ev": "values", "now": ..., "data": {"mono.gauge": {"error": "no value (not connected?)", "state": "error", "t": ...}}}
{"ev": "write",  "data": {"name": "...", "state": "busy"|"moving"|"idle"|"error", "message": "..."}}
{"ev": "queue_output", ...}   // planned
```

Value fields: `text` always; `num` for a finite real, `bool`, `enum` (labels), `cls`
`"green"`/`"red"` for open/closed. Values are coalesced per item and flushed at most every 100 ms per client (latest
value wins), so a 100 Hz channel cannot flood a phone over a tunnel. `t` is the
read time on the gateway; the client shows age from it (clock skew: the gateway
sends its own `now` in every `values` batch).

Value encoding: numbers/str/bool as JSON; numpy scalars converted; arrays only
on explicit array subscriptions (later, binary frames); anything else
`format_value()` from `live_values` as a string.

## Not in v0

Binary frames (camera images, arrays), user input routing, multi-user presence,
saved layouts on the server.
