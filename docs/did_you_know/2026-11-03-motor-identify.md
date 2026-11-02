---
added: 2026-11-03
tags: [motors]
details: powerbrick_parameters
---

# What kind of motor is that, and how does it home?

`identify()` on any motor record tells you its controller (PowerBrick,
Schneider MDrive/MForce/LMM, SmarAct, XPS, ...), the drive model, whether it
has an encoder, and what HOMF/HOMR really does there. It asks the IOC and the
drive, and only reads.

```python
mono.theta.identify()
```

More: {doc}`../powerbrick_parameters`
