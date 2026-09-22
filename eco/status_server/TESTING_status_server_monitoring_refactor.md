# Testing: StatusServer monitor start/stop + diagnostics Detectors

Test plan for the online/live session to run — this work was implemented on
an offline checkout with no access to a real status server, EPICS, or the
`bpy312` Python, so nothing below has been exercised end-to-end yet. Written
alongside `eco/status_server/NAMESPACE_MONITOR_FORMAT.md`, same handoff-note
spirit.

## What changed

1. `eco.status_server.namespace_component.StatusServer` (`bernina.status_server`)
   gained `start_monitoring(pgroup, run_number, ...)` / `stop_monitoring(...)`
   methods — the recording-id (`"{pgroup}_run{run_number:04d}"`) and default
   filename (`"namespace_monitor.ixp.h5"`) conventions moved here from
   `daq_client.py`, where they used to be built inline.
2. `Daq.start_scan_monitoring` / `Daq.end_scan_monitoring`
   (`eco/acquisition/daq_client.py`) now call `self.status_server.start_monitoring(...)`
   / `.stop_monitoring(...)` — `self.status_server` is a new lazily-resolved
   property (`NamespaceComponent(self.namespace, "status_server")`, the same
   pattern `self.pgroup`/`self.checker` already use) instead of hand-building
   the request and calling `self.status_client` directly. `self.status_client`
   itself, and every off-switch (`use_running_status_server`, `scan_monitoring`,
   `_status_server_ok_for_this_scan`), are unchanged.
3. `StatusServer` gained five read-only `Detector` children (`DetectorGet`,
   same pattern as `eco/elements/adj_obj.py`), backed by data `/health`/`/stats`
   already serve — no server-side changes:
   - `n_initialized`, `n_failed`, `failed_names`
   - `last_namespace_update` (ISO datetime of `/health`'s `last_init_finished`)
   - `last_request` (`{"kind", "at", "duration_s"}` from `/stats`' summary)
   Backed by two 1s-TTL memoizing helpers (`_cached_health`/`_cached_stats`) so
   reading all five together costs at most 2 HTTP calls, not 5.

**No server-side files changed** (`namespace_server.py`, `client.py`,
`namespace_store.py`, `query_stats.py`, `config.py` are all untouched) — per
the precedent in this repo's history (e.g. commit `5cbe3341`'s filename
change), this should **not** require restarting the running server on
`saresb-cons-04`. Only a session importing the updated
`eco.status_server.namespace_component` / `eco.acquisition.daq_client` needs
the new code on its `PYTHONPATH` — i.e. this needs to reach whichever checkout
that session actually runs from (confirm with `cat /proc/<pid>/environ`'s
`PYTHONPATH` if unsure, same check used historically to confirm the server was
running from `gac-bernina`'s checkout, not a personal one).

## Before testing live: two pre-existing, unrelated things to know

- **`/recording/start` (`RecordingSession.start()`) is a known-slow,
  synchronous, unthreaded CA-attach loop over bernina's ~13,000+ monitorable
  channels** that reliably exceeds the client timeout at full production
  scale — an existing, not-yet-fixed issue (real fix: make it async, tracked
  separately). `StatusServer.start_monitoring()` wraps this unchanged. If
  `start_scan_monitoring`/step 4 below times out or prints "start monitoring
  failed ... falling back", that is this pre-existing issue, **not** a
  regression from this change — test against a reduced/test namespace (below)
  to avoid it, not full production.
- **`Daq.scan_monitoring` defaults to `True` in code** (`daq_client.py`'s
  `Daq.__init__`) — the `False` stopgap for the issue above was applied live
  on an already-running session (`bernina.daq.scan_monitoring = False`), not
  committed to `bernina_daq.py`. A fresh/restarted session will have
  monitoring **on** by default and can hit the slow-attach issue on a real
  scan. Check `bernina.daq.scan_monitoring` before assuming either state.

## 1. Unit tests (no live server needed)

```bash
PYTHONPATH=<this checkout>:$PYTHONPATH \
  /sf/bernina/applications/python/.pixi/envs/bpy312/bin/python -m pytest \
  tests/test_status_server_namespace_component.py \
  tests/test_daq_status_server.py \
  tests/test_daq_scan_monitors.py -q
```

Run per-file as usual (CLAUDE.md notes the whole-suite run can dump core
partway through under pytest+offscreen Qt — not applicable to these three
files specifically, but keep the habit).

## 2. Smoke-test `StatusServer.start_monitoring`/`stop_monitoring` directly

Do this against the **disposable test-server workflow** already documented
(not production `saresb-cons-04`), or against production only with the safe
commissioning pgroup `p19641` (dormant, bernina-staff) and a made-up run
number, exactly like the 2026-09-09 verification did for `/recording/capture`:

```python
import eco
bernina = eco.start(...)  # however this session normally starts
ss = bernina.status_server
ss.base_url  # confirm which server this points at before touching anything

recording_id, result = ss.start_monitoring("p19641", 998, mode="throttle")
print(recording_id, result["n_channels_attached"], result["n_channels_requested"])
# expect recording_id == "p19641_run0998"

job = ss.stop_monitoring(recording_id, "p19641", 998)
print(job)  # {"job_id": ..., "path": ".../run0998/aux/namespace_monitor.ixp.h5"}
# the broker upload is *expected* to fail since run 998 isn't a real run --
# that's correct behavior (same as the 2026-09-09 verification), not a bug.
```

## 3. Smoke-test the five diagnostics Detectors

```python
ss.n_initialized.get_current_value()   # int, e.g. 83
ss.n_failed.get_current_value()        # int, e.g. 0
ss.failed_names.get_current_value()    # list, e.g. []
ss.last_namespace_update.get_current_value()  # ISO datetime string
ss.last_request.get_current_value()    # {"kind": ..., "at": ..., "duration_s": ...} or None
ss.status()   # unchanged existing summary -- should still print/return fine
```

Cross-check `n_initialized`/`n_failed`/`failed_names` against `ss.health()`'s
raw dict and `last_request` against `ss.stats()`'s `summary` — they should
read the same underlying values (the Detectors are read-only wrappers, not a
new data source).

## 4. End-to-end via a real scan (only against the test server / p19641)

```python
bernina.daq.scan_monitoring = True  # if it's currently off from the earlier stopgap
# run a short real (or test) scan on the test server / p19641
# then check:
scan.counter_scratch("daq").get("monitoring_recording_id")
scan.scan_parameters.get("monitors")  # "aux/namespace_monitor.ixp.h5"
```

Confirm no "start monitoring failed ... falling back to the local namespace
mechanism" warning appears (if it does and you're against the test server /
small namespace, that would be a real regression worth reporting — against
full production it's the known pre-existing issue above, expected until the
async fix lands).

## 5. If something looks wrong

Revert is a plain two-file rollback — `git diff` /
`git checkout -- eco/status_server/namespace_component.py
eco/acquisition/daq_client.py` restores the previous direct
`self.status_client.start_recording`/`capture_recording` calls, since neither
the client (`StatusServerClient`) nor the server routes changed underneath.
