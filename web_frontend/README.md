# eco web front-end (PWA)

A fourth eco front-end, a **Progressive Web App** (an installable web page,
touch-first, works on a phone), next to and independent of the other three:

| Front-end | Process model | Strength |
|---|---|---|
| `shell` (IPython) | the session | everything, for experts |
| `desktop` (Qt) | in-process with the session | full-featured workbench on a console |
| `lab` / `voila` (ipywidgets) | one kernel per viewer | notebooks, interactive exploration |
| **`web` (PWA)** | one gateway process, many thin clients | phones/tablets/laptops, remote via SSH tunnel |

## Status (2026-10-30): spike implemented, not wired into eco

The code is in `eco/web/` and `tests/test_web_*.py`. **Nothing else in eco
imports it** — not `eco/__init__.py`, not `eco_cli.py`, not `pyproject.toml` —
so it can't affect other work and can be developed alongside it. It's
switched on only by explicitly calling it (below). Wiring it in (an
`eco.start_web()` wrapper, `--ui web`, an `eco[web]` extra, package-data for
`eco/web/static/`) is a separate small step once the test on the beamline
went well — see [SPIKE.md](SPIKE.md).

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
- SVG panels (`_widget_svg_panel` / `_show_svg`), live-refreshed, sanitized; a
  tap on a device opens its view (the `// eco: <cmd>` commands are **not run**)
- token auth + HttpOnly cookie, binds 127.0.0.1, ping/reconnect after network
  drops, clean shutdown, service worker (app shell only), light/dark

Not yet: queues/scan progress, cameras, plots, alarms, dashboards, status-server
(shared, read-only) mode, the monospace-label convention of the static beamline
SVG. See [PARITY.md](PARITY.md) for the order.

## Try it

Anywhere, without a beamline (simulated motors, valves, an SVG panel, a slow and
a broken component):

```bash
python -m eco.web --demo            # read-only
python -m eco.web --demo --writes   # with writes
```

In a running eco session (uses the session's `namespace`):

```python
from eco.web import start_web, stop_web
gw = start_web()               # read-only; prints the URL with the token
gw = start_web(writes=True)    # writes from the browser allowed
stop_web()
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
eco/web/__main__.py        python -m eco.web --demo
eco/web/gateway.py         tornado app on its own thread: token, static, /api/svg, /ws
eco/web/client_session.py  one client: the protocol, LiveReader, writes (no transport)
eco/web/viewmodels.py      eco objects/values -> plain JSON (no tornado)
eco/web/backends.py        SessionBackend (a live namespace); status-server backend later
eco/web/demo.py            simulated namespace
eco/web/static/            index.html, app.js, style.css, sw.js, manifest, icon (no build step)
```
