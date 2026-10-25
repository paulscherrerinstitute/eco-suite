# Live schematic panels: prepump, alarms, beamline

Some assemblies can draw themselves as a clickable SVG schematic that updates
live from the control system. These are a scriptable, in-session version of
the corresponding caQtDM panels. Open one with `.show()`:

```python
prepump.show()                 # prepump P&ID: valves, gauges, pumps, per-line state
alarms.show(live=True)         # Bernina alarm overview, green/red per channel
beamline.svg_panel(live=True)  # X-ray beamline layout
```

Where the panel appears is chosen automatically: a native Qt window in a
terminal session, inline in a notebook, docked in the desktop. Force it with
`backend="window"|"inline"|"sidecar"`. Clicking a symbol acts on that
component in your live session (e.g. toggling a valve).

## Prepump system

`prepump` holds the beamline's pre-vacuum lines (`line1_usd`, `line2_lic`,
`line3`, `line4`) together with the roots pump and the `gp` gauge. Pump-down
and vent are scripted sequences with the interlocking done for you:

```python
prepump.pump_down("line1_usd")   # or prepump.line1_usd.pump_down()
prepump.vent("line2_lic")
prepump.isolate_all()
prepump.check()                  # lists missing or failed components at every depth
```

`pump_down` closes the line's vent valve and isolates the *other* lines (so
they are not pumped against a rising `gp`). It then opens the line's pump
valve and waits until both `gp` and the line's own gauge reach the target
pressure. Afterwards each other line is restored to what it was doing before.
It returns `True` on success and `False` on timeout (`timeout=700` s by
default).

The building blocks (`VacuumGauge`, `Valve`, `TurboPump`, `IonPump`, ...)
are described in `eco/devices_general/vacuum/README.md` in the repository.

## Alarm panels

`alarms` (the Bernina overview), `papamoll_alarms_35fs` and
`papamoll_alarms_100fs` model the caQtDM alarm overviews. Each channel
reports `OK`, `WARNING` or `ALARM`:

```python
alarms.worst_severity()          # one value for the whole panel
alarms.show(live=True)
```

Build your own panel from a list of channels with
`eco.elements.alarm.AlarmPanel`. See `eco/devices_general/alarms/README.md` in
the repository.

## Your own assembly

Any `Assembly` that implements `_widget_svg_panel(self, live=False, **kwargs)`
gets this `.show()` behaviour for free. See {doc}`widget_containers` for
composing panels from existing widgets instead.
