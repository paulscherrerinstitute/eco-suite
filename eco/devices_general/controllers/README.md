# controllers — Bernina controller boxes as one lazy, deselectable branch

Groups hardware-**controller** PVs (a motor-controller box, a WAGO I/O unit,
...) into one eco `Assembly` per physical box, matching the single caqtdm
panel each box actually has (`ESB_MFORCE_motors.ui`,
`SARES20_CWAG_GPS01-OVERVIEW.ui`, ...) — see `bernina_controllers.py`'s
docstring for the concrete boxes and their source panels.

## Why a separate package (and a separate namespace branch)

Today, PVs belonging to one physical controller are wired up **one channel at
a time**, under whatever function-specific name each experiment needed, with
no single object for the box itself. E.g. `SARES20-MF1:MOT_13`/`_14` show up
in `eco.bernina.bernina` as `x_target_totem`/`y_target_totem` (inside some
unrelated sample-stage assembly), `SARES20-XPS1:MOT_1`/`_2` as
`slit_hor`/`slit_ver`, and `SARES20-CWAG-GPS01`'s three known thermosensor
channels are hand-built inside `SampleHeaterJet` while the WAGO unit's own
analog in/out are two more, separately-registered top-level namespace names
(`analog_inputs`/`analog_outputs`) — 13 of its 16 thermosensor channels have
no eco object at all. **None of that changes here** — every existing,
working, function-named component stays exactly as it is.

This package adds a **second, physical-box-shaped view** of the same
hardware, opt-in and additive, so `bernina.controllers.mforce_mf1.axis_13`
and the pre-existing `bernina.<wherever>.x_target_totem` are two names for
the same PV, and a controller has one identity (`.ioc_status()`,
`.restart_IOC()`, ...) instead of being scattered.

## Wiring into `bernina.py`

```python
from eco.devices_general.controllers import build_bernina_controllers
namespace.append_obj(build_bernina_controllers, lazy=True, name="controllers")
```

One lazy top-level namespace entry -- `build_bernina_controllers()` itself is
cheap (just registers more lazy proxies, touches no PV) so it's safe as an
`append_obj(..., lazy=True)` factory. Being one top-level name also means it
can be left out of a default startup selection like anything else, via
`namespace.select_required_names()` / `namespace.required_names([...])` (see
`eco.utilities.config.Namespace`) — "deselecting" the whole branch.

## Laziness, at every level

1. **The branch itself**: lazy at the `Namespace.append_obj(lazy=True)`
   level, same as any other top-level component.
2. **Each controller inside it**: `LazyControllers.add()` registers each box
   behind its own `eco.utilities.config.Proxy` (the same tab-completion-safe
   lazy proxy the top-level namespace uses) — touching
   `bernina.controllers.wago_gps01` does not build `mforce_mf1`.
3. **IOC identification**: `IOCMixin.ioc()` never runs at construction time;
   the `iocinfo.psi.ch` lookup only happens on first call to `.ioc_status()`
   / `.ioc_host_ping()` / `.restart_IOC()`, and is cached after that.

Only step 4 is *not* lazy per-attribute: once a controller box itself is
built, all of its axes/channels are constructed together (matching every
other multi-channel Assembly in this codebase, e.g. `SmaractController`,
`DigitizerIoxos`) — an unwired channel just fails softly
(`Assembly._append`'s default `optional=True`) rather than blocking the box.

## Per-controller IOC methods

Every class here mixes in `eco.epics_utils.ioc_mixin.IOCMixin`:

```python
wago = bernina.controllers.wago_gps01   # builds just this controller
wago.ioc_status()      # {"ioc": ..., "console": "host:port", "console_reachable": True, ...}
wago.ioc_host_ping()   # True/False/None -- pure TCP liveness probe, no side effects
wago.restart_IOC()     # prompts y/N, then sends procServ's restart hotkey -- REAL, no undo
```

IOC name/console are found via `eco.epics_utils.iocinfo.find_ioc()` against
the controller's `prefix`, unless pinned explicitly (see
`MOTOR_CONTROLLER_BOXES`'s `ioc_name` column for the two boxes where it's
already known). `restart_IOC()` is confirmed operationally only for the
Bernina MForce IOCs as of 2026-08-18 — see `iocinfo`'s module docstring.

## Adding another controller box

Same shape either way — an `Assembly` subclass mixing in `IOCMixin`, plus one
line in `bernina_controllers.py`:

```python
MOTOR_CONTROLLER_BOXES["my_box"] = ("SARES20-XYZ", 12, None)  # motor box
WAGO_CONTROLLER_BOXES["my_wago"] = ("SARES20-CWAG-OTHER", None)  # WAGO unit
```

or, for a controller family that isn't a plain motor box, write a small
`Assembly` + `IOCMixin` class (see `wago_controller.py` for the shortest
example) and `controllers.add(YourClass, ..., name="...")` it in
`build_bernina_controllers()`.
