# Scanning: mesh scans, co-moving axes, bs streams and peak analysis

eco has no scan class to subclass. A scan is a call on `scans` (or on a
detector's own `.scans`). This page covers what scans can do since the
2026 rework.

## One call for any scan shape: `scans.scan`

Each positional argument is one **dimension** of the scan grid. A dimension is
either one adjustable or a list of adjustables that **move together**:

```python
# 2-D mesh: mot2 is scanned for every mot1 position (last axis fastest)
scans.scan((mot1, 0, 1, 10), (mot2, -1, 1, 20), exposure_def=100)

# a2scan-style: mot2 and mot3 move together, 21 points
scans.scan([(mot2, 0, 1, 20), (mot3, 5, 6, 20)], exposure_def=100)

# both: a mesh of mot1 against the co-moving pair
scans.scan((mot1, 0, 1, 10), [(mot2, 0, 1, 20), (mot3, 5, 6, 20)], exposure_def=100)
```

There is no limit on the number of dimensions. `scans.a2scan` and
`scans.meshscan` are thin wrappers around this call.

**Step specification** (the part after the adjustable):

| Spec | Meaning |
|------|---------|
| `(start, end, 10)` | an **int** is the number of intervals, giving 11 points |
| `(start, end, 0.1)` | a **float** is the step size |
| `([p0, p1, p2, ...],)` | an explicit list of positions |
| `(-1e-12, ('lin', 0.1e-12), 2e-12, ('log', 2e-12), 1e-6)` | piecewise linear/logarithmic spacing between anchor points (int = number of intervals, float = interval size) |

The adjustables in one co-moving group must have the same number of points.

Other options: `scanning_order="first_fastest"`, `repetitions=n`, `counters=[...]`,
`settling_time=`, and `queue=True` (or a queue name), which test-builds the
scan and appends it to a task queue instead of running it now -- see
[Queues](queues.md).

## Automatic peak analysis

Every 1-D detector scan opens a live plot with escape's **Fit**, **Peak** and
**Peak params** buttons, and fits a peak or step as the scan runs:

```python
s = mon_opt.intensity.scans.dscan(mot, -1, 1, 40, 100)
# ... Peak analysis: peak at ..., FWHM ..., S/N ...
s.peak_analysis.center()
s.peak_analysis.fwhm()
```

The fields are readable while the scan is still running. A copy goes into the
scan's `scan_info["peak_analysis"]`, and so into the elog scan table:

| Field | Meaning |
|-------|---------|
| `valid` | a result exists |
| `is_peak` | a peak (or dip), not a step |
| `center` | halfway between the half-maximum crossings, or the step centre |
| `fwhm` | full width at half maximum, or the ~12–88 % step width |
| `peak_x`, `peak_y` | the position and value of the extremum |
| `height` | peak height above the baseline, negative for a dip |
| `offset`, `gradient` | the baseline |
| `snr` | signal over noise (scatter-only traces read below ~3) |
| `n_points` | points used |

Only 1-D scans with a single channel are analysed; mesh scans are not. To
switch the analysis off or tune it, pass `peak_analysis=False` or a dict
(`n_bg`, `bg_model="linear"|"offset"`, `fixed_offset`,
`mode="auto"|"peak"|"step"`) to the counter. Needs escape-fel ≥ 0.2.14.

## Scanning bs streams directly

Every escape bs `Stream`, including derived ones, can be used as a scan
counter as it is:

```python
mon_opt.intensity.stream.scans.ascan(mot, 0, 1, 20, 100)
(s1 / s2).scans.dscan(mot, -1, 1, 20, 100)          # ratios, .element(0), ...
```

Underneath this uses a `BsStreamCounter`: the beam-synchronous data is binned
per scan step, reduced (mean by default), live-plotted for 1-D scans, and
stored at the end of the scan as escape Arrays in a
`<timestamp>.<name>.esc.h5` file. A plain `mon_opt.intensity.scans` still uses
the EPICS-based counter unless the detector was created with
`bs_scan_mode=True`.

For mesh scans, or to keep the data at hand afterwards, create the counter
explicitly:

```python
from eco.detector.bs_counter import BsStreamCounter

c = BsStreamCounter("SAROP21-PBPS133:INTENSITY", name="i0")
scans.meshscan((m1, 0, 1, 10), (m2, 0, 1, 10), exposure_def=100, counters=[c])
c.grid().plot()                                   # N-D binned, live while running
c.last_arrays["SAROP21-PBPS133:INTENSITY"]        # escape Array of the last scan
c.stored_filename
```

Options: `reduction=np.mean`, `live_plot=True`, `store=True`,
`storage_dir="auto"` (the current directory), `peak_analysis=True`,
`timeout=10`. If storing fails, a message is printed and the scan carries on.

## Detectors as counters

`counters=` (which replaces the default counters, so list the DAQ too) also
takes detectors directly; the scan picks the counter for each:

```python
scans.ascan(mot, 0, 1, 20, 100, counters=[daq, mon_opt.intensity, diode])
```

- a counter (the DAQ, a `BsStreamCounter`, ...) is used as it is;
- a bs detector (`DetectorBsStream`, an escape `Stream`) gets its live bs
  counter, built once and kept on the detector, so every scan reuses the same
  subscription. If the dispatcher does not have the channel and the detector
  has an EPICS PV, the PV is used instead (with a note);
- all other detectors share **one** EPICS monitor counter (`CounterValue`).

The same detector given twice, or given both in `counters=` and as an adaptive
scan's `feedback=`, is read by one counter. An object can choose its own
counter by defining `_scan_counter()`.

## The first counter sets the exposure, the others follow

`exposure_def` (the argument after the positions, e.g. the `100` in
`ascan(mot, 0, 1, 20, 100)`) is the exposure of the **first** counter, in its
own unit: shots for the DAQ, seconds for a monitor counter, shots for a bs
counter. `N_pulses=100`, the DAQ's own keyword, works as well.
`help(scans.ascan)` says what it means for the scans' first default counter.
The other counters do not read it. They collect for exactly as long as the
first one acquires:

- the first counter records its window per step: the DAQ its pulse ids
  (`start_id` to `start_id + N - 1`) and times, a monitor counter its times, a
  bs counter the pulse ids and times of its shots;
- a monitor counter following it cuts its monitors to that time window;
- a bs counter following it waits until the last of those pulses has arrived
  (bs data come some time after the pulse) and keeps exactly those shots.

A bs counter collects only while a step acquires and keeps exactly the shots
it was reduced from (the first counter's pulses, or its own first N), so what
it stores, plots and analyses contains no shots from moves or settling.

So `ascan(mot, 0, 1, 20, 100, counters=[daq, mon_opt.intensity, diode])` takes
100 DAQ shots per step, the bs counter the same 100 pulses, and the monitor
counter the same time. The window of each step is kept in its step info
(`scan.scan_info["scan_step_info"][i]["window"]`). A counter that cannot follow
(e.g. a second DAQ) still acquires `exposure_def` in parallel, as before.

A counter can be given its own acquisition with `counter_options`, one dict
(or `None`) per entry of `counters`, by position. It then acquires in parallel
with its own keywords instead of following:

```python
scans.ascan(mot, 0, 1, 20, 100, counters=[daq, diode],
            counter_options=[None, {"collection_time": 0.5}])
```

The keywords are the counter's own: `Npulses` for the DAQ and bs counters,
`collection_time` (seconds) for a monitor counter.

## Live counters next to the DAQ for every scan

`scans` is set up for the DAQ. To have a live counter in every scan, e.g. for a
live plot of the DAQ's own shots:

```python
scans.append_counter(mon_opt.intensity)   # follows the DAQ
scans.auxiliary_counters
scans.clear_auxiliary_counters()          # back to the DAQ alone
```

A scan given `counters=` uses exactly those instead.

```{admonition} Did you know?
:class: tip

Every scan also records where each step measured: `mesh_specs` in
`scan.scan_info["scan_parameters"]` and a `mesh_index` per step. That works
for adaptive scans and co-moving axes too ({doc}`multidim_scan_data`).
```

## Where the time goes

Every step line ends with how the scan's time has been spent so far, and the
scan ends with a summary:

```
Step 12 of 40   x @ 0.620 ; Ctrs daq done.  | acq 61% move 30% settle 0% cb 9%
Scan timing: 40 steps in 52.3 s (1.31 s/step): acq 60% move 31% settle 0% cb 9%
```

`acq` is the acquisition with all counters, `move` the moves of the
adjustables, `settle` the `settling_time`, `cb` the per-step callbacks. If
less than half of the time goes into acquiring, a warning names where it
goes, once per scan. Thresholds: `eco.acquisition.scan_timing`. The numbers
are in `scan.timing`.

## Resuming a scan that stopped early

A scan that ends before its last step (an error in a step, Ctrl-C, a stop
from a queue or the GUI) says so in red, with the command that continues it
from the next step:

```
Scan aborted or crashed after 12 of 40 steps; you may be able to resume it with
  > scans.acquiring_scan.scan_all()
```

The command is the scan's own name in the session, checked before it is
shown: a scan started from a detector reads e.g.
`mon_opt.intensity.scans.acquiring_scan.scan_all()`, and once a newer scan has
replaced it as `acquiring_scan` no command is offered. Resuming continues the
same scan: the counters' monitors and live plots carry on, and the data of
all steps end up together. If the scan moved back to its start values when it
ended, the next step moves to where it left off first.

```{admonition} Did you know?
:class: tip

You can try any of these scans without a beamline: `sim.peak.x` and
`sim.peak.signal` are a motor and a detector with a peak between them
({doc}`simulated_targets`). Move `sim.peak.center` while the scan runs.
```

## See also

- {doc}`adaptive_scans`: scans that choose their next points from the live
  data.

- {doc}`optimize`: maximise a signal over several coupled axes.

- {doc}`status_server`: every scan also records the full update history of
  the namespace (`namespace_monitor.ixp.h5`).
