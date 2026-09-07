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

`/sf/bernina/bin/eco-status-server` is a symlink to
[`scripts/eco-status-server`](../../scripts/eco-status-server) in the
**shared `gac-bernina` checkout** (`/sf/bernina/code/gac-bernina/eco`, not a
personal one) - editing that checkout takes effect immediately, nothing to
redeploy. Not `eco-dev`-prefixed: unlike `eco-dev` (which deliberately always
runs whichever checkout it happens to be symlinked into, personal or not),
there is exactly one place this is meant to run from, so there is nothing to
disambiguate against - see §11. Without
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

### GUI

```bash
eco-status-server gui                   # small Qt window: status, reinit, query stats
```

Polls `/health` and `/stats` every couple of seconds. Shows state, init
progress, a bold-red banner the moment any *required* component is missing
from `initialized_names` (`failed_required` - see §8), buttons to reinitialize
(`failed`/`full`/`restart`) with a progress bar and ETA while it runs, and a
table of the last `/status/snapshot` and `/status/capture` calls this server
has served - duration, entry count, and any error. Needs a desktop/X session;
`--url` picks a server other than the site default.

### As a systemd user service

```bash
eco-status-server-install-user-service  # run this from a shell where CA works
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
from eco.status_server.client import StatusServerClient
c = StatusServerClient("http://saresb-cons-04:8091")

# read it - the same dict as namespace.get_status(base=None)
st = c.get_status()
st["status"]["bernina.att.transmission"]

# read it AND have the server write it into a run's aux directory
c.snapshot(pgroup="p19641", run_number=331, save=True, key="status_run_start")
c.snapshot(pgroup="p19641", run_number=331, save=True, key="status_run_end")
# -> .../run0331/aux/status.json, both blocks merged into the one file
```

`save=True` needs `pgroup` and `run_number`; `key` is the block name inside
`status.json` and defaults to `status_run_start`. The call returns the status
dict plus `saved_to`. About 20 s per call on bernina (~16 500 channels) — it
is the same `get_status()` fan-out, so budget for it in a scan the way the
local call was budgeted for.

Same thing from a shell, if you just want to look:

```bash
curl -s http://saresb-cons-04:8091/health | python -m json.tool
curl -s -X POST http://saresb-cons-04:8091/status/snapshot \
     -H 'Content-Type: application/json' \
     -d '{"save": true, "pgroup": "p19641", "run_number": 331}'
```

### Reachability and access

The server binds `0.0.0.0`, so any host that can route to it can use it —
verified from `saresb-cons-01/02/05` against `saresb-cons-04`, a few
milliseconds each. There is **no authentication**: whoever can reach port
8091 can also `POST /admin/reinit` and `/admin/restart`. That is fine inside
the beamline network and is the reason not to expose the port beyond it.

The client is a plain `requests` wrapper, so it works from any session that
can `import eco` — a scan script, a notebook, another beamline's console.

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
locally, and the two status callbacks hand the whole job — snapshot, write
`status.json`, upload it to the run — to the server and return immediately.
Measured on bernina: **0.03 s** on the client, against ~22 s per callback when
it waited (the server still spends ~21 s snapshotting, 0.6 s writing and ~9 s
uploading, just not on the scan's clock). The file, its location and its
contents are unchanged either way. Set `status_server_async=False` to go back
to waiting and getting the status dict returned.

### When the server is not usable

Before each scan the client checks `/health` and decides once (the same
decision is reused for that scan's end callback, so a run can't have its start
status from the server and its end status from somewhere else):

| situation | what happens |
| --- | --- |
| no server configured | local namespace, as before |
| unreachable or still initializing | message, falls back to the local namespace |
| ready, namespace < `status_server_max_age` (12 h) | used |
| ready but older than that | see below |

A server that has been up for days is the case worth being careful about: a
snapshot re-reads every EPICS channel, but `AdjustableMemory`/`DetectorMemory`
values are process-local to the server and an `AdjustableFS` setting someone
changed in their own session never reaches it. So past 12 h the client asks,
with a **20 s timeout**:

```
status server: its namespace was built 20.3 h ago, older than the 12.0 h limit.
Restart it now and wait (a few minutes), or use this session's namespace? [R/l] (20 s -> restart):
```

Answer `l` for the local namespace; anything else, or letting it time out,
restarts the server and waits — the option that leaves the next run, and every
other session's, fast. `status_server_stale_action` picks a fixed answer
instead (`"restart"`, `"local"`, `"use"`), and `status_server_max_age=None`
switches the check off.

`status_server_strict=True` turns every fallback into a hard error instead,
which is what you want when testing the server path itself.

### Aliases

`copy_aliases_to_scan` — the callback that writes each run's
`aux/aliases.json` (short name → PV/channel, used to map recorded status/data
back to human names) — is server-aware the same way the status callbacks are,
and reuses the same per-scan decision. With the server in use it asks it to
compute the alias list from its own, already-initialized namespace
(`namespace.alias.get_all()`, a pure in-memory tree walk — no CA traffic, so
it is fast even though it fires as a `/aliases/capture` background job for
consistency with the status path) and write/upload the file, instead of
calling `self.namespace.alias.get_all()` against *this* session's own
namespace. That local call used to run unconditionally, including in
status-server mode — against a namespace status-server mode deliberately
never initializes, so `aliases.json` was silently missing everything the
local session had not happened to touch. File location, name and content are
unchanged either way.

To fetch the list directly instead of writing it to a run:

```python
c.aliases()                       # [{"alias": ..., "channel": ..., "channeltype": ...}, ...]
c.aliases(channeltypes=["CA"])    # only EPICS channels
```

### Writing status for one run by hand

```python
daq.write_status(pgroup="p19641", run_number=331)              # status_run_start
daq.write_status(run_number=331, key="status_run_end")         # merged into the same file
```

Defaults to this Daq's pgroup and the broker's current run number, decides
about the server the same way the callbacks do (`use_server=True`/`False`
overrides), and unlike the callbacks it waits for the file to land before
returning. `upload=False` writes it without handing it to the broker.

### The run table

`Run_Table_DataFrame._get_adjustable_values` looks names up as
`"bernina." + name` in a **flat** `{full_name: value}` mapping. `Daq` used to
hand it the whole `get_status()` result, so nothing ever matched and it
quietly read every adjustable over Channel Access instead — a second full CA
fan-out at every scan start, while apparently being handed the values. It now
gets `...["status_run_start"]["status"]`, and `_status_values()` accepts
either shape so no caller can reintroduce it.

With the server, those values arrive ~20 s after the scan started, so the row
is appended from a background thread once the capture job reports them
(`capture(keep_status=True)` → `wait_write_job(include_status=True)`; the
server hands them over once and then drops them, so a finished job does not
sit on a few MB for the life of the process). The values never travel via the
written file, so NFS visibility does not come into it. If the capture fails
the row is still appended, filled the old way — a run without a run-table row
is worse than one filled slowly.

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
| `broker_address_aux` | sf_daq_broker's slow broker, used by `/status/capture` to attach the file to the run itself |

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
| `POST /status/capture` | snapshot **+** write **+** upload to the run, all in the background; answers immediately with a job id. `keep_status` keeps the values for one `GET /status/job/<id>?include_status=1` |
| `GET /aliases` | this namespace's current alias list, `namespace.alias.get_all()` verbatim — no CA traffic, so this answers fast |
| `POST /aliases/capture` | compute **+** write **+** upload `aliases.json` to the run, in the background — the alias equivalent of `/status/capture`, same job id space |
| `GET /status/job/<id>` | state of an async write (shared by `/status/capture` and `/aliases/capture`) |
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
- **Which account runs the server matters for the run tree.** Writes go
  through `eco.utilities.datafiles`, so every directory level comes out
  `2775` and every file `0664` — but a level some *other* account created at
  `0755` before that (or outside eco) still blocks it, and the failure is a
  bare `PermissionError` on the run directory. `datafiles.repair_tree()`,
  run as the account that owns the offending levels, fixes it.
- Files the server writes appear on other hosts only after the NFS attribute
  cache expires (seconds). Harmless for `append_aux`, which hands the broker
  a path rather than reading the file locally, but it is why a freshly
  written `status.json` can `stat` as missing from the console you are
  sitting at.

## 11. Why not `eco-dev-status-server`, and could this be an installed command

Not `eco-dev`-prefixed, deliberately: `eco-dev` earns that prefix because
there really are two things it could mean - the checkout you happen to be
in, or whatever `eco` is `pip`/`pixi`-installed in the environment - and it
always picks the former. `eco-status-server` has no second meaning to
disambiguate from: as of 2026-09-06 the code lives only in the shared
`gac-bernina` checkout (`/sf/bernina/code/gac-bernina/eco`, kept in sync via
its own git remote), `/sf/bernina/bin/eco-status-server` is a symlink
straight into it, and that is the one and only place this runs from. (It
briefly *was* `eco-dev-status-server`, pointed at a personal checkout, while
the code was still being developed there - renamed back once it landed in
the shared one.)

**The two scripts are not the same thing.** `eco-status-server` is the
day-to-day tool: start/stop/status/wait/stats/gui/logs — one running server,
managed. `eco-status-server-install-user-service` is a one-shot generator,
run once (or again with `--force`) to *produce* a `systemd --user` unit file
and environment file for that server — after which `systemctl` manages it,
not this script again. Confusingly similar names for two different jobs, kept
separate on purpose: the daily driver stays a small, dependency-free shell
script, while unit-file generation (capturing `EPICS_CA_*`, writing to
`~/.config/systemd/user/`) is templating logic that does not belong mixed
into it.

**Could either become a real `pyproject.toml` [project.scripts] entry**, so
`pip install eco` gives you an `eco-status-server` command directly (the way
`eco = "eco_cli:main"` already does for the main package)? Only after a
rewrite, not as-is:

- `[project.scripts]` entries are Python callables (`module:function`), not
  arbitrary executables — pip generates a tiny wrapper that imports the
  module and calls the function. `eco-status-server` is a genuine shell
  script (`pgrep`, `systemctl`, `nohup`, signal handling for `stop`) with no
  Python equivalent to point at.
- The install script is inherently host-filesystem-shaped (writes into
  `~/.config/systemd/user/`, reads `$BASH_SOURCE` to find its sibling) — an
  installed console-script would need that logic ported to Python
  (`importlib.resources`/`shutil` instead of `dirname "$(readlink -f ...)"`),
  which is a real, if mechanical, rewrite.
- The one piece that *is* already plain Python with an argparse `main()` is
  the GUI (`eco.status_server.gui:main`) — that one could become
  `[project.scripts]` today with a single `pyproject.toml` line, independent
  of the other two.

Worth doing once the status-server code actually lands in a checkout meant to
be `pip install`ed rather than run from a specific path — not before, since
today every meaningful default (which checkout, which config) *is* "wherever
this script lives," which a `[project.scripts]` wrapper would have no way to
express.
