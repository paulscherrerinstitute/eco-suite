---
title: Motors and adjustables
words: [motor, motors, move, moving, mv, umv, mvr, limit, limits, tweak, jog, stage, stages, smaract, powerbrick, deltatau, motorrecord, offset, calibrate, home, homing, readback, identify]
---
- Every Adjustable has `mv(value)` (move and wait), `mvr(delta)` (relative),
  `umv(value)` (with live readback), `wm()` (where: the present value),
  `tweak(step)` (step from the keyboard), and `set_target_value(value)`,
  which returns a Changer with `.wait()` and `.stop()`.
- EPICS motors are `MotorRecord`s: `.stop()`, `.get_limits()`,
  `.set_limits(low_limit, high_limit)`, `.reset_current_value_to(value)` (redefine
  the present position, nothing moves), `.identify()` (which controller and
  drive are behind it), and components such as `.speed`, `.offset`,
  `.limit_low`, `.limit_high`, `.readback`.
- An Assembly (a whole device, e.g. `mono`, `kb`) is not itself an
  adjustable unless its class says so: move its components
  (`kb.ver.x.mv(...)`), and see them with `dev.get_tree()` or `dev.status()`.
- `mot.tweak(0.1, detectors=[mon_opt.intensity])` records and plots a
  detector while tweaking; arrow keys move and change the step, `q` ends.
- SmarAct controllers: `smaract_usd`, `smaract_user1`, `smaract_user2`, with
  channels `stage1` ... `stage18`. After a reconnect:
  `smaract_usd.stage4.startup_stage()` or `smaract_usd.startup([4, 5, 6])`;
  when channels do not respond, `smaract_usd.diagnose()` (moves nothing).
- Moves are real: say that a suggestion moves hardware.

```python
kb.ver.x.mvr(0.01)
smaract_usd.diagnose()
smaract_usd.startup([4, 5])
```
