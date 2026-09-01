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
  (`eco.epics_utils.utilities_epics.Monitor`, `eco.epics_utils.detector.CallbackEpics`)
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

**Superseded (kept for the record).** The concurrency constraint above no
longer holds: `Namespace._run_init_pass` now attaches every worker thread
to one shared CA context (`ca.use_initial_context()`, the fix prototyped
in `parallel_init.py`), which is exactly what the segfault was about.
`init_all()`'s default is `max_workers=8` in both execution modes - there
used to be a separate `background_max_workers`, since removed - and
`namespace_store.py` configures its own via `init_workers`/`retry_workers`
rather than hardcoding 1. What stays true is the timing: it is a
multi-minute operation, which is why the server binds its port first and
initializes on a background thread.

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

---

## 14. Implemented and measured end-to-end (supersedes the "prototype" framing above)

Everything from section 12 onwards was written as a proposal against an
untested prototype. This section records what the namespace-hosted mode
actually does now, and what was measured running it on `saresb-cons-04`
against the real `bernina` namespace with a client on `saresb-cons-05`. It
supersedes earlier statements where they conflict - notably the
"`max_workers` must be 1" constraint, which no longer holds (see 13:
`Namespace._run_init_pass` now attaches every worker to the shared CA
context, and 8 workers were used throughout the measurements below).

For how to run and use it, see `README.md` in this directory - this
section is the record of what was found, not the manual.

### 14.1 What changed relative to the prototype

- **Startup no longer blocks.** `create_namespace_app()` returns
  immediately and `init_all()` runs on a background thread. `__main__`
  binds the port *first*, then starts the store, so a port clash costs a
  failed bind rather than a wasted multi-minute init nobody can reach.
- **A real state machine**: `importing` -> `initializing` -> `ready`, plus
  `reinitializing` and `failed`, with live progress
  (`n_initialized`/`n_target_names`/`n_failed`) on `/health`, and a
  `generation` counter that increments on every successful (re)build.
  `generation` is what makes "wait for my reinit to finish" correct: a
  reinit request returns while the server is still reporting the previous
  `ready` state, so polling readiness alone races.
- **Snapshots are `namespace.get_status(base=None)`**, not a separate CA
  monitor cache. That was a deliberate narrowing: matching the daq client
  bit-for-bit matters more than a second value-collection mechanism that
  can drift from it, and the win being sought is the *initialization*, not
  the fan-out. The monitor implementation is still there behind
  `use_monitors: true`, unused by default.
- **Target-name selection never writes `namespace.required_names()`.**
  That is an `AdjustableFS` backed by a file shared with every interactive
  session at the beamline; the server expresses its own scope with
  `init_all(required_only=False, exclude_names=...)` instead.
- **Reinit is a real API**: `/admin/reinit` with `mode` =
  `failed`/`names`/`full`/`init`/`reimport`, plus `/admin/restart`, which
  re-execs the process. The client's `reinit()` defaults to `restart`
  because it is the only unconditionally correct one (see 14.4).

### 14.2 Bugs this shook out (all found by running it, not by reading it)

1. **`init_all()`'s retry loop could never terminate.** It retries names
   that raise `IsInitialisingError`, which is also what a build slower than
   the 30 s `init_timeout` raises when another worker is already building
   the same name - so a slow component can raise it every round, forever.
   Observed directly: a server sat at "76 of 87 initialized" for over ten
   minutes, cycling four names. `init_all` already had an unused `N_cycles`
   parameter; it now caps that loop (`Namespace._run_init_pass`), and names
   still pending at the cap are reported as failures instead of retried
   silently for ever. This is a latent bug in ordinary interactive use too,
   where it would show up as a background thread spinning unnoticed.
2. **Names given up on that way carry no exception.** `giveup_failed`
   sweeps whatever is still lazy into `failed_items` without one, so
   "failed" mixed two very different things: a broken device, and a device
   that was simply never built. The store now distinguishes them and runs
   extra `init_all()` passes for the latter only. On bernina this is the
   difference between `att`, `att_usd`, `kb` and `xrd` being present in
   every snapshot or missing from all of them - which of the two happened
   was pure scheduling luck between runs.
3. **numpy values are not JSON-serializable by Flask.** Waveform PVs and
   image stats return `ndarray`; one such value 500'd the entire snapshot.
   The app now uses the same numpy handling the status file is written
   with, plus a `str()` fallback so one odd value degrades one entry.
4. **`/admin/restart` re-exec'd itself into a dead process, twice.** First
   because `[sys.executable] + sys.argv` re-runs `__main__.py` *as a
   script*, which dies on the package's relative imports; then because
   Flask's `app.run()` goes through `run_simple()`, which does
   `srv.socket.set_inheritable(True)` for the dev reloader - so the
   listening socket survived `execv` and the new process could not rebind
   ("Address already in use"). Fixed by reconstructing the `-m <package>`
   form and by using `make_server` directly plus an explicit
   `server_close()` before the exec.
5. **`Namespace.resolve_item()` built the very thing it was looking up.**
   It resolved a name with `lazy_items.get(n) or failed_items.get(n) or
   ...`, and truthiness of a still-lazy `Proxy` *resolves* it - so looking a
   name up initialized the device, and for a previously-failed name it
   re-raised the stored exception. `reinitialize()` used the same chain, so
   the server's `/admin/reinit` with `mode="failed"` - "rebuild whatever
   failed" - raised the exact failure it was called to clear
   (`MotorException: SARES20-MF2:MOT_4 is not an Epics Motor`, from
   `prof_kb`, before it had rebuilt anything). Both now use membership
   tests. Worth knowing beyond this service: any `or`/`if x:` over a
   namespace item is a device build waiting to happen.
6. **One HTTP timeout cannot serve both purposes.** `/health` answers in
   milliseconds and wants a short timeout so an unreachable server fails
   fast; a snapshot is a 13.7k-channel fan-out and takes 10-20 s. A single
   10 s timeout made every real snapshot fail. Client and `Daq` now carry
   both.

### 14.3 Measured on `saresb-cons-04` (bernina namespace, 8 init workers)

| what | measured |
| --- | --- |
| components targeted | 87 (the 92 `required_names` minus `elog`, `scilog`, `daq`, `scans`, `opa_he`) |
| `init_all()` to `ready` | **143 s** (8 parallel workers, then serial retry passes) |
| components initialized | 79 of 87; the remaining 8 are partially-initialized assemblies (`las`, `tt_kb`, `rixs`, `xrd`, ...) that still contribute status |
| status detectors served | 16 528 |
| one snapshot | 11-18 s server-side, ~0.2 s more over HTTP; 2.8-3.4 MB of JSON |
| snapshot vs `read_workers` | 20 -> 16.3 s, 64 -> 12.7 s, 128 -> 11.1 s: flat enough that the fan-out is dominated by CA timeouts on disconnected channels, not by concurrency |
| `status.json` written per run | 9.2 MB (both `status_run_start` and `status_run_end`, ~16 200 entries each) |

Getting to those numbers took two goes. With the retry passes also running 8
workers, init took **639 s** and still left `att`, `att_usd`, `kb` and `xrd`
uninitialized on some runs, costing ~2 500 status entries (15 %) versus a
local `get_status()`. Running only the *first* pass in parallel and the
retry passes serially fixed both at once - 143 s, and 16 134 entries against
16 185 from a full local namespace, i.e. coverage parity (the residual 50 are
`xrd` sub-entries). Which makes sense: workers colliding on a shared
dependency is what makes a component a straggler in the first place, and one
worker cannot collide with itself.

The other honest reading: **a single snapshot is not faster than doing it
locally** - it is the same `get_status()` call, just executed elsewhere.
The entire win is that the client never pays `init_all()`.

Excluded on purpose: `scilog` blocks on an interactive password prompt in
`__init__` (a headless server has no stdin to answer it), `elog` depends on
it, and `daq`/`scans` depend on `elog`; `opa_he` times out. Those five are
what turned a 3-minute init into an endless retry cycle before the
`N_cycles` cap existed. None of them contributes status entries, so
excluding them costs nothing measurable.

### 14.4 Reinitialization: what actually works

`Namespace.reinitialize(reload_modules=True)` deliberately refuses to
reload the namespace's own assembly module, because reloading `bernina.py`
means re-running the entire beamline setup script. So for anything beyond
one device driver, "re-initialize the namespace" has to mean something
bigger:

- `mode="reimport"` drops every `eco.*` module from `sys.modules` and
  re-imports. It does re-run `bernina.py`, but it cannot *unload* code that
  live objects still reference - the next import creates a second, distinct
  copy of every eco class. That is not theoretical: doing it inside the
  test suite left a later test unable to recognise `IsInitialisingError`,
  because the class it caught was no longer the class being raised. The old
  namespace's CA channels and device threads are not reclaimed either.
- `mode="restart"` re-execs the process. Everything is genuinely fresh,
  at the cost of the full init time. Measured round trip, client call to
  `ready` again: **652 s**. The client watches `instance_id` (not
  `generation`, which restarts at 0) to know the new process is up.

`restart` is therefore the default for `StatusServerClient.reinit()`. The
in-process modes are the fast paths for the narrow cases they fit -
`mode="failed"` in particular is the cheap "that IOC is back up now" retry.

Measured, once the `resolve_item()` bug in 14.2 was fixed:
`reinit(mode="failed")` over the 8 partially-initialized bernina assemblies
takes **188-190 s** and bumps `generation` by one each time. Repeated three
times in a row, the served detector count stayed at 16 616 - i.e.
`reinitialize()`'s `status_collection.remove()` / `alias.pop_object()`
teardown really does clean up after itself, and a long-lived server does not
accumulate stale status entries across rebuilds. (The first rebuild after
startup did add 88 detectors, from components that had come up incomplete;
that is a one-off, not growth.)

While a rebuild runs, `/status/snapshot` answers 503 and a second
`/admin/reinit` answers 409 - both verified against the live server, not
just in tests. That matters: a reinit tears down and re-registers the very
`status_collection` a snapshot walks.

### 14.5 Verified against a real DAQ run

`Daq(status_server=...)` was exercised with real `ascan`s over
`dummy_adjustable` (3 steps x 10 pulses) in `p19641`, from a session on
`saresb-cons-05` while the server ran on `saresb-cons-04`. The same script
was run both ways:

| | server (run 331) | local, today's behaviour (run 330) |
| --- | --- | --- |
| whole `ascan` call | **75 s** | **982 s** |
| status entries written | 16 209 / 16 194 | 16 185 / 16 280 |
| client-side namespace cost | 14 s import + 7 s for the seven items `Daq` itself needs | the same, **plus** `init_all()` inside the scan |

In both cases `append_start_status_to_scan` and
`append_status_to_scan_and_store` produced
`/sf/bernina/data/p19641/res/run_data/daq/runNNNN/aux/status.json` with both
blocks merged into one file, and `append_aux` had the broker copy it to
`/sf/bernina/data/p19641/raw/runNNNN/aux/status.json` - "copying user
file(s) finished successfully". The only difference is where the values came
from.

Note what the 982 s is and is not: `Daq.init_namespace` calls `init_all()`
with the default `max_workers=1`, i.e. serially, so part of that gap is
parallelism the local path could have too (the server uses 8). It was left
alone here deliberately - changing how every scan at the beamline
initializes its namespace is a separate decision from adding an opt-in
alternative. But it means "13x" is the measured end-to-end difference
between the two code paths as they stand today, not a claim about the
theoretical floor of the local one.

---

## 15. Monitor recording, and whether downthrottling helps (measured)

`/recording/start` attaches a CA monitor to every `MonitorableValueUpdate`
detector in the namespace, buffers updates, and `/recording/stop` writes them
as one `escape.ArrayTimestamps` per channel. Measured on `saresb-cons-04`
against the live bernina namespace, 180 s per run, client on
`saresb-cons-05`.

Scale of one run: **10 139 monitorable detectors**, of which **7 785 attach**
(the rest are `MonitorableValueUpdate` implementations whose
`set_current_value_callback` has no underlying PV to give). Attaching all of
them takes **3.0 s** — `add_current_value=False` and `with_ctrlvars=False`
are what keep it there; either default would issue a blocking CA round trip
per channel, which is the get-storm this whole service exists to avoid.

### 15.1 The four modes, side by side

| mode | updates/s | points stored | file | server CPU | RSS growth |
| --- | --- | --- | --- | --- | --- |
| `all` | 5 141 | 993 783 | 267 MB | 0.53 core | +292 MB |
| `sample`, 0.1 s | 4 972 | 185 134 | 236 MB | 0.56 core | +238 MB |
| `throttle`, 0.1 s | 5 047 | 170 102 | 200 MB | 0.53 core | **+5 MB** |
| `all` + `DBE_LOG` | 5 066 | 966 613 | 262 MB | 0.54 core | +35 MB |

The first column is the answer to "can we downthrottle to save work?":
**no client-side option changed the update rate at all**, `DBE_LOG` included.
Subscribing to the IOC's archive deadband stream instead of `DBE_VALUE`
delivered the same ~5 000 updates/s, i.e. ADEL is 0 on these records, so the
IOC has nothing to decimate by.

And because the rate is unchanged, **the CPU is unchanged** — 0.53 to 0.56 of
one core in every mode, including the one whose callback does almost nothing
(`sample`). That is the substantive finding: the cost of an update is the
fixed crossing from libca's receive thread into Python (GIL acquisition,
kwargs dict, callback dispatch), not the body of the callback. There is no
Python-side filter that avoids it, because the filter itself has to run
inside it.

So, to the question as asked: a downthrottle helps the **data**, not the
**performance during monitoring**. The only lever on the latter is to reduce
what the IOC sends (a real MDEL/ADEL deadband on the record — a facility-wide
change, not this server's to make) or to not monitor the channel at all.

Two things worth knowing about the modes themselves:

- `sample` was, in its first form, *worse than useless*: sampling every
  channel onto the grid produced **14.8 million points and a 636 MB file**,
  15x more than recording every update, because 7 440 of the 7 719 channels
  update slower than 0.1 Hz and were being upsampled. It now stores a channel
  only when its CA timestamp actually changed; the numbers in the table are
  after that fix.
- `throttle` is the only mode whose memory stays flat (+5 MB over 3 minutes,
  against +292 MB for `all`). For anything longer than a scan, that is the
  difference that matters — a previous prototype left a single ~100 Hz PV
  monitored overnight and the OOM killer took out unrelated system processes
  (see the incident log above). `max_points_per_channel` is the hard backstop.

### 15.2 Where the updates actually come from

Of 7 719 channels with data in a 193 s recording:

| rate | channels | share of all updates |
| --- | --- | --- |
| ≥ 50 Hz | **48** | **87 %** |
| 10–50 Hz | 9 | 3 % |
| 1–10 Hz | 182 | 9 % |
| < 1 Hz | 7 480 | 1 % |

48 channels — `event_system.pulse_id`, the eight `digitizer_ioxos_user`
channels, `las_inc.energymeter_intensity_lraw`, the `mon_mono.signal_*_raw`
group, `fel.bam_*` — produce seven eighths of the load. Any effective
reduction has to target those specifically; a blanket throttle spends its
effort on the 7 480 channels that cost nothing.

### 15.3 Where the file size actually comes from

Not where the updates come from, which is the surprise:

| | channels | storage |
| --- | --- | --- |
| 2-D waveform channels | 38 | **208 MB (96 %)** |
| of which `digitizer_keysight_user.channel_1/2.waveform_slow` | 2 | 204 MB |
| all scalar channels | 7 677 | 8 MB |
| HDF5 structure | 7 715 | 15 MB (1.9 KiB/channel) |

Two channels carrying 8000-sample waveforms at ~8 Hz are 85 % of a 239 MB
file. Every 100 Hz scalar in the namespace put together is 8 MB. So the lever
on output size is `max_value_elements` (drop oversized values) or an explicit
channel list — not the sample rate.

The 1.9 KiB/channel of HDF5 structure is itself the result of a fix:
`write_monitor_recording` now creates the file with `libver="latest"`. With
HDF5's default backwards-compatible object headers the same file was 267 MB,
and a synthetic 3 000-channel one-point-each file was **16.5 MB versus 5.9 MB**
— 2.8x — for identical content. For a file that is thousands of tiny
datasets, the format version is worth more than the compression would be.

### 15.4 Effect on the server's day job

None measurable. Snapshot latency, idle versus during a full-rate recording:

| | idle | during | after |
| --- | --- | --- | --- |
| `all` | 21.0, 20.0 s | 19.5, 20.0 s | 16.9, 18.3 s |
| `throttle` | 20.0, 22.8 s | 18.5, 19.5 s | 17.7, 20.0 s |

A snapshot is dominated by CA timeouts on disconnected channels, not by CPU,
so 0.5 core of monitoring in the background does not show up. Thread count is
also unchanged (89 before and after) — the monitors ride libca's existing
receive threads rather than adding any.

Writing the file takes **30–37 s** for ~7 700 channels, and reading it back
with `escape.DataSet.load_from_result_file` takes ~29 s. Both are per-channel
costs, so both scale with how many channels are recorded, not with duration.

### 15.5 Two bugs this found

1. **`format_manual_instantiation()` was constructing devices.** It reprs
   every constructor argument to build a "copy/paste this" hint, on *every*
   initialization — and `Proxy.__repr__` resolves a lazy namespace component,
   i.e. builds the real device. Found with `kill -USR1`: the server was stuck
   three frames deep inside a diagnostic string, building `xp` while trying
   to build `att`. This is what made startup take anywhere from 137 s to over
   670 s depending on scheduling luck; with the fix (a repr that leaves
   unresolved proxies alone) it is 84–140 s. Same family as the
   `resolve_item()` bug in §14.2: in this codebase, introspecting a namespace
   item is never free.
2. **`faulthandler.register(SIGUSR1, chain=True)` killed the server.**
   SIGUSR1's default disposition is Term, so chaining to the previous handler
   dumped the stacks and then terminated the process — the diagnostic took
   down what it was diagnosing. `chain=False`.

### 15.6 Recommended defaults

Measured, same server, same 180 s window, `mode="throttle",
min_interval=0.1, max_value_elements=1024`:

| | `all` | recommended |
| --- | --- | --- |
| points stored | 1 003 570 | **173 992** |
| file | 239 MB | **21.6 MB** |
| server RSS growth | +292 MB | **+38 MB** |
| channels with data | 7 715 | 7 713 |

An 11x smaller file and an 8x smaller memory footprint, with two channels'
worth of coverage lost (the ones that only ever produced an oversized
waveform). Update rate and CPU are, as above, unchanged - 5 431 updates/s
either way.

- Record with `mode="throttle", min_interval=0.1` unless you specifically
  need every transition: 5.8x fewer points, flat memory, no loss on any
  channel slower than 10 Hz (which is 99.4 % of them).
- Set `max_value_elements` (e.g. 1024) unless waveforms are the point of the
  recording.
- Leave `subscription_mask` alone. `DBE_LOG` measurably did nothing here, and
  the alternative — pyepics's `PV(monitor_delta=...)` — is a trap: it first
  tries to `caput` the IOC's `.MDEL` field, changing the record for every
  client at the facility, and only falls back to a local filter if that write
  is refused.
