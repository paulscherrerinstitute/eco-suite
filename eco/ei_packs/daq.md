---
title: DAQ, runs and run history
words: [daq, run, runs, run number, pgroup, pedestal, jungfrau, jf, recorded, recording, run_status, run table, runtable, status.json, aux, retrieve]
---
- `daq` is the SwissFEL DAQ client; scans record through it by default. A
  recording without moving anything is `scans.acquire(N_pulses)`.
- The pgroup comes from `config_bernina.pgroup`. Run numbers count up per
  pgroup: `daq.get_last_run_number()`, `daq.get_next_run_number()`.
- Jungfrau pedestals: `daq.take_pedestal()`; detectors available and
  running: `daq.get_JFs_available()`, `daq.get_JFs_running()`.
- Every run stores the status of the whole namespace (`aux/status.json`).
  Look back with `run_status`: `dev.run_status(1234)` (one run),
  `dev.run_status([-2, -1])` (the last two side by side),
  `dev.run_status.table()` and `.plot()` over many runs;
  `dev.apply_run_settings(1234)` sets a device back to a run's settings (moves
  hardware). For everything at once: `run_status.table(prefix="bernina.mono")`.
- Run data is analysed with escape (escape-fel), not with eco devices.

```python
kb.ver.run_status([-2, -1])
run_status.table(columns=["bernina.mono.energy", "scan_info.description"])
```
