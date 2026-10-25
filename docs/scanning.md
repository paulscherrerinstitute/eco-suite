# Scanning: mesh scans, co-moving axes, bs streams and peak analysis

eco has no scan class to subclass. A scan is a call on `scans` (or on a
detector's own `.scans`). This page covers what scans can do since the
2026 rework.

## One call for any scan shape: `scans.scan`

Each positional argument is one **dimension** of the scan grid. A dimension is
either one adjustable or a list of adjustables that **move together**:

```python
# 2-D mesh: mot2 is scanned for every mot1 position (last axis fastest)
scans.scan((mot1, 0, 1, 10), (mot2, -1, 1, 20), N_pulses=100)

# a2scan-style: mot2 and mot3 move together, 21 points
scans.scan([(mot2, 0, 1, 20), (mot3, 5, 6, 20)], N_pulses=100)

# both: a mesh of mot1 against the co-moving pair
scans.scan((mot1, 0, 1, 10), [(mot2, 0, 1, 20), (mot3, 5, 6, 20)], N_pulses=100)
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
scans.meshscan((m1, 0, 1, 10), (m2, 0, 1, 10), N_pulses=100, counters=[c])
c.grid().plot()                                   # N-D binned, live while running
c.last_arrays["SAROP21-PBPS133:INTENSITY"]        # escape Array of the last scan
c.stored_filename
```

Options: `reduction=np.mean`, `live_plot=True`, `store=True`,
`storage_dir="auto"` (the current directory), `peak_analysis=True`,
`timeout=10`. If storing fails, a message is printed and the scan carries on.

## See also

- {doc}`status_server`: every scan also records the full update history of
  the namespace (`namespace_monitor.ixp.h5`).
