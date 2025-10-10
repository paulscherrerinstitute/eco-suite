from eco.devices_general.wago import AnalogInput
from eco.elements.adjustable import AdjustableVirtual


class OxygenSensor(AnalogInput):
    def __init__(self, pvname, name=None):
        super().__init__(pvname, name=name)
        self.unit.set_target_value("%")

    def set_no_oxygen(self, val_curr=None):
        if not val_curr:
            val_curr = self.raw.get_current_value()
        slo = self.linear_calibration_slope.get_current_value()
        off = self.linear_calibration_offset.get_current_value()
        # slo_new = slo*((100-val_curr)/(100-off))
        slo_new = 100 / ((100 - off) / slo - val_curr)
        self.linear_calibration_offset(val_curr)
        self.linear_calibration_slope(slo_new)

    def set_full_oxygen(self, val_curr=None):
        if not val_curr:
            val_curr = self.raw.get_current_value()
        sval = (100 - self.linear_calibration_offset.get_current_value()) / val_curr
        self.linear_calibration_slope(sval)
