---
added: 2026-08-18
tags: [hardware, motors, expert]
details: examples/deltatau_config
---

# PowerBrick motor configs are scriptable, with a dry run

Read, edit, apply and verify Delta Tau PowerBrick configurations from Python.
`apply` checks that the config is allowed on that host and can preview what
it would do; `check` compares the live controller with the expected config:

```python
from eco.motion.deltatau_config import apply, check
apply(host="EXP1", config="stageXYZ", dry_run=True)
ok, report = check("EXP1", "stageXYZ")
```

More: {doc}`../examples/deltatau_config`, {doc}`../deltatau_servo`
