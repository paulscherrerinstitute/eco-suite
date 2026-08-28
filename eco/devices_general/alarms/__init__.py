"""Bernina alarm-overview panels as eco assemblies.

Reverse-engineered from the caQtDM alarm panels under
``/sf/bernina/config/src/caqtdm/alarms/`` (entry point: the "Alarms overview"
launcher item in ``/sf/bernina/config/launcher/S_charts.json`` ->
``/sf/bernina/bin/alarms_caqtdm``), the same "modelled from a caqtdm panel"
approach as ``eco.devices_general.vacuum`` -- see that package's README.

See :mod:`eco.elements.alarm` for the generic `Alarm`/`AlarmGroup`/
`AlarmPanel` classes this is built on, and `panels.py` in this package for
the concrete Bernina/Papamoll panels and their provenance/caveats.
"""

from .panels import BerninaAlarmsOverview, PapamollAlarms
from eco.elements.alarm import Alarm, AlarmGroup, AlarmPanel, AlarmSeverity

__all__ = [
    "BerninaAlarmsOverview",
    "PapamollAlarms",
    "Alarm",
    "AlarmGroup",
    "AlarmPanel",
    "AlarmSeverity",
]
