# Adaptive scans

A normal scan measures every point of a fixed list. Most of those points are
often uninteresting: flat background far from a peak, an edge that is only
somewhere in the middle. An **adaptive scan** starts with a coarse survey and
then puts the next points where the data so far says they help most: on the
flanks of the peak, along the edge, across the oscillation. The scan itself is
an ordinary eco step scan, recorded by the DAQ as usual. Only *which* point
comes next is decided while it runs.

The strategies come from `escape.adaptive_scan` (escape-fel 0.2.17 or newer).

## Try it without hardware

`sim` in the bernina namespace holds simulated signals (a peak, an edge, a
gradient, a 2-D peak and pump-probe oscillations) with motors and detectors
that behave like real ones. See `eco/devices_general/simulated_signals.py`.

```python
scans.adaptive_scan(
    [sim.peak.x, -2, 3, 0.02],     # axis and grid: start, end, step
    N_pulses=0.2,                  # exposure per point
    feedback=sim.peak.signal,      # what the strategy steers by
    max_points=40,
)
```

The first 15 points are an even survey. After that, almost every point goes
to the peak. Two plots are shown: the usual counter plot, and the strategy's
own plot with the data and what it concludes from them.

## Syntax

```python
scans.adaptive_scan(*axes, N_pulses=..., feedback=..., budget=... or max_points=...,
                    strategy="modelfree", batch=5, exposure="fixed", motion="auto",
                    plot=True, counters=[], queue=None, ...)
```

| Argument | Meaning |
|---|---|
| axes | one or two, written as for [`scans.scan`](scanning.md): `[adjustable, start, end, step]` (a float is the step size, an int the number of intervals) or `[adjustable, [p0, p1, ...]]`. This is the **grid**: points are only ever taken from it, so its spacing is the finest resolution. It may be unevenly spaced. |
| `N_pulses` | exposure per point, in the unit of the first counter (shots for the DAQ, seconds for a monitor counter); the other counters follow it, see [scanning](scanning.md) |
| `feedback` | the live signal the strategy steers by: a detector, or a counter, see below |
| `budget` | time limit: seconds or `"90s"`, `"10min"`, `"1h"` |
| `max_points` | point limit. Give `budget`, `max_points` or both. |
| `strategy` | what to look for, see the table below |
| `batch` | points planned at a time (default 5) |
| `exposure` | `"fixed"`: always `N_pulses`. `"auto"`: 0.5 to 8 times `N_pulses`, chosen per point. Or a list of allowed values. |
| `motion` | `"auto"` (default): learned from the scan's own moves. Or escape `Axis` objects in seconds, or `None` for "moves cost nothing". |
| `counters`, `settling_time`, `description`, `return_at_end`, `queue` | as for every other scan |

| `strategy=` | Looks for | Axes |
|---|---|---|
| `"modelfree"` | anything; refines where straight lines between the points fit worst (peaks, kinks) | 1 |
| `"gradient"` | where the signal changes fastest | 1 |
| `"uniform"` | nothing: coarse to fine everywhere, a fair baseline | 1 or 2 |
| `"edge"` | an edge: its position and width | 1 |
| `"oscillation"` | a pump-probe trace: time zero, step, 1-3 damped oscillations | 1 |
| `"peak2d"` | a 2-D peak: centre, widths, tilt, asymmetry | 2 |

A ready escape strategy object can be passed instead of a name, and
`strategy_options={...}` passes options to the strategy class (for example
`n_initial` for the survey size).

Examples:

```python
# time zero and oscillation of a pump-probe signal, with exposure chosen per point
scans.adaptive_scan([las.delay, -1, 6, 0.02], N_pulses=100, feedback=mon_opt,
                    strategy="oscillation", exposure="auto", budget="20min")

# a 2-D peak on a 0.05 mm grid
scans.adaptive_scan([xrd.x, -1, 1, 0.05], [xrd.y, -1, 1, 0.05], N_pulses=100,
                    feedback=diode, strategy="peak2d", max_points=150)

# an uneven grid: dense where the edge is expected
scans.adaptive_scan([mot, [-2, -1, -0.5, -0.2, -0.1, 0, 0.1, 0.2, 0.5, 1, 2]],
                    N_pulses=100, feedback=diode, strategy="edge", max_points=30)
```

## How it works

<svg viewBox="0 0 770 410" width="100%" role="img" aria-label="Adaptive scan architecture" style="max-width:770px;font-family:sans-serif;font-size:13px">
  <defs>
    <marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="currentColor"/>
    </marker>
  </defs>
  <g fill="none" stroke="currentColor" stroke-opacity="0.6">
    <rect x="20" y="20" width="330" height="40" rx="6" fill="rgba(127,127,127,0.10)"/>
    <rect x="20" y="100" width="330" height="285" rx="8" fill="rgba(127,127,127,0.05)"/>
    <rect x="40" y="135" width="290" height="36" rx="5" fill="rgba(127,127,127,0.10)"/>
    <rect x="40" y="190" width="290" height="36" rx="5" fill="rgba(127,127,127,0.10)"/>
    <rect x="40" y="245" width="290" height="36" rx="5" fill="rgba(127,127,127,0.10)"/>
    <rect x="40" y="330" width="290" height="36" rx="5" fill="rgba(127,127,127,0.10)"/>
    <rect x="390" y="135" width="170" height="44" rx="5" fill="rgba(127,127,127,0.10)"/>
    <rect x="390" y="195" width="170" height="44" rx="5" fill="rgba(56,132,255,0.18)"/>
    <rect x="390" y="255" width="170" height="44" rx="5" fill="rgba(56,132,255,0.18)"/>
    <rect x="600" y="135" width="150" height="164" rx="5" fill="rgba(56,132,255,0.18)"/>
  </g>
  <g fill="none" stroke="currentColor" stroke-width="1.3" marker-end="url(#ah)">
    <line x1="185" y1="60" x2="185" y2="97"/>
    <line x1="185" y1="171" x2="185" y2="188"/>
    <line x1="185" y1="226" x2="185" y2="243"/>
    <path d="M40,348 H28 V153 H38"/>
    <line x1="330" y1="208" x2="388" y2="160"/>
    <line x1="330" y1="208" x2="388" y2="217"/>
    <line x1="330" y1="263" x2="388" y2="277"/>
    <line x1="475" y1="239" x2="475" y2="253"/>
    <line x1="560" y1="277" x2="598" y2="230"/>
    <path d="M675,299 V348 H332"/>
  </g>
  <g fill="currentColor" stroke="none">
    <text x="185" y="45" text-anchor="middle">scans.adaptive_scan( axes, feedback=…, budget=… )</text>
    <text x="34" y="122" font-weight="bold">StepScan: the ordinary step loop</text>
    <text x="185" y="158" text-anchor="middle">1  move to the next queued point</text>
    <text x="185" y="213" text-anchor="middle">2  acquire with all counters</text>
    <text x="185" y="268" text-anchor="middle">3  end-of-step callbacks</text>
    <text x="185" y="353" text-anchor="middle">queued points (and N_pulses each)</text>
    <text x="390" y="125" font-weight="bold">counters, in this order</text>
    <text x="475" y="153" text-anchor="middle">DAQ</text>
    <text x="475" y="170" text-anchor="middle" font-size="11">data to disk, not for steering</text>
    <text x="475" y="213" text-anchor="middle">feedback counter</text>
    <text x="475" y="230" text-anchor="middle" font-size="11">monitor or bs stream, live</text>
    <text x="475" y="273" text-anchor="middle">AdaptiveDriver</text>
    <text x="475" y="290" text-anchor="middle" font-size="11">last; acquires nothing</text>
    <text x="482" y="250" font-size="11">mean ± error</text>
    <text x="675" y="157" text-anchor="middle" font-weight="bold">escape strategy</text>
    <text x="675" y="185" text-anchor="middle">add(x, y, error)</text>
    <text x="675" y="210" text-anchor="middle">fit (once per batch)</text>
    <text x="675" y="235" text-anchor="middle">plan(next batch)</text>
    <text x="675" y="262" text-anchor="middle" font-size="11">uses motion model and</text>
    <text x="675" y="277" text-anchor="middle" font-size="11">time per exposure</text>
    <text x="577" y="244" font-size="11">point</text>
    <text x="500" y="342" text-anchor="middle" font-size="11">append the next batch when the queue runs out</text>
    <text x="20" y="402" font-size="11">Blue: added for adaptive scans. Everything else is the ordinary scan, unchanged.</text>
  </g>
</svg>

1. `scans.adaptive_scan` builds an ordinary `StepScan` with the strategy's
   survey points queued, and adds two counters: the feedback counter (if it is
   not already one of the counters) and the `AdaptiveDriver`, last.
2. The scan runs its normal loop: move, acquire with all counters (the DAQ
   records as always), end-of-step callbacks.
3. The driver's end-of-step callback asks the feedback counter for this step's
   mean and error and adds the point to the strategy.
4. When the queue runs out, the strategy fits its model and plans the next
   batch, in the fastest visiting order. The driver appends it to the running
   scan. When the budget is used up, it appends nothing and the scan ends
   normally.

Everything else (DAQ runs, run table, elog, `pause`/`stop`, queues, the end
question) works as in every scan. After the scan, `scan.adaptive.strategy`
holds all points and the fitted result, for example
`scan.adaptive.strategy.summary()`.

## Feedback: live data, never the DAQ

The DAQ writes its files during the run, so its data cannot steer the scan.
The strategy steers by a live signal over the same step instead. It is
recorded over roughly the same time window, but it is not matched pulse by
pulse to the DAQ data. Analyse the DAQ data afterwards as usual.

| `feedback=` | Uses |
|---|---|
| a detector | monitored through a `CounterValue`, mean of the samples in the step's acquisition window |
| a `CounterValue` or `BsStreamCounter` | the counter you made; `(counter, "channel")` picks a channel |
| `f(scan)` returning `(value, error)` | anything you can compute |
| nothing | the first counter in `counters` that can give feedback |

For a ratio like signal / I0, give a bs counter a single stream made from the
ratio (`sig / i0`), so the ratio is taken shot by shot.

## What you see while it runs

- **The step line** ends with where the time went so far, for every scan,
  adaptive or not:
  `Step 12 of 40   x @ 0.62 ; Ctrs ... done.  | acq 61% move 30% settle 0% cb 9% (planning 4%)`.
  The time of the last step is not in it yet: its end-of-step callbacks
  are still running when the line is printed.
- **A warning**, once per scan, when less than half of the time goes into
  acquiring, naming what takes the time; and when the strategy's planning takes
  more than 20 %.
- **The step count and the progress bar** follow the current estimate: the
  queued points plus what the budget still allows, re-estimated after every
  step from the average step time.
- **The motion model**, as soon as there are enough moves to determine it:
  `adaptive: motion model for x (fitted to 4 moves): Axis(speed=1.99, overhead=0.35) in seconds`.
  The strategy uses it to avoid long moves that are not worth their time.
  Copy it into `motion=[Axis(...)]` to give it from the start next time
  (`from escape.adaptive_scan import Axis`).
- **The strategy's plot**, redrawn with each batch, next to the counter plot.

## Two axes stay on a grid

A 2-D adaptive scan only picks points of the grid given by its two axes. The
scan records the grid as for a mesh scan (`grid_specs`, and each step's
`grid_index`). escape places the data on the grid from each step's
`grid_index`, so the result loads and plots like a mesh scan whose cells are
partly empty (NaN). A point can be measured more than once. On the grid, the
last value of such a cell is shown for now. With a `BsStreamCounter` among the
counters, cells are not revisited, because that counter bins by grid cell.

Freer layouts (points anywhere, averaging of revisits, scattered 2-D data) are
a proposal for now: [Multi-dimensional scan data](multidim_scan_data.md).

## Time units

The strategies weigh exposure time against move time. Here the time unit is
one `N_pulses` unit. eco measures how many seconds one unit takes from the
acquisitions, and converts the motion model (always given and printed in
seconds) to it. The dead time per point (settling and callbacks) is measured
too and counted as overhead per point.

## Does it change normal scans?

Adaptive scans are a separate method with their own counter. Normal scans
are not affected, apart from three small changes that apply to every scan:

- the step line shows the duty cycle, with the summary line and the warning
  described above;
- the progress bar and `number_of_steps` are computed as steps done + steps
  queued. For a fixed scan that is the same number as before;
- `CounterValue` and `BsStreamCounter` gained a `step_feedback()` method.
  Nothing calls it unless an adaptive scan does.
