---
title: Scans and queues
words: [scan, scans, ascan, dscan, a2scan, mesh, meshscan, snakescan, acquire, counter, counters, exposure, steps, queue, queued, "%queue", adaptive]
---
- A scan is a call on `scans`; there is no scan class to write. The argument
  after the positions, `exposure_def`, is the exposure of the *first* counter
  in its own unit: shots for the DAQ (default), seconds for an EPICS monitor
  counter. The other counters follow its window.
- `scans.ascan(adj, start, end, N_intervals, exposure_def)`: absolute;
  `N_intervals=20` gives 21 points. `scans.dscan(...)` is the same relative to
  the present position and returns there. `scans.ascan_position_list(adj,
  [p0, p1, ...], exposure_def)` for explicit positions.
- Any shape: `scans.scan((adj1, 0, 1, 10), (adj2, -1, 1, 20), exposure_def=100)`
  is a mesh (last axis fastest); a list `[(adj2, 0, 1, 20), (adj3, 5, 6, 20)]`
  is one dimension whose axes move together. In a spec an int is a number of
  intervals, a float a step size.
- `scans.acquire(exposure_def)` records without moving anything (one run).
- `counters=[...]` replaces the default counters, so list `daq` too if the
  DAQ should record: `counters=[daq, mon_opt.intensity]`. Detectors can be
  given directly. `scans.append_counter(det)` adds a live counter to every
  scan; `scans.clear_auxiliary_counters()` removes them again.
- `description="..."` names the run. `queue=True` (or a queue name) test-builds
  the scan and appends it to a task queue instead of running it now.
- Queues: `%queue <statement>` runs statements one after another on a
  worker thread; the prompt stays free. Every task is test-run when queued
  (names, attributes, signatures, limits). `%queue` alone shows the queue,
  `%queue pause` / `resume` / `stop`; a failing task pauses the queue.
- A scan moves hardware and starts the DAQ: say so in the answer.

```python
scans.ascan(mono.energy, 7000, 7100, 20, 100, description="energy scan")
scans.scan((xrd.nu, 10, 12, 20), exposure_def=100)
scans.dscan(lxt, -1e-12, 1e-12, 40, 200, counters=[daq, mon_opt.intensity])
```
