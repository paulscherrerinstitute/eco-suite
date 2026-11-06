---
added: 2026-11-03
tags: [motors]
details: powerbrick_parameters
---

# What drives this motor? Ask `motor.controller`

Every motor record has `controller`: the controller type, the physical
link, and what homing does on it. It is looked up the first time you ask
and kept for the session, so status displays and scans never wait for it.

```python
mono.theta.controller.type
mono.theta.controller.homing
```

More: {doc}`../powerbrick_parameters`
