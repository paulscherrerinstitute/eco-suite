---
added: 2026-10-06
tags: [scans, analysis]
details: scanning
---

# 1-D scans fit the peak for you

Every 1-D detector scan now fits a peak or step while it runs, prints the
result at the end, and stores it with the scan and in the elog scan table.
The live plot gets escape's Fit / Peak buttons.

```python
s = mon_opt.intensity.scans.dscan(mot, -1, 1, 40, 100)
s.peak_analysis.center(), s.peak_analysis.fwhm(), s.peak_analysis.snr()
```

More: {doc}`../scanning`
