

from functools import partial
from eco import Assembly
from eco.elements.detector import DetectorVirtual
from eco.epics.detector import DetectorPvData


class ChillerThermotek(Assembly):
    def __init__(self,pvbase="SARES20-CHIL",name=None):
        self.pvbase = pvbase
        super().__init__(name=name)
        self._append(DetectorPvData,pvbase+":H2O_FLUSS", name='flow_rate', is_display=True)
        self._append(DetectorPvData,pvbase+":T_VORLAUF", name='temp_feed', is_display=True)
        self._append(DetectorPvData,pvbase+":bitIO",name='bitIO',is_display=False)
        self._append(ThermotekChillerFlags,self.bitIO,name='flags')
        



flag_names_thermotek_chiller = [
    "operation",
    "error",
    "pressure_state",
    "temperature_state",
    "flow_state",
    "state_flow2",
    "liquid_level_state",
    "condictivity_state",
    "ambient_temperature_state",
    "interruption_clearance",
    "alarm_beep",
    "control_valve",
    "compressor",
    "heater",
    "pump",
    "LF_valve",
    "remote_start",
    "error_max_pressure",
    "error_min_pressure",
    "error_fill_level",
    "warning_fill_level",
    "flow_ping0",
    "flow_ping1",
    # "flow_switch",
]

class ThermotekChillerFlags(Assembly):
    def __init__(self, flags, name="flags"):
        super().__init__(name=name)
        self._flags = flags
        for flag_name in flag_names_thermotek_chiller:
            self._append(
                DetectorVirtual,
                [self._flags],
                partial(self._get_flag_name_value, flag_name=flag_name),
                name=flag_name,
                is_status=True,
                is_display=True,
            )

    def _get_flag_name_value(self, value, flag_name=None):
        index = flag_names_thermotek_chiller.index(flag_name)
        return int("{0:015b}".format(int(value))[-1 * (index + 1)]) == 1
