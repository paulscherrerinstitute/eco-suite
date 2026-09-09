# `namespace_monitor.h5` — format and escape integration

Handoff note for whoever extends `escape_fel` to load this file. Written from
the eco (status-server) side; the escape-side integration described below
was worked out by reading `escape_fel` 0.2.7's actual source
(`escape/swissfel/parse.py`, `escape/utilities.py`,
`escape/storage/dataset.py`) rather than guessing at its API, but has **not**
been run against a real escape load — verify against one real recorded file
before relying on it.

## What it is

A per-run, whole-namespace channel-access recording: every monitorable
channel's update history during a scan (not just the two `status.json`
snapshots at scan start/end). Produced by `eco.status_server` (the
long-running namespace-mode status server), written directly into the run's
own aux directory as part of the run, the same way `aliases.json` and
`status.json` already are.

- Written by: `eco.status_server.storage.write_monitor_recording()`.
- Produced end-to-end (start recording → stop, write, upload) by: `POST
  /recording/capture` (`eco/status_server/namespace_server.py`), driven by
  `Daq.start_scan_monitoring()` / `Daq.end_scan_monitoring()`
  (`eco/acquisition/daq_client.py`) — **not yet wired into a real scan's
  callbacks** as of this writing, so no run has one yet; this document
  describes the format ahead of that.
- Location: `<pgroup>/res/run_data/daq/run<NNNN>/aux/namespace_monitor.h5`,
  next to `status.json`/`aliases.json`/`scan_info_rel.json`.
- **Only exists when a status server was in use for the run.** Unlike
  status/aliases, there is deliberately no local-namespace fallback — a
  local recording would need the scanning session's own namespace holding a
  live CA monitor per channel for the run's whole duration, which is exactly
  the per-session cost the status server exists to avoid. A run without a
  status server simply has no `namespace_monitor.h5` and nothing should
  treat that as an error.

## File format

One `escape.ArrayTimestamps` per channel, in one HDF5 file
(`libver="latest"` — needs a reasonably modern HDF5 to read; this cuts
per-channel structural overhead severalfold over the default backwards-
compatible layout, which matters here because a namespace recording is
thousands of small datasets, not a few large ones — see
`write_monitor_recording`'s comment for the measured numbers).

Per channel, under an HDF5 group named by the channel's **full alias**
(exact string, see naming below):

```
<alias>/data_0000                    # values, shape (n_points,) or (n_points, ...)
<alias>/timestamps_0000              # float unix timestamps, shape (n_points,)
<alias>/scan/timestamp_intervals     # shape (1, 2): [[recording_started_at, recording_stopped_at]]
```

(`_0000` is `escape.ArrayTimestamps`' own single-bin indexing — a namespace
recording is one continuous window, not a stepped scan, so there is always
exactly one interval covering the whole recording, unlike a per-step scan
array which has one bin per step.)

Two known gaps, both intentional, both visible in the file if you go
looking:

- **Not every monitorable channel is necessarily present.** A channel whose
  value type doesn't survive `numpy.asarray()` into a numeric/string dtype
  (an enum PV giving something exotic, in practice) is dropped, not
  corrupted-in; `write_monitor_recording`'s return carries a
  `write_report: {n_written, n_skipped, skipped: {alias: reason}}` (the
  route also surfaces this as `n_written`/`skipped` on the `/recording/*`
  job), but that report itself is **not** written into the h5 file. If you
  need it at load time, read the corresponding `/status/job/<id>` response
  captured at write time, or treat a missing expected channel as "was
  skipped, not an error."
- **Channels whose recording mode dropped points are still internally
  consistent**, just sparser than the true update rate — `mode`,
  `min_interval` (throttle) or `sample_interval` (sample) on the recording
  determine this; none of it is recorded per-point in the file itself
  (a flat decision for the whole recording, not per-channel). If this
  matters for analysis, it needs to come from the same job metadata as
  the skip report.

## Naming: the same alias space as everything else

The HDF5 group name for each channel is the **exact same dotted alias
string** used as the key in `status.json`'s `status`/`status_channels`
blocks and as the `"alias"` field in `aliases.json` — e.g.
`bernina.mono.mono_und_energy`. All three files come from the same
`Namespace.alias` tree (`eco/aliases/aliases.py`'s `Alias.get_all()`), so a
channel's identity is consistent across all three without any name
translation. This is the property the escape integration below leans on.

## Registration in `scan_info_rel.json`

Same mechanism `aliases.json`/`status.json` already use
(`Scan.set_scan_parameter`, `eco/acquisition/scan.py:458`) —
`Daq.end_scan_monitoring()` calls:

```python
scan.set_scan_parameter("monitors", "aux/namespace_monitor.h5")
```

right after dispatching the (async) capture job, the same "register the
relative path now, the actual write/upload job finishes in the background
later" pattern the status/aliases callbacks already use. `copy_scan_info_to_raw`
then carries it into `scan_info_rel.json`'s `"scan_parameters"` block
exactly the way it already carries `"aliases"` and `"status"` — so a loader
already reading `scan_parameters["status"]`/`scan_parameters["aliases"]`
just needs to also check for `scan_parameters["monitors"]`, the same shape
(a path relative to the metadata file's parent, like the existing `"aux/" +
s["scan_parameters"]["status"]` handling in
`escape/swissfel/parse.py:574-588`).

Presence of the key does not guarantee the file exists yet at the instant
`scan_info_rel.json` is written (the write+upload job is async, seconds
behind) — same caveat that already applies to `"status"`/`"aliases"` there.

## Loading it in escape — recommended integration

**The clean way in, found while reading the source, not yet exercised**:
`escape.storage.dataset.DataSet.__init__` defaults to `mode="r"`, and when
given `results_file=<path to an h5 file>` its `_init_datasets()` already
calls `dict2structure({tname: self.datasets[tname]}, base=self)` for every
top-level name in the file (`escape/storage/dataset.py:238-293`). Since this
file's top-level group names **are** the dotted alias strings, simply doing

```python
mon = escape.DataSet(results_file=monitor_path)   # mode="r" is the default
```

already produces a `DataSet` whose own attributes are the correctly nested
`mon.bernina.mono.mono_und_energy` (etc.) tree — `dict2structure` splits on
"." internally (`escape/utilities.py:97-124`), so no manual restructuring is
needed at all. This is a materially cleaner integration than the existing
**"monitor data hack"** in `escape/swissfel/parse.py:609-634`, which:

- reads a **pickle** file (`aux/scan_monitor.pkl`) that nothing in eco's
  current status-server work produces (a different, older mechanism),
- wraps each channel by hand in a `MonitorData` object
  (`escape/swissfel/parse.py:642-653`) rather than a real
  `escape.ArrayTimestamps`, so it doesn't get any of `Array`'s own
  behaviour (slicing, `.scan`, plotting, `escaped()` wrapping, ...),
- and attaches the result as a **separate** top-level `ds.monitored_data`
  bucket, not merged into the same namespace as everything else — which is
  exactly the part this format is meant to fix (see below).

Recommended shape for the new code path, parallel to the existing status
block at `escape/swissfel/parse.py:573-607` (reading
`s["scan_parameters"]["status"]`): read
`s["scan_parameters"]["monitors"]` the same way, resolve it relative to the
metadata file's parent the same way `"status"` already is, and load it with
`escape.DataSet(results_file=...)` as above.

## The "superseded by bs" merge

The requirement: a component that was **also** captured as a proper
bsread/detector dataset for this run (already loaded into `d`/`ds`'s main
per-channel space earlier in `parse.py`, before the status/monitor blocks
run — see `escape/swissfel/parse.py:554-568`) should end up using **that**
dataset, not the CA-monitor one, once everything is merged — the bsread
data is pulse-synchronized and higher fidelity where it exists; the CA
monitor recording exists specifically to *fill the gap* for everything that
has no bsread channel, and should only be visible where nothing better was
co-loaded.

This falls out of `dict2structure`'s own merge rule almost for free, **if
integrated as one merge rather than two separate attachments**:
`dict2structure` only refuses to overwrite an existing attribute when that
attribute is *already a `StructureGroup`* (an intermediate branch); a plain
leaf value gets silently replaced by whatever `dict2structure` sees for that
same dotted path in a *later* call (`escape/utilities.py:119-123`). So:

- **Merge order decides precedence** - call `dict2structure` (or build one
  merged flat `{alias: value}` dict and call it once) with the CA-monitor
  data **first**, and the bs/detector data **second**, onto the *same*
  `StructureGroup` (i.e. `ds` itself, or a shared sub-branch of it — not two
  separate attachments the way `status_run_start`/`monitored_data` are kept
  apart today). Whichever alias paths exist in both get the bs value; paths
  that exist only in the monitor recording keep it.
- This only works cleanly if both sides are flattened to the **same alias
  key space** first. The bs/detector side's own keys today come through
  `alias_mappings` (`escape/swissfel/parse.py:540-568`) rather than
  necessarily being the same dotted `bernina.*` strings the monitor file
  uses — that mapping needs to be checked/aligned before merge, not assumed
  identical. This is the one piece of the puzzle not already fully
  cross-referenced by reading the source; worth confirming against a real
  run's `alias_mappings` before trusting the merge silently does the right
  thing.
- Whether the merge target is `ds` itself (flat, matching how `status_run_start`
  currently gets its own `ds.status_run_start` sub-tree rather than living at
  `ds` top level) or a new dedicated attribute (e.g. `ds.bernina` mirroring
  the actual namespace root name) is an escape-side API design choice, not
  dictated by the file format — this document only fixes what's *in* the
  file and its key space, not where escape chooses to hang the merged tree.

## Open questions / what to verify before relying on this

- **No real run has this file yet** (`start_scan_monitoring`/
  `end_scan_monitoring` are written but not wired into
  `Daq.callbacks_start_scan`/`callbacks_end_scan` as of this writing) - the
  format above is implemented and unit/live-tested on the eco side
  (`tests/test_status_server.py`'s `/recording/capture` tests; a live
  end-to-end test against the production server wrote a real 18 MB/323-channel
  file successfully), but nothing has round-tripped it through
  `escape.DataSet(results_file=...)` yet. Do that against a real file before
  writing the parse.py integration for real.
- **`alias_mappings` alignment** (previous section) - confirm the bs/detector
  side's keys actually match the monitor file's dotted alias strings before
  assuming the merge "just works".
- **The skip report and recording mode are not in the file** (see "File
  format" above) - if escape's loader wants to surface "323 of 9720 channels
  attached, 66680 updates throttled" type provenance, that has to come from
  the `/recording/*` job response at write time, not from the h5 file
  itself. Not currently persisted anywhere alongside the file - flag if
  that's wanted, it would need a small additional write (e.g. an HDF5
  attribute on the root group) on the eco side.
