---
added: 2026-09-04
tags: [timing, calibration]
details:
---

# Timetool calibration is one call

`tt_kb.calibrate()` scans the delay in both directions, fits, plots, saves raw
data and figure, posts to the elog, and then asks whether to apply the new
calibration. Feedback is switched off for the scan and always switched back
on, even if something fails.

```python
tt_kb.calibrate()                                               # full run, asks before applying
tt_kb.calibrate(update_pipeline_config=False, ask_apply=False)  # measure only
```

All options: `help(tt_kb.calibrate)`
