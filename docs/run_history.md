# Looking back at runs

At the start of every run the DAQ client stores the status of the whole
namespace in the run's `aux/status.json`. `run_status` reads it back: for one
run, side by side for a few runs, or as a table and plot over a whole
beamtime.

## One run, or a few side by side

```python
kb.ver.run_status(1234)                       # like kb.ver's own display, for run 1234
kb.ver.run_status(1234, compare_live=True)    # next to the present values
kb.ver.run_status([-2, -1])                   # the last two runs side by side
kb.ver.apply_run_settings(1234)               # go back to that run's settings
```

The `x` column marks settings: what `apply_run_settings` would set. Which
items are settings is known to the device itself (`is_setting=True`), so this
works for every run, including those whose status file only stores the
status.

## Many runs: a table

```python
kb.ver.run_status.table()                         # all runs
kb.ver.run_status.table(runs=slice(-50, None))    # the last 50 runs
kb.ver.run_status.table(settings_only=True)       # only the settings
```

One row per run, a `time` column with the run's start, then one column per
name below the object, named relative to it. The object's own value is the
column `value`.

`run_status` in the namespace does the same for everything, for the
pgroup set in `config_bernina.pgroup`, with full names:

```python
run_status.table(prefix="bernina.mono", runs=slice(-20, None))
run_status.table(columns=["bernina.mono.energy", "scan_info.description"])
```

The table also has `scan_info.description`, `scan_info.adjustables` and
`scan_info.steps` from each run's scan info; ask for them by name with
`columns=[...]`, since `prefix=` and a device's own `run_status` leave them
out.

## Plotting over runs

```python
kb.ver.x.run_status.plot()                        # against time, run numbers on top
kb.ver.x.run_status.plot(x="run")                 # against run number, time on top
# several values of one device:
kb.ver.run_status.plot(columns=["x", "y"], runs=slice(-100, None))
run_status.plot(["bernina.mono.energy"])
```

Time is the primary axis by default, so nights and beam downtime show as gaps.
The second axis labels real runs at evenly spaced positions; it comes from
escape-fel's `add_crossing_secondary_axis`, which stays correct when runs
were written out of order.

## Where the table comes from

Every run's `status.json` stays the original record. The table is a cache
built from them, `res/run_data/run_table/<pgroup>_runs.parquet`, and each
question first adds the runs that are new or changed since:

| | p21582 (413 runs) | p23926 (494 runs) |
|---|---|---|
| first build, once per pgroup | 44 s | 29 s |
| a new session: open the cache, add new runs | 1.6 s | 1.6 s |
| further questions in the same session | 0.7 s | 0.8 s |
| cache size (status files: 5.3 GB) | 27 MB | 39 MB |

The cache can be deleted at any time; the next question rebuilds it. Status
files that cannot be read (p21582 has 11) are skipped and listed in
`run_status.store.unreadable`.

Status files are looked up in `res/run_data/daq/` first, which has every run,
then in `raw/`, which since 2025 has only some (for p23926, 40 of 494).

Run start times come from the scan's own start time when the status has it,
otherwise from the status file's modification time, which can be late for a
file written again afterwards.

```{admonition} Did you know?
:class: tip

`run_status.table(columns=["bernina.mono.energy", "scan_info.description"])`
puts each run's scan description next to the value, a quick way to find
which run was which.
```
