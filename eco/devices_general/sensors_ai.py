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

        off_n = -slo * val_curr
        af = (100 - off) / slo
        # slo_new = slo*((100-val_curr)/(100-off))
        slo_n = 100 / af
        self.linear_calibration_offset(off_n)
        self.linear_calibration_slope(slo_n)

    def set_full_oxygen(self, val_curr=None):
        if not val_curr:
            val_curr = self.raw.get_current_value()
        af = val_curr
        slo = self.linear_calibration_slope.get_current_value()
        off = self.linear_calibration_offset.get_current_value()

        az = -off / slo
        slo_n = 100 / (af - az)
        off_n = -slo_n * az
        self.linear_calibration_offset(off_n)
        self.linear_calibration_slope(slo_n)
