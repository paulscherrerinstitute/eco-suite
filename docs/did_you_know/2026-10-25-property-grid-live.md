---
added: 2026-10-25
tags: [ui, widgets]
details: widget_views
---

# Device panels never freeze during a move, and show what each row is doing

In `.widget()` property grids (Qt, JupyterLab/Voila and Tk), a click starts
the move in the background, so 🛑 Stop works while it moves. A status dot per
row shows its state: green monitored, blue polled, orange moving, red failed
or blocked (hover for the reason). The motor tweak step is the IOC's TWV.

```python
xrd.widget()
```

More: {doc}`../widget_views`
