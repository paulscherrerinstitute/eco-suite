---
added: 2026-10-25
tags: [plots, bs]
details:
---

# Stream plots reuse their window instead of piling up "Figure 7"s

`plot_hist()`, `plot_med()`, `plot()` and `plot_corr()` on a bs stream open a
window named after the data, e.g. `hist: mon_mono.intensity.stream
[SAROP21-PBPS103:INTENSITY]`. Calling it again refreshes that same window, at
the same place and size.

```python
mon_mono.intensity.stream.plot_hist()   # again: same window, fresh plot
```

`eco.detector.stream_plots.ENABLED = False` restores escape's behaviour.
