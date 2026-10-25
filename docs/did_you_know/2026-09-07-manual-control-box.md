---
added: 2026-09-07
tags: [hardware, motors]
details:
---

# Drive any adjustable from the handheld control box

The touchscreen box with joystick and knob walks your session's own device
tree and jogs whatever you select. The operator accepts the connection on the
box itself.

```python
bernina.manual_control_box.start()     # .status(), .stop()
bernina.manual_control_box.manual()    # the full manual
```

`eco-box-server start -b` keeps the box served without an open session.
