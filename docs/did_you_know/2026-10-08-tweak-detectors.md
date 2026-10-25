---
added: 2026-10-08
tags: [tweak, alignment]
details: tweak
---

# `tweak` can record and plot a detector while you move

```python
mot.tweak(0.1, detectors=[mon_opt.intensity])
```

Each tweak position is read out and plotted live against the position, and
the points are kept as a DataFrame afterwards. In a notebook or the desktop
console a tweak panel opens instead of the keyboard loop (force one with
`ui="terminal"|"qt"|"ipy"`).

More: {doc}`../tweak`
