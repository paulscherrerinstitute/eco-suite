import sys

sys.path.append("..")
from ..devices_general.motors import MotorRecord
from ..devices_general.smaract import SmarActRecord
from ..devices_general.adjustable import PvRecord
import numpy as np
from epics import PV
from ..aliases import Alias, append_object_to_object
from time import sleep
import escape.parse.swissfel as sf
from ..bernina import config
import pylab as plt
import escape
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
    def __init__(self, name=None, Id=None, alias_namespace=None):
        self.Id = Id
        self.name = name
        self.alias = Alias(name)

        self.motor_configuration = {
            "rx": {
                "id": "-ESB13",
                "pv_descr": "Motor7:1 THz Chamber Rx",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "x": {
                "id": "-ESB14",
                "pv_descr": "Motor7:2 THz Chamber x ",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "z": {
                "id": "-ESB10",
                "pv_descr": "Motor6:1 THz Chamber z ",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "ry": {
                "id": "-ESB11",
                "pv_descr": "Motor6:2 THz Chamber Ry",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "rz": {
                "id": "-ESB12",
                "pv_descr": "Motor6:3 THz Chamber Rz",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
        }

        ### in vacuum smaract motors ###
        for name, config in self.motor_configuration.items():
            addSmarActRecordToSelf(self, Id=Id + config["id"], name=name)

    def set_stage_config(self):
        for name, config in self.motor_configuration.items():
            mot = self.__dict__[name]._device
            mot.put("NAME", config["pv_descr"])
            mot.put("STAGE_TYPE", config["type"])
            mot.put("SET_SENSOR_TYPE", config["sensor"])
            mot.put("CL_MAX_FREQ", config["speed"])
            sleep(0.5)
            mot.put("CALIBRATE.PROC", 1)

    def home_smaract_stages(self, stages=None):
        if stages == None:
            stages = self.motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = self.motor_configuration[name]
            mot = self.__dict__[name]._device
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.put("FRM_BACK.PROC", 1)
                while mot.get("STATUS") == 7:
                    sleep(1)
                if mot.get("GET_HOMED") == 0:
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.put("FRM_FORW.PROC", 1)
            elif config["home_direction"] == "forward":
                mot.put("FRM_FORW.PROC", 1)
                while mot.get("STATUS") == 7:
                    sleep(1)
                if mot.get("GET_HOMED") == 0:
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.put("FRM_BACK.PROC", 1)

    def get_adjustable_positions_str(self):
        ostr = "*****THz chamber motor positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def __repr__(self):
        return self.get_adjustable_positions_str()


class High_field_thz_table:
    def __init__(self, name=None, Id=None, alias_namespace=None):
        self.Id = Id
        self.name = name
        self.alias = Alias(name)

        self.motor_configuration = {
            "mirr2_x": {
                "id": "-ESB1",
                "pv_descr": "Motor3:1 THz mirror x ",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "mirr2_rz": {
                "id": "-ESB2",
                "pv_descr": "Motor3:2 THz mirror rz ",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "mirr2_ry": {
                "id": "-ESB3",
                "pv_descr": "Motor3:3 THz mirror ry ",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "mirr2_z": {
                "id": "-ESB4",
                "pv_descr": "Motor4:1 THz mirror z",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "par2_x": {
                "id": "-ESB5",
                "pv_descr": "Motor4:2 THz parabola2 x",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "mirr1_x": {
                "id": "-ESB7",
                "pv_descr": "Motor5:1 near IR mirror x",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "mirr1_ry": {
                "id": "-ESB8",
                "pv_descr": "Motor5:2 near IR mirror ry",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "mirr1_rx": {
                "id": "-ESB9",
                "pv_descr": "Motor5:3 near IR mirror rx",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
        }

        ### in vacuum smaract motors ###
        for name, config in self.motor_configuration.items():
            addSmarActRecordToSelf(self, Id=Id + config["id"], name=name)

    def set_stage_config(self):
        for name, config in self.motor_configuration.items():
            mot = self.__dict__[name]._device
            mot.put("NAME", config["pv_descr"])
            mot.put("STAGE_TYPE", config["type"])
            mot.put("SET_SENSOR_TYPE", config["sensor"])
            mot.put("CL_MAX_FREQ", config["speed"])
            sleep(0.5)
            mot.put("CALIBRATE.PROC", 1)

    def home_smaract_stages(self, stages=None):
        if stages == None:
            stages = self.motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = self.motor_configuration[name]
            mot = self.__dict__[name]._device
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.put("FRM_BACK.PROC", 1)
                while mot.get("STATUS") == 7:
                    sleep(1)
                if mot.get("GET_HOMED") == 0:
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.put("FRM_FORW.PROC", 1)
            elif config["home_direction"] == "forward":
                mot.put("FRM_FORW.PROC", 1)
                while mot.get("STATUS") == 7:
                    sleep(1)
                if mot.get("GET_HOMED") == 0:
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.put("FRM_BACK.PROC", 1)

    def get_adjustable_positions_str(self):
        ostr = "*****THz table motor positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def __repr__(self):
        return self.get_adjustable_positions_str()


class electro_optic_sampling:
    def __init__(self, name=None, Id=None, alias_namespace=None, pgroup=None, diode_channels=None):
        self.Id = Id
        self.name = name
        self.alias = Alias(name)
        self.diode_channels = diode_channels
        self.basepath = f'/sf/bernina/data/{pgroup}/res/scan_info/'
        self.motor_configuration = {
            "ry": {
                "id": "-ESB16",
                "pv_descr": "Motor8:1 EOS prism ry ",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "rx": {
                "id": "-LIC11",
                "pv_descr": "Motor6:2 EOS prism rx ",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "x": {
                "id": "-LIC10",
                "pv_descr": "Motor6:1 EOS prism x ",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
        }

        ### in vacuum smaract motors ###
        for name, config in self.motor_configuration.items():
            addSmarActRecordToSelf(self, Id=Id + config["id"], name=name)

    def set_stage_config(self):
        for name, config in self.motor_configuration.items():
            mot = self.__dict__[name]._device
            mot.put("NAME", config["pv_descr"])
            mot.put("STAGE_TYPE", config["type"])
            mot.put("SET_SENSOR_TYPE", config["sensor"])
            mot.put("CL_MAX_FREQ", config["speed"])
            sleep(0.5)
            mot.put("CALIBRATE.PROC", 1)

    def home_smaract_stages(self, stages=None):
        if stages == None:
            stages = self.motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = self.motor_configuration[name]
            mot = self.__dict__[name]._device
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.put("FRM_BACK.PROC", 1)
                while mot.get("STATUS") == 7:
                    sleep(1)
                if mot.get("GET_HOMED") == 0:
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.put("FRM_FORW.PROC", 1)
            elif config["home_direction"] == "forward":
                mot.put("FRM_FORW.PROC", 1)
                while mot.get("STATUS") == 7:
                    sleep(1)
                if mot.get("GET_HOMED") == 0:
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.put("FRM_BACK.PROC", 1)

    def get_adjustable_positions_str(self):
        ostr = "*****EOS motor positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr


    def loaddata(self, fname, diode_channels=None):
        data = sf.parseScanEco_v01(self.basepath+fname)
        print(data)
        dat = {name: data[ch].data.compute() for name, ch in diode_channels.items()}
        ch = list(diode_channels.values())[0]
        xlab = list(data[ch].scan.parameter.keys())[0]
        x = data[ch].scan.parameter[xlab]['values']
        shots = len(list(dat.values())[0])//len(x)
        numsteps = len(list(dat.values())[0])//shots
        daton = np.ndarray((numsteps,))
        datoff = np.ndarray((numsteps,))
        datmean = {name: np.array([ dat[name][n*shots:(n+1)*shots].mean() for n in range(numsteps)]) for name in dat.keys()}
        return {xlab: x}, datmean, dat


    def plotEOS(self,fname,diode_channels=None):
        if diode_channels is None:
            diode_channels = self.diode_channels
        x, datmean, dat = self.loaddata(fname, diode_channels)
        x_motor, x = list(x.items())[0]

        for name, data in datmean.items():
            plt.figure()
            plt.title(name)
            plt.plot(x, data)
            plt.xlabel(x_motor)

        #plt.figure('difference')
        #plt.plot(motors, dat0-dat1)
        #plt.figure('sum')
        #plt.clf()
        #plt.plot(motors, dat0+dat1)
        #plt.plot(motors, dat0)
        #plt.plot(motors, dat1)
    
        #plt.figure('diff/sum')
        #plt.plot(motors,  (dat0-dat1)/(dat0+dat1), '-o')
        return 
    
    def calcField(self, DiffoverSum, L = 100e-6, wl = 800e-9, r41 = 0.97e-12, n0 = 3.19):
        #Parameters: L: GaP thickness, lambda: EOS wavelength, r41: EO coefficient, n0: refractive index of EOS sampling
        #Field transmission assuming that n(THz) = n(opt)
        #Angle between THz polarization and (001)
        alpha = 90./180*np.pi
        #angle between probe polarization and (001)
        th = 0./180*np.pi
        t = 2./(n0+1)
        geoSens = np.cos(alpha)*np.sin(2*th)+2*np.sin(alpha)*np.cos(2*th)
        E = np.arcsin(DiffoverSum)*wl / (np.pi*L*r41*n0**3*t)/geoSens

        return E
     
    
    
    def __repr__(self):
        return self.get_adjustable_positions_str()
