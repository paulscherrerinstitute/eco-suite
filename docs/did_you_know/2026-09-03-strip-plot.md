---
added: 2026-09-03
tags: [plots, monitoring]
details: examples/archiver_stripchart
---

# Any device has a live strip plot that keeps running during a `umv`

```python
mot.strip_plot()                                  # readback; "More channels..." adds the rest
archiver.strip_plot(channels=["SARFE10-PBPG050:HAMP-INTENSITY-CAL"], window=120)
```

The plot runs in its own process, so it keeps updating while your session is
busy. The handle can `.record()` and `.to_dataset()`.

More: {doc}`../examples/archiver_stripchart`
