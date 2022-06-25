from eco import Assembly
from eco.epics.adjustable import AdjustablePv, AdjustablePvEnum
from eco.epics.detector import DetectorPvData, DetectorPvDataStream


class LabMaxEnergyMonitor(Assembly):
    def __init__(self,pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(DetectorPvDataStream, pvname+':READ_SC',name='pulse_energy')
        self._append(DetectorPvDataStream, pvname+':ENERGY_AVE100',name='pulse_energy_avg100')
        self._append(AdjustablePvEnum, pvname+':READ.SCAN',name='read_mode')
        self._append(AdjustablePv, pvname+':SET_FSD',name='output_signal_voltage', is_setting=True)
        self._append(AdjustablePvEnum, pvname+':WL_CORR_MODE',name='correct_wavelenght', is_setting=True)
        self._append(AdjustablePvEnum, pvname+':TRIG_SOURCE',name='trigger_source', is_setting=True)
        self._append(AdjustablePv, pvname+':SET_TRIG_LEVEL',name='output_signal_voltage', is_setting=True)
        self._append(DetectorPvData, pvname+':GET_RANGE_SC',name='range', is_setting=True)
        self._append(AdjustablePv, pvname+':SELECT_RANGE',name='set_range', is_setting=True)
        
        





