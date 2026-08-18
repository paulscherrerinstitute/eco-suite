# motors — EPICS-independent motor drivers

Modern, self-contained Python 3.12+ drivers that talk to the hardware
**directly**, with no EPICS / channel-access dependency. Extracted/rebuilt from
the intent of the old `motor_newport*` references in the legacy XPP package
(the original direct drivers were never present — everything went through EPICS
PVs).

They ship as ordinary submodules of `eco.devices_general`, so import them by
their package path. They are deliberately **not** re-exported from
`eco/devices_general/__init__.py`: that keeps `pyserial` an optional dependency
(only the Schneider driver needs it) and keeps these low-level drivers separate
from the EPICS-backed `eco` `Adjustable`/`Assembly` objects — they are helper
libraries you reach for directly, not beamline components wired into a session.

| Module | Hardware | Transport | Dependency |
|--------|----------|-----------|------------|
| [`schneider_mcode.py`](schneider_mcode.py) | Schneider / IMS **MDrive · MForce · Lexium MDrive** steppers | Serial (RS-232/422/**485 party mode**), MCode ASCII | [`pyserial`](https://pypi.org/project/pyserial/) |
| [`newport_xps.py`](newport_xps.py) | Newport **XPS** (C/D/Q/RL) motion controllers | TCP socket, port 5001 Group API | stdlib only |

Both modules: full type hints, dataclass config, context-manager lifecycle,
structured exceptions, thread-safe I/O, a low-level escape hatch **and** typed
convenience methods, plus a `python -m` CLI probe for bench testing.

## Install

```bash
pip install pyserial          # only needed for the Schneider serial driver
```

## Schneider (serial MCode)

```python
from eco.devices_general.schneider_mcode import SchneiderMotor, SchneiderBus

# One drive on a dedicated port:
with SchneiderMotor.open_single("/dev/ttyUSB0", baudrate=9600) as m:
    print(m.firmware_version())
    m.run_current = 50            # % of drive max
    m.max_velocity = 200_000     # steps/s
    m.move_relative_and_wait(1000)
    print(m.position)

# Several drives sharing one RS-485 party-mode bus:
with SchneiderBus("/dev/ttyUSB0", baudrate=115200, party=True) as bus:
    x, y = bus.motor("1"), bus.motor("2")
    x.move_absolute(10_000)
    y.move_absolute(-5_000)
    bus.wait_all(x, y)
```

Anything in the MCode variable/flag space that isn't wrapped is still reachable:

```python
m.set("S3", 16)         # configure I/O point 3
m.get("S3")             # -> "16"
m.command("PR C1")      # raw command, returns the reply text
```

CLI probe:

```bash
python -m eco.devices_general.schneider_mcode /dev/ttyUSB0 -b 9600 PR VR      # single drive
python -m eco.devices_general.schneider_mcode /dev/ttyUSB0 -b 115200 -a 1 PR P   # party-mode addr '1'
```

## Newport (network XPS)

```python
from eco.devices_general.newport_xps import NewportXPS

with NewportXPS("192.168.0.254") as xps:
    print(xps.firmware_version())
    xps.initialize("Group1")
    xps.home("Group1")
    xps.move_absolute_and_wait("Group1", 10.0)   # mm or deg per stage config
    print(xps.position("Group1"))

# Single-axis stages read like a plain motor:
with NewportXPS("192.168.0.254") as xps:
    stage = xps.axis("Group1", "Pos")            # Group1.Pos
    stage.enable()
    stage.move_relative(1.5, wait=True)
    v, a, jmin, jmax = stage.get_velocity()
    print(stage.position, stage.limits)
```

Raw escape hatch for any XPS API call:

```python
xps.send("GroupStatusStringGet(11,char *)")      # -> ['...']
```

CLI probe:

```bash
python -m eco.devices_general.newport_xps 192.168.0.254 -g Group1
python -m eco.devices_general.newport_xps 192.168.0.254 "FirmwareVersionGet(char *)"
```

## Notes

- **Units.** Schneider positions/velocities are in the drive's native
  (micro)step counts; XPS positions are in the engineering units configured per
  stage (mm / deg). No unit scaling is imposed by the drivers.
- **MCode variability.** Command mnemonics differ slightly across MForce/MDrive
  firmware. The convenience methods use the common set; anything else is a
  one-liner via `get`/`set`/`command`.
- **Verified without hardware.** Both drivers are functionally tested against a
  fake XPS TCP server and a pseudo-terminal MForce responder (echo stripping,
  party addressing, move/status parsing, error propagation). Point them at real
  hardware to confirm the exact command set of your units.
