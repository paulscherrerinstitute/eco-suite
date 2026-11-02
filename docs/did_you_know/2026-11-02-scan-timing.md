---
added: 2026-11-02
tags: [scans]
details: scanning
---

# Every scan tells you where its time went

Each step line ends with the share of time spent acquiring, moving,
settling and in callbacks, and the scan ends with a summary. If less than
half of the time is spent acquiring, eco says once what takes the rest.

```
Step 12 of 40   x @ 0.620 ; Ctrs daq done.  | acq 61% move 30% settle 0% cb 9%
```

The numbers are in `scan.timing`.

More: {doc}`../scanning`
