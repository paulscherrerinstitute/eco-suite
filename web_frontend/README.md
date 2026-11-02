# eco web front-end (PWA)

A fourth eco front-end, a **Progressive Web App** (an installable web page,
touch-first, works on a phone), next to and independent of the other three:

| Front-end | Process model | Strength |
|---|---|---|
| `shell` (IPython) | the session | everything, for experts |
| `desktop` (Qt) | in-process with the session | full-featured workbench on a console |
| `lab` / `voila` (ipywidgets) | one kernel per viewer | notebooks, interactive exploration |
| **`web` (PWA)** | one gateway process, many thin clients | phones/tablets/laptops, remote via SSH tunnel |

## Status (2026-11-02): a front-end of eco, started with `eco web`

The code is in `eco/web/` and `tests/test_web_*.py`; it is started with
`eco web -s <scope>` (`eco_cli.py`), `eco.start_web()` or `python -m eco.web`,
installed with the `eco-suite[web]` extra (tornado). Nothing else in eco
imports it, so it stays independent of work on the other front-ends.
User docs: `docs/web_app.md`.

What works (tested against the simulated namespace, and in headless Chrome at
phone size):

- namespace list (initialized / lazy / failed with reason), filter, tap a lazy
  component to initialize it
- device view: an assembly's display rows (hidden ones behind a toggle), live
  values via `eco.widgets.live_values.LiveReader` (monitored where possible,
  polled otherwise — the same engine as the Qt and ipywidgets grids),
  status dot, value age and staleness, units, open/closed colouring
- writes, **only when the gateway was started with them**: ▲/▼ with step,
  absolute move (dialog), enum select, Stop, triggers (confirmed) — through
  `set_target_value`, so the access gate and queue claims apply
- motor records (an Assembly that is also an Adjustable): value and controls
  on the row, as in the grids; their own view starts with that value
- **jog** for hardware jogs (motor records): hold to move, jog speed ÷2/×2/set,
  arrow keys on a laptop; the gateway stops a jog 1 s after the held button
  stops re-confirming it, when the client goes away and when the session exits;
  checks the access gate and queue claims (the jog fields bypass the Changer)
- SVG panels (`_widget_svg_panel` / `_show_svg`), live-refreshed, sanitized; a
  tap on a device opens its view (the `// eco: <cmd>` commands are **not run**);
  built into a file of the web app's own and shared by clients for 2 s
- nested devices: sub-assemblies fold/unfold in place (▸/▾) or open as their own
  view (›); items of a recursive sub-assembly under a small group header
- per-row ⋯ menu: pin live strip chart, tweak, jog, details, copy name
- pinned items in a dock at the bottom, live while navigating: strip chart
  (30 s .. 1 h) with optional running average, survive a reload
- picker: find any readable item of the initialized components by name
- tweak view: big step buttons, ×2/÷2, go to, back to start, stop, arrow keys
- token auth + HttpOnly cookie, binds 127.0.0.1, ping/reconnect after network
  drops, clean shutdown, service worker (app shell only), light/dark

Not yet: queues/scan progress, archiver history, memories, cameras, alarms,
status-server (shared, read-only) mode. See [PARITY.md](PARITY.md) for the order.

Points to keep in mind (found while wiring it in):

- every client has its own `LiveReader`: N phones on a polled item read it N
  times per interval (monitored items cost nothing extra). A shared reader in
  the gateway is the fix if that ever matters (cameras/array detectors first).
- writes run as the session's account: the access gate sees the session's
  user, not who holds the phone; the log names the client id only.
- an `input()` question from a queue task is only answerable in the terminal.
- `eco web` binds 8097 by default; a second account's session on the same
  console needs `--port`.

## Try it

Anywhere, without a beamline (simulated motors and motor records, valves, an
SVG panel, a nested device, a slow and a broken component):

```bash
python -m eco.web --demo            # read-only
python -m eco.web --demo --writes   # with writes
```

With a beamline namespace:

```bash
eco web -s bernina [--writes]       # console session + web app (scripts/eco-dev from a checkout)
eco web -s bernina --no-console     # only the web app
```

or in a running session:

```python
gw = eco.start_web()               # read-only; prints the URL with the token
gw = eco.start_web(writes=True)    # writes from the browser allowed
eco.stop_web()
```

Open the printed `http://localhost:8097/?token=…`. From another machine, forward
the port first: `ssh -L 8097:localhost:8097 <host>` (plus your usual jump host).
Always use the same local port: browser storage and the installed app are per
`localhost:<port>`.

Tests (per file, like the rest of the suite):

```bash
PYTHONPATH=$PWD python -m pytest tests/test_web_viewmodels.py -q
PYTHONPATH=$PWD python -m pytest tests/test_web_client_session.py -q
PYTHONPATH=$PWD python -m pytest tests/test_web_gateway.py -q     # needs tornado
PYTHONPATH=$PWD python -m pytest tests/test_web_cli.py -q
```

## Documents

1. [ARCHITECTURE.md](ARCHITECTURE.md) — gateway, hosting modes, how the UI families
   stay in step, security, SSH.
2. [PARITY.md](PARITY.md) — every Qt feature, its PWA cost, what stays Qt-only.
3. [PROTOCOL.md](PROTOCOL.md) — the wire protocol (v0, implemented).
4. [SPIKE.md](SPIKE.md) — the beamline test: prerequisites, procedure, go/no-go.

## Code map

```
eco/web/__init__.py        start_web() / stop_web() / connection_help()
eco/web/__main__.py        python -m eco.web -s <scope> | --demo
eco/web/gateway.py         tornado app on its own thread: token, static, /api/svg, /ws
eco/web/client_session.py  one client: the protocol, LiveReader, writes (no transport)
eco/web/viewmodels.py      eco objects/values -> plain JSON (no tornado)
eco/web/backends.py        SessionBackend (a live namespace); status-server backend later
eco/web/demo.py            simulated namespace
eco/web/static/            index.html, app.js, strip.js, style.css, sw.js, manifest, icon (no build step)
```
