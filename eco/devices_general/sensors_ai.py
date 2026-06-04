from eco.devices_general.wago import AnalogInput
from eco.elements.adjustable import AdjustableVirtual


class OxygenSensor(AnalogInput):
    def __init__(self, pvname, name=None):
        super().__init__(pvname, name=name)
        self.unit.set_target_value("%")

    def set_no_oxygen(self, raw_val=None):
        if not raw_val:
            raw_val = self.raw.get_current_value()
        slo = self.linear_calibration_slope.get_current_value()
        off = self.linear_calibration_offset.get_current_value()
        r0 = raw_val
        r100 = (100 - off) / slo
        slo_n = 100 / (r100 - r0)
        off_n = -slo_n * r0
        self.linear_calibration_offset(off_n)
        self.linear_calibration_slope(slo_n)

    def set_full_oxygen(self, raw_val=None):
        if not raw_val:
            raw_val = self.raw.get_current_value()
        slo = self.linear_calibration_slope.get_current_value()
        off = self.linear_calibration_offset.get_current_value()
        r0 = -(off / slo)
        r100 = raw_val
        slo_n = 100 / (r100 - r0)
        off_n = -slo_n * r0
        self.linear_calibration_offset(off_n)
        self.linear_calibration_slope(slo_n)
