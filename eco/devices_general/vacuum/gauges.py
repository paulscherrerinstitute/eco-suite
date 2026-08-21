"""Vacuum gauges (full-range, cold-cathode, Pirani).

Modelled from the SwissFEL VCS caqtdm panel ``S_VCS__Gauge0.ui`` and its
detail subpanel ``S_VCS__gauge.ui``. Device tags of the form
``SARES21-VMFR140-500`` (full range), ``SARES21-VMCP...`` / ``SARES21-VMCC...``
(cold cathode / cold-cathode-Pirani combos) all share the same PV interface, so
one class covers them all.

Minimal use::

    from eco.devices_general.vacuum import VacuumGauge
    g = VacuumGauge("SARES21-VMFR140-500", name="gauge_test")
    g.pressure()          # -> pressure in mbar (Detector convention)
    g.get_current_value() # same, so the gauge reads like a plain detector
"""

from eco import Assembly
from eco.epics_utils.adjustable import AdjustablePv, AdjustablePvEnum, AdjustablePvString
from eco.epics_utils.detector import DetectorPvData, DetectorPvEnum, DetectorPvString


class VacuumGauge(Assembly):
    """A single vacuum gauge channel.

    The everyday reading is :attr:`pressure`; everything the ``S_VCS__gauge.ui``
    subpanel shows (status, on/off, gauge model, controller) is exposed as named
    components so the whole subpanel is represented in the object.
    """

    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        # --- primary reading ---------------------------------------------
        # measured pressure (with EGU, typ. mbar) -- the value you normally want
        self._append(
            DetectorPvData, f"{pvbase}:PRESSURE", has_unit=True, name="pressure"
        )
        # controller status text, e.g. "MEASURE", "SENSOR OFF", "UNDER RANGE"
        self._append(DetectorPvString, f"{pvbase}:STATUS", name="status")
        # --- gauge on/off (cold cathodes can be switched) ----------------
        self._append(
            AdjustablePvEnum, f"{pvbase}:ONOFF", name="on", is_setting=True
        )  # emission on/off command
        self._append(
            DetectorPvEnum, f"{pvbase}:ONOFFG", name="on_readback"
        )  # emission on/off state readback
        # --- interlock / trip setpoint fed back from the PLC -------------
        # pressure threshold the PLC uses for vacuum interlock decisions
        self._append(
            AdjustablePv, f"{pvbase}:PLC_SETPOINT", name="setpoint", is_setting=True
        )
        # --- identification / housekeeping -------------------------------
        self._append(
            DetectorPvString, f"{pvbase}:GAUGE-TYPE", name="gauge_type"
        )  # sensor model, e.g. "PKR" (Pirani/cold-cathode combo)
        self._append(
            DetectorPvString, f"{pvbase}:CONTROLLER", name="controller"
        )  # controller unit name (e.g. TPG gauge controller)

    def get_current_value(self, *args, **kwargs):
        """Read like a detector: return the pressure."""
        return self.pressure.get_current_value(*args, **kwargs)
