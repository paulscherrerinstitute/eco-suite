---
added: 2026-08-28
tags: [ui, vacuum, alarms]
details: schematic_panels
---

# Live, clickable schematics: `prepump.show()` and `alarms.show()`

Some assemblies draw themselves as a live SVG schematic, a scriptable
in-session version of the caQtDM panels. Clicking a symbol acts on it:

```python
prepump.show()                   # valves, gauges, pumps, line states
prepump.pump_down("line1_usd")   # interlocked sequence, waits for the target pressure
alarms.show(live=True); alarms.worst_severity()
```

More: {doc}`../schematic_panels`
