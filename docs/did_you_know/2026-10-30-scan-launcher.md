---
added: 2026-10-30
tags: [scans, queues, ui]
details: queues
---

# Build a scan by picking devices instead of typing them

The scan launcher is a form for `ascan`, `dscan`, `meshscan` and the other
scans, with device pickers for adjustables and counters. **Run** appends the
scan to a task queue, and the panel next to it pauses, resumes or stops it.
In eco desktop: **Tools → Scan Launcher**.

```python
scans.launcher()                  # queue="default"
```

More: {doc}`../queues`
