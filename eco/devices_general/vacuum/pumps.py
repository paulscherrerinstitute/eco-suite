"""Turbomolecular pumps and ion getter pumps.

Modelled from the SwissFEL VCS caqtdm panel:

* :class:`TurboPump` -- turbomolecular pumps (device tags ``...-VPTM...``), from
  ``S_VCS__VPT-4.ui`` plus its detail subpanel ``S_VCS__TurboPump.ui``. The
  handful of everyday controls live on the object directly; the large detail
  subpanel (temperatures, drive telemetry, running hours, error history) is
  grouped into the :attr:`details` sub-assembly -- the eco equivalent of
  clicking the subpanel open.
* :class:`IonPump` -- ion getter pumps driven by a Varian/Agilent 4UHV
  controller (device tags ``...-VPIG...`` and ``...-VPNG...``), from
  ``S_VCS__PG1.ui`` plus its detail subpanel ``S_VCS__VarianPump.ui``. The full
  4UHV controller detail is the :attr:`controller` sub-assembly.

Minimal use::

    from eco.devices_general.vacuum import TurboPump, IonPump
    tp = TurboPump("SARES21-VPTM140-700", name="turbo_test")
    tp.speed()               # rotor speed, Hz
    tp.details.temp_motor()  # everything the subpanel showed

    ip = IonPump("SARES21-VPIG140-400", name="ionpump_test")
    ip.pressure()            # controller pressure display
    ip.controller.serial_number()
"""

from eco import Assembly
from eco.epics_utils.adjustable import AdjustablePv, AdjustablePvEnum
from eco.epics_utils.detector import DetectorPvData, DetectorPvEnum, DetectorPvString


class TurboPumpDetails(Assembly):
    """Telemetry/diagnostics behind a turbo pump's detail subpanel
    (``S_VCS__TurboPump.ui``): drive electronics, temperatures, running hours,
    firmware/hardware identification and the last-10 error history."""

    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        # drive telemetry
        self._append(DetectorPvData, f"{pvbase}:ACC", has_unit=True, name="acceleration")  # rotor accel (Hz/s)
        self._append(DetectorPvData, f"{pvbase}:DRV-CURR", has_unit=True, name="drive_current")  # motor drive current
        self._append(DetectorPvData, f"{pvbase}:DRV-VOLT", has_unit=True, name="drive_voltage")  # motor drive voltage
        self._append(DetectorPvData, f"{pvbase}:DRV-PWR", has_unit=True, name="drive_power")  # motor drive power
        # temperatures
        self._append(DetectorPvData, f"{pvbase}:TEMP-ELEC", has_unit=True, name="temp_electronics")  # drive electronics temp
        self._append(DetectorPvData, f"{pvbase}:TEMP-PUMP-BOT", has_unit=True, name="temp_pump_bottom")  # pump body (bottom) temp
        self._append(DetectorPvData, f"{pvbase}:TEMP-BEARING", has_unit=True, name="temp_bearing")  # bearing temp
        self._append(DetectorPvData, f"{pvbase}:TEMP-MOTOR", has_unit=True, name="temp_motor")  # motor temp
        # lifetime counters
        self._append(DetectorPvData, f"{pvbase}:OP-HRS-ELEC", has_unit=True, name="op_hours_electronics")  # electronics running hours
        self._append(DetectorPvData, f"{pvbase}:OP-HRS-PUMP", has_unit=True, name="op_hours_pump")  # pump running hours
        self._append(DetectorPvData, f"{pvbase}:PUMP-CYCLES", has_unit=True, name="pump_cycles")  # spin-up/down cycle count
        # identification
        self._append(DetectorPvString, f"{pvbase}:ELEC-NAME", name="controller_type")  # electronics/controller model
        self._append(DetectorPvString, f"{pvbase}:FW-VERSION", name="firmware_version")
        self._append(DetectorPvString, f"{pvbase}:HW-VERSION", name="hardware_version")
        # last-10 error history ring, error_hist1 = most recent
        for i in range(1, 11):
            self._append(
                DetectorPvString, f"{pvbase}:ERROR-HIST{i}", name=f"error_hist{i}"
            )


class TurboPump(Assembly):
    """A turbomolecular pump station.

    Everyday controls: :meth:`start`/:meth:`stop` (via :attr:`start_stop`),
    :attr:`vent`, :attr:`speed_setpoint`. Everyday readings: :attr:`speed`,
    :attr:`at_speed_80` / :attr:`at_speed_100`, :attr:`error`. Full diagnostics
    are in :attr:`details`.
    """

    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        # --- controls ----------------------------------------------------
        # main start/stop of the rotor (enum: "start"/"stop")
        self._append(
            AdjustablePvEnum, f"{pvbase}:START", name="start_stop", is_setting=True
        )
        # venting valve command (enum: "open"/"close")
        self._append(AdjustablePvEnum, f"{pvbase}:VENT", name="vent", is_setting=True)
        # enable the turbo pump within the station
        self._append(
            AdjustablePvEnum, f"{pvbase}:TPSET", name="pump_enable", is_setting=True
        )
        # enable the whole pump station (turbo + backing)
        self._append(
            AdjustablePvEnum, f"{pvbase}:PSSET", name="station_enable", is_setting=True
        )
        # rotation-speed mode selector (normal / standby / set-speed)
        self._append(
            AdjustablePvEnum, f"{pvbase}:SPEED", name="speed_mode", is_setting=True
        )
        # numeric rotor-speed setpoint used in set-speed mode
        self._append(
            AdjustablePv, f"{pvbase}:SETHZ", name="speed_setpoint",
            unit="Hz", is_setting=True,
        )
        # error acknowledge (momentary write of 1): the caqtdm "Reset" button
        self._append(
            AdjustablePv, f"{pvbase}:ACK-ERR", name="_ack_error", is_setting=False,
            is_status=False,
        )
        # --- readbacks ---------------------------------------------------
        self._append(DetectorPvData, f"{pvbase}:HZ", has_unit=True, name="speed")  # actual rotor speed (Hz)
        self._append(DetectorPvEnum, f"{pvbase}:80", name="at_speed_80")  # reached 80% of set speed (enum YES/NO)
        self._append(DetectorPvEnum, f"{pvbase}:100", name="at_speed_100")  # reached 100% / at full speed
        self._append(DetectorPvString, f"{pvbase}:ERROR", name="error")  # active error code, "000000" = none
        # --- detail subpanel ---------------------------------------------
        # everything behind the "click-open" detail subpanel (telemetry, temps,
        # running hours, error history); see TurboPumpDetails
        self._append(TurboPumpDetails, pvbase, name="details")

    def start(self):
        """Start the turbo pump."""
        return self.start_stop.set_target_value("start")

    def stop(self):
        """Stop the turbo pump."""
        return self.start_stop.set_target_value("stop")

    def reset_error(self):
        """Acknowledge/reset the pump error (momentary, writes 1)."""
        return self._ack_error.set_target_value(1)

    def get_current_value(self, *args, **kwargs):
        """Read like a detector: return the rotor speed."""
        return self.speed.get_current_value(*args, **kwargs)


class IonPumpController(Assembly):
    """The Varian/Agilent 4UHV controller detail behind an ion pump
    (``S_VCS__VarianPump.ui``): protection/HV enables, step-start, interlocks
    and controller identification."""

    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        # HV "protection" enable; readback (PROT) and setpoint (PROT-SET) are
        # separate PVs, bridged by AdjustablePvEnum's pvname_set
        self._append(
            AdjustablePvEnum, f"{pvbase}:PROT", pvname_set=f"{pvbase}:PROT-SET",
            name="protection", is_setting=True,
        )
        # step-start (ramp HV in steps) mode; separate readback / set PVs
        self._append(
            AdjustablePvEnum, f"{pvbase}:STEP", pvname_set=f"{pvbase}:STEP-SET",
            name="step_start", is_setting=True,
        )
        self._append(DetectorPvEnum, f"{pvbase}:HV", name="hv_on")  # high-voltage on/off state
        # interlocks (enum OK/tripped)
        self._append(DetectorPvEnum, f"{pvbase}:CABLE-ILK", name="interlock_cable")  # HV cable interlock
        self._append(DetectorPvEnum, f"{pvbase}:REMOTE-ILK", name="interlock_remote")  # remote/PLC interlock
        self._append(DetectorPvEnum, f"{pvbase}:4UHV-ILK", name="interlock_4uhv")  # controller-internal interlock
        # identification
        self._append(DetectorPvString, f"{pvbase}:CONTROLLER", name="controller_name")  # controller unit name
        self._append(DetectorPvString, f"{pvbase}:4UHV-SN", name="serial_number")  # controller serial number
        self._append(DetectorPvString, f"{pvbase}:4UHV-TYPE", name="controller_type")  # controller model/type
        self._append(DetectorPvString, f"{pvbase}:PUMPTYPE", name="pump_type")  # connected pump type


class IonPump(Assembly):
    """An ion getter pump on a 4UHV controller.

    Everyday reading: :attr:`pressure` (plus :attr:`current`, :attr:`voltage`).
    :attr:`on` switches the pump; :attr:`hv` / :attr:`error` /
    :attr:`operating_mode` report state. The full controller detail subpanel is
    the :attr:`controller` sub-assembly.
    """

    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        self._append(
            AdjustablePvEnum, f"{pvbase}:ONOFF", name="on", is_setting=True
        )  # pump HV on/off command
        # controller pressure display; shows "HV OFF" when the pump is off, so
        # it is treated as a string rather than a number.
        self._append(DetectorPvString, f"{pvbase}:PRESS-DISP", name="pressure")
        self._append(DetectorPvData, f"{pvbase}:CURRENT", has_unit=True, name="current")  # pump current (~ pressure)
        self._append(DetectorPvData, f"{pvbase}:VOLTAGE", has_unit=True, name="voltage")  # HV output voltage
        self._append(DetectorPvEnum, f"{pvbase}:HV", name="hv")  # high-voltage on/off state
        self._append(DetectorPvEnum, f"{pvbase}:OPMODE", name="operating_mode")  # controller mode (Serial/Local/...)
        self._append(DetectorPvString, f"{pvbase}:ERROR", name="error")  # active error/fault text
        # PLC vacuum-interlock setpoint; optional (not populated on every unit)
        self._append(
            AdjustablePv, f"{pvbase}:PLC_SETPOINT", name="setpoint",
            is_setting=True, optional=True,
        )
        # full 4UHV controller detail ("click-open" subpanel); see IonPumpController
        self._append(IonPumpController, pvbase, name="controller")

    def get_current_value(self, *args, **kwargs):
        """Read like a detector: return the controller pressure display."""
        return self.pressure.get_current_value(*args, **kwargs)
