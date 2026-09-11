from ..epics_utils.adjustable import AdjustablePvEnum, AdjustablePvString, AdjustablePv
from ..elements.assembly import Assembly
from ..epics_utils.detector import DetectorPvEnum, DetectorPvData
from .detectors import DetectorVirtual
from functools import partial
from eco.elements.adjustable import spec_convenience


class PowerSocket(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(
            AdjustablePvString,
            pvname + ":POWERONOFF-DESC",
            name="description",
            is_setting=True,
        )
        self._append(
            DetectorPvEnum, pvname + ":POWERONOFF-RB", name="stat", is_display=True
        )
        self._append(
            AdjustablePvEnum,
            pvname + ":POWERONOFF",
            name="on_switch",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvString,
            pvname + ":POWERCYCLE",
            name="powercycle_for_10s",
            is_setting=False,
            is_display=False,
        )

    def toggle(self):
        self.on_switch(int(not (self.stat() == 1)))

    def on(self):
        self.on_switch(1)

    def off(self):
        self.on_switch(0)

    def __call__(self, *args):
        if not args:
            self.toggle()
        else:
            self.on_switch(args[0])


class GudeStrip(Assembly):
    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        for n in range(1, 5):
            self._append(
                PowerSocket,
                pvbase + f"-CH{n}",
                is_display="recursive",
                is_setting=True,
                name=f"ch{n}",
            )
        self._append(
            DetectorPvData, pvbase + ":CURRENT", is_display=True, name="current"
        )
        self._append(
            DetectorPvData, pvbase + ":VOLTAGE", is_display=True, name="voltage"
        )


class MpodStatus(Assembly):
    def __init__(self, pvbase, channel_number, module_string="LV_OMPV_1", name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        self._module_string = module_string
        self.channel_number = channel_number
        self._append(
            DetectorPvEnum,
            self.pvbase + f":{self._module_string}_CH{self.channel_number}_ON",
            name="is_on",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase + f":{self._module_string}_CH{self.channel_number}_INHIBIT",
            name="inhibited",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_FAILURE_MIN_SENS_VOLTAGE",
            name="voltage_readback_low",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_FAILURE_MAX_SENS_VOLTAGE",
            name="voltage_readback_high",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_FAILURE_MAX_TERM_VOLTAGE",
            name="terminal_voltage_readback_high",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_FAILURE_MAX_CURRENT",
            name="current_too_high",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_FAILURE_MAX_TEMP",
            name="temperature_high",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_FAILURE_MAX_POWER",
            name="output_power_high",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase + f":{self._module_string}_CH{self.channel_number}_TIMEOUT",
            name="communication_timeout",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase + f":{self._module_string}_CH{self.channel_number}_CURR_CTRL",
            name="constant_current_mode",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase + f":{self._module_string}_CH{self.channel_number}_RMP_UP",
            name="ramping_up",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase + f":{self._module_string}_CH{self.channel_number}_RMP_DOWN",
            name="ramping_down",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase + f":{self._module_string}_CH{self.channel_number}_KILL",
            name="kill_enabled",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_EMERGENCY_OFF",
            name="emergency_off",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase + f":{self._module_string}_CH{self.channel_number}_FINE_ADJUST",
            name="fine_adjustment",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_VOLTAGE_CTRL",
            name="constant_voltage_mode",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_LOW_CURR_MEAS",
            name="current_readback_range_low",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_OUT_CURR_OOB",
            name="current_readback_range_high",
        )
        self._append(
            DetectorPvEnum,
            self.pvbase + f":{self._module_string}_CH{self.channel_number}_OVERCURRENT",
            name="overcurrent",
        )


@spec_convenience
class MpodVoltageAdjustable(Assembly):
    """`AdjustablePv` wrapper that also exposes its EPICS setpoint limits
    (`.LOPR`/`.HOPR`) as `limit_low`/`limit_high` status/display children.

    `AdjustablePv` already enforces these limits on every write (`change()`
    raises if the target is outside `[pvlowlim, pvhighlim]`) -- this just
    makes the limit values themselves visible, instead of only living inside
    the underlying PVs.
    """

    def __init__(
        self,
        pvsetname,
        pvlowlimname,
        pvhighlimname,
        pvreadbackname=None,
        name=None,
    ):
        super().__init__(name=name)
        self._adjustable = AdjustablePv(
            pvsetname,
            pvreadbackname=pvreadbackname,
            pvlowlimname=pvlowlimname,
            pvhighlimname=pvhighlimname,
            name=name,
        )
        self._append(
            DetectorPvData, pvlowlimname, name="limit_low", is_status=True, is_display=True
        )
        self._append(
            DetectorPvData,
            pvhighlimname,
            name="limit_high",
            is_status=True,
            is_display=True,
        )

    def get_current_value(self, *args, **kwargs):
        return self._adjustable.get_current_value(*args, **kwargs)

    def set_target_value(self, *args, **kwargs):
        return self._adjustable.set_target_value(*args, **kwargs)

    def get_limits(self):
        return self._adjustable.get_limits()

    def set_limits(self, *args, **kwargs):
        return self._adjustable.set_limits(*args, **kwargs)


@spec_convenience
class MpodChannel(Assembly):
    def __init__(self, pvbase, channel_number, module_string="LV_OMPV_1", name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        self._module_string = module_string
        self.channel_number = channel_number
        self._append(
            AdjustablePvEnum,
            self.pvbase + f":{self._module_string}_CH{self.channel_number}_SWITCH_SP",
            name="on",
            is_setting=True,
        )
        self._append(
            MpodVoltageAdjustable,
            self.pvbase + f":{self._module_string}_CH{self.channel_number}_OUTPUT_V_SP",
            pvlowlimname=self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_OUTPUT_V_SP.LOPR",
            pvhighlimname=self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_OUTPUT_V_SP.HOPR",
            pvreadbackname=self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_MEAS_SENS_V",
            name="voltage",
            is_setting=True,
            is_display="recursive",
        )
        self._append(
            MpodVoltageAdjustable,
            self.pvbase + f":{self._module_string}_CH{self.channel_number}_OUTPUT_V_SP",
            pvlowlimname=self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_OUTPUT_V_SP.LOPR",
            pvhighlimname=self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_OUTPUT_V_SP.HOPR",
            name="voltage_set_point",
            is_setting=True,
            is_display="recursive",
        )
        self._append(
            AdjustablePv,
            self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_RMP_UP_RATE_SP",
            pvlowlimname=self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_RMP_UP_RATE_SP.LOPR",
            pvhighlimname=self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_RMP_UP_RATE_SP.HOPR",
            name="ramp_up",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_RMP_DOWN_RATE_SP",
            pvlowlimname=self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_RMP_DOWN_RATE_SP.LOPR",
            pvhighlimname=self.pvbase
            + f":{self._module_string}_CH{self.channel_number}_RMP_DOWN_RATE_SP.HOPR",
            name="ramp_down",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self.pvbase + f":{self._module_string}_CH{self.channel_number}_MEAS_OUT_A",
            name="current",
            is_setting=False,
            is_display=True,
        )
        self._append(
            MpodStatus,
            self.pvbase,
            self.channel_number,
            self._module_string,
            name="flags",
        )

    def get_current_value(self, *args, **kwargs):
        return (
            self.voltage_set_point.get_current_value(*args, **kwargs),
            self.on.get_current_value(*args, **kwargs),
        )

    def set_target_value(self, value, *args, **kwargs):
        if isinstance(value, bool):
            return self.on.set_target_value(value, *args, **kwargs)
        return self.voltage_set_point.set_target_value(value, *args, **kwargs)


class MpodModule(Assembly):
    def __init__(
        self, pvbase, channelnumbers, channelnames, module_string="LV_OMPV_1", name=None
    ):
        super().__init__(name=name)
        for channelnumber, channelname in zip(channelnumbers, channelnames):
            self._append(
                MpodChannel,
                pvbase,
                channel_number=channelnumber,
                module_string=module_string,
                name=channelname,
            )


# for new ioc by Thierry

flag_names_mpod = [
    "outputOn",
    "outputInhibit",
    "outputFailureMinSenseVoltage",
    "outputFailureMaxSenseVoltage",
    "outputFailureMaxTerminalVoltage",
    "outputFailureMaxCurrent",
    "outputFailureMaxTemperature",
    "outputFailureMaxPower",
    "outputFailureTimeout",
    "outputCurrentLimited",
    "outputRampUp",
    "outputRampDown",
    "outputEnableKill",
    "outputEmergencyOff",
    "outputAdjusting",
    "outputConstantVoltage",
    "outputLowCurrentRange",
    "outputCurrentBoundsExceeded",
    "outputFailureCurrentLimit",
    "outputCurrentIncreasing",
    "outputCurrentDecreasing",
    "outputConstantPower",
    "outputVoltageRampSpeedLimited",
    "outputVoltageBottomReached",
    "outputInitCrcCheckBad",
]


class NEW_MpodFlags(Assembly):
    def __init__(self, flags, name="flags"):
        super().__init__(name=name)
        self._flags = flags
        for flag_name in flag_names_mpod:
            self._append(
                DetectorVirtual,
                [self._flags],
                partial(self._get_flag_name_value, flag_name=flag_name),
                name=flag_name,
                is_status=False,
                is_display=True,
            )

    def _get_flag_name_value(self, value, flag_name=None):
        index = flag_names_mpod.index(flag_name)
        return int("{0:015b}".format(int(value))[-1 * (index + 1)]) == 1


class NEW_MpodChannel(Assembly):
    def __init__(self, pvbase, channel_number, module_string, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        self._module_string = module_string
        self.channel_number = channel_number

        self._append(
            AdjustablePvEnum,
            self.pvbase + f":{self._module_string}0{self.channel_number}-SWITCH_SP",
            name="on",
            is_setting=True,
            is_display=True,
        )

        self._append(
            AdjustablePv,
            self.pvbase + f":{self._module_string}0{self.channel_number}-V_SP",
            pvreadbackname=self.pvbase
            + f":{self._module_string}0{self.channel_number}-V_RB",
            pvlowlimname=self.pvbase,
            name="voltage",
            is_setting=True,
            is_display=True,
        )

        self._append(
            AdjustablePv,
            self.pvbase + f":{self._module_string}0{self.channel_number}-I_SP",
            pvreadbackname=self.pvbase
            + f":{self._module_string}0{self.channel_number}-I_RB",
            pvlowlimname=self.pvbase,
            name="current",
            is_setting=True,
            is_display=True,
        )

        # self._append(
        #     AdjustablePv,
        #     self.pvbase + f":{self._module_string}0{self.channel_number}-VRISE_SP",
        #     pvreadbackname=self.pvbase
        #     + f":{self._module_string}0{self.channel_number}-VRISE_RB",
        #     pvlowlimname=self.pvbase,
        #     name="V_rise",
        #     is_setting=True,
        #     is_display=True,
        # )

        # self._append(
        #     AdjustablePv,
        #     self.pvbase + f":{self._module_string}0{self.channel_number}-IRISE_SP",
        #     pvreadbackname=self.pvbase
        #     + f":{self._module_string}0{self.channel_number}-IRISE_RB",
        #     pvlowlimname=self.pvbase,
        #     name="I_rise",
        #     is_setting=True,
        #     is_display=True,
        # )
        # self._append(
        #     AdjustablePv,
        #     self.pvbase + f":{self._module_string}0{self.channel_number}-VFALL_SP",
        #     pvreadbackname=self.pvbase
        #     + f":{self._module_string}0{self.channel_number}-VFALL_RB",
        #     pvlowlimname=self.pvbase,
        #     name="V_fall",
        #     is_setting=True,
        #     is_display=True,
        # )

        # self._append(
        #     AdjustablePv,
        #     self.pvbase + f":{self._module_string}0{self.channel_number}-IFALL_SP",
        #     pvreadbackname=self.pvbase
        #     + f":{self._module_string}0{self.channel_number}-IFALL_RB",
        #     pvlowlimname=self.pvbase,
        #     name="I_fall",
        #     is_setting=True,
        #     is_display=True,
        # )

        self._append(
            DetectorPvData,
            self.pvbase + f":{self._module_string}0{self.channel_number}-STAT",
            name="_flags",
            is_setting=False,
        )

        self._append(
            NEW_MpodFlags,
            self._flags,
            name="flags",
            is_setting=False,
            is_status=True,
        )

    def get_current_value(self, *args, **kwargs):
        return self.on.get_current_value(*args, **kwargs)

    def set_target_value(self, *args, **kwargs):
        return self.on.set_target_value(*args, **kwargs)


class NEW_MpodModule(Assembly):
    def __init__(self, pvbase, channelnumbers, channelnames, module_string, name=None):
        super().__init__(name=name)
        for channelnumber, channelname in zip(channelnumbers, channelnames):
            self._append(
                NEW_MpodChannel,
                pvbase,
                channel_number=channelnumber,
                module_string=module_string,
                name=channelname,
            )
