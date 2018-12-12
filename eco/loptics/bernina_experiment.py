from ..aliases import Alias
from ..devices_general.motors import MotorRecord
from ..devices_general.smaract import SmarActRecord

from epics import PV
from ..devices_general.delay_stage import DelayStage
from ..devices_general.user_to_motor import User_to_motor

def addMotorRecordToSelf(self, Id=None, name=None):
    self.__dict__[name] = MotorRecord(Id, name=name)
    self.alias.append(self.__dict__[name].alias)

def addSmarActRecordToSelf(self, Id=None, name=None):
    self.__dict__[name] = SmarActRecord(Id, name=name)
    self.alias.append(self.__dict__[name].alias)

def addDelayStageToSelf(self, stage=None, name=None):
    self.__dict__[name] = DelayStage(stage, name=name)
    self.alias.append(self.__dict__[name].alias)

class Laser_Exp:
    def __init__(self, Id=None, name=None):
        self.Id = Id
        self.IdExp1 = 'SARES20-EXP'
        self.IdSA = 'SARES23'
        self.name=name
        self.alias=Alias(name)
        
        #Waveplate and Delay stage
        addMotorRecordToSelf(self, self.Id+'-M534:MOT', name="wp")
        try:  
            addMotorRecordToSelf(self, Id=self.Id+'-M521:MOTOR_1', name="_eos_delay")
            addDelayStageToSelf(self,stage=self.__dict__["_eos_delay"], name="eos_delay")
        except:
            print('No eos delay stage')
            pass
        try:  
            addMotorRecordToSelf(self, Id=self.Id+'-M522:MOTOR_1', name="_thz_delaystg")
            addDelayStageToSelf(self,self.__dict__["_thz_delaystg"], name="spatialenc_delay")
            #addDelayStageToSelf(self,self.__dict__["_thz_delaystg"], name="thz_delay")
        except:
            print('No thz delay stage')
            pass

        #compressor
        addMotorRecordToSelf(self, Id=self.Id+'-M532:MOT', name="compressor")
        #self.compressor = MotorRecord(Id+'-M532:MOT')

        #LAM delay stages
        addSmarActRecordToSelf(self, Id='SLAAR21-LMTS-LAM11', name="_lam_delay_smarstg")
        addDelayStageToSelf(self,self.__dict__["_lam_delay_smarstg"], name="lam_delay_smar")
        #self._lam_delayStg_Smar = SmarActRecord('SLAAR21-LMTS-LAM11')
        #self.lam_delay_Smar = DelayStage(self._lam_delayStg_Smar)

        addMotorRecordToSelf(self, Id=self.Id+'-M548:MOT', name="_lam_delaystg")
        addDelayStageToSelf(self,self.__dict__["_lam_delaystg"], name="lam_delay")
        #self._lam_delayStg = MotorRecord(self.Id+'-M548:MOT')
        #self.lam_delay = DelayStage(self._lam_delayStg)

        #PALM delay stages 
        addMotorRecordToSelf(self, Id=self.Id+'-M552:MOT', name="_palm_delaystg")
        addDelayStageToSelf(self,self.__dict__["_palm_delaystg"], name="palm_delay")
        #self._palm_delayStg = MotorRecord(self.Id+'-M552:MOT')
        #self.palm_delay = DelayStage(self._palm_delayStg)

        #PSEN delay stages
        #self._psen_delayStg = MotorRecord(self.Id+'')
        #self.psen_delay = DelayStage(self._pump_delayStg)

        #SmarAct ID
        ### Mirrors used in the experiment ###
        try:
            addSmarActRecordToSelf(self, Id=self.IdSA+'-ESB18', name="spatialenc_rot")
            #self._eos_rot = SmarActRecord(self.IdSA+'-ESB18')
            #self.eos_rot = User_to_motor(self._eos_rot,180./35.7,0.)
        except:
            print('No Smaract EOSrot')
            pass

        try:
            addSmarActRecordToSelf(self, Id=self.IdSA+'-ESB2', name="eos_gonio")
        except:
            print('No Smaract EOSGonio')
            pass

        try:
            addSmarActRecordToSelf(self, Id=self.IdSA+'-ESB1', name="eos_x")
        except:
            print('No Smaract EOSx')
            pass

        try:
            addSmarActRecordToSelf(self, Id=self.IdSA+'-ESB16', name="thz_rot")
            #self.thz_rot = User_to_motor(self._thz_rot,180./35.7,0.)
        except:
            print('No Smaract THzrot')
            pass

        try:
            addSmarActRecordToSelf(self, Id=self.IdSA+'-ESB5', name="thz_gonio")
        except:
            print('No Smaract THzGonio')
            pass
        
        try:
            addSmarActRecordToSelf(self, Id=self.IdSA+'-ESB4', name="tar_t")
            #self.thz_z = SmarActRecord(self.IdSA+'-ESB4')
        except:
            print('No Smaract THzZ')
            pass

        try:
            addSmarActRecordToSelf(self, Id=self.IdSA+'-ESB6', name="spatialenc_gon")
            #self.par_x = SmarActRecord(self.IdSA+'-ESB6')
        except:
            print('No Smaract ParX')
            pass
        try:
            addSmarActRecordToSelf(self, Id=self.IdSA+'-ESB3', name="par_z")
        except:
            print('No Smaract ParZ')
            pass


        ### Motors on EXP1 deltatau
        try:
            addMotorRecordToSelf(self, Id=self.IdExp1+':MOT_VT80', name="tar_y")
            #self.par_y = MotorRecord(self.IdExp1+':MOT_VT80')
        except:
            print('No Smaract ParY')
            pass


    def get_adjustable_positions_str(self):
        ostr = '*****Laser motor positions******\n'

        for tkey,item in self.__dict__.items():
            if hasattr(item,'get_current_value'):
                pos = item.get_current_value()
                ostr += '  ' + tkey.ljust(17) + ' : % 14g\n'%pos
        return ostr
                



    def __repr__(self):
        return self.get_adjustable_positions_str()
        
