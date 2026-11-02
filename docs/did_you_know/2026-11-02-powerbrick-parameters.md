---
added: 2026-11-02
tags: [motors, powerbrick]
details: powerbrick_parameters
---

# See the PowerBrick's own view of a motor

Motors driven by a PowerBrick now have `cnf_pb`: the controller's
parameters for that axis (amplifier fault, following error, closed loop,
in-position band, ...), read in one batched request per controller and only
when first needed. Controllers behind sf-gw stay locked (🔑) until you log in
with your own account.

```python
mono.theta.cnf_pb
kbver.bend1.cnf_pb.unlock()
```

More: {doc}`../powerbrick_parameters`
