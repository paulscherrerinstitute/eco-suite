from enum import IntEnum
from eco.elements.adjustable import spec_convenience
from eco.elements.detector import DetectorGet, DetectorVirtual
from epics import PV

from eco.elements.assembly import Assembly
from eco.epics.adjustable import AdjustablePv, AdjustablePvEnum
from eco.epics.detector import DetectorPvData
from eco.timing.event_timing_new_new import EvrOutput, EvrPulser
from ..devices_general.utilities import Changer
from time import sleep
import numpy as np
from ..devices_general.motors import MotorRecord

from ..aliases import Alias


def addMotorRecordToSelf(self, name=None, Id=None):
    try:
        self.__dict__[name] = MotorRecord(Id, name=name)
        self.alias.append(self.__dict__[name].alias)
    except:
        print(f"Warning! Could not find motor {name} (Id:{Id})")


class Pulsepick:
    def __init__(self, Id=None, evronoff=None, evrsrc=None, name=None):
        self.name = name
        self.alias = Alias(name)
        self.evrsrc = evrsrc
        self.evronoff = evronoff

        self.Id = Id
        self._openclose = PV(self.evronoff)
        self._evrsrc = PV(self.evrsrc)
        addMotorRecordToSelf(self, Id=self.Id + ":MOTOR_X1", name="x")
        addMotorRecordToSelf(self, Id=self.Id + ":MOTOR_Y1", name="y")

    def movein(self):
        self.x.set_target_value(4.45)
        self.y.set_target_value(-1.75)

    def moveout(self):
        self.x.set_target_value(-5)
        self.y.set_target_value(-1.75)

    def open(self):
        self._openclose.put(1)
        #self._evrsrc.put(62)
        print("Opened Pulse Picker")

    def close(self):
        self._openclose.put(0)
        #self._evrsrc.put(62)
        print("Closed Pulse Picker")

    def trigger(self):
        self._openclose.put(1)
        self._evrsrc.put(0)
        print("Set Pulse Picker to trigger (src 0 and output On)")

    def get_status(self):
        stat = self._evrsrc.get()
        if stat == 62 and self._openclose.get() == 1:
            return "open"
        if self._openclose.get() == 0:
            return "closed"
        else:
            return "unknown"

    def __repr__(self):
        return f"FEL pulse picker state {self.get_status()}."

@spec_convenience
class XrayPulsePicker(Assembly):
    def __init__(self,pvbase="",
                 evr_output_base=None, 
                 evr_pulser_base=None, 
                 evronoff=None, evrsrc=None, event_master = None, name=None):
        super().__init__(name=name)
        self.evrsrc = evrsrc
        self.evronoff = evronoff
        
        self._append(AdjustablePv,self.evronoff,name="evr_output_enable",is_display=False, is_setting=True)
        self._append(AdjustablePv,self.evrsrc,name="evr_output_source",is_display=False, is_setting=True)
        
        self.pvbase=pvbase
        self._append(AdjustablePvEnum,self.pvbase+":PRESET_SP", name="position")
        self._append(MotorRecord,self.pvbase+":MOTOR_X1", name="x", is_setting=True)
        self._append(MotorRecord,self.pvbase+":MOTOR_Y1", name="y", is_setting=True)
        self._append(AdjustablePv,self.pvbase+":MOTOR_X1_PRESET_SP.B",name="out_x_pos",is_display=False, is_setting=True)
        self._append(AdjustablePv,self.pvbase+":MOTOR_X1_PRESET_SP.C",name="in_x_pos",is_display=False, is_setting=True)
        self._append(AdjustablePv,self.pvbase+":MOTOR_Y1_PRESET_SP.B",name="out_y_pos",is_display=False, is_setting=True)
        self._append(AdjustablePv,self.pvbase+":MOTOR_Y1_PRESET_SP.C",name="in_y_pos",is_display=False, is_setting=True)
        self._append(DetectorPvData,self.pvbase+":TC1",name="temperature",is_display=True, unit="°C")
        pulser = EvrPulser(evr_pulser_base, event_master, name="pulser_xp")
        self._append(EvrOutput,evr_output_base, pulsers=[pulser], name='evr_output', is_display=False)
        self._append(DetectorGet,self.get_picker_status,name='picker_status')
        self._STATENUM = IntEnum('Pulsepicker_open', {'open': 1, 'closed':0, 'unknown':-1})
        
    def get_picker_status(self):
        stat = self.evr_output_source.get_current_value()
        if stat == 62 and self.evr_output_enable.get_current_value() == 1:
            return "open"
        if self.evr_output_enable.get_current_value() == 0:
            return "closed"
        else:
            return "unknown"
        
    def open(self,verbose=True):
        self.evr_output_enable.set_target_value(1).wait()
        #self._evrsrc.put(62)
        if verbose:
            print("Opened Pulse Picker")

    def close(self,verbose=True):
        self.evr_output_enable.set_target_value(0).wait()
        #self._evrsrc.put(62)
        if verbose:
            print("Closed Pulse Picker")

    def set_target_value(self,value,hold=False):
        if value:
            return Changer(changer=lambda v:self.open() ,hold=hold)
        else:
            return Changer(changer=lambda v:self.close(),hold=hold)
    
    def get_current_value(self):
        stat = self.get_picker_status()
        if stat=='open':
            return self._STATENUM(1)
        elif stat=='closed':
            return self._STATENUM(0)
        if stat=='unknown':
            return self._STATENUM(-1)