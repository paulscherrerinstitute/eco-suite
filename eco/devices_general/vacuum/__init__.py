"""EPICS vacuum components as eco assemblies.

Reusable, beamline-agnostic building blocks for the SwissFEL VCS vacuum
controls, reverse-engineered from the caqtdm panels under ``/sf/vcs/config/qt``
(entry point ``S_VCS_SARES21-ES.ui``). Each caqtdm reusable widget maps to one
class here; the widget's detail subpanel maps to a nested sub-assembly so all
the information is represented in the object:

===========================  ==========================  =========================
caqtdm widget                eco class                   detail subpanel
===========================  ==========================  =========================
``S_VCS__Gauge0.ui``         :class:`VacuumGauge`        (folded in)
``S_VCS__valve_h1/v2.ui``    :class:`Valve`              (folded in)
``S_VCS__VPR1.ui``           :class:`PrePump`            --
``S_VCS__VPT-4.ui``          :class:`TurboPump`          ``.details``
``S_VCS__PG1.ui``            :class:`IonPump`            ``.controller``
===========================  ==========================  =========================

These are the generic device classes only; instantiate them with a concrete
device tag (``DEV`` macro), e.g. ``VacuumGauge("SARES21-VMFR140-500")``. See
``README.md`` in this package for the walkthrough.
"""

from .gauges import VacuumGauge
from .valves import Valve, FastValve, PrePump
from .pumps import TurboPump, TurboPumpDetails, IonPump, IonPumpController
from .prepump import (
    PrepumpLine,
    PrepumpSystem,
    make_prepump_system,
    make_bernina_prepump_system,
    BERNINA_PREPUMP_CONFIG,
)

__all__ = [
    "VacuumGauge",
    "Valve",
    "FastValve",
    "PrePump",
    "TurboPump",
    "TurboPumpDetails",
    "IonPump",
    "IonPumpController",
    "PrepumpLine",
    "PrepumpSystem",
    "make_prepump_system",
    "make_bernina_prepump_system",
    "BERNINA_PREPUMP_CONFIG",
]
