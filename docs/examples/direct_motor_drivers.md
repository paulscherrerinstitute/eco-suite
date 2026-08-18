# Direct motor drivers (Newport XPS · Schneider MCode)

Almost every motor in eco is reached through EPICS: a PV name in, a channel-access
call out. That is the right default on the beamline, but it assumes an IOC already
sits in front of the hardware. Sometimes there isn't one — a controller on the
bench, a spare stage on a laptop, a unit you are commissioning before its IOC
exists. For those cases eco ships two small **EPICS-independent** drivers that talk
to the hardware directly:

| Module | Hardware | Transport |
|--------|----------|-----------|
| {py:mod}`eco.devices_general.newport_xps` | Newport **XPS** (C/D/Q/RL) controllers | TCP socket, port 5001 |
| {py:mod}`eco.devices_general.schneider_mcode` | Schneider / IMS **MDrive · MForce · Lexium** steppers | Serial, MCode ASCII |

:::{note}
These are **helper libraries, not eco components.** They are deliberately not
re-exported from `eco.devices_general` and they are not
{py:class}`~eco.elements.adjustable.AdjustableGetSet` /
{py:class}`~eco.elements.assembly.Assembly` objects — you cannot drop an
`XPSAxis` into a scan the way you would a normal eco motor. Think of them as a
clean way to *reach a controller directly* when EPICS is not in the picture
(bench work, commissioning, diagnostics). Import them by their full package path.
:::

:::{warning}
The Schneider driver needs [`pyserial`](https://pypi.org/project/pyserial/)
(`pip install pyserial`); the Newport driver is standard-library only. Because
`pyserial` is optional, importing `eco.devices_general` does **not** pull either
driver in — you import the submodule you want explicitly.
:::

## Newport XPS

### The controller's protocol, in one paragraph

An XPS answers an ASCII socket API: you send a C-style function call
(`FunctionName(arg1,arg2,...)`) and it replies with
`<errorCode>,<value1>,...,EndOfAPI`, where `errorCode == 0` means success. Output
parameters appear in the request as the literal placeholders `double *` / `int *`
/ `char *`. Axes are grouped, and a positioner is addressed as
`GroupName.PositionerName`. The driver inserts the placeholders, checks the error
code (raising {py:class}`~eco.devices_general.newport_xps.XPSCommandError` with the
controller's own error text on failure) and parses the reply for you.

### Minimal runnable example

Open a session, identify the controller, bring a group up and move it. The
context manager owns the TCP socket, so the connection is always closed:

```python
from eco.devices_general.newport_xps import NewportXPS

with NewportXPS("192.168.0.254") as xps:      # controller IP / hostname
    print(xps.firmware_version())
    xps.initialize("Group1")                  # GroupInitialize
    xps.home("Group1")                        # GroupHomeSearch (blocks on the XPS)
    xps.move_absolute_and_wait("Group1", 10.0)  # mm or deg, per stage config
    print(xps.position("Group1"))             # -> [10.0]
```

Positions are always returned as a `list[float]` — a group can hold several
positioners — and are in whatever engineering units the stage is configured for
(mm for linear, deg for rotary). No scaling is imposed by the driver.

### Reading like a plain motor: `XPSAxis`

A single-axis group is the common case, and typing the group name into every call
gets old. {py:meth}`~eco.devices_general.newport_xps.NewportXPS.axis` binds one
`Group.Positioner` into an
{py:class}`~eco.devices_general.newport_xps.XPSAxis` that reads like a simple
motor:

```python
with NewportXPS("192.168.0.254") as xps:
    stage = xps.axis("Group1", "Pos")         # Group1.Pos
    stage.enable()
    stage.move_relative(1.5, wait=True)        # block until the move finishes
    print(stage.position, stage.limits)        # scalar float, (min, max)
    v, a, jmin, jmax = stage.get_velocity()    # S-curve profile
```

### Polling and the raw escape hatch

`is_moving` / `is_ready` classify the numeric group status for you; if you need a
call the convenience layer doesn't wrap,
{py:meth}`~eco.devices_general.newport_xps.NewportXPS.send` gives you the raw API
with the same error checking:

```python
with NewportXPS("192.168.0.254") as xps:
    while xps.is_moving("Group1"):
        ...
    xps.send("GroupStatusStringGet(11,char *)")   # -> ['Ready state from homing']
```

## Schneider / IMS MCode

### One drive on its own port

The stepper drives speak **MCode** ASCII over a serial line. For a single drive on
a dedicated port, {py:meth}`~eco.devices_general.schneider_mcode.SchneiderMotor.open_single`
opens the port and hands you the motor; the `with` block closes it again.
Positions and velocities are in the drive's native (micro)step units:

```python
from eco.devices_general.schneider_mcode import SchneiderMotor

with SchneiderMotor.open_single("/dev/ttyUSB0", baudrate=9600) as m:
    print(m.firmware_version())      # PR VR
    m.run_current = 50               # RC, % of drive maximum
    m.max_velocity = 200_000         # VM, steps/s
    m.move_relative_and_wait(1000)   # MR 1000, then poll MV until stopped
    print(m.position)                # PR P
```

The named properties (`run_current`, `max_velocity`, `acceleration`, `position`,
…) are thin wrappers over the corresponding MCode variables — assigning to them
issues the `set`, reading them issues `PR`.

### Several drives on one RS-485 bus (party mode)

Party mode multi-drops several drives on one bus, each with a one-character device
name. {py:class}`~eco.devices_general.schneider_mcode.SchneiderBus` owns the shared
port; every {py:class}`~eco.devices_general.schneider_mcode.SchneiderMotor` it
hands out prefixes its commands with its address and the I/O is serialised behind
a lock, so the same bus is safe to drive from several threads:

```python
from eco.devices_general.schneider_mcode import SchneiderBus

with SchneiderBus("/dev/ttyUSB0", baudrate=115200, party=True) as bus:
    x, y = bus.motor("1"), bus.motor("2")
    x.move_absolute(10_000)
    y.move_absolute(-5_000)
    bus.wait_all(x, y)               # block until both have stopped
```

### Reaching the rest of MCode

The convenience API covers the common moves and parameters; the *entire* MCode
variable and flag space stays reachable through the generic accessors, so an
unusual unit or an I/O point never leaves you stuck:

```python
m.set("S3", 16)          # configure I/O point 3
m.get("S3")              # -> "16"
m.command("PR C1")       # any raw command line, returns the reply text
```

:::{note}
MCode mnemonics differ slightly across MForce / MDrive / Lexium firmware. The
typed methods use the set common to those product lines; if a particular unit
disagrees, reach it with `get` / `set` / `command` and, if it recurs, add a thin
property alongside the existing ones.
:::

## From the command line

Both modules expose a `python -m` probe for bench testing without writing a
script — handy for a first contact with a new controller:

```bash
# Newport: firmware + a group's status/position, or a raw API call
python -m eco.devices_general.newport_xps 192.168.0.254 -g Group1
python -m eco.devices_general.newport_xps 192.168.0.254 "FirmwareVersionGet(char *)"

# Schneider: single drive, or a party-mode address, running an MCode command
python -m eco.devices_general.schneider_mcode /dev/ttyUSB0 -b 9600 PR VR
python -m eco.devices_general.schneider_mcode /dev/ttyUSB0 -b 115200 -a 1 PR P
```

## A note on verification

Both drivers are functionally tested against a **fake XPS TCP server** and a
**pseudo-terminal MForce responder** — echo stripping, party addressing, move and
status parsing, and error propagation are exercised, but no real controller was in
the loop. Point them at your actual hardware to confirm the exact command set of
your units before trusting an unattended move, and prefer the `_and_wait` helpers
(or an explicit `wait`) so a script never races ahead of the mechanics.

## Related

- {doc}`deltatau_config` — read/apply/verify PowerBrick (Delta Tau) configs.
- {doc}`../concepts` — the eco `Adjustable`/`Assembly` model these drivers sit
  *outside* of.
