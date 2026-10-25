---
added: 2026-08-28
tags: [ui, widgets]
details: widget_containers
---

# Build your own panel from existing eco widgets

Stack assembly grids, adjustable controls, detector readouts and camera viewers
into one panel. The same code works in the Qt desktop and in a notebook:

```python
from eco.widgets.containers import stack, aligned, assembly_widget, adjustable_control, viewer
stack(assembly_widget(prepump), adjustable_control(mot), aligned(viewer(cam_north), align="center"))
```

More: {doc}`../widget_containers`
