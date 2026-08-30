# eco status server — manual

A long-running process that holds one initialized eco namespace (e.g.
`bernina`) and answers `namespace.get_status()` over HTTP, so an interactive
session or a DAQ run does not have to initialize a namespace of its own to
record run status. It can also monitor every monitorable channel for the
duration of a run and write the result as an `escape` file.

`DESIGN.md` next to this file is the proposal and the record of what was
measured; this file is how to run and use it.

Two modes exist. Everything here is the **namespace mode**
(`namespace_store.py`, `namespace_server.py`), which hosts the real namespace
object and therefore matches `get_status()` exactly. The lightweight
**registry mode** (`channel_registry.py`, `monitor_store.py`, `server.py`)
serves a bare alias/PV list without importing eco; it cannot represent
`AdjustableMemory`/`AdjustableFS`-backed values and is not what the daq
client talks to.

---

## 1. Start it

```bash
eco-status-server start -b     # detached, logs to ~/.eco/status_server_<host>.log
eco-status-server wait         # block until it reports ready, printing progress
eco-status-server status       # state, init progress, memory, running recordings
eco-status-server stop
```

`/sf/bernina/bin/eco-status-server` is installed from
[`bin/eco-status-server`](bin/eco-status-server) in this directory. Without
`-b` it runs in the foreground, which is what the systemd unit uses — the
service and the interactive command run exactly the same thing. It reads
`/sf/bernina/config/eco_status_server/env` for the checkout, config and
interpreter to use, and each of those is overridable per invocation:

```bash
ECO_STATUS_SERVER_CHECKOUT=~/my-eco eco-status-server start -b
```

The port is bound immediately; `namespace.init_all()` then runs on a
background thread and takes a few minutes. Until it finishes, `/health`
reports `state="initializing"` with live progress and every value route
answers 503 — so start it and poll, don't wait on the socket.

Without the wrapper it is just:

```bash
python -m eco.status_server --mode namespace --config /path/to/bernina_namespace.json
```

### As a systemd user service

```bash
eco-status-server-install-user-service      # run this from a shell where CA works
loginctl enable-linger $USER
systemctl --user daemon-reload
systemctl --user enable --now eco-status-server
```

User-level rather than system-level on purpose: it needs no root, and the
service then runs as the invoking account — which matters twice here, since
the server writes into the pgroup's `res/` tree and several bernina
components authenticate over SSH during `__init__`.

**Run the installer from a shell where Channel Access works.** A systemd user
service inherits almost nothing, and it captures the current `EPICS_*`
variables into its `EnvironmentFile` for exactly that reason: without
`EPICS_CA_ADDR_LIST` the server starts happily and then talks to the local
broadcast domain instead of the beamline gateway. Measured on
`saresb-cons-04`, that is the difference between 77 of 87 components
initializing and 49 of 87.

`kill -USR1 <mainpid>` dumps every thread's stack into the log (or the
journal) — the way to find out which component a stuck initialization is
sitting in. A stale system-level unit template is kept in
[`systemd/`](systemd/) for reference.

## 2. Check it

```bash
curl -s http://saresb-cons-04:8091/health | python -m json.tool
```

```python
from eco.status_server.client import StatusServerClient
c = StatusServerClient("http://saresb-cons-04:8091")

c.health()                    # state, progress, failures, cpu/rss/threads
c.wait_ready(progress=True)   # block until usable, printing progress
c.names()                     # target / initialized / failed / still-initializing
c.failures()                  # why a component is missing
```

`state` is `importing` → `initializing` → `ready`, plus `reinitializing` and
`failed`. `generation` increments on every successful (re)build — that is
what lets you tell "my reinit finished" from "my reinit hasn't started yet",
which polling `ready` alone cannot.

## 3. Get status from it

```python
c.get_status()      # the same dict as namespace.get_status(base=None)

# ... and have the server write it into a run's aux directory:
c.snapshot(pgroup="p19641", run_number=331, save=True, key="status_run_start")
```

## 4. Use it from the DAQ

```python
daq = Daq(..., status_server="http://saresb-cons-04:8091")
```

For bernina this is already wired to an environment variable, so a session
opts in without editing anything:

```bash
ECO_STATUS_SERVER=http://saresb-cons-04:8091 scripts/eco-dev -s bernina
```

`init_namespace` then waits for the server instead of running `init_all()`
locally, and the two status callbacks ask the server for a snapshot *and* to
write `status.json` into the run's aux directory; this side still uploads it
with `append_aux`, so the file, its location and its contents are unchanged
either way. If the server is unreachable or still initializing, the callbacks
warn and fall back to the local mechanism — `status_server_strict=True` turns
those fallbacks into hard errors instead, which is what you want when testing
the server path itself.

## 5. Record all monitorable channels during a run

```python
c.start_recording("run0332", mode="sample", sample_interval=0.1)
...                                   # the run happens
c.stop_recording("run0332", pgroup="p19641", run_number=332)
# -> .../run0332/aux/monitors.esc.h5, one escape ArrayTimestamps per channel
```

`c.recording(rec_id)` reports live counters (updates/s, points stored, drops)
while it runs; `c.recordings()` lists them. A reinit stops any running
recording rather than leaving callbacks on objects it is about to tear down.

Modes — they differ only in what the per-update callback does, which is the
only part of the cost this process controls:

| mode | per update | memory | use when |
| --- | --- | --- | --- |
| `all` (default) | append value+timestamp | unbounded (capped by `max_points_per_channel`, default 100 000) | you want every transition |
| `throttle`, `min_interval=s` | append only if `s` since this channel's last point | bounded by duration/`s` | you want raw timestamps but less of them |
| `sample`, `sample_interval=s` | overwrite a "latest" slot; a sampler thread stores each channel at most once per tick, and only if it actually changed | at most duration/`s` per channel | you want the fast channels decimated and the slow ones untouched |

None of the three changes how often the IOC *sends*, so none of them reduces
the per-update cost of crossing into Python. The only option here that does
is `subscription_mask="log"`, which subscribes to the IOC's archive deadband
(`DBE_LOG`) instead of `DBE_VALUE`. DESIGN.md §15 has the measurements.

`max_value_elements=1024` drops oversized values, which is how you keep
waveform channels out — and that, not the sample rate, is what governs the
file size: in a 3-minute bernina recording two 8000-sample digitizer
waveforms were 204 MB of a 239 MB file, against 8 MB for all 7 677 scalar
channels put together.

Sensible default for a run:

```python
c.start_recording(rec_id, mode="throttle", min_interval=0.1,
                  max_value_elements=1024)
```

Measured against `mode="all"` over the same 180 s and the same 7 781
channels: **21.6 MB instead of 239 MB**, **+38 MB of server memory instead of
+292 MB**, 174 000 points instead of 1 000 000 — and still 7 713 of 7 715
channels covered. Nothing slower than 10 Hz loses a single point, which is
99.4 % of the namespace.

**Do not** reach for pyepics's `PV(monitor_delta=...)` here: it first tries to
`caput` the IOC's `.MDEL` field, i.e. it changes the record for every client
at the facility, and only falls back to a local filter if that write is
refused.

### Does throttling make monitoring cheaper?

Only the storage, not the work. Measured over 7 785 channels at ~5 100
updates/s: every mode cost the same 0.53–0.56 of one core, including
`sample`, whose callback does almost nothing, and including a `DBE_LOG`
subscription, which delivered exactly the same update rate. The cost of an
update is the fixed crossing from libca's receive thread into Python, and a
client-side filter has to run inside that crossing to do its filtering. Only
reducing what the IOC sends — a real record deadband, or not monitoring the
channel — reduces it. Full numbers in DESIGN.md §15.

Monitoring does not measurably slow the server's day job: snapshot latency
was the same idle and during a full-rate recording (a snapshot is dominated
by CA timeouts, not CPU), and the thread count does not change.

## 6. Reinitialize it

```python
c.reinit()                                  # default: full process restart, waits
c.reinit(mode="failed")                     # cheap: retry only what failed
c.reinit(mode="full", reload_modules=True)  # rebuild everything, reloading device modules
```

`restart` is the default because it is the only unconditionally correct one:
the in-process modes cannot unload code that live objects still reference,
and `Namespace.reinitialize(reload_modules=True)` deliberately refuses to
reload the namespace's own assembly module (reloading `bernina.py` would
re-run the whole beamline setup script). `mode="reimport"` is the in-process
approximation — it drops every `eco.*` module and re-imports — but it cannot
reclaim the old namespace's CA channels or device threads, and leaves two
copies of every eco class in the process.

## 7. Configuration

| key | meaning |
| --- | --- |
| `module_name`, `attr_name` | where to import the namespace from, e.g. `eco.bernina.bernina` / `namespace` |
| `names` | explicit list of namespace names to initialize and serve |
| `exclude_names` | names to drop from whatever `names`/`init_required_only` selected |
| `init_required_only` | if `names` is unset: initialize only `namespace.required_names()` |
| `init_workers` | workers for the first `init_all()` pass (default 8) |
| `init_cycles` | cap on `init_all()`'s internal retry loop (default 4) |
| `init_retry_passes`, `retry_workers` | extra whole passes for components that were never really attempted, and their worker count (default 2 passes, 1 worker — serial, because worker collisions are what made them stragglers) |
| `read_workers` | `max_workers` for the `get_status()` fan-out per snapshot (default 20) |
| `use_monitors` | keep a CA monitor cache instead of calling `get_status()` per request (default false) |
| `max_value_elements` | (per recording, not config) drop values with more elements than this — see §5 |
| `host`, `port` | listen address |
| `data_root_pattern` | where `save=true` writes into |

`names`/`exclude_names` exist because `namespace.required_names()` for
bernina is an `AdjustableFS` backed by a file **shared with every interactive
session** — the server must never write it just to narrow its own scope.
Components that block on an interactive credential prompt in `__init__`
(bernina's `scilog`), or that only make sense in a live session (`daq`,
`scans`, `elog`), belong in `exclude_names`: a headless server has no stdin
to answer them with.

## 8. HTTP API

| route | purpose |
| --- | --- |
| `GET /health` | state, readiness, generation, init progress, failures, cpu/rss/threads |
| `GET /names` | target / all / initialized / failed / lazy / currently-initializing names |
| `GET /failures` | per-name exception for everything that failed to initialize |
| `POST /status/snapshot` | a `get_status(base=None)` result; `save`+`pgroup`+`run_number`+`key` also write `status.json`, `write_async` returns a job id |
| `GET /status/job/<id>` | state of an async write |
| `GET /recording`, `GET /recording/<id>` | list / live counters |
| `POST /recording/start`, `POST /recording/stop` | start; stop and (by default) write `monitors.esc.h5` |
| `POST /admin/reinit` | `mode` = `failed` / `names` / `full` / `init` / `reimport` |
| `POST /admin/restart` | re-exec the process |

## 9. What it costs and what it saves

Measured on bernina (server on `saresb-cons-04`, client on `saresb-cons-05`,
87 target components, ~16 600 status detectors), running the same `ascan`
over `dummy_adjustable` both ways:

| | with the status server | today's local behaviour |
| --- | --- | --- |
| whole `ascan` call | **75 s** | **982 s** |
| status entries written | 16 209 | 16 185 |
| server `init_all()` to ready | 143 s, once, at server start | — |
| one snapshot | 11–18 s, ~3 MB of JSON | the same call, run locally |
| `status.json` per run | 9.2 MB, both blocks | 9.2 MB, both blocks |

The snapshot itself is *not* faster — it is the same `get_status()` call, run
elsewhere. The whole win is that a session never pays `init_all()`. (Part of
that gap is also parallelism: `Daq.init_namespace` runs `init_all()` with the
default `max_workers=1`, the server with 8.) If per-snapshot latency ever
becomes the bottleneck instead, that is what `use_monitors` is for.

Recording numbers and the downthrottling analysis are in DESIGN.md §15.

## 10. Caveats

- `AdjustableMemory` / `DetectorMemory` values are **process-local**. A
  second copy of the namespace in another process holds its own instances, so
  anything a scientist changes through such an object in their own session is
  invisible to the server, and vice versa. Values backed by EPICS,
  `AdjustableFS` files or any other shared system are unaffected.
- A snapshot is refused (503) while a reinit runs: a reinit rebuilds the very
  `status_collection` a snapshot walks.
- The server writes into the pgroup's `res/` tree, so its account needs
  pgroup write access — and several bernina components authenticate to
  auxiliary machines over SSH during `__init__`, so its SSH credentials have
  to work non-interactively.
