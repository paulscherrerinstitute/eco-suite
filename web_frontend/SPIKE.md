# Spike: the first test

**Status 2026-10-30:** step 1 of the procedure (implementation) is done, see
`eco/web/` and README.md; the scope below is covered, including the stretch
(writes, behind `writes=True`). Next: the prerequisites, then steps 2-5 on a
beamline console.

Purpose: answer "is this worth building?" with the smallest thing that exercises
every risky part — gateway threading, live values over a websocket, SSH tunnel to
a phone, PWA install, touch UX — without touching existing eco behaviour.

## Scope (in)

- `eco.start_web(port=8097, writes=False)` in a live session (mode B), lazily
  imported, nothing else changed.
- Namespace list (initialized / lazy / failed), tap to initialize.
- One assembly view: property grid rows with live values and value age, via
  `live_values.LiveReader`.
- One SVG panel (beamline synoptic or prepump) with live colouring.
- Manifest + service worker (app shell only), install on phone.
- Token auth, bind to 127.0.0.1.

## Scope (out)

Writes, queues, scans, cameras, plots, notifications, status-server mode A.
(Optional stretch, only if time: writes for **one** harmless adjustable,
e.g. a soft/memory adjustable, to test the confirm/Stop UX.)

## Prerequisites — check before starting (each ~minutes)

- [ ] `tornado` importable in bpy312 (expected yes; via jupyter_server) — the gateway's only extra dependency. Also note
      whether `flask_sock`/`starlette`/`uvicorn` are there (for mode A later).
      `/sf/bernina/applications/python/.pixi/envs/bpy312/bin/python -c "import tornado; print(tornado.version)"`
- [ ] SSH port forwarding allowed on the target console host
      (`sshd -T | grep -i allowtcpforwarding` or simply try `ssh -L 8097:localhost:8097 <host>`
      from a laptop, through the usual jump host).
- [ ] The same from a phone: Android Termux/ConnectBot, iOS Blink/Termius — with
      the PSI auth method in use (key + jump host / MFA). **This is the most likely
      blocker**; check it first.
- [ ] Port 8097 unused on the host (status server uses 8091, docs site 8095).
- [ ] Ask (informally) whether IT has a stance on long-lived SSH forwards from
      mobile devices. SSH itself is sanctioned; this is about expectations, not
      permission for a new service.

## Procedure

1. ~~Implement (`eco/web/` + static client)~~ — done; try it first with `python -m eco.web --demo`.
2. Laptop: tunnel, open `http://localhost:8097/?token=…`, browse, open assembly,
   open SVG. Measure: time to first value, update latency vs. Qt grid side by side.
3. Phone (Android first, then iOS): same via tunnel app; install PWA
   ("Add to Home screen"); use it for a day of a real shift in read-only mode.
4. Kill the tunnel / lock the phone / switch networks — client must show
   "disconnected", then recover and resubscribe without reload.
5. Load: 5 clients × 200 subscribed items; check session responsiveness
   (prompt latency, scan step time) is unchanged — the gateway must not compete
   with the session for the CA lock in a noticeable way (pyepics serialises CA
   calls behind one global lock; see `eco/epics_utils/ca_tuning.py` and
   `eco/status_server/DESIGN.md` §15 for the measured `get_status()` timings).

## Go / no-go

Go if all of:

- Values on the phone within 2 s of opening a view, updates < 0.5 s behind Qt.
- Reconnect works without user action after a network switch.
- No measurable effect on scan-step duration with 5 clients connected.
- At least one person who was on shift says they'd use it.

No-go / rethink if the phone tunnel is impractical (auth, iOS backgrounding) —
then the PWA is a laptop/tablet tool only, which changes priorities (Tier 2 scan
launcher before mobile polish), or the question of a PSI-hosted endpoint has to
be raised properly.

## Wiring it in, once the test went well (small, touches shared files)

- `eco/__init__.py`: a lazy `start_web()` wrapper like `start_webapp()`.
- `eco_cli.py`: `--ui web` (start a session, then `start_web()`).
- `pyproject.toml`: `eco[web] = ["tornado"]`; `eco/web/static/*` under
  `[tool.setuptools.package-data]` (needed for `pip install`, not for checkouts).
- `docs/`: a short page on starting it, and the gateway's threading and security rules.
