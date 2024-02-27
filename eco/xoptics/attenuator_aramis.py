from eco.epics.detector import DetectorPvData
from ..devices_general.motors import MotorRecord
from epics import PV
from time import sleep
from ..devices_general.utilities import Changer
from ..aliases import Alias
from eco.epics.adjustable import AdjustablePvEnum, AdjustablePv
from ..elements.assembly import Assembly
from ..elements.adjustable import (
    AdjustableError,
    AdjustableGetSet,
    AdjustableVirtual,
    spec_convenience,
    update_changes,
    value_property,
    AdjustableFS,
)
from numpy import isnan, all
import time



@spec_convenience
@value_property
class AttenuatorAramis(Assembly):
    def __init__(
        self, Id, E_min=1500, sleeptime=10, name=None, set_limits=[-52, 2], shutter=None
    ):
        super().__init__(name=name)
        self.pvname = Id
        self._append(AdjustablePvEnum,self.pvname+":UsrRec.MODE",name='execution_mode')
        self._append(DetectorPvData,self.pvname+":HOMING",name='homing_status')
        self._append(AdjustablePvEnum,self.pvname+":ENY_SEL",name='energy_monitor_source')
        self._append(DetectorPvData,self.pvname+":ENY_CALC",name='energy_monitor')
        # self._append(AdjustablePv,self.pvname+"SAROP21-OATT135:UsrRec.TC1",name='_transmission_fund')
        # self._append(AdjustablePv,self.pvname+"SAROP21-OATT135:UsrRec.TC3",name='_transmission_3rdharm')

        self._append(AdjustablePv,self.pvname+":UsrRec.ERY",name='energy_calc')
        self._append(AdjustablePvEnum,self.pvname+":UsrRec.HRM3",name='_set_energy_harm')
        self._append(AdjustablePv,self.pvname+":UsrRec.TD",name='_set_transmission')
        
        for tn in range(1,7,1):
            self._append(AdjustablePvEnum,self.pvname+f":UsrRec.FIC{tn}",self.pvname+f":UsrRec.FI{tn}",name=f'target{tn}')
        
    

        self.E_min = E_min
        # self._pv_status_str = PV(self.pvname + ":MOT2TRANS.VALD")
        # self._pv_status_int = PV(self.pvname + ":IDX_RB")
        # self._sleeptime = sleeptime
        # self._xp = shutter
        self.motors = []
        for n in range(6):
            self._append(MotorRecord, f"{self.pvname}:MOTOR_{n+1}", name=f"motor{n+1}", is_setting=True, is_display=False)
            self.motors.append(self.__dict__[f"motor{n+1}"])
        # self._append(AdjustableFS, f'/sf/bernina/config/eco/reference_values/{name}_limit_high.json', default_value=1, name="limit_high", is_setting=True)
        # self._append(AdjustableFS, f'/sf/bernina/config/eco/reference_values/{name}_limit_low.json', default_value=0, name="limit_low", is_setting=True)
        # self._append(AdjustablePv, "SAROP21-ARAMIS:ENERGY", name="energy_rb", is_setting=False, is_display=False)
        # self._append(AdjustablePv, "SARUN:FELPHOTENE", name="energy_rb_backup", is_setting=False, is_display=False)
        # self._append(AdjustablePv, self.pvname + ":ENERGY", name="energy", is_setting=True, is_display=False)
        # self._append(AdjustablePv, self.pvname + ":TRANS_SP", name="set_transmission", is_setting=False, is_display=False)


        # alias_fields = {
        #     "transmission": "TRANS_RB",
        #     "transmission_3rd": "TRANS3EDHARM_RB",
        # }
        # for an, af in alias_fields.items():
        #     ach = ":".join([self.pvname, af])
        #     self.alias.append(Alias(an, channel=ach, channeltype="CA"))
        self._append(DetectorPvData,self.pvname+":UsrRec.TC1",name='_transmission_fund',is_display=False)
        self._append(DetectorPvData,self.pvname+":UsrRec.TC3",name='_transmission_3rdharm',is_display=False)
        self._append(
            AdjustableGetSet,
            self._transmission_fund.get_current_value,
            lambda value: self.set_transmission(value,harm=1),
            is_setting=True,
            name='transmission_fund')
        self._append(
            AdjustableGetSet,
            self._transmission_3rdharm.get_current_value,
            lambda value: set_transmission(value,harm=3),
            is_setting=True,
            name='transmission_3rdharm')
        

    def updateE(self, energy=None):
        while not energy:
            energy = self.energy_monitor.get_current_value()
            if energy < self.E_min:
                energy = None
                print(
                    f"Machine photon energy is below {self.E_min} - waiting for the machine to recover"
                )
                sleep(self._sleeptime)
        self.energy_calc(energy)
        print("Calculating transmission for %s eV" % energy)
        return

    def set_transmission(self, value, harm=1, check_mode=True, energy=None):
        self._xp.close()
        self.updateE(energy)
        if harm == 1:
            self._set_energy_harm.set_target_value(0).wait()
            self._xp.open()
        elif harm == 2:
            self._set_energy_harm.set_target_value(1).wait()
            self._xp.open()
        else:
            raise Exception("Only first and 3rd harmonics implamented!")
        
        self._set_transmission(value)
        


    def get_moveDone(self):
        return all([m.get_moveDone() for m in self.motors])

    def get_current_value(self, *args, **kwargs):
        return self.transmission_fund.get_current_value(*args, **kwargs
                                                        )
    def set_target_value(self, *args, **kwargs):
        return self.transmission_fund.set_target_value(*args, **kwargs)

    # def get_limits(self):
    #     return (self.limit_low(), self.limit_high())

    # def set_limits(self, limit_low, limit_high):
    #     self.limit_low(limit_low)
    #     self.limit_high(limit_high)

    def stop(self):
        """Adjustable convention"""
        for m in self.motors:
            m.stop()
    
        

    # def move(self, value, check=True, wait=True, update_value_time=0.1, timeout=120):
    #     if check:
    #         lim_low, lim_high = self.get_limits()
    #         if not ((lim_low <= value) and (value <= lim_high)):
    #             raise AdjustableError("Soft limits violated!")
    #     self.updateE()
    #     self._xp.close()
    #     self.set_transmission(value)
    #     if wait:
    #         t_start = time.time()
    #         time.sleep(.2)
    #         while not self.get_moveDone():
    #             if (time.time() - t_start) > timeout:
    #                 raise AdjustableError(f"motion timeout reached in att motion")
    #             time.sleep(update_value_time)
    #         self._xp.open()

    # def set_target_value(self, value, hold=False, check=True):
    #     changer = lambda value: self.move(value, check=check, wait=True)
    #     return Changer(
    #         target=value,
    #         parent=self,
    #         changer=changer,
    #         hold=hold,
    #         stopper=self.stop,
    #     )



@spec_convenience
@value_property
class AttenuatorAramisOld(Assembly):
    def __init__(
        self, Id, E_min=1500, sleeptime=10, name=None, set_limits=[-52, 2], shutter=None
    ):
        super().__init__(name=name)
        self.pvname = Id
        self.E_min = E_min
        self._pv_status_str = PV(self.pvname + ":MOT2TRANS.VALD")
        self._pv_status_int = PV(self.pvname + ":IDX_RB")
        self._sleeptime = sleeptime
        self._xp = shutter
        self.motors = []
        for n in range(6):
            self._append(MotorRecord, f"{self.pvname}:MOTOR_{n+1}", name=f"motor{n+1}", is_setting=True, is_display=True)
            self.motors.append(self.__dict__[f"motor{n+1}"])
        self._append(AdjustableFS, f'/sf/bernina/config/eco/reference_values/{name}_limit_high.json', default_value=1, name="limit_high", is_setting=True)
        self._append(AdjustableFS, f'/sf/bernina/config/eco/reference_values/{name}_limit_low.json', default_value=0, name="limit_low", is_setting=True)
        self._append(AdjustablePv, "SAROP21-ARAMIS:ENERGY", name="energy_rb", is_setting=False, is_display=False)
        self._append(AdjustablePv, "SARUN:FELPHOTENE", name="energy_rb_backup", is_setting=False, is_display=False)
        self._append(AdjustablePv, self.pvname + ":ENERGY", name="energy", is_setting=True, is_display=False)
        self._append(AdjustablePv, self.pvname + ":TRANS_SP", name="set_transmission", is_setting=False, is_display=False)


        alias_fields = {
            "transmission": "TRANS_RB",
            "transmission_3rd": "TRANS3EDHARM_RB",
        }
        for an, af in alias_fields.items():
            ach = ":".join([self.pvname, af])
            self.alias.append(Alias(an, channel=ach, channeltype="CA"))

    def __str__(self):
        pass

    def __status__(self):
        pass

    def updateE(self, energy=None):
        while not energy:
            energy = self.energy_rb()
            if isnan(energy):
                energy = self.energy_rb_backup()*1000
            if energy < self.E_min:
                energy = None
                print(
                    f"Machine photon energy is below {self.E_min} - waiting for the machine to recover"
                )
                sleep(self._sleeptime)
        self.energy(energy)
        print("Calculating transmission for %s eV" % energy)
        return

    def set_transmission(self, value, energy=None):
        self.updateE(energy)
        PV(self.pvname + ":3RD_HARM_SP").put(0)
        self.transmission(value)

    def set_transmission_third_harmonic(self, value, energy=None):
        self.updateE(energy)
        PV(self.pvname + ":3RD_HARM_SP").put(1)
        self.transmission(value)

    def setE(self):
        pass

    def get_moveDone(self):
        return all([m.get_moveDone() for m in self.motors])

    def get_transmission(self, verbose=True):
        tFun = PV(self.pvname + ":TRANS_RB").value
        tTHG = PV(self.pvname + ":TRANS3EDHARM_RB").value
        if verbose:
            print("Transmission Fundamental: %s THG: %s" % (tFun, tTHG))
        return tFun, tTHG

    def get_current_value(self, *args, **kwargs):
        return self.get_transmission(*args, verbose=False, **kwargs)[0]

    def get_limits(self):
        return (self.limit_low(), self.limit_high())

    def set_limits(self, limit_low, limit_high):
        self.limit_low(limit_low)
        self.limit_high(limit_high)

    def stop(self):
        """Adjustable convention"""
        for m in self.motors:
            m.stop()
        print("STOPPING AT: \n" + get_transmission())
        pass

    def move(self, value, check=True, wait=True, update_value_time=0.1, timeout=120):
        if check:
            lim_low, lim_high = self.get_limits()
            if not ((lim_low <= value) and (value <= lim_high)):
                raise AdjustableError("Soft limits violated!")
        self.updateE()
        self._xp.close()
        self.set_transmission(value)
        if wait:
            t_start = time.time()
            time.sleep(.2)
            while not self.get_moveDone():
                if (time.time() - t_start) > timeout:
                    raise AdjustableError(f"motion timeout reached in att motion")
                time.sleep(update_value_time)
            self._xp.open()

    def set_target_value(self, value, hold=False, check=True):
        changer = lambda value: self.move(value, check=check, wait=True)
        return Changer(
            target=value,
            parent=self,
            changer=changer,
            hold=hold,
            stopper=self.stop,
        )

    def get_status(self):
        s_str = self._pv_status_str.get(as_string=True)
        s_int = self._pv_status_int.get()
        return s_str, s_int

    def __repr__(self):
        t = self.get_transmission()
        s = "1st harm. transmission = %g\n" % t[0]
        s += "3rd harm. transmission = %g\n" % t[1]
        s += "Targets in beam:\n"
        s += "%s" % self.get_status()[0]
        return s


class AttenuatorAramisStandalone(Assembly):
    def __init__(self, pvname, path_cfg="~/eco/att135_cfg", shutter=None, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self.E_min = E_min
        self.shutter = shutter
        self.cfg = AdjustableFS(path_cfg, name="cfg")
        for n in range(6):
            self._append(
                MotorRecord,
                f"{self.pvname}:MOTOR_{n+1}",
                name=f"motor{n+1}",
                is_setting=True,
                is_display=False,
            )
