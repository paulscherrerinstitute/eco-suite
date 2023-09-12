from ..epics.adjustable import AdjustablePvEnum, AdjustablePvString, AdjustablePv
from ..elements.assembly import Assembly
from ..epics.detector import DetectorPvEnum, DetectorPvData
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
    def __init__(self,pvbase,channel_number, module_string='LV_OMPV_1', name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        self._module_string = module_string
        self.channel_number = channel_number
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_ON',name='is_on')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_INHIBIT',name='inhibited')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_FAILURE_MIN_SENS_VOLTAGE',name='voltage_readback_low')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_FAILURE_MAX_SENS_VOLTAGE',name='voltage_readback_high')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_FAILURE_MAX_TERM_VOLTAGE',name='terminal_voltage_readback_high')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_FAILURE_MAX_CURRENT',name='current_too_high')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_FAILURE_MAX_TEMP',name='temperature_high')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_FAILURE_MAX_POWER',name='output_power_high')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_TIMEOUT',name='communication_timeout')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_CURR_CTRL',name='constant_current_mode')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_RMP_UP',name='ramping_up')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_RMP_DOWN',name='ramping_down')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_KILL',name='kill_enabled')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_EMERGENCY_OFF',name='emergency_off')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_FINE_ADJUST',name='fine_adjustment')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_VOLTAGE_CTRL',name='constant_voltage_mode')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_LOW_CURR_MEAS',name='current_readback_range_low')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_OUT_CURR_OOB',name='current_readback_range_high')
        self._append(DetectorPvEnum,self.pvbase + f':{self._module_string}_CH{self.channel_number}_OVERCURRENT',name='overcurrent')

@spec_convenience
class MpodChannel(Assembly):
    def __init__(self,pvbase,channel_number, module_string='LV_OMPV_1', name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        self._module_string = module_string
        self.channel_number = channel_number
        self._append(AdjustablePvEnum,self.pvbase+f':{self._module_string}_CH{self.channel_number}_SWITCH_SP', name='on', is_setting=True)
        self._append(AdjustablePv,
            self.pvbase+f':{self._module_string}_CH{self.channel_number}_OUTPUT_V_SP', 
            pvreadbackname = self.pvbase+f':{self._module_string}_CH{self.channel_number}_MEAS_SENS_V', 
            pvlowlimname = self.pvbase+f':{self._module_string}_CH{self.channel_number}_OUTPUT_V_SP.LOPR', 
            pvhighlimname = self.pvbase+f':{self._module_string}_CH{self.channel_number}_OUTPUT_V_SP.HOPR', 
            name='voltage', is_setting=True)
        self._append(AdjustablePv,
            self.pvbase+f':{self._module_string}_CH{self.channel_number}_RMP_UP_RATE_SP', 
            pvlowlimname = self.pvbase+f':{self._module_string}_CH{self.channel_number}_RMP_UP_RATE_SP.LOPR', 
            pvhighlimname = self.pvbase+f':{self._module_string}_CH{self.channel_number}_RMP_UP_RATE_SP.HOPR', 
            name='ramp_up', is_setting=True)
        self._append(AdjustablePv,
            self.pvbase+f':{self._module_string}_CH{self.channel_number}_RMP_DOWN_RATE_SP', 
            pvlowlimname = self.pvbase+f':{self._module_string}_CH{self.channel_number}_RMP_DOWN_RATE_SP.LOPR', 
            pvhighlimname = self.pvbase+f':{self._module_string}_CH{self.channel_number}_RMP_DOWN_RATE_SP.HOPR', 
            name='ramp_down', is_setting=True)            
        self._append(MpodStatus,self.pvbase, self.channel_number, self._module_string, name='flags')
    
    def get_current_value(self,*args,**kwargs):
        return self.on.get_current_value(*args,**kwargs)
    
    def set_target_value(self,*args,**kwargs):
        return self.on.set_target_value(*args,**kwargs)

class MpodModule(Assembly):
    def __init__(self,pvbase,channelnumbers, channelnames, module_string='LV_OMPV_1', name=None):
        super().__init__(name=name)
        for channelnumber,channelname in zip(channelnumbers,channelnames):
            self._append(MpodChannel,pvbase,channel_number=channelnumber, module_string=module_string,name=channelname)
