# Architecture

## 1. The one structural decision

A PWA is a thin client; it can't hold an eco namespace or Channel Access
connections. So a PWA front-end is really two things:

- a **gateway**: a Python process that holds eco objects and serves a small JSON
  protocol over HTTP + websocket;
- a **static client** (HTML/JS/CSS + manifest + service worker) that only talks
  that protocol.

This is the opposite of Voila, where every viewer gets its own kernel that
imports eco. Here there is one eco process and any number of browsers.

## 2. Where the gateway runs — two hosting modes, same code

| Mode | Host process | Sees | Writes | Typical use |
|---|---|---|---|---|
| **A. shared** | the status server (`eco/status_server`, already holds an initialized namespace, always on) | namespace, values, monitors, recordings | **none** (read-only by policy) | "what is the beamline doing" from anywhere |
| **B. session** | a user's own eco session (`eco.start_web()`, like `start_webapp()` today) | the above **plus** task queues, running scan, `user_ns`, memories | yes, through the normal `set_target_value` → `Changer` path | operating the experiment from a tablet/phone |

The gateway code only depends on a small "backend" interface (resolve a name to
an object, list names with lazy/initialized/failed state, access queues if any).
The status server provides one implementation, a live session another. Starting
with mode B is simpler (no shared-service deployment); mode A comes almost free
afterwards.

Mode A must stay read-only: the status server is a shared service running under
`gac-bernina` for the DAQ, and must not become an unaudited write path.

## 3. Keeping three UI families in step (the parallelization question)

eco already uses the right pattern in a few places, it just isn't a rule yet:

```
eco objects (Assembly / Adjustable / Detector / TaskQueue / Memory ...)
        │
  toolkit-free logic ("view models")          ← already exists for some widgets:
        │                                         live_values.py, component_selector.py,
        │                                         tweak_panel.py, Memory (elements/memory.py),
        │                                         task_queue.py, log_timeline_common.py
        ├── Qt adapter         (display_qt, tweak_panel_qt, component_selector_qt, ...)
        ├── ipywidgets adapter (display_widget, tweak_panel_ipy, component_selector_widget, ...)
        └── web adapter        (gateway: view model → JSON messages)  →  PWA client
```

Rules to adopt (only for new or touched code, no big-bang refactor):

1. **Logic first.** A feature's state and actions live in a module with no GUI
   imports (like `live_values.py` / `tweak_panel.py` today). Each front-end is an
   adapter.
2. **View-model state is plain data** (dicts/lists/str/float/bool, numpy only at
   the edge). If it is JSON-serializable, the web adapter is nearly mechanical.
   A cheap test enforces this per view model.
3. **Actions are named methods with plain arguments** (`move(i, sign)`,
   `set_step(i, step)`, `go(i, value)` — exactly `TweakController`'s shape).
   The gateway exposes them as protocol `call`s by an explicit allow-list, never
   by arbitrary attribute access.
4. **Parity is tracked, not assumed** — PARITY.md has a column per front-end. A
   feature may stay Qt-only on purpose; that must be a recorded decision.
5. Qt and ipywidgets keep running in-process exactly as now. Nothing in this plan
   requires changing them; the logic extraction is opportunistic.

What is *already* toolkit-free and directly reusable by the gateway:

- `eco/widgets/live_values.py` — `LiveReader` (subscribe-or-poll, "a subscription is
  not trusted until an event arrives"), `start_write`, `stop_move`, step helpers.
  This is the property grid's whole engine; the gateway uses it unchanged and
  pushes results down the websocket instead of into a Qt queue.
- `eco/widgets/tweak_panel.py` — `TweakController` incl. `handle_key`.
- `eco/widgets/component_selector.py` — tree/filter logic for pickers.
- `eco/elements/memory.py` — Memory recall/save (memory_widget only renders it).
- `eco/utilities/task_queue.py` — queues, tasks, output listeners.
- `eco/widgets/log_timeline_common.py` — `TimelineEntry`.
- SVG panels — `_widget_svg_panel()` already returns an SVG file; browsers render
  SVG natively, so this is the cheapest high-value port of all.

## 4. Client technology

Constraint: maintained by Python people, deployed on beamline hosts without a JS
build pipeline.

Recommendation: **no build step to start with.** Plain ES modules + a small
vendored view library (Preact + htm, or Lit; one file each, checked in), uPlot for
strip charts, `<canvas>` for camera images, native SVG for panels. Add Vite/TypeScript
only if the client grows past the point where that hurts (say, >5k lines).

- Manifest + service worker: cache the app shell only. **Never cache values**;
  stale beamline values shown as current would be actively dangerous. Offline
  view = "disconnected" banner, nothing else.
- Every value carries its age; the client greys a value whose last update is
  older than its expected refresh.

## 5. Server technology

Prefer what the beamline environment already ships:

- **tornado** — certainly present (jupyter_server/voila depend on it), native
  websockets, its own event loop thread. First choice for mode B.
- The status server is Flask (sync, no websockets). For mode A either add
  `flask-sock` (if available in bpy312) or run the tornado gateway as a second
  listener inside the same process. Decide at the time; check availability first
  (SPIKE.md prerequisites).

Threading: the gateway runs its own event loop on a background thread. It must
**never** touch hardware on that loop — reads go through `LiveReader`'s pool,
writes through `start_write` threads, exactly as the Qt grid does. Results are
handed to the loop with `loop.add_callback`. It must not require the main thread,
so `eco.utilities.mainthread` is irrelevant to it (an advantage over Qt).

Interaction with existing session machinery:

- writes go through `set_target_value` → `Changer`, so the access gate
  (`eco/elements/access.py`) and task-queue claims (`MoveBlocked`) apply unchanged.
  The gateway thread is "another thread", so a move on an adjustable claimed by a
  running queue task is blocked — which is the correct behaviour for a phone.
- questions from `user_input` (`input()` from a queue task) are a later feature;
  initially they stay at the terminal.

## 6. Remote access (SSH only)

- The gateway binds **127.0.0.1 only**. Reaching it requires an SSH login to that
  host — SSH is the outer authentication.
- On a shared console host any local account can reach 127.0.0.1, so the gateway
  **also** requires a per-session token (printed on start, carried in the URL
  once, then stored by the client). Mode B additionally restricts writes.
- `http://localhost:<port>` counts as a *secure context* in browsers, so a PWA
  (service worker, install) works through a plain `ssh -L` without any TLS setup.

Per device:

| Device | How | Caveat |
|---|---|---|
| Laptop | `ssh -L 8097:localhost:8097 <host>` (+ jump host as usual) | none |
| Android | Termux (`ssh -L ...`) or ConnectBot port forward, then browser at `localhost:8097` | keep the SSH app's notification / battery exemption so it survives in background |
| iPhone/iPad | Blink Shell or Termius port forward | iOS suspends background apps; the tunnel dies minutes after switching away. Usable for "look now", not for "leave it open". |

Origin = `http://localhost:<port>`, so **always use the same local port**
everywhere: browser storage, the installed PWA and its token are per origin.

Push notifications (Web Push) need the *sender* to reach Google/Apple/Mozilla
push services outbound; whether a beamline host can (via proxy) is unknown, and a
localhost origin may not get push on every browser. Treat notifications as out of
scope for the SSH setup; revisit only if a PSI-hosted endpoint ever exists.

## 7. Safety model for writes (mode B)

- Off by default: `eco.start_web(writes=False)`.
- With writes on: per-adjustable allow-list (default: whatever the access layer
  permits **and** is in an explicit `web_writable` set), confirmation sheet for
  absolute moves on touch devices, Stop always available and never confirmed.
- Every web write is logged with client id (same audit file as `access.py`).
- No arbitrary code execution from the client, ever — notably SVG panel
  `onclick="// eco: <command>"` commands are **not** executed by the gateway; in
  the web client a click on an SVG element opens that device's view instead.

## 8. Code layout (implemented, see README.md for the map)

```
eco/web/                 # Python, inside the package
  __init__.py            # start_web(...) — lazily imported from eco/__init__.py
  gateway.py             # tornado app, auth, websocket sessions
  backends.py            # SessionBackend (mode B), StatusServerBackend (mode A)
  viewmodels/            # thin adapters over existing toolkit-free logic
  static/                # the PWA (or keep in web_frontend/client/ and ship as package data)
tests/test_web_*.py      # protocol + view-model JSON tests, no browser needed
```

`eco_cli.py` gets a `--ui web` only once the spike succeeded. Packaging: a new
optional extra `eco[web]` (tornado is already pulled by `lab`/`voila`).
