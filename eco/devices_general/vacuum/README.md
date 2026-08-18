# vacuum — EPICS vacuum components as eco assemblies

Reusable eco `Assembly` wrappers for the SwissFEL vacuum-control-system (VCS)
EPICS devices: **valves, turbo pumps, ion getter pumps, gauges and fore-vacuum
(pre-)pumps**. They were reverse-engineered from the caqtdm panels under
`/sf/vcs/config/qt`, starting from the endstation overview
[`S_VCS_SARES21-ES.ui`](/sf/vcs/config/qt/S_VCS_SARES21-ES.ui).

## Why

That overview panel is built by *including* a handful of reusable widgets, once
per physical device, each carrying a `DEV=<tag>` macro:

```
caInclude  S_VCS__PG1.ui      DEV=SARES21-VPIG140-400   # ion getter pump
caInclude  S_VCS__Gauge0.ui   DEV=SARES21-VMFR140-500   # gauge
caInclude  S_VCS__valve_h1.ui DEV=SARES21-VVPG141-250   # valve
caInclude  S_VCS__VPT-4.ui    DEV=SARES21-VPTM140-700   # turbo pump
caInclude  S_VCS__VPR1.ui     DEV=SARES21-VPFO140-750   # fore-vacuum pump
```

Each widget's small overview face plus its "click to open" **detail subpanel**
together define a fixed PV interface per device family. This package turns each
of those families into one eco class, so the devices are scriptable and appear
in `get_status`, `set`/`get_current_value`, the archiver helpers, etc. — the
same way every other eco component does.

## The mapping

| caqtdm widget (+ detail subpanel) | eco class | detail as |
|---|---|---|
| `S_VCS__Gauge0.ui` (+ `S_VCS__gauge.ui`) | `VacuumGauge` | folded into the object |
| `S_VCS__valve_h1/v2.ui` (+ `S_VCS__Valve_ErrorMessage.ui`) | `Valve` | folded into the object |
| `S_VCS__FastValve.ui` | `FastValve` (status-only) | folded into the object |
| `S_VCS__VPR1.ui` | `PrePump` | — |
| `S_VCS__VPT-4.ui` (+ `S_VCS__TurboPump.ui`) | `TurboPump` | `.details` sub-assembly |
| `S_VCS__PG1.ui` (+ `S_VCS__VarianPump.ui`) | `IonPump` | `.controller` sub-assembly |

Where a device family has a large detail subpanel (turbo pump, ion pump), the
everyday controls live on the object directly and the rich telemetry is grouped
into a **nested sub-assembly** — the eco equivalent of clicking the subpanel
open. Small devices (gauge, valve) fold everything onto the object.

## Minimal example — one device

```python
from eco.devices_general.vacuum import VacuumGauge, Valve, TurboPump, IonPump

g = VacuumGauge("SARES21-VMFR140-500", name="gauge")
g.pressure()            # -> pressure, mbar (Detector convention)
g.get_current_value()   # same: the gauge reads like a plain detector
g.status()              # e.g. "SENSOR OFF"

v = Valve("SARES21-VVPG140-240", name="valve")
v.open(); v.close()     # request open / close
v.get_current_value()   # -> "open" / "closed" / "moving" from the PLC readbacks

tp = TurboPump("SARES21-VPTM140-700", name="turbo")
tp.speed()                     # rotor speed, Hz
tp.details.temp_motor()        # everything the detail subpanel showed
tp.start(); tp.reset_error()

ip = IonPump("SARES21-VPIG140-400", name="ionpump")
ip.pressure()                        # controller pressure display
ip.controller.serial_number()        # 4UHV controller detail
ip.controller.protection.set_target_value("ON")   # HV enable
```

Each PV suffix maps to a named component, so tab-completion (`g.<TAB>`) and
`g` (its `get_status` repr) list exactly what the panel showed.

## Compose — a station

Group same-type devices with `Assembly._append`, exactly like the overview
panel groups includes. A device that fails to connect is skipped (appends are
optional by default), so a partial station still builds:

```python
from eco import Assembly
from eco.devices_general.vacuum import Valve, TurboPump, IonPump

class MyStation(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(Valve, "SARES21-VVPG140-240", name="gate_valve")
        self._append(TurboPump, "SARES21-VPTM140-700", name="turbo")
        self._append(IonPump, "SARES21-VPIG140-400", name="ionpump")
```

> Only the generic device classes ship here. The concrete SARES21 endstation
> composition (which tag is which physical device, with human-friendly names)
> is intentionally left out for now — build it in a session/beamline module by
> instantiating these classes with the tags listed in `S_VCS_SARES21-ES.ui`.

## PV interface reference

Suffixes appended to each device tag (`$(DEV)`), as read from the panels:

- **Gauge** (`VMFR`/`VMCP`/`VMCC`): `:PRESSURE` `:STATUS` `:ONOFF` `:ONOFFG`
  `:PLC_SETPOINT` `:GAUGE-TYPE` `:CONTROLLER`
- **Valve** (`VVPG`/`VVPP`): `:REQUEST` `:PLC_OPEN` `:PLC_CLOSE` `:PLC_ER_MESSAGE`
- **PrePump** (`VPFO`): `:REQUEST` `:PLC_READY` `:SET`
- **TurboPump** (`VPTM`): controls `:START` `:VENT` `:TPSET` `:PSSET` `:SPEED`
  `:SETHZ` `:ACK-ERR`; readbacks `:HZ` `:80` `:100` `:ERROR`; details `:ACC`
  `:DRV-CURR` `:DRV-VOLT` `:DRV-PWR` `:TEMP-*` `:OP-HRS-*` `:PUMP-CYCLES`
  `:ELEC-NAME` `:FW-VERSION` `:HW-VERSION` `:ERROR-HIST1..10`
- **IonPump** (`VPIG`/`VPNG`, 4UHV controller): `:ONOFF` `:PRESS-DISP` `:CURRENT`
  `:VOLTAGE` `:HV` `:OPMODE` `:ERROR` `:PLC_SETPOINT`; controller `:PROT(-SET)`
  `:STEP(-SET)` `:CABLE-ILK` `:REMOTE-ILK` `:4UHV-ILK` `:CONTROLLER` `:4UHV-SN`
  `:4UHV-TYPE` `:PUMPTYPE`
