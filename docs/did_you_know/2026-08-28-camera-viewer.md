---
added: 2026-08-28
tags: [ui, cameras]
details: live_gui
---

# `cam.widget()` opens a live camera viewer

Basler and PCO cameras open a native Qt viewer (like pshell's Screen Panel):
contrast with a draggable histogram, ROI, marker and reticle, background
subtraction, frame averaging, a render-rate throttle, and colour images.

```python
cam_north.widget()                 # live viewer
cam_north.widget(dock_in=True)     # docked in the eco desktop
cam_north.widget(normal=True)      # the plain settings grid instead
```

All options: `help(cam_north._widget_viewer)`
