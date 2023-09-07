from eco import Assembly
from eco.epics.adjustable import AdjustablePv, AdjustablePvEnum, AdjustablePvString
from eco.epics.detector import DetectorPvData, DetectorPvEnum
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
        self._append(DetectorPvData,f'{self.pvbase}_CH{self.channel_number}:TEMP', has_unit=True, name='temp')
        self._append(DetectorPvData,f'{self.pvbase}_CH{self.channel_number}:HUMIREL', has_unit=True, name = 'humi')
        self._append(DetectorPvData,f'{self.pvbase}_CH{self.channel_number}:PRES', has_unit=True, name='pres')
        self._append(AdjustablePv,f'{self.pvbase}_CH{self.channel_number}:ONOFF', name='enabled', is_setting=True)
        self._append(AdjustablePvEnum,f'{self.pvbase}_CH{self.channel_number}:SENSOR_TYPE', name='sensor_type', is_setting=True)
        self._pv_init = PV(self.pvbase+':INIT.PROC')
    
    def get_current_value(self,*args,**kwargs):
        return f'{self.temp.get_current_value(*args,**kwargs):.2f}°C , {self.humi.get_current_value(*args,**kwargs):.2f}%relHum, {self.pres.get_current_value(*args,**kwargs):.2f} mB'
    
    # def set_target_value(self,*args,**kwargs):
    #     return self.enabled.set_target_value(*args,**kwargs)
    
    def initialize(self):
        self._pv_init.put(1)




class I2cModule(Assembly):
    def __init__(self,pvbase='SARES20-CI2C',N_channels=8, name=None):
        super().__init__(name=name)
        for n in range(1,N_channels+1):
            self._append(I2cChannel,pvbase,channelnumber=n,name=f'ch{n}')


class BerninaEnvironment(Assembly):
    def __init__(self,pvbases=['SLAAR21-LI2C01', 'SARES20-CI2C'],channels=[[1,2,3,4,5,6,7,8],[4,5,6,7,8]], channelnames = [['las_tab1_in', 'las_tab1_cen', 'las_tab1_out', 'las_tab2_in', 'las_tab2_cen', 'las_tab2_out', 'las_tab2_below', 'tt_spec'], ['tt_opt', 'tt_kb', 'exp1', 'exp2', 'exp3']], name=None):
        super().__init__(name=name)
        for pvbase,channelnumbers,tnames in zip(pvbases,channels,channelnames):
            for n,tname in zip(channelnumbers,tnames):
                self._append(I2cChannel,pvbase,channelnumber=n,name=tname)


class WagoSensor(Assembly):
    def __init__(self,pvbase='SARES20-CWAG-GPS01:TEMP-T9', name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        self._append(DetectorPvData,f'{self.pvbase}', unit='°C', name='temperature')
        self._append(DetectorPvEnum,f'{self.pvbase}-SS', name='status')
        self._append(AdjustablePv,f'{self.pvbase}-WLEN', name='cable_length', unit='m', is_setting=True)
        self.unit = self.temperature.unit
    
    def get_current_value(self):
        return self.temperature.get_current_value()
    
    

