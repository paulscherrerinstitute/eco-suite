from eco.elements.assembly import Assembly
from eco.epics.detector import DetectorPvData
from eco.epics.adjustable import (
    AdjustablePvString,
    AdjustablePv,
    spec_convenience,
    tweak_option,
)


class AnalogInput(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(
            DetectorPvData, self.pvname, name="value", is_setting=False, is_display=True
        )
        self._append(
            AdjustablePvString,
            self.pvname + ".DESC",
            name="description",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePvString,
            self.pvname + ".EGU",
            name="unit",
            is_setting=False,
            is_display=False,
        )
        self._append(
            DetectorPvData,
            self.pvname + ".RVAL",
            name="raw",
            is_setting=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ".AOFF",
            name="_adj_offset",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ".ASLO",
            name="_adj_slope",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ".EOFF",
            name="linear_calibration_offset",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ".ESLO",
            name="linear_calibration_slope",
            is_setting=True,
            is_display=True,
        )

    def get_current_value(self):
        return self.value.get_current_value()


class WagoAnalogInputs(Assembly):
    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        for n in range(1, 9):
            self._append(AnalogInput, pvbase + f":ADC{n:02d}", name=f"ch{n:d}")


@spec_convenience
@tweak_option
class AnalogOutput(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(
            AdjustablePv,
            self.pvname,
            name="value",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePvString,
            self.pvname + ".DESC",
            name="description",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePvString,
            self.pvname + ".EGU",
            name="unit",
            is_setting=False,
            is_display=False,
        )
        self._append(
            DetectorPvData,
            self.pvname + ".RVAL",
            name="raw",
            is_setting=False,
            is_display=False,
        )

    def get_current_value(self):
        return self.value.get_current_value()

    def set_target_value(self, *args, **kwargs):
        return self.value.set_target_value(*args, **kwargs)

    def __call__(self, *args):
        if args:
            self.value.set_target_Value(*args).wait()
        else:
            return self.value.get_current_value()


class WagoAnalogOutputs(Assembly):
    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        for n in range(1, 9):
            self._append(
                AnalogOutput,
                pvbase + f":DAC{n:02d}",
                name=f"ch{n:d}",
                is_setting=True,
                is_display=True,
            )
