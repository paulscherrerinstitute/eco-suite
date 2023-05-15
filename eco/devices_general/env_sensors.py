from eco import Assembly
from eco.epics.adjustable import AdjustablePv, AdjustablePvEnum, AdjustablePvString
from eco.epics.detector import DetectorPvData
from epics import PV




class I2cChannel(Assembly):
    def __init__(self, pvbase, channelnumber, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        self.channel_number = channelnumber
        self._append(
            AdjustablePvString,
            f'{self.pvbase}_CH{self.channel_number}:PROCESS.DESC',
            name="description",
            is_setting=True,
        )
        self._append(DetectorPvData,f'{self.pvbase}_CH{self.channel_number}:TEMP', has_unit=True, name='temperature')
        self._append(DetectorPvData,f'{self.pvbase}_CH{self.channel_number}:HUMIREL', has_unit=True, name = 'humidity')
        self._append(DetectorPvData,f'{self.pvbase}_CH{self.channel_number}:PRES', has_unit=True, name='pressure')
        self._append(AdjustablePv,f'{self.pvbase}_CH{self.channel_number}:ONOFF', name='enabled', is_setting=True)
        self._append(AdjustablePvEnum,f'{self.pvbase}_CH{self.channel_number}:SENSOR_TYPE', name='sensor_type', is_setting=True)
        self._pv_init = PV(self.pvbase+':INIT.PROC')
    
    def get_current_value(self,*args,**kwargs):
        return f'{self.temperature.get_current_value(*args,**kwargs):.2f}°C , {self.humidity.get_current_value(*args,**kwargs):.2f}%relHum, {self.pressure.get_current_value(*args,**kwargs):.2f} mB'
    
    # def set_target_value(self,*args,**kwargs):
    #     return self.enabled.set_target_value(*args,**kwargs)
    
    def initialize(self):
        self._pv_init.put(1)




class I2cModule(Assembly):
    def __init__(self,pvbase='SARES20-CI2C',N_channels=8, name=None):
        super().__init__(name=name)
        for n in range(4,N_channels+1):
            self._append(I2cChannel,pvbase,channelnumber=n,name=f'ch{n}')


class BerninaEnvironment(Assembly):
    def __init__(self,pvbases=['SLAAR21-LI2C01', 'SARES20-CI2C'],channels=[[1,2],[4,5,6,7,8]], name=None):
        super().__init__(name=name)
        for pvbase,channelnumbers in zip(pvbases,channels):
            for n in channelnumbers:
                self._append(I2cChannel,pvbase,channelnumber=n,name=f'ch{n}')


