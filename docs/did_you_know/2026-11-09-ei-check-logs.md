---
added: 2026-11-09
tags: [ei, logbook]
details: ask_eco
---

# Which logbook code no longer works?

`%ei --check-logs` reads the code in the indexed logbooks against eco as it
is today, without running it, and lists what no longer fits: renamed
components, removed methods, wrong keywords, how often each occurs and
where it was used last.

```python
%ei --check-logs 90
```

More: {doc}`../ask_eco`
