---
added: 2026-11-05
tags: [motors, smaract]
details: smaract
---

# Bring SmarAct stages back after a reconnect, several at once

`startup_stage()` sets the sensor type and frequency, calibrates and homes a
stage, waiting for each step. `startup()` does several stages in parallel.
`diagnose()` finds out why channels don't respond, without moving anything.

```python
smaract_usd.startup([4, 5, 6])
smaract_usd.diagnose()
```

More: {doc}`../smaract`
