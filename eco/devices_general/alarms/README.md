# alarms — Bernina alarm-overview panels as eco assemblies

Reusable eco `Assembly` wrappers for the caQtDM alarm-overview panels used
operationally at Bernina, reverse-engineered from the panels under
[`/sf/bernina/config/src/caqtdm/alarms/`](/sf/bernina/config/src/caqtdm/alarms/),
starting from the "Alarms overview" launcher entry (Strip charts menu,
`/sf/bernina/config/launcher/S_charts.json` -> `/sf/bernina/bin/alarms_caqtdm`
-> `alarms.ui`).

## Why

Each of those panels is a grid of caQtDM `alarm_channel.ui` includes, each
carrying a macro of the form `NAME=<label>,PV=<pv>,MIN=<min>,MAX=<max>` — a
channel name, the PV it reads, and a soft in-range/out-of-range threshold set
in the panel config itself (not necessarily the PV's own EPICS alarm limits).
This package turns that same idea into eco objects, generically:

- `eco.elements.alarm.Alarm` — one channel: wraps a `Detector` (typically a
  PV) plus that `[min, max]`, and reports `AlarmSeverity.OK`/`WARNING`/`ALARM`
  — or falls back to the wrapped detector's own EPICS severity
  (`get_severity()`) when no explicit range is given.
- `eco.elements.alarm.AlarmGroup` — one labelled section (a caQtDM group box,
  e.g. "Oscillator"), a flat collection of named `Alarm`s.
- `eco.elements.alarm.AlarmPanel` — a whole panel: a collection of
  `AlarmGroup` sections, with a `.show(live=True)` clickable colour-coded SVG
  (green/red per channel) via the same `_svg()` hook
  `PrepumpSystem`/`XrayBeamline` use — see
  `eco/devices_general/vacuum/prepump.py`.

## The concrete panels

| caQtDM panel | eco class | source |
|---|---|---|
| `alarms.ui` (the main overview) | `BerninaAlarmsOverview` | `panels.py` |
| `papamoll_alarms_overview_26l_dean_1um_35fs.ui` ("-35 fs" button) | `PapamollAlarms("26l_dean_1um_35fs")` | `panels.py` |
| `papamoll_alarms_overview_26h_orr_510nm_100fs.ui` ("-100 fs" button) | `PapamollAlarms("26h_orr_510nm_100fs")` | `panels.py` |

Channel data (name/PV/min/max per section) lives in `data.py`, extracted
verbatim from the source `.ui` files. **Only the two Papamoll panels wired
into the main overview's "Expert" buttons are modelled here** — the other
`papamoll_alarms_overview_*.ui` variants sitting in the same source directory
(different pulse-duration/NOPA/THz configs) are not yet encoded; add them to
`data.py` the same way if/when needed. See `data.py`'s docstring for a caveat
on section-label provenance (a handful of source panels place their section
header off to the side of their data block; labels were cross-checked by
majority vote across all `papamoll_alarms_overview_*.ui` variants where a
channel group repeats).

## Minimal example

```python
from eco.devices_general.alarms import BerninaAlarmsOverview, PapamollAlarms

alarms = BerninaAlarmsOverview(name="alarms")
alarms.swissfel.electron_beam.get_current_value()   # -> the PV's live value
alarms.swissfel.electron_beam.get_severity()         # -> AlarmSeverity.OK / .ALARM
alarms.worst_severity()                              # -> worst AlarmSeverity across the whole panel
alarms.status()                                       # every channel, like any eco Assembly
alarms.show(live=True)                                # clickable colour-coded panel (Jupyter or in_window=True)

pump = PapamollAlarms("26l_dean_1um_35fs", name="papamoll_35fs")
pump.oscillator.get_severities()                      # {"fwhm": AlarmSeverity.OK, ...}
```

## Compose your own panel

`AlarmPanel`/`AlarmGroup` take plain data, so a new panel is just a list of
`(section_label, channels)`:

```python
from eco.elements.alarm import AlarmPanel

my_panel = AlarmPanel(
    [
        ("Cryo", [
            {"name": "temp_sample", "pv": "SARES20-CRYO:TEMP", "min": 10, "max": 15},
            {"name": "level_ln2", "pv": "SARES20-CRYO:LN2LEVEL", "min": 20, "max": 100},
        ]),
    ],
    name="my_panel",
)
```

Pass `"detector": <a Detector instance>` instead of `"pv"` for a channel
that isn't a plain PV (a computed `DetectorGet`, another device's readback).
