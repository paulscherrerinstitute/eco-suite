import sys

sys.path.append("..")
from ..devices_general.motors import MotorRecord
from ..devices_general.smaract import SmarActRecord
from ..devices_general.adjustable import PvRecord

from epics import PV
from ..aliases import Alias, append_object_to_object
from time import sleep



def addMotorRecordToSelf(self, name=None, Id=None):
    try:
        self.__dict__[name] = MotorRecord(Id, name=name)
        self.alias.append(self.__dict__[name].alias)
    except:
        print(f"Warning! Could not find motor {name} (Id:{Id})")

def addSmarActRecordToSelf(self, Id=None, name=None):
        self.__dict__[name] = SmarActRecord(Id, name=name)
        self.alias.append(self.__dict__[name].alias)



class High_field_thz_chamber:
    def __init__(
        self, name=None, Id=None, alias_namespace=None
    ):
        self.Id = Id
        self.name = name
        self.alias = Alias(name)

        self.motor_configuration = {
            'rx': {'id': '-ESB4',  'pv_descr': 'Motor4:1 THz Chamber Rx', 'type': 2, 'sensor': 1, 'speed': 250, 'home_direction': 'back'},
            'x':  {'id': '-ESB5',  'pv_descr': 'Motor5:2 THz Chamber x ', 'type': 1, 'sensor': 0, 'speed': 250, 'home_direction': 'back'},
            'z':  {'id': '-ESB10', 'pv_descr': 'Motor6:1 THz Chamber z ', 'type': 1, 'sensor': 0, 'speed': 250, 'home_direction': 'back'},
            'ry': {'id': '-ESB11', 'pv_descr': 'Motor6:2 THz Chamber Ry', 'type': 2, 'sensor': 1, 'speed': 250, 'home_direction': 'back'},
            'rz': {'id': '-ESB12', 'pv_descr': 'Motor6:3 THz Chamber Rz', 'type': 2, 'sensor': 1, 'speed': 250, 'home_direction': 'back'},
            }

        ### in vacuum smaract motors ###
        for name, config in self.motor_configuration.items():
            addSmarActRecordToSelf(self, Id=Id + config['id'], name=name)


    def set_stage_config(self):
        for name, config in self.motor_configuration.items():
            mot = self.__dict__[name]._device
            mot.put('NAME', config['pv_descr'])
            mot.put('STAGE_TYPE', config['type'])
            mot.put('SET_SENSOR_TYPE', config['sensor'])
            mot.put('CL_MAX_FREQ', config['speed'])
            sleep(0.5)
            mot.put('CALIBRATE.PROC',1)
    
    def home_smaract_stages(self, stages = None):
        if stages == None:
            stages = self.motor_configuration.keys()
        print('#### Positions before homing ####')
        print(self.__repr__())
        for name in stages:
            config = self.motor_configuration[name]
            mot = self.__dict__[name]._device
            print('#### Homing {} in {} direction ####'.format(name, config['home_direction']))
            sleep(1)
            if config['home_direction'] == 'back':
                mot.put('FRM_BACK.PROC', 1)
                while mot.get('STATUS') == 7:
                    sleep(1)
                if mot.get('GET_HOMED') == 0:
                    print('Homing failed, try homing {} in forward direction'.format(name))
                    mot.put('FRM_FORW.PROC', 1)
            elif config['home_direction'] == 'forward':
                mot.put('FRM_FORW.PROC', 1)
                while mot.get('STATUS') == 7:
                    sleep(1)
                if mot.get('GET_HOMED') == 0:
                    print('Homing failed, try homing {} in backward direction'.format(name))
                    mot.put('FRM_BACK.PROC', 1)



    def get_adjustable_positions_str(self):
        ostr = "*****THz chamber motor positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def __repr__(self):
        return self.get_adjustable_positions_str()


