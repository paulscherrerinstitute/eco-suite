# PowerBrick parameters and module loading

Most Bernina motors are driven by Delta Tau PowerBrick controllers. The
EPICS motor record shows position, limits and speed. The controller has much
more: amplifier and following-error faults, closed-loop state, the in-position
band, encoder loss. eco shows those controller parameters next to the motor
record, as `cnf_pb`, and can load the diffractometers' exchangeable modules
(Kappa, SampleStage) onto their PowerBrick.

## The parameters of a motor: `cnf_pb`

```python
mono.theta.cnf_pb                       # status of the axis on its controller
mono.theta.cnf_pb.Motor_AmpFault()      # one parameter (read)
mono.theta.cnf_pb.Motor_InPosBand.mv(0.5)   # a writable one (gpascii Motor[n].InPosBand)
```

The names are the controller's gpascii names, with the axis number left out
and `.` written as `_`: `Motor[6].AmpFault` becomes `Motor_AmpFault`. They
appear in the motor's status like every other component.

Which motors have it: a motor record built with `pb_conf="auto"` gets
`cnf_pb` only if the record is really driven by a PowerBrick. That is the
case if it is served by a `*-CPPM-*` IOC, or, behind the channel-access
gateway, if its OUT link names the PPMAC port. The motor records of the mono,
the offset mirrors, the slits, the KB mirrors, the spectrometer and the
diffractometers' flight-tube slits use `pb_conf="auto"`. Motors on other
controllers (MForce, ...) simply have no `cnf_pb`.

What happens behind it:

- **Nothing on startup.** Building `cnf_pb` contacts nothing. The first read,
  normally the first `status()`, finds the controller and the axis and opens
  the SSH session to the controller.
- **The axis is the one the IOC drives**: it is read from the motor record's
  OUT link (`@asyn(PPMAC1,6)` → axis 6), not from a hardcoded table.
- **The controller** is the record's channel-access server, or, when channel
  access only shows a gateway, the IOC host from iocinfo.psi.ch.
- **One request per controller.** All parameters of all axes of a controller
  are read in one batched request and kept for 1 s, so a `status()` of a
  device with ten PowerBrick motors costs one round trip per controller.
  A write clears that cache.
- **An offline record fails fast**: after a failed lookup, reads fail at once
  for 30 s instead of waiting each time.

## Controllers behind sf-gw: locked until you log in

Some controllers (the KB mirrors' `SAROP21-CPPM-*`, on the machine network)
can only be reached through the `sf-gw` jump host. Their parameters are for
development, so eco does not read them by default: their status shows a
🔑 key and nothing else.

```python
kbver.bend1.cnf_pb               # 🔑 locked (sf-gw), .unlock()
kbver.bend1.cnf_pb.unlock()      # log into sf-gw with *your* account
kbver.bend1.cnf_pb.lock()        # close it again
```

`unlock()` logs into sf-gw as you. ssh asks for the password itself, so eco
never sees it, and with a Kerberos ticket it doesn't ask at all. On the shared
gac-bernina account it asks for your PSI account name first. The login stays
open as a private connection through which the PowerBrick sessions are
tunnelled, and it closes with `lock()`, at the end of the session, or an hour
after its last use. One `unlock()` opens every controller behind sf-gw.

## Loading a module onto the diffractometer's PowerBrick

The Kappa and the SampleStage are run by one dedicated PowerBrick per
diffractometer (`SARES21-CPPM-XRD1` for `xrd`, `SARES22-CPPM-GPS1` for `gps`).
It boots without a motor configuration on purpose, since a wrong one can
destroy motors. `module_ioc` loads one:

```python
xrd.module_ioc                       # host, and which configuration is loaded
xrd.module_ioc.load("kappa")         # asks for confirmation, copies, restarts the IOC (~30 s)
xrd.module_ioc.load("stage")
xrd.module_ioc.list_motors()         # motor records of the running IOC
xrd.module_ioc.open_loop(xrd.eta_kap, xrd.kappa)   # these motors without encoder
```

It runs the controls group's own `ConfigMotorIOC.py`, so hosts,
configurations and the open-loop table stay the ones the controls group
maintains. eco adds:

- a confirmation with the script's safety password before anything is changed;
- eco's write gates for the module's motors (access control, task-queue
  claims);
- a refusal while one of the module's motors is moving;
- a check over channel access afterwards that the configuration's motor
  records came up.

## What is behind a motor record: `identify()`

`identify()` works on every motor record, not only PowerBrick ones. It
reports which controller and drive the record runs, whether there is an
encoder, and what homing actually does:

```python
mono.theta.identify()                    # one motor record
mono.theta.identify(deep=False)          # channel access and IOC files only

from eco.devices_general.motor_identify import identify_motor, identify_motors, survey_table
identify_motor("SARES20-DSD:MOTOR_DSDX")
print(survey_table(identify_motors(["SARES20-DSD:MOTOR_DSDX", ...])))   # one line per motor
```

The result names each finding together with where it came from (`.evidence`):

- **Controller:** PowerBrick, Schneider, SmarAct MCS, Newport XPS or
  piezosystem jena dDrive. It is found from the asyn port in the record's
  OUT link, looked up in the IOC's startup script under `/ioc` (mounted on
  the consoles). The same script gives the physical link (TCP host:port or
  serial tty).
- **Drive:** a Schneider drive is asked through the IOC's own `_get`
  passthrough. Its part number tells an MDrive Plus, an MForce and a Lexium
  Motion Module apart; the drive also reports its encoder, how its inputs are
  set up and the homed flag. A PowerBrick axis is read over gpascii; behind
  sf-gw it stays locked until `unlock()`, as above.
- **Homing:** what HOMF/HOMR does on this controller. On a Schneider drive it
  searches for a home *switch*; on a PowerBrick it captures whatever
  `CaptCtrl`/`CaptFlagSel` select. The report also lists the other
  references the hardware has (index mark, limit switch) and whether a PSI
  homing script (`<prefix>:HOMING`) is attached.

It only reads: no motion command, no configuration write. `deep=True` (the
default) also asks the drive, about 0.3 s per value on a Schneider drive.
`identify_motors()` runs many records in parallel.

```{admonition} Did you know?
:class: tip

On a Schneider drive, HOMF cannot do index-mark homing: the record has no
way to send `HI`. That is why the separate PSI homing scripts exist, and
`identify()` shows whether one is attached.
```

See also: {doc}`deltatau_servo` for the servo parameters and
{doc}`examples/deltatau_config` for reading, editing and applying whole
controller configurations.

```{admonition} Did you know?
:class: tip

Once a controller's parameters have been read, the whole controller is
read in one request: a `status()` of the mono costs one round trip, not one
per motor.
```
