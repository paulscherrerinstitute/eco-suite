---
added: 2026-11-03
tags: [runs, data]
details: run_history
---

# Every device's values over a whole beamtime, as a table

`run_status.table()` gives one row per run and one column per value below a
device, from the status every run stores. `.plot()` draws them over time or
run number. The first call reads all runs (half a minute); after that, only
new runs are added, and a question takes about a second.

```python
kb.ver.run_status.table(runs=slice(-50, None))
kb.ver.x.run_status.plot()
```

More: {doc}`../run_history`
