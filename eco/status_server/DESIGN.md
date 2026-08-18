# Namespace status/monitor server - design proposal (draft, for discussion)

Status: **prototype for discussion, not deployed, not wired into any
existing eco code path.** Everything under `eco/status_server/` is new and
additive; nothing else in the repo has been changed. This document explains
the problem, the proposed architecture, and what would still need to be
decided/tested before this replaces anything in production.

## 1. The problem, concretely

`Daq.append_start_status_to_scan` and `Daq.append_status_to_scan_and_store`
in `eco/acquisition/daq_client.py` (lines 649 and 815) each call
`self.namespace.get_status(base=None)` once per scan (start and end). That
in turn is `Assembly.get_status` in `eco/elements/assembly.py:240`, which:

1. Walks `status_collection.get_list()` to find every `Detector` in the
   namespace.
2. Calls `get_current_value()` on each one, fanned out over a
   `ThreadPoolExecutor(max_workers=20)` (assembly.py:285-294).

For the `bernina` namespace this is **1120 aliased channels**
(`eco/aliases/namespaces/bernina.json`, counted directly). Crucially, the
`Detector*` classes behind most of these channels
(`DetectorPvData`, `DetectorPvDataStream`, `DetectorPvEnum`,
`DetectorPvString` in `eco/epics/detector.py`) construct their PV with
`auto_monitor=False`, so `get_current_value()` is a **live CA get**, not a
read from an existing subscription. Net effect: every scan start and every
scan end fires roughly a thousand concurrent CA GET request/response round
trips against the IOCs, from whichever eco session happens to be running
that scan - and it's redone from scratch every time, independently, by
every concurrent session. That's the network traffic problem.

This is also why the code has accumulated workarounds elsewhere for the
same class of issue - see the `pulse_id` handling in `Daq.__init__`
(daq_client.py:74-92) and `Daq.start` (daq_client.py:262-281), which
deliberately keep one dedicated, permanently-monitored PV instead of
issuing a CA get, specifically to avoid adding to CA traffic at scan start.
The status server generalizes that same idea (subscribe once, read from
cache many times) to the whole namespace instead of one PV.

## 2. Goal

Move the "read everything" step out of the client session and into a
long-lived server process that:

- Subscribes to every channel in a namespace **once**, via CA monitor
  (`auto_monitor=True`), and keeps the subscriptions open for the life of
  the process.
- Answers "give me a status snapshot" from that cache - no live CA traffic
  per request, regardless of how many scans/sessions ask, or how often.
- Additionally offers **opt-in recording**: start/stop commands (from a
  client, e.g. at scan start/end) turn on buffering of channel updates,
  and on stop, writes them out as an `ArrayTimestamps`-based h5 file, the
  same format already used for scan monitors elsewhere in this codebase
  (`Daq.end_scan_monitors`, `EpicsDaq.store_arrays` in
  `eco/acquisition/epics_data.py`).
- Is reachable over a small REST API, so any eco session (or anything else)
  can request a snapshot or a recording without importing eco at all.
- Is deployable as a systemd service, one process per namespace/instrument.

## 3. Non-goals / explicitly out of scope for this draft

- Not touching `daq_client.py`, `assembly.py`, or `namespace.get_status()`.
  Section 9 sketches what an eventual integration could look like, as an
  illustration, not a change to apply now.
- Not handling `"BS"`-type channels (bsread / DataBuffer - see
  `HISTORY_BACKENDS`/`LIVE_SOURCES` in `eco/dbase/archiver.py`). Of the
  1120 bernina channels, 1120 are `"CA"` and there's exactly one `"JF"`
  entry; `"BS"` channels aren't monitorable the same way (different
  transport, the `Dispatcher`/`Daqbuf` stack) and are filtered out by
  `channel_registry.load_channel_registry`'s `channeltypes` argument. If
  BS-channel status is needed, that's a separate v2 concern.
- Not solving authentication/access control beyond what's sketched in
  Section 10 - this is an internal-network service today.

## 4. Architecture

```
                     ┌───────────────────────────────────────────┐
                     │        eco-status-server (systemd,         │
                     │        one process per namespace)          │
                     │                                             │
   channel_file ───▶ │  channel_registry.py                       │
   (namespaces/      │    load_channel_registry()                 │
   bernina.json,     │           │                                 │
   or exported       │           ▼                                 │
   via export_       │  monitor_store.py                          │
   namespace_        │    MonitorStore                            │
   channels())        │      - one PV(auto_monitor=True) per       │
                     │        channel, opened once at startup      │
                     │      - LatestValueCache (always on)         │
                     │      - RecordingSession (opt-in, per run)   │
                     │           │                                 │
                     │           ▼                                 │
                     │  server.py (Flask)                         │
                     │    /health  /channels                      │
                     │    /status/snapshot                        │
                     │    /recording/start /stop /list             │
                     └───────────────┬─────────────────────────────┘
                                     │ HTTP (json)
              ┌──────────────────────┼───────────────────────┐
              ▼                      ▼                       ▼
     eco session A            eco session B             ops / monitoring
     (scan start/end)         (another scan)             (curl, dashboards)
```

Everything to the left of `server.py` never touches EPICS more than once
per channel; everything to the right never touches EPICS at all.

## 5. Components (implemented as a runnable prototype in this directory)

- `channel_registry.py` - loads `[{"alias", "channel", "channeltype"}, ...]`
  from a JSON file (`Channel` dataclass). Deliberately **does not import
  `eco`** - reads the namespace's existing alias file directly with
  `json.load`, so the server process has no dependency on hardware
  objects, motor/device classes, or anything else `import eco` would pull
  in. Also provides `export_namespace_channels(namespace, path)`, to be
  run once from a live eco session to (re)generate that file via
  `namespace.alias.get_all()` - a pure attribute walk, no PV access, so
  it's safe to call any time (e.g. after adding a device).

  Caveat checked carefully: `eco.utilities.config.Namespace` does call
  `alias_namespace.update(...)` as components are appended
  (`config.py:1210,1253`), but the commented-out `.store()` calls in
  `eco/bernina/__init__.py` suggest persistence-to-disk isn't automatic
  today. Don't assume `eco/aliases/namespaces/bernina.json` is
  perfectly fresh without checking - regenerate with
  `export_namespace_channels` if in doubt.

- `monitor_store.py` - `MonitorStore` owns exactly one `PV` per channel.
  `LatestValueCache` attaches one permanent callback per PV and keeps only
  the latest `(value, timestamp)` - this is what makes a snapshot request
  free. `RecordingSession` attaches a second, temporary callback on the
  *same* `PV` objects (no duplicate subscriptions) while a recording is
  running, appending every update to a list; `stop()` detaches and returns
  the buffers. This mirrors patterns already in the codebase
  (`eco.epics.utilities_epics.Monitor`, `eco.epics.detector.CallbackEpics`)
  - same idea, just centralized and kept alive permanently instead of
  per-scan.

- `storage.py` - writes `status.json` in the same shape as today's
  `Daq.append_start_status_to_scan` output (so nothing downstream that
  reads it needs to change), and writes recordings as `escape.DataSet` /
  `escape.ArrayTimestamps` h5 files (`*.esc.h5`), matching the format used
  by `EpicsDaq.store_arrays` / `Daq.end_scan_monitors` elsewhere.

- `server.py` - thin Flask app wiring the above into REST routes (below).

- `config.py` - `ServerConfig` dataclass, loaded from a JSON file; also
  encodes the output directory convention
  (`/sf/{instrument}/data/{pgroup}/res/run_data/daq/run{run_number:04d}/aux`)
  already used throughout `daq_client.py`.

- `client.py` - a `StatusServerClient` used only for illustration/testing
  right now (see Section 9) - not imported by anything else in eco yet.

- `__main__.py` - CLI entry point: `python -m eco.status_server --config
  path/to/config.json`.

- `example_config/bernina.json`, `systemd/eco-status-server@.service` -
  example deployment artifacts.

All of the above have been smoke-tested against real infrastructure while
drafting this (not just unit-tested against mocks):
`load_channel_registry` against the actual
`eco/aliases/namespaces/bernina.json` (1120 channels parsed correctly);
`MonitorStore` against a real PV
(`SLAAR11-LTIM01-EVR0:RX-PULSEID`) - connects, caches the latest value with
zero extra `get()`s, and records ~150 live updates over 1.5s via the
monitor callback; the full Flask app end-to-end over HTTP (`/health`,
`/status/snapshot` with `save=true`, `/recording/start` +
`/recording/stop` with `save=true`), producing a real `status.json` and a
real `monitors.esc.h5` that round-trips through
`escape.DataSet.load_from_result_file`.

## 6. Channel coverage and scale/threading feasibility (tested against real infrastructure)

Two follow-up questions came up when discussing this draft, and both were
worth testing rather than guessing at.

### 6.1 Does the namespace alias file cover everything that should be monitored?

No - not by a long way. There's a `pv_list.pkl` in the home directory
(`/photonics/home/gac-bernina/pv_list.pkl`, 7062 unique PV name strings,
generated 2026-06-13, presumably by walking the live namespace's device
objects more deeply than the alias tree does). Comparing it against
`eco/aliases/namespaces/bernina.json` (1121 entries):

- Only **549** channels are in both.
- **572** are in the namespace alias file but not in `pv_list.pkl` (likely
  devices added since that dump was taken - another reason not to treat
  either file as automatically authoritative, see 5's caveat about
  `.store()`).
- **6513** are in `pv_list.pkl` but *not* in the namespace alias file -
  overwhelmingly individual motor-record engineering fields (`.VELO`,
  `.RBV`, `.DIR`, `.ACCL`, `.LLM`, `.HLM`, `.SPMG`, `.MSTA`, `.DESC`, ...:
  251-443 occurrences each) plus timing-system channels (EVR pulse
  configuration, `SIN-TIMAST-TMA` event fields). These are ordinary CA
  channels - nothing about them is unmonitorable - they're just never
  registered as a top-level alias, because `namespace.get_status()` (and
  therefore today's `append_start_status_to_scan`) never reads them
  either. So: the 1121-channel alias file is the *right* set to match
  today's status-snapshot behaviour 1:1, but it's the wrong set if the
  goal is "monitor everything associated with this namespace" - for that,
  the flat PV inventory needs to be merged in.

  `channel_registry.py` now has `load_flat_pv_list()` (reads a `.pkl` list
  or a newline-delimited text file, using the PV name itself as the alias
  since there's no better one) and `merge_channels()` (dedupes by PV name,
  keeping whichever source's alias is passed first) to combine both
  sources. `ServerConfig.supplementary_pv_list` wires an optional second
  source into `server.py`'s `create_app`. Verified: merging
  `bernina.json` (1120 CA channels) with the home-directory `pv_list.pkl`
  (7062) gives 7633 unique channels (1 exact string overlap collapses).

  Caveat: a flat list like this carries no channel-type metadata, so
  `load_flat_pv_list` assumes every entry is a plain CA channel (true for
  everything checked in this particular file). If a future PV inventory
  mixes in bsread/`"BS"`-type names, those would just never show as
  connected in `/health` / `connection_report()` rather than being
  silently wrong - but worth a manual pass over any new source file before
  trusting it wholesale.

### 6.2 Is monitoring all of them (~7600 channels) through one process feasible, or does it need multiprocessing to avoid threads colliding?

Tested directly against the real bernina IOCs, not simulated - all
figures below are measured, not estimated:

| Step | Result |
|---|---|
| Create 7634 `PV(auto_monitor=True)` objects, single thread | **0.5 s**, ~48 MB RSS |
| Connected after a 15-20s settle | **7142/7634 (93.6%)** |
| Attach a per-channel Python callback to all connected PVs | **0.04 s** (see gotcha below) |
| OS threads in the process throughout | **~101-107** (not ~7600 - libca/pyepics uses a small internal dispatch-thread pool, not one thread per channel) |
| RSS after everything is live | **~75-80 MB** |
| 16 threads doing add/remove-callback (3 rounds each, simulating concurrent recording start/stop) *concurrently with* 16 threads hammering the shared snapshot dict, across the full 7142-channel set | **0.61 s, 0 errors** |

Conclusions:

- **No multiprocessing is needed.** A single process, with all PVs created
  from one thread at startup (exactly what `MonitorStore.__init__` already
  does) and later cross-thread access limited to (a) reading a
  lock-protected dict and (b) `add_callback`/`remove_callback` on
  already-connected PV objects, does not collide or deadlock - confirmed
  under real concurrent load, not just structurally argued. Flask's
  `threaded=True` dev server (or a real WSGI server later) can safely call
  into the shared `MonitorStore` from its worker threads.
- **One real gotcha, found by testing, not by inspection: it hung.** A
  first attempt at this test *did* hang past a 2-minute timeout. Cause:
  pyepics's `PV.add_callback()` defaults to `with_ctrlvars=True`, which
  performs a blocking `get_ctrlvars()` CA round trip (units/limits
  metadata, `timeout=5` each) for every already-connected PV the moment a
  callback is attached. Doing that for ~7000 channels at once **recreates
  the exact get-storm this service exists to eliminate** - just moved from
  "every scan" to "every service start/recording start". Fixed in
  `monitor_store.py`: both `LatestValueCache` and `RecordingSession` now
  pass `with_ctrlvars=False` explicitly. This is the one place in the
  prototype where the naive pyepics default would have quietly
  reintroduced the original problem - worth flagging prominently for
  anyone extending this code.
- The one-time `pv.get()` used to seed the cache at startup
  (`LatestValueCache.__init__`) is *not* a problem at this scale: once a
  channel is monitored, `.get()` returns the last value pyepics already
  cached from the subscription rather than issuing a new CA round trip -
  confirmed at 0.04 s for ~7100 sequential calls.
- The ~6.4% that don't connect within 20 s are worth a closer look before
  relying on this list operationally (stale/decommissioned PVs vs. genuinely
  slow IOCs vs. namespace/pv_list.pkl drift per 6.1) - `/health`'s
  `n_connected` and `MonitorStore.latest.connection_report()` surface this
  per-channel for exactly that kind of triage, rather than hiding it.

## 7. REST API (v1 sketch)

All bodies/responses are JSON.

- `GET /health` -> `{status, namespace, n_channels, n_connected, uptime_s}`
- `GET /channels` -> `[{alias, pvname}, ...]`
- `POST /status/snapshot`
  - body: `{"aliases": [..] | null, "save": bool, "pgroup": str, "run_number": int, "key": "status_run_start"}`
  - `aliases: null` means "all channels in the namespace".
  - returns the snapshot dict `{alias: {value, timestamp, timestamp_local, pvname}}`;
    if `save`, also writes/merges into `<data_root>/status.json` under
    `key` (so a start-of-run and end-of-run snapshot can coexist in one
    file, same as today) and returns `saved_to`.
- `POST /recording/start`
  - body: `{"recording_id": str, "aliases": [..] | null}`
  - `recording_id` is caller-chosen (e.g. `f"{pgroup}_{run_number}"` or a
    scan uuid) so a client can start a recording before it knows the final
    run number, and supply pgroup/run_number only at stop time.
- `POST /recording/stop`
  - body: `{"recording_id": str, "save": bool, "pgroup": str, "run_number": int, "filename": "monitors.esc.h5"}`
  - returns metadata and, if `save`, `saved_to`.
- `GET /recording/list` -> currently running/finished recordings this
  process still holds in memory.

## 8. Data formats

- `status.json` - unchanged shape:
  `{"status_run_start": {"status": {alias: value}, "status_channels": {alias: pvname}}}`,
  merged with any existing keys already in the file (e.g. adding
  `status_run_end` later without clobbering `status_run_start`).
- `monitors.esc.h5` - one `escape.ArrayTimestamps` per recorded channel,
  written via `escape.DataSet`, loadable exactly like existing
  `*.esc.h5` outputs (`escape.DataSet.load_from_result_file(...).datasets[alias]`).

## 9. Illustrative integration sketch (not applied)

If/when this is validated, `Daq.append_start_status_to_scan` in
`daq_client.py` could shrink from a local `namespace.get_status()` call plus
manual file writing to something like:

```python
def append_start_status_to_scan(self, scan=None, pgroup=None, append_status_info=True, **kwargs):
    if not append_status_info:
        return
    runno = scan.daq_run_number.get_current_value()
    result = self.status_client.snapshot(
        pgroup=pgroup or self.pgroup, run_number=runno, save=True, key="status_run_start"
    )
    scan.namespace_status = {"status_run_start": result["status"]}
```

with `self.status_client = StatusServerClient("http://bernina-status:8090")`
set up once in `Daq.__init__`. This removes the CA fanout from the client
entirely - the call becomes one small HTTP request. This is **only a
sketch** to make the intended end state concrete for discussion; it is not
part of this change, and should only be adopted after the shadow-mode
comparison in Section 11.

## 10. Open questions / things to decide before productionizing

- **Dependency**: this prototype uses Flask (not currently an eco
  dependency - confirmed not installed in the `default` env; verified
  installable from the PSI PyPI mirror). Alternative: stick to stdlib
  `http.server` to avoid adding a dependency, at the cost of writing more
  plumbing by hand. Recommend Flask + `waitress` (production WSGI server)
  for simplicity, but this is a decision for whoever owns the deployment.
- **File ownership**: writing into `/sf/<instrument>/data/<pgroup>/...`
  requires the service account to `chown`/write with the right group,
  same requirement `Daq.append_aux` already has. Should run under
  whatever service account already has these rights (e.g. same as
  `sf_daq_broker`), not a personal account. `storage.py`'s
  `_ensure_group_writable` mirrors the existing best-effort
  chmod/chown-and-ignore-failure pattern from `daq_client.py`, but this
  needs a real ops decision, not a code-level one.
- **Freshness of `channel_file`**: see the caveat in Section 5 - decide
  whether to read the checked-in namespace JSON directly or require an
  explicit `export_namespace_channels()` step in each namespace's startup
  script, and how re-deploys/reloads happen when devices are added.
  A `POST /admin/reload` endpoint (not yet implemented) that re-reads the
  channel file and adds new subscriptions without a restart would help.
- **Cache warm-up**: `LatestValueCache` seeds itself with a one-time `get()`
  per channel at startup for channels that connect immediately (so a
  snapshot taken 1 second after boot isn't full of nulls), then relies
  purely on monitor callbacks after that. A channel that reconnects later
  (IOC restart, network blip) will populate on its next update, same as
  any CA monitor - `connection_report()` / `/health`'s `n_connected` make
  that state visible for debugging.
- **Multiple recordings, memory growth**: `RecordingSession` buffers live
  in process memory as plain Python lists until `stop()`. Fine for
  single-run durations; would need bounding if a recording could be left
  running indefinitely (e.g. add a max-duration safety stop).
- **Scale beyond one namespace**: one process per namespace/instrument, per
  the systemd template (`eco-status-server@bernina`,
  `eco-status-server@alvra`, ...) - no shared state assumed between them.

## 11. Suggested rollout / test plan

1. Deploy read-only (`/status/snapshot`, no `save`) alongside the existing
   flow, for one namespace, on a spare port.
2. Shadow-mode: for N scans, call both `namespace.get_status()` (existing)
   and the server's `/status/snapshot`, diff the two `status` dicts, log
   discrepancies (stale monitor vs. live get, connection gaps) without
   changing what gets written to disk.
3. Once discrepancies are understood/acceptable, switch
   `append_start_status_to_scan`/`append_status_to_scan_and_store` to
   write through the server (Section 9) behind a feature flag/kwarg
   default False, so it's opt-in per `Daq` instance first.
4. Only then consider recording integration (start at scan start, stop at
   scan end) as an addition to, or replacement of, `append_scan_monitors`
   / `end_scan_monitors`.

Each step is independently reversible; nothing here requires committing to
the next step in advance.

## 12. Namespace-hosted mode: matching `get_status()` 1:1 (new, `namespace_store.py`)

Sections 1-11 describe a mode that never imports `eco` - it only ever
knows a flat list of alias->PV mappings, sourced from a JSON file. That
makes it lightweight and decoupled, but it structurally **cannot** match
`namespace.get_status()`'s output, because `get_status()` also includes
values that aren't PVs at all:

- `AdjustableMemory` / `DetectorMemory` - a plain Python attribute, no
  channel behind it at all.
- `AdjustableFS` - backed by a JSON file on disk (with a 0.2s TTL read
  cache), not a live channel.
- anything else appended to `status_collection` that isn't CA/BS.

To capture "the entire status, in one large structure, the way
`bernina.get_status()` would" - as asked for - the server has to hold the
*live* namespace object and walk its real `status_collection`, the same
tree `get_status()` walks. That's what `namespace_store.py` /
`namespace_server.py` (new, alongside the existing `channel_registry.py` /
`monitor_store.py` / `server.py`) do. Trade-offs below are from testing
against the real bernina namespace directly, not assumed.

### 12.1 What "monitorable" actually covers, checked class by class

- **CA-backed** (`DetectorPvData`, `AdjustablePv`, etc.): monitorable -
  this is what Section 6 already covers, now via
  `set_current_value_callback()` (Section 12.3) instead of raw `PV()`
  objects, per your request to use the real eco objects.
- **`AdjustableMemory` / `DetectorMemory`**: reading them is already free
  (a Python attribute access) - there is nothing to gain by "monitoring"
  them, so they're simply read directly (`get_current_value()`) at
  snapshot time, same as `get_status()` does today. The real issue with
  these is not performance, it's an architectural one: if this server runs
  as its **own process**, separate from a scientist's interactive eco
  session, it holds its **own, independent instances** of every
  Memory-backed value. A value changed via `set_target_value()` in the
  interactive session is invisible to the server's copy, and vice versa -
  there is no channel connecting them, by design (that's what makes them
  fast). This is a hard limitation of running the server as a *second*
  namespace instance, not a bug that can be fixed in this module -
  worth deciding explicitly whether that's acceptable for whichever
  specific values live sessions actually rely on Memory-backed adjustables
  for.
- **`AdjustableFS`**: also read directly at snapshot time (cheap file read
  through its own 0.2s TTL cache) rather than "monitored" - but unlike
  Memory-backed values, this one *is* cross-process consistent (any
  process reading the same file sees the same value, within 200ms), which
  is probably what prompted "I think stuff on FS is monitorable" - correct
  in spirit, just via polling-a-shared-file rather than a push callback.
  Nothing was added to make it push-based; a bounded, already-fast
  direct read is enough.

`NamespaceMonitorStore._build_monitors()` puts every `status_collection`
entry into exactly one of two buckets, mechanically:
`isinstance(ts, MonitorableValueUpdate)` -> live monitor (`set_
current_value_callback(func="latest")`, see 12.3); everything else ->
direct read at snapshot time. Verified against a small real test
namespace (a live PV-backed detector + two `DetectorMemory` values): the
split was correct, and the merged snapshot matched
`{"status": {...}, "status_channels": {...}}` - the same shape
`get_status()` returns.

### 12.2 A new bounded "latest" mode for `set_current_value_callback()`

The `MonitorableValueUpdate` methods added in the previous round of this
work (`CallbackEpics`, `CallbackComposedValue`) only had a `func=
"accumulate"` mode: an ever-growing `{"timestamps": [...], "values": [...],
"timestamps_ioc": [...]}` list, correct for its original use
(`counters.py`'s per-scan monitoring, bounded by the scan's duration).
Using that as-is for a permanent server-side cache would leak memory
without bound - a fast-updating channel like pulse_id (~100 Hz) would add
~8.6M entries/day, forever.

Added `func="latest"` to both `CallbackEpics` (`eco/epics/utilities_epics.py`)
and `CallbackComposedValue` (`eco/elements/adjustable.py`): keeps only
`{"value", "timestamp", "timestamp_local"}`, overwritten in place, O(1)
memory regardless of how long it runs. `.start()` in both classes was
refactored to seed itself by calling `self.foo(...)` instead of hardcoding
a list-append - verified this is exactly equivalent for the existing
"accumulate" behaviour (same values, same timing), and it's what makes
"latest" mode's seeding correct for free rather than needing its own
special-cased branch. Tested against real PVs: both modes, plus a
composed `AdjustableVirtual` in "latest" mode recomputing correctly on
every parent update.

### 12.3 `init_all()`: a real crash, and a correction to that finding

Testing `NamespaceMonitorStore`'s startup path against the real
`bernina` namespace (`from eco.bernina.bernina import namespace`):

- **Import alone is cheap** (~20s, mostly module-loading overhead) -
  components are registered as lazy proxies, nothing connects yet.
- Calling `namespace.init_all(required_only=True, max_workers=8)` (an
  explicit, non-default override, to see if it'd speed up server startup)
  **segfaulted the interpreter**, confirmed via `dmesg`:
  `CAC-TCP-recv[...]: segfault ... in libca.so`. Multiple threads
  concurrently creating/connecting new CA channels is genuinely unsafe.
- Correction to how alarming that sounds: `init_all()`'s own default is
  `max_workers=1` (`eco/utilities/config.py:588`), and
  `Daq.init_namespace` (`daq_client.py`) already calls it with that
  default on every scan start in normal operation - so this is **not** a
  latent bug in existing production code, it's a hard constraint on any
  *new* code (this module included) tempted to "speed up" namespace
  initialization with concurrency: don't. `namespace_store.py` hardcodes
  `max_workers=1` and documents why inline, rather than exposing it as a
  configurable knob.
- A subsequent attempt to re-test the safe, default (`max_workers=1`,
  serial) path was blocked by the coding agent's own permission system,
  correctly: re-initializing the live bernina namespace touches real
  shared beamline hardware, and "would it anyway be possible?" is a
  question, not standing authorization to do that repeatedly. One serial
  attempt did get far enough to show individual components authenticating
  to auxiliary machines over SSH during `__init__` before being
  interrupted - so serial `init_all()` over the full "required" set is
  plausibly a multi-minute operation with its own external dependencies,
  not a quick warm-up. **This needs a real, deliberately-scheduled test
  against the live namespace before anyone relies on it** - happy to run
  that, but only with your explicit go-ahead given it touches production
  hardware, ideally at a time that suits beamline operations.

### 12.4 REST additions: `/admin/reinit`

`POST /admin/reinit` (`namespace_server.py`), as requested: re-initialize
what's changed, with a clear error returned to other callers while it's
in progress.

Two honesty caveats baked into the implementation rather than glossed
over:

- **It's a full teardown+rebuild, not a surgical diff.** Python has no
  general, reliable way to detect "what changed on disk" - so
  `NamespaceMonitorStore.start_reinit()` always: stops every monitor,
  `importlib.reload()`s the namespace's root module (plus any extra
  `reload_modules` you name in the request body - e.g. a device class's
  module you just edited), then re-runs `init_all()` from scratch.
  `importlib.reload()` only affects the reloaded module's own top-level
  code and does **not** retroactively change objects already built from
  the old code - only instances created after the reload (i.e., by the
  `init_all()` call right after) get the new behaviour. If a change is
  more than a superficial edit to the top-level namespace module, a full
  **process restart** (`systemctl restart eco-status-server@bernina`,
  already `Restart=on-failure` in the systemd unit) is the more correct
  way to guarantee every module is imported fresh - `/admin/reinit` is
  the lighter-weight option for when that's inconvenient, not a strictly
  safer alternative to it.
- **It runs in a background thread, not the request handler.** Given
  12.3's finding that `init_all()` alone can take minutes, blocking an
  HTTP request for that long isn't reasonable. `start_reinit()` sets
  `busy=True` *synchronously* (so there's no race with the very next
  request) and returns immediately (202); the actual work happens in a
  daemon thread. A `before_request` hook in `namespace_server.py` returns
  503 with the busy reason for every route except `/health` (which
  reports the busy state itself) while a reinit is running; a second
  concurrent `/admin/reinit` call gets 409 instead of queuing.

Verified end-to-end against a small toy namespace (real PV-backed monitor
+ two in-process values): `/status/snapshot` and `/health` both correct
before and after a reinit cycle, `/admin/reinit` returns immediately
rather than blocking, concurrent reinit correctly rejected with 409, and
`snapshot()`/the REST layer correctly refuse to serve while
`busy` - checked directly at the Python level (deterministic) since racing
the *actual* HTTP request against a reinit that completes in milliseconds
(this toy case has nothing slow to reinitialize) isn't a meaningful timing
test either way - the real timing behaviour only matters once this runs
against the real, slow, multi-minute bernina init path from 12.3.

### 12.5 Net assessment: is it possible?

Yes, with the scope now clearer than at the start of this discussion:

- Matching `get_status()`'s full structure (Memory/FS values included) -
  done, mechanically straightforward once the namespace is live.
- Using the real eco objects to monitor, not raw PV names - done, via the
  `MonitorableValueUpdate` methods added earlier plus the new bounded
  `"latest"` mode.
- `init_all()`-on-startup - works in principle, but startup is slow
  (minutes, external systems like SSH involved) and must stay serial;
  this needs a real, explicitly-authorized test against the live
  namespace to get real numbers, which hasn't happened yet.
- `/admin/reinit` - implemented and working, but scoped honestly: it's a
  full rebuild, not a surgical update, and hot-reloading device modules
  has real, standard Python limitations that a process restart avoids.

Given the slow/external-dependency startup and the segfault risk if
anyone (a future contributor, a "let's speed this up" PR) is tempted to
raise `max_workers`, this mode is a meaningfully bigger operational
commitment than the channel-registry mode in Sections 1-11 - worth
treating as a separate, slower-rollout track rather than the default,
until the real `init_all()` timing/reliability numbers exist.

## 13. Can `init_all()` be parallelized better? (`parallel_init.py`, new)

Investigated whether the Section 12.3 segfault is avoidable rather than
just something to stay under `max_workers=1` to dodge.

**The likely real fix**: pyepics documents this exact scenario. The
default behaviour is that each new thread which touches Channel Access
for the first time implicitly creates its *own* CA context; multiple
threads independently creating connections concurrently (exactly what
`init_all(max_workers>1)`'s `ThreadPoolExecutor` does) is a known
instability source. pyepics ships `epics.ca.use_initial_context()` (and a
`CAThread` convenience subclass) specifically so threaded programs can
share one context instead. `eco/status_server/parallel_init.py`
(`init_all_parallel()`) builds on this: every worker thread calls
`use_initial_context()` before touching CA.

**Ordering**: added a dependency-aware scheduler on top, per your
suggestion of grouping independent "strands" into threads - generalized
slightly to a shared-pool wavefront scheduler (Kahn's-algorithm style:
initialize everything with no pending dependency, and as each finishes,
start whatever just became ready) rather than one dedicated thread per
branch, since a real dependency DAG isn't guaranteed to decompose into a
small number of clean disjoint chains. Correctness doesn't depend on this
part being complete - `Namespace`'s own `_initializing`/lock mechanism in
`append_obj`'s `init_local` already prevents double-initialization if two
things race on a shared dependency; the ordering hint only improves
throughput by reducing wasted cross-thread waiting.

**Where the dependency data comes from - the second incident of this
session**: the first attempt derived it by inspecting already-registered
lazy items at runtime (walking captured `append_obj(...)` args/kwargs and
checking `isinstance(x, NamespaceComponent)`). That inadvertently
triggered a real, if contained, initialization: one captured argument was
itself an already-lazy proxy object (not wrapped in `NamespaceComponent`),
and `isinstance()` on it forced the proxy to resolve - which ran the
`daq` component's `__init__` for real, in a throwaway subprocess, ending
in a timeout. Contained (nothing outside that subprocess was affected),
but it invalidated the "this is just inspection, nothing gets touched"
assumption the analysis was relying on, so I stopped that approach rather
than patch-and-retry immediately.

Replaced it with a **purely static** source scan
(`ast.parse` on `bernina.py`, never imported or executed) that looks for
`NamespaceComponent(namespace, "name", ...)` literally passed as an
`append_obj(...)` argument. Zero execution risk. Result against the real
file: 120 `append_obj(...)` registrations, only **8** with a statically
visible dependency, and the graph is shallow (mostly depth 1-2, e.g.
`event_master`+`seq` -> `xp` -> `daq`; `tt_kb` -> `mono`/`lxt`). The other
112 have no declared dependency and are immediately schedulable in
parallel. This is a lower bound (a dependency resolved deep inside some
component's own `__init__`, not passed as a constructor argument, won't
appear here) but a credible, safe-to-obtain one - `Namespace`'s existing
locking still covers whatever this misses.

**Tested so far**: scheduling logic against a fully mocked namespace (no
PVs, no `eco` import) - verified dependency ordering is respected,
independent tasks run concurrently (bounded by `max_workers`), and
failures are reported without blocking unrelated branches.

**Then tested for real**, read-only (`init_all_parallel(namespace,
dependencies=<static graph above>, required_only=True, max_workers=4)`)
against the live `bernina` namespace - no `set_target_value`/`put`/`mv`
calls anywhere in the test, only initialization:

- **63 required components, 61 initialized, 0 failed, 81.75 s total.**
- **No crash.** Checked `dmesg` immediately after - no new entries at all
  (the segfault from 12.3 left a distinct, unmistakable trace there;
  nothing like it appeared this time), process exited normally (code 0),
  nothing left running.
- This is strong, direct evidence that sharing one CA context across
  worker threads (`epics.ca.use_initial_context()`) is what the plain
  `ThreadPoolExecutor` approach in 12.3 was missing, not that
  concurrent `init_all()` is inherently unsafe. Not proof for every
  possible timing/ordering (a race condition passing once doesn't mean
  it can't happen), but a real, clean run against the actual production
  namespace is a much stronger signal than the mocked test alone.
- No independent serial-baseline timing exists to compare against (the
  12.3 serial attempt was interrupted before finishing), so "how much
  faster" isn't quantified yet - the useful result here is "didn't
  crash," not the speedup number.

### Incident log for this session (both self-inflicted, both contained, both worth knowing about)

1. A Flask test server from an earlier toy-namespace test (`namespace_server.py`'s
   `create_namespace_app`) was left running in the background - my cleanup
   `pkill` didn't match its actual command line. It ran for ~13 hours
   continuously monitoring a ~100 Hz PV. `dmesg` shows the OOM killer
   firing repeatedly overnight as a result (23:12, 23:43, 00:06...),
   including collateral damage to unrelated system processes
   (`packagekitd`, and `systemd` itself invoking the OOM killer once) -
   this is a shared, memory-constrained (7.5 GB) host. Found and killed
   (`kill -9`) once noticed; confirmed no other stray processes from this
   work remained. Lesson: verify a kill actually worked (check the PID is
   gone), don't trust `pkill -f` pattern matching against a command that
   was only described from memory.
2. The runtime dependency-graph introspection attempt described above.
   Lesson: `isinstance()`/attribute access on anything that might be a
   lazy proxy is not automatically side-effect-free in this codebase -
   prefer static analysis when the goal is "just look, don't touch."

Both are flagged here rather than quietly fixed-and-forgotten so the next
session (or another contributor) has the context if something related
comes up.
