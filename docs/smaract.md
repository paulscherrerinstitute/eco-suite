# SmarAct stages: start-up and diagnosis

Bernina's SmarAct MCS controllers are `smaract_usd` (SARES20-MCS1),
`smaract_user1` (MCS2) and `smaract_user2` (MCS3), with up to 18 channels
each, `stage1` to `stage18`. After a controller or a stage has been
(re)connected, a stage has to be brought into a defined state before it
moves reliably. When channels don't respond at all, `diagnose()` looks
for the reason without moving anything.

## Bringing stages up after a reconnect

```python
smaract_usd.stage4.startup_stage()              # one stage, waits until done
smaract_usd.startup([4, 5, 6])                  # several stages at the same time
smaract_usd.startup(["stage4", "stage5"], home=False)
```

`startup_stage()` runs four steps in sequence:

1. set the sensor type (default: the type the controller reports now);
2. set the maximum closed-loop frequency (default: the present setting);
3. calibrate the sensor and wait until the calibration has finished;
4. home the stage and wait until it is referenced.

`calibrate=False` or `home=False` skip a step. `sensor_type=` and
`max_frequency=` set other values. `calibration_timeout` (60 s) and
`homing_timeout` (120 s) limit the waits. With `wait=False` it runs in the
background and returns the thread; `thread.join()` gives the result or
raises the error.

`startup()` on the controller does several stages at once. It waits for all
of them, even when one fails, so one broken channel does not leave the
others half done. Then it raises if any failed; the result names each
stage with its outcome.

`home()` and `calibrate_sensor()` now wait as well: until the action has
started and has finished, with a timeout. Before, `home()` on a stage that
was already homed returned at once.

## Why does a channel not work? `diagnose()`

```python
smaract_usd.diagnose()                          # all channels, EPICS only
smaract_usd.stage1.diagnose()                   # one channel
smaract_usd.diagnose(query_controller=True)     # also ask the controller itself
```

It prints a report per channel and returns it as a list of dicts:

- whether the IOC has polled the axis at all since it started;
- the motor record's status flags (MSTA) and alarms;
- the sensor type;
- whether failing channels cluster on one 3-channel module of the
  controller, which points to the module rather than the stages.

`query_controller=True` also asks the controller directly, through the
IOC's own connection, with read-only *get* commands (number of channels,
status, sensor, position). Nothing is set and nothing moves. This works with
first-generation MCS controllers only; MCS2-generation controllers are
recognised and skipped.

What it can tell is limited: the controller's answers say little more than
whether a sensor is present. Why the IOC stops polling an axis is only
visible in the IOC's console messages. The module docstring of
`eco.motion.smaract_diagnose` records what was observed on MCS1.

```{admonition} Did you know?
:class: tip

A channel the IOC never polls was, on MCS1, one without a sensor when the
IOC started: the driver leaves such axes out, while the controller module
itself was fine. `diagnose()` shows exactly this pattern.
```

See also: {doc}`powerbrick_parameters` for `motor.identify()`, which tells
what controller and drive are behind any motor record.
