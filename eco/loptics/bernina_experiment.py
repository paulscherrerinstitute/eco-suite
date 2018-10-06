from ..devices_general.motors import MotorRecord
from ..devices_general.smaract import SmarActRecord

from epics import PV
from ..devices_general.delay_stage import DelayStage
from ..devices_general.user_to_motor import User_to_motor


class Laser_Exp:
    def __init__(self, Id):
        self.Id = Id

        try:
            self.lensx = MotorRecord("SARES20-EXP:MOT_DIODE")
        except:
            print("No owis lens x motor")
            pass

        # Waveplate and Delay stage
        self.wp = MotorRecord(Id + "-M534:MOT")

        self._pump_delayStg = MotorRecord(self.Id + "-M521:MOTOR_1")
        self.pump_delay = DelayStage(self._pump_delayStg)

        # LAM delay stages
        self._lam_delayStg_Smar = SmarActRecord("SLAAR21-LMTS-LAM11")
        self.lam_delay_Smar = DelayStage(self._lam_delayStg_Smar)

        self._lam_delayStg = MotorRecord(self.Id + "-M548:MOT")
        self.lam_delay = DelayStage(self._lam_delayStg)

        # PALM delay stages
        self._palm_delayStg = MotorRecord(self.Id + "-M552:MOT")
        self.palm_delay = DelayStage(self._palm_delayStg)

        # PSEN delay stages
        # self._psen_delayStg = MotorRecord(self.Id+'')
        # self.psen_delay = DelayStage(self._pump_delayStg)

        # SmarAct ID
        self.IdSA = "SARES23"

        ### Mirrors used in the expeirment ###
        try:
            self.eos_rot = SmarActRecord(self.IdSA + "-ESB18")
        except:
            print("No Smaract EOSrot")
            pass

        try:
            self.eos_gonio = SmarActRecord(self.IdSA + "-ESB3")
        except:
            print("No Smaract EOSGonio")
            pass

        try:
            self._pump_rot = SmarActRecord(self.IdSA + "-ESB16")
            self.pump_rot = User_to_motor(self._pump_rot, 180.0 / 35.7, 0.0)
        except:
            print("No Smaract THzrot")
            pass

        try:
            self.pump_gonio = SmarActRecord(self.IdSA + "-ESB2")
        except:
            print("No Smaract THzGonio")
            pass

        try:
            self.pump_x = SmarActRecord(self.IdSA + "-ESB1")
        except:
            print("No Smaract THzZ")
            pass

        try:
            self.par_x = SmarActRecord(self.IdSA + "-ESB5")
        except:
            print("No Smaract ParX")
            pass
        try:
            self.par_z = SmarActRecord(self.IdSA + "-ESB4")
        except:
            print("No Smaract ParZ")
            pass

    def get_adjustable_positions_str(self):
        ostr = "*****SmarAct motor positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(10) + " : % 14g\n" % pos
        return ostr

    # def pos(self):
    #    s = []
    #    for i in sorted(self.__dict__.keys()):
    #        s.append[i]
    #    for n, mo^tor in enumerate (s):
    #        s[n] += ':  ' + str(self.__dict__[motor])
    #    return s

    def __repr__(self):
        return self.get_adjustable_positions_str()
