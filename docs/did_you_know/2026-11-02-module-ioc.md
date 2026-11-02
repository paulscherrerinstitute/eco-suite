---
added: 2026-11-02
tags: [motors, diffractometer, powerbrick]
details: powerbrick_parameters
---

# Load the Kappa onto the diffractometer from eco

`xrd.module_ioc` and `gps.module_ioc` load the Kappa or the SampleStage
configuration onto the diffractometer's module PowerBrick, with the controls
group's own script. eco asks for confirmation, refuses while a module motor
moves, and checks afterwards that the motor records came up.

```python
xrd.module_ioc                  # which configuration is loaded
xrd.module_ioc.load("kappa")
```

More: {doc}`../powerbrick_parameters`
