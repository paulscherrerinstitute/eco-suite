from scipy import constants
from eco.devices_general.powersockets import MpodChannel
from eco.devices_general.wago import AnalogOutput
from eco.epics.detector import DetectorPvDataStream
import sys

from eco.elements.detector import DetectorVirtual

sys.path.append("..")
from ..devices_general.motors import (
    MotorRecord,
    SmaractRecord,
    ThorlabsPiezoRecord,
    SmarActOpenLoopRecord,
)
from ..epics.adjustable import AdjustablePv
import numpy as np
from epics import PV
from ..aliases import Alias, append_object_to_object
from time import sleep
import escape.parse.swissfel as sf
import pylab as plt
import escape
from pathlib import Path
from ..elements.adjustable import AdjustableVirtual, AdjustableFS
from ..elements.assembly import Assembly
from ..loptics.bernina_laser import DelayTime
from mpl_toolkits import mplot3d
import matplotlib.pyplot as plt
from eco.epics.detector import DetectorPvData

import numpy as np
from scipy.spatial.transform import Rotation


def addMotorRecordToSelf(self, name=None, Id=None):
    try:
        self.__dict__[name] = MotorRecord(Id, name=name)
        self.alias.append(self.__dict__[name].alias)
    except:
        print(f"Warning! Could not find motor {name} (Id:{Id})")


def addSmarActRecordToSelf(self, Id=None, name=None, **kwargs):
    self.__dict__[name] = SmaractRecord(Id, name=name, **kwargs)
    self.alias.append(self.__dict__[name].alias)


class THzVirtualStages(Assembly):
    def __init__(self, name=None, mz=None, pz=None):
        super().__init__(name=name)
        self._mz = mz
        self._pz = pz
        self._append(
            AdjustableFS,
            "/photonics/home/gac-bernina/eco/configuration/p21145_mirr_z0",
            name="offset_mirr_z",
            default_value=0,
            is_setting=True,
        )
        self._append(
            AdjustableFS,
            "/photonics/home/gac-bernina/eco/configuration/p21145_par_z0",
            name="offset_par_z",
            default_value=0,
            is_setting=True,
        )

        def get_focus_z(mz, pz):
            return pz - self.offset_par_z()

        def set_focus_z(z):
            mz = self.offset_mirr_z() + z
            pz = self.offset_par_z() + z
            return mz, pz

        self._append(
            AdjustableVirtual,
            [mz, pz],
            get_focus_z,
            set_focus_z,
            reset_current_value_to=True,
            name="focus_virtual",
        )

    def set_offsets_to_current_value(self):
        self.offset_mirr_z.mv(self._mz())
        self.offset_par_z.mv(self._pz())


class High_field_thz_chamber(Assembly):
    def __init__(
        self,
        name=None,
        configuration=[],
        illumination_mpod=None,
        helium_control_valve=None,
    ):
        super().__init__(name=name)
        self.par_out_pos = [-20, -9.5]
        self.motor_configuration = {
            "rx": {
                # "id": "SARES23-USR:MOT_13",
                "id": "SARES23-USR:MOT_16",
                "pv_descr": "Module6:1 THz Chamber Rx",
                "direction": 1,
                "sensor": 47,
                "speed": 250,
                "home_direction": "back",
                "kwargs": {"accuracy": 0.01},
            },
            "x": {
                # "id": "SARES23-USR:MOT_14",
                "id": "SARES23-USR:MOT_17",
                "pv_descr": "Module6:2 THz Chamber x ",
                "direction": 1,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "z": {
                # "id": "SARES23-USR:MOT_10",
                "id": "SARES23-USR:MOT_13",
                "pv_descr": "Module5:1 THz Chamber z ",
                "direction": 0,
                "sensor": 1,
                "speed": 250,
                "home_direction": "forward",
            },
            "ry": {
                # "id": "SARES23-USR:MOT_11",
                "id": "SARES23-USR:MOT_14",
                "pv_descr": "Module5:2 THz Chamber Ry",
                "direction": 0,
                "sensor": 2,
                "speed": 250,
                "home_direction": "back",
            },
            "rz": {
                # "id": "SARES23-USR:MOT_12",
                "id": "SARES23-USR:MOT_15",
                "pv_descr": "Module5:3 THz Chamber Rz",
                "direction": 0,
                "sensor": 48,
                "speed": 250,
                "home_direction": "back",
            },
        }

        self.motor_configuration_cube = {
            "inc_rz": {
                "id": "SARES23-USR:MOT_5",
                "pv_descr": "Module2:2 THz Inc Cube Rz",
                "direction": 1,
                "sensor": 53,
                "speed": 250,
                "home_direction": "back",
                "kwargs": {"accuracy": 0.01},
            },
            "inc_z": {
                "id": "SARES23-USR:MOT_4",
                "pv_descr": "Module2:1 THz Inc Cube z ",
                "direction": 1,
                "sensor": 42,
                "speed": 250,
                "home_direction": "back",
            },
            "inc_x": {
                "id": "SARES23-USR:MOT_6",
                "pv_descr": "Module2:3 THz Inc Cube x ",
                "direction": 1,
                "sensor": 42,
                "speed": 250,
                "home_direction": "forward",
            },
            "inc_ry": {
                "id": "SARES23-USR:MOT_18",
                "pv_descr": "Module6:3 THz Inc Cube Ry ",
                "direction": 0,
                "sensor": 2,
                "speed": 250,
                "home_direction": "forward",
            },
        }

        self.motor_configuration_ocb = {
            "inc_x": {
                "id": "SARES23-USR:MOT_6",
                "pv_descr": "Module2:3 THz Inc Cube x ",
                "direction": 1,
                "sensor": 42,
                "speed": 250,
                "home_direction": "forward",
            },
        }
        ### lakeshore temperatures ####
        self._append(
            AdjustablePv,
            pvsetname="SARES20-LS336:LOOP1_SP",
            pvreadbackname="SARES20-LS336:A_RBV",
            accuracy=0.1,
            name="temp_sample",
            is_setting=False,
        )
        self._append(
            AdjustablePv,
            pvsetname="SARES20-LS336:LOOP2_SP",
            pvreadbackname="SARES20-LS336:B_RBV",
            accuracy=0.1,
            name="temp_coldfinger",
            is_setting=False,
        )
        self._append(
            AdjustablePv,
            pvsetname="SARES20-LS336:LOOP3_SP",
            pvreadbackname="SARES20-LS336:C_RBV",
            accuracy=0.1,
            name="temp_gishield",
            is_setting=False,
        )
        ### in vacuum smaract motors ###
        # for name, config in self.motor_configuration.items():
        #    if "kwargs" in config.keys():
        #        tmp_kwargs = config["kwargs"]
        #    else:
        #        tmp_kwargs = {}
        #    self._append(
        #        SmaractStreamdevice,
        #        pvname=Id + config["id"],
        #        name=name,
        #        is_setting=True,
        #        **tmp_kwargs,
        #    )
        ### in vacuum smaract motors ###
        for name, config in self.motor_configuration.items():
            self._append(
                SmaractRecord,
                pvname=config["id"],
                name=name,
                is_setting=True,
            )

        self._append(
            AdjustableFS,
            "/sf/bernina/config/eco/reference_values/thc_parabola_center.json",
            name="x_center",
            is_setting=True,
        )

        self._append(
            DetectorVirtual,
            [self.x, self.x_center],
            lambda x, xc: -1 * (x - xc) / 1000 / constants.c,
            name="delay_x_center",
        )

        if "cube" in configuration:
            for name, config in self.motor_configuration_cube.items():
                self._append(
                    SmaractRecord,
                    pvname=config["id"],
                    name=name,
                    is_setting=True,
                )

            def home_smaract_stages_cube():
                return self.home_smaract_stages(
                    motor_configuration=self.motor_configuration_cube
                )

            def set_stage_config_cube():
                return self.set_stage_config(cfg=self.motor_configuration_cube)

            self.home_smaract_stages_cube = home_smaract_stages_cube
            self.set_stage_config_cube = set_stage_config_cube

            ### Virtual stages ###
            self._append(
                THzVirtualStages,
                name="virtual_stages",
                mz=self.inc_z,
                pz=self.z,
                is_setting=False,
            )
        if "ocb" in configuration:
            pass

        self._append(
            AdjustableFS,
            "/sf/bernina/config/eco/reference_values/thc_par_in_pos",
            name="par_in_pos",
            is_setting=False,
        )
        if "ottifant" in configuration:
            self._append(
                MotorRecord,
                "SARES20-EXP:MOT_RY",
                name="otti_nu",
                is_display=True,
                is_setting=True,
            )
            self._append(
                MotorRecord,
                "SARES20-EXP:MOT_RZ",
                name="otti_del",
                is_display=True,
                is_setting=True,
            )
            self._append(
                DetectorPvData,
                "SARES20-EXP:DET_RY.RBV",
                name="otti_det",
                is_display=True,
            )
            self._append(
                AdjustablePv,
                "SARES20-EXP:DET_RY.OFF",
                name="otti_det_offset",
                is_display=False,
                is_setting=True,
            )
            self._append(
                AdjustableFS,
                "/sf/bernina/config/eco/reference_values/otti_det_rot_offset.json",
                name="otti_det_rotation",
                is_display=True,
                is_setting=True,
            )
        if illumination_mpod:
            for illu in illumination_mpod:
                self._append(
                    MpodChannel,
                    illu["pvbase"],
                    illu["channel_number"],
                    module_string=illu["module_string"],
                    name=illu["name"],
                )

        if helium_control_valve:
            # self._append(
            #     MpodChannel,
            #     helium_control_valve["pvbase"],
            #     helium_control_valve["channel_number"],
            #     module_string=helium_control_valve["module_string"],
            #     name="_helium_valve_mpod_ch",
            #     is_display=True,
            #     is_setting=True,
            # )
            self._append(
                AnalogOutput,
                helium_control_valve["pvname"],
                name="_helium_valve_mpod_ch",
                is_display=True,
                is_setting=True,
            )

            def get_valve(voltage):
                if voltage < 2.9:
                    val = 0
                elif voltage > 5.5:
                    val = 100
                else:
                    val = (voltage - 2.9) / (5.5 - 2.9) * 100
                return val

            def set_valve(val):
                if val < 1:
                    voltage = 0.5
                else:
                    voltage = val * (5.5 - 2.9) / 100 + 2.9
                return voltage

            self._append(
                AdjustableVirtual,
                [self._helium_valve_mpod_ch.value],
                get_valve,
                set_valve,
                name=helium_control_valve["name"],
                is_display=True,
                is_setting=False,
            )

    def moveout(self):
        change_in_pos = str(
            input(
                f"Do you want to store the current parabola positions as the set values when moving the parabola back in (y/n)? "
            )
        )
        if change_in_pos == "y":
            self.par_in_pos.set_target_value(
                [self.x.get_current_value(), self.z.get_current_value()]
            )
        print(
            f"Moving parabola out. Previous positions (x,z): ({self.x.get_current_value()}, {self.z.get_current_value()}), target positions: ({self.par_out_pos[0]}, {self.par_out_pos[1]})"
        )
        self.z.set_target_value(self.par_out_pos[1]).wait()
        self.x.set_target_value(self.par_out_pos[0])

    def movein(self):
        print(
            f"Moving parabola in. Target positions (x,z): ({self.par_in_pos()[0]}, {self.par_in_pos()[1]})"
        )
        self.x.set_target_value(self.par_in_pos()[0]).wait()
        self.z.set_target_value(self.par_in_pos()[1])

    def set_stage_config(self, cfg=None):
        if cfg is None:
            cfg = self.motor_configuration
        for name, config in cfg.items():
            mot = self.__dict__[name]
            mot.description(config["pv_descr"])
            # mot.stage_type(config["type"])
            mot.motor_parameters.sensor_type_num(config["sensor"])
            mot.direction(config["direction"])
            mot.motor_parameters.max_frequency(config["speed"])
            sleep(0.5)
            mot.calibrate_sensor()

    def home_smaract_stages(self, motor_configuration=None):
        if motor_configuration == None:
            motor_configuration = self.motor_configuration
        stages = motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = motor_configuration[name]
            mot = self.__dict__[name]
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.home_reverse(1)
                sleep(0.5)
                while not mot.flags.motion_complete():
                    sleep(1)
                if not mot.flags.is_homed():
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.home_forward(1)
            elif config["home_direction"] == "forward":
                mot.home_forward(1)
                sleep(0.5)
                while not mot.flags.motion_complete():
                    sleep(1)
                if not mot.flags.is_homed():
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.home_reverse(1)

    def calc_otti(
        self, otti_nu=None, otti_del=None, otti_det=None, plotit=True, **kwargs
    ):
        if otti_nu is None:
            otti_nu = self.otti_nu.get_current_value()
        if otti_del is None:
            otti_del = self.otti_del.get_current_value()
        if otti_det is None:
            otti_det = (
                self.otti_det.get_current_value()
                + self.otti_det_rotation.get_current_value()
            )
        elif otti_det == "auto":
            otti_det = self.otti_det_rotation.get_current_value() - otti_nu
        print(otti_nu, otti_del, otti_det)
        return calc_otti(otti_nu, otti_del, ottidet=otti_det, plotit=plotit, **kwargs)


class Organic_crystal_breadboard(Assembly):
    def __init__(self, delay_offset_detector=None, thc_x_adjustable=None, name=None):
        super().__init__(name=name)
        self.delay_offset_detector = delay_offset_detector
        self._thc_x_adjustable = thc_x_adjustable
        self.motor_configuration = {
            # "mir_x": {
            #    # "id": "-LIC17",
            #    "id": "-USR:MOT_8",
            #    "pv_descr": "Motor8:2 THz mirror x ",
            #    "type": 1,
            #    "sensor": 13,
            #    "speed": 250,
            #    "home_direction": "back",
            # },
            # "mir_rz": {
            #    # "id": "-LIC18",
            #    "id": "-USR:MOT_9",
            #    "pv_descr": "Motor8:3 THz mirror rz ",
            #    "type": 1,
            #    "sensor": 13,
            #    "speed": 250,
            #    "home_direction": "back",
            # },
            # "mir_ry": {
            #    # "id": "-ESB1",
            #    "id": "-LIC:MOT_18",
            #    "pv_descr": "Motor3:1 THz mirror ry ",
            #    "type": 2,
            #    "sensor": 1,
            #    "speed": 250,
            #    "home_direction": "forward",
            # },
            # "mir_z": {
            #    # "id": "-LIC16",
            #    "id": "-USR:MOT_7",
            #    "pv_descr": "Motor8:1 THz mirror z",
            #    "type": 1,
            #    "sensor": 13,
            #    "speed": 250,
            #    "home_direction": "back",
            # },
            # "par_x": {
            #    # "id": "-ESB3",
            #    "id": "-LIC:MOT_17",
            #    "pv_descr": "Motor3:3 THz parabola2 x",
            #    "type": 1,
            #    "sensor": 0,
            #    "speed": 250,
            #    "home_direction": "back",
            # },
            # "delaystage_thz": {
            #    "id": "-USR:MOT_1",
            #    "pv_descr": "Motor8:3 NIR delay stage",
            #    "type": 1,
            #    "sensor": 0,
            #    "speed": 100,
            #    "home_direction": "back",
            # },
            "nir_m1_ry": {
                "id": "SARES23-LIC:MOT_18",
                "pv_descr": "Module6:3 NIR Mirr1 Ry",
                "sensor": 2,
                "direction": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "nir_m1_rx": {
                "id": "SARES23-USR:MOT_10",
                "pv_descr": "Module4:1 NIR Mirr1 Rx",
                "sensor": 53,
                "speed": 250,
                "direction": 1,
                "home_direction": "back",
            },
            "nir_m2_ry": {
                "id": "SARES23-USR:MOT_1",
                "pv_descr": "Module1:1 NIR Mirr2 Ry",
                "sensor": 2,
                "speed": 250,
                "direction": 1,
                "home_direction": "back",
            },
            "nir_m2_rx": {
                # "id": "-USR:MOT_4",
                "id": "SARES23-USR:MOT_7",
                "pv_descr": "Module3:1 NIR Mirr2 rx",
                "sensor": 53,
                "direction": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "delay_400nm": {
                # "id": "-USR:MOT_4",
                "id": "SARES23-LIC:MOT_17",
                "pv_descr": "Module6:2 400nm_stage",
                "sensor": 2,
                "direction": 0,
                "speed": 250,
                "home_direction": "back",
            },
            # "crystal": {
            #    "id": "-USR:MOT_2",
            #    "pv_descr": "Motor3:2 crystal rotation",
            #    "type": 2,
            #    "sensor": 1,
            #    "speed": 250,
            #    "home_direction": "back",
            # },
            # "wp": {
            #    "id": "-USR:MOT_7",>
            #    "pv_descr": "Motor5:1 waveplate rotation",
            #    "type": 2,
            #    "sensor": 1,
            #    "speed": 250,
            #    "home_direction": "back",
            #    "direction": 1,
            # },
        }

        self.motor_configuration_thorlabs = {
            "polarizer": {
                "pvname": "SLAAR21-LMOT-ELL1",
            },
            "waveplate_ir": {
                "pvname": "SLAAR21-LMOT-ELL5",
            },
            "eos_block": {
                "pvname": "SLAAR21-LMOT-ELL2",
            },
            "crystal": {
                "pvname": "SLAAR21-LMOT-ELL3",
            },
            "thz_filter": {
                "pvname": "SLAAR21-LMOT-ELL4",
            },
            "waveplate_Thz": {
                "pvname": "SLAAR21-LMOT-ELL6",
            },
        }

        ### smaract motors ###
        for name, config in self.motor_configuration.items():
            self._append(
                SmaractRecord,
                pvname=config["id"],
                name=name,
                is_setting=True,
            )

        ### thorlabs piezo motors ###
        for name, config in self.motor_configuration_thorlabs.items():
            self._append(
                ThorlabsPiezoRecord,
                pvname=config["pvname"],
                name=name,
                is_setting=True,
            )
        self._append(
            MotorRecord,
            pvname="SLAAR21-LMOT-M522:MOTOR_1",
            name="delaystage_thz",
            is_setting=True,
            is_display=False,
            is_status=True,
        )
        self._append(
            DelayTime,
            self.delaystage_thz,
            name="delay_thz",
            offset_detector=self.delay_offset_detector,
            is_setting=False,
            is_display=True,
            is_status=True,
        )
        if self._thc_x_adjustable is not None:

            def movexcomp(x):
                delay = self.delay_thz.get_current_value()
                dx = x - self._thc_x_adjustable.get_current_value()
                new_delay = delay + dx / 1000 / constants.c
                return x, new_delay

            self._append(
                AdjustableVirtual,
                [self._thc_x_adjustable, self.delay_thz],
                lambda x, delay_thz: x,
                movexcomp,
                name="thcx_delaycomp",
                is_setting=False,
            )
        # self.thz_polarization = AdjustableVirtual(
        #     [self.crystal, self.waveplate_ir],
        #     self.thz_pol_get,
        #     self.thz_pol_set,
        #     name="thz_polarization",
        # )
        self._append(
            AdjustableVirtual,
            [self.crystal, self.waveplate_ir],
            self._thz_pol_get,
            self._thz_pol_set,
            reset_current_value_to=False,
            is_setting=False,
            name="thz_polarization",
        )

    def _thz_pol_set(self, val):
        return 1.0 * val, 1.0 / 2 * val

    def _thz_pol_get(self, val, val2):
        return 1.0 * val

    def set_stage_config(self, cfg=None):
        if cfg is None:
            cfg = self.motor_configuration
        for name, config in cfg.items():
            mot = self.__dict__[name]
            mot.description(config["pv_descr"])
            # mot.stage_type(config["type"])
            mot.motor_parameters.sensor_type_num(config["sensor"])
            mot.direction(config["direction"])
            mot.motor_parameters.max_frequency(config["speed"])
            mot.speed(0)
            sleep(0.5)
            mot.calibrate_sensor()

    def home_smaract_stages(self, motor_configuration=None):
        if motor_configuration == None:
            motor_configuration = self.motor_configuration
        stages = motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = motor_configuration[name]
            mot = self.__dict__[name]
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.home_reverse(1)
                sleep(0.5)
                while not mot.flags.motion_complete():
                    sleep(1)
                if not mot.flags.is_homed():
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.home_forward(1)
            elif config["home_direction"] == "forward":
                mot.home_forward(1)
                sleep(0.5)
                while not mot.flags.motion_complete():
                    sleep(1)
                if not mot.flags.is_homed():
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.home_reverse(1)

    def get_adjustable_positions_str(self):
        ostr = "*****Organic Crystal Breadboard positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    # def __repr__(self):
    #     return self.get_adjustable_positions_str()


class LiNbO3_crystal_breadboard:
    def __init__(self, name=None, Id=None, alias_namespace=None):
        self.Id = Id
        self.name = name
        self.alias = Alias(name)

        self.motor_configuration = {
            "rz": {
                "id": "-ESB7",
                "pv_descr": "Motor5:1 THz mirror rz ",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "ry": {
                "id": "-ESB8",
                "pv_descr": "Motor5:2 THz mirror ry ",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "z": {
                "id": "-ESB9",
                "pv_descr": "Motor5:3 THz mirror z ",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "x": {
                "id": "-ESB15",
                "pv_descr": "Motor7:3 THz mirror x",
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
            mot = self.__dict__[name]
            mot.caqtdm_name(config["pv_descr"])
            mot.stage_type(config["type"])
            mot.sensor_type(config["sensor"])
            mot.speed(config["speed"])
            sleep(0.5)
            mot.calibrate_sensor(1)

    def home_smaract_stages(self, stages=None):
        if stages == None:
            stages = self.motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = self.motor_configuration[name]
            mot = self.__dict__[name]
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.home_backward(1)
                while mot.status_channel().value == 7:
                    sleep(1)
                if mot.is_homed() == 0:
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.home_forward(1)
            elif config["home_direction"] == "forward":
                mot.home_forward(1)
                while mot.status_channel().value == 7:
                    sleep(1)
                if mot.is_homed() == 0:
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.home_backward(1)

    def get_adjustable_positions_str(self):
        ostr = "*****LiNbO3 crystal breadboard positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def __repr__(self):
        return self.get_adjustable_positions_str()


class Electro_optic_sampling(Assembly):
    def __init__(self, name=None, diode_channels=None):
        super().__init__(name=name)
        self.name = name
        self.alias = Alias(name)
        self.diode_channels = diode_channels
        self.basepath = f"/sf/bernina/data/p18915/res/scan_info/"
        self.motor_configuration = {
            "ry": {
                "id": "SARES23-USR:MOT_3",
                "pv_descr": "Module1:3 EOS Ry",
                "sensor": 2,
                "direction": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "rx": {
                "id": "SARES23-USR:MOT_8",
                "pv_descr": "Motor3:2 EOS Rx",
                "sensor": 53,
                "speed": 250,
                "direction": 0,
                "home_direction": "back",
            },
            "x": {
                "id": "SARES23-USR:MOT_9",
                "pv_descr": "Module3:3 EOS x",
                "sensor": 42,
                "speed": 250,
                "direction": 0,
                "home_direction": "back",
            },
        }

        self._append(
            MotorRecord,
            "SLAAR21-LMOT-M521:MOTOR_1",
            name="delaystage_pump",
            is_setting=True,
        )
        self._append(
            DelayTime,
            self.delaystage_pump,
            name="delay_pump",
            is_setting=True,
        )
        ### in vacuum smaract motors ###
        for name, config in self.motor_configuration.items():
            self._append(
                SmaractRecord,
                pvname=config["id"],
                name=name,
                is_setting=True,
            )

    def set_stage_config(self, cfg=None):
        if cfg is None:
            cfg = self.motor_configuration
        for name, config in cfg.items():
            mot = self.__dict__[name]
            mot.description(config["pv_descr"])
            # mot.stage_type(config["type"])
            mot.motor_parameters.sensor_type_num(config["sensor"])
            mot.direction(config["direction"])
            mot.motor_parameters.max_frequency(config["speed"])
            sleep(0.5)
            mot.calibrate_sensor()

    def home_smaract_stages(self, motor_configuration=None):
        if motor_configuration == None:
            motor_configuration = self.motor_configuration
        stages = motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = motor_configuration[name]
            mot = self.__dict__[name]
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.home_reverse(1)
                sleep(0.5)
                while not mot.flags.motion_complete():
                    sleep(1)
                if not mot.flags.is_homed():
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.home_forward(1)
            elif config["home_direction"] == "forward":
                mot.home_forward(1)
                sleep(0.5)
                while not mot.flags.motion_complete():
                    sleep(1)
                if not mot.flags.is_homed():
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.home_reverse(1)

    def fit_funvction(self, t, t0, w, tau):
        from scipy.special import erf

        tt = t - t0
        y = (
            0.5
            * exp(-(2 * tau * tt - w**2) / (2 * tau**2))
            * (1 - erf((-tau * tt + w**2) / (sqrt(2) * tau * w)))
        )
        return y

    def get_adjustable_positions_str(self):
        ostr = "*****EOS motor positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def loaddata(self, runno, diode_channels=None):
        json_dir = Path(self.basepath)
        fname = list(json_dir.glob(f"run{runno:04}*"))[0]
        data = sf.parseScanEco_v01(fname)
        # print(data)
        dat = {name: data[ch].data.compute() for name, ch in diode_channels.items()}
        ch = list(diode_channels.values())[0]
        xlab = list(data[ch].scan.parameter.keys())[0]
        x = data[ch].scan.parameter[xlab]["values"]
        shots = len(list(dat.values())[0]) // len(x)
        numsteps = len(list(dat.values())[0]) // shots
        daton = np.ndarray((numsteps,))
        datoff = np.ndarray((numsteps,))
        datmean = {
            name: np.array(
                [dat[name][n * shots : (n + 1) * shots].mean() for n in range(numsteps)]
            )
            for name in dat.keys()
        }
        return {xlab: x}, datmean, dat

    def plotEOS_list(
        self, runlist, what="diff", diode_channels=None, t0_corr=True, offset_sub=False
    ):
        """
        what = 'diff' the read out from the channel 3 of the balanced diode
               'diff/sum'  (diode1 - diode2)/(diode1+diode2)
        t0_corr: True or False
                it finds the position the maximum / minimum peak and correct time zero in the time axis
        """
        fig, ax = plt.subplots(1, 2, figsize=(10, 5), num="Runlist")
        ax[1].set_xlabel("Frequency [THz]")
        ax[1].set_ylabel("normalized ampl")

        for rr in runlist:
            if diode_channels is None:
                diode_channels = self.diode_channels
            x, datmean, dat = self.loaddata(rr, diode_channels)
            x_motor, x = list(x.items())[0]
            if "delay" in x_motor:
                x = np.array(x) * 1e12  # covert to ps
                x_motor = x_motor + " [ps]"
            dat1 = datmean["d1"]
            dat2 = datmean["d2"]

            if what == "diff":
                diff = datmean["diff"]
            elif what == "ratio":
                diff = dat1 / dat2
            elif what == "sum":
                diff = dat1 + dat2
            elif what == "diff":
                diff = dat1 - dat2
            elif what == "diff/sum":
                diff = (dat1 - dat2) / (dat1 + dat2)
            if "delay" in x_motor:
                freq, ampl = self.calcFFT(x, diff.T)

            else:
                freq, ampl = 0, 0
            if offset_sub:
                diff = diff - np.mean(diff[:5])
            max_pos = np.argmax(abs(diff))
            t0_pos = x[int(max_pos)]
            if t0_corr:
                x = x - t0_pos
            np.savetxt(f"eos_data/eos_Scan{rr}.txt", [x, diff])
            ax[0].plot(x, diff, label=f"Run_{rr}: t0={t0_pos:0.2f}")
            ax[0].legend()
            ax[1].plot(freq, ampl)
        ax[0].set_xlabel(x_motor)

    def plotEOS(self, runno, diode_channels=None):
        if diode_channels is None:
            diode_channels = self.diode_channels
        x, datmean, dat = self.loaddata(runno, diode_channels)
        x_motor, x = list(x.items())[0]
        if "delay" in x_motor:
            x = np.array(x) * 1e12  # covert to ps
            x_motor = x_motor + " [ps]"
        fig, ax = plt.subplots(2, 2, figsize=(9, 7), num=f"Run_{runno}")
        dat1 = datmean["d1"]
        dat2 = datmean["d2"]
        diff = datmean["diff"]
        diffOverSum = (dat1 - dat2) / (dat1 + dat2)
        if "delay_thz" in x_motor:
            freq_0, ampl_0 = self.calcFFT(x, diff.T)
            freq_1, ampl_1 = self.calcFFT(x, diffOverSum.T)
        else:
            freq_0 = 0
            freq_1 = 0
            ampl_0 = 0
            ampl_1 = 0
        ax[0, 0].set_title(f"Run_{runno}")
        ax[0, 0].plot(x, diff, "k-", label="Channel3 (diff)")
        ax[1, 0].plot(x, diffOverSum, "r-", label="Diff / Sum ")
        ax[0, 1].plot(x, dat1, label="Diode1")
        ax[0, 1].plot(x, dat2, label="Diode2")
        ax[0, 1].set_xlabel(x_motor)
        ax[1, 1].plot(freq_0, ampl_0, "k-", label="Channel3(diff)")
        ax[1, 1].plot(freq_1, ampl_1, "r-", label="Diff/Sum")
        ax[1, 1].set_xlabel("Frequency [THz]")
        ax[1, 1].set_ylabel("normalized ampl")
        for ii in range(2):
            ax[ii, 0].legend()
            ax[ii, 0].set_xlabel(x_motor)
            ax[0, ii].legend()
        return x, diffOverSum

    def calcFFT(self, x, y, norm=True, lim=[0.1, 15]):
        # lim: min and max in THz for normalization and plotting
        x = abs(x)
        N = x.size
        T = x[N - 1] - x[0]
        te = x[1] - x[0]
        fe = 1.0 / te
        fft_cal = np.fft.fft(y) / N
        ampl = np.absolute(fft_cal)
        freq = np.arange(N) / T
        ind0 = int(np.argmin(abs(freq - lim[0])))
        ind1 = int(np.argmin(abs(freq - lim[1])))
        ampl = ampl[ind0:ind1]
        freq = freq[ind0:ind1]
        if norm:
            ampl = ampl / np.max(ampl[2:])
        return freq, ampl

    def calcField(self, DiffoverSum, L=100e-6, wl=800e-9, r41=0.97e-12, n0=3.19):
        # Parameters: L: GaP thickness, lambda: EOS wavelength, r41: EO coefficient, n0: refractive index of EOS sampling
        # Field transmission assuming that n(THz) = n(opt)
        # Angle between THz polarization and (001)
        alpha = 90.0 / 180 * np.pi
        # angle between probe polarization and (001)
        th = 0.0 / 180 * np.pi
        t = 2.0 / (n0 + 1)
        geoSens = np.cos(alpha) * np.sin(2 * th) + 2 * np.sin(alpha) * np.cos(2 * th)
        E = np.arcsin(DiffoverSum) * wl / (np.pi * L * r41 * n0**3 * t) / geoSens

        return E

    def __repr__(self):
        return self.get_adjustable_positions_str()


class Electro_optic_sampling_new(Assembly):
    def __init__(self, name=None, diode_channels=None):
        super().__init__(name=name)
        self.name = name
        self.alias = Alias(name)
        self.diode_channels = diode_channels
        self.basepath = f"/sf/bernina/data/p18915/res/scan_info/"
        self.motor_configuration = {
            "ry": {
                "id": "SARES23-USR:MOT_3",
                "pv_descr": "Module1:3 EOS Ry",
                "sensor": 2,
                "direction": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "rx": {
                "id": "SARES23-USR:MOT_8",
                "pv_descr": "Motor3:2 EOS Rx",
                "sensor": 53,
                "speed": 250,
                "direction": 0,
                "home_direction": "back",
            },
            "x": {
                "id": "SARES23-USR:MOT_9",
                "pv_descr": "Module3:3 EOS x",
                "sensor": 42,
                "speed": 250,
                "direction": 0,
                "home_direction": "back",
            },
        }

        self._append(
            MotorRecord,
            "SLAAR21-LMOT-M521:MOTOR_1",
            name="delaystage_eos",
            is_setting=True,
        )
        self._append(
            DelayTime,
            self.delaystage_eos,
            name="delay_eos",
            is_setting=True,
        )
        self._append(
            MotorRecord,
            "SLAAR21-LMOT-M522:MOTOR_1",
            name="delaystage_thz",
            is_setting=True,
        )
        self._append(
            DelayTime,
            self.delaystage_thz,
            name="delay_thz",
            is_setting=True,
        )

        def calc_new_eos_thz_delay(delay):
            delay_eos_start = self.delay_eos.get_current_value()
            delay_thz_start = self.delay_thz.get_current_value()
            delay_rel = delay - delay_thz_start
            return delay_eos_start + delay_rel, delay

        self._append(
            AdjustableVirtual,
            [self.delay_eos, self.delay_thz],
            lambda deos, dthz: deos,
            calc_new_eos_thz_delay,
            name="delay_thz_eos",
        )
        ### in vacuum smaract motors ###
        for name, config in self.motor_configuration.items():
            self._append(
                SmaractRecord,
                pvname=config["id"],
                name=name,
                is_setting=True,
            )

    def set_stage_config(self, cfg=None):
        if cfg is None:
            cfg = self.motor_configuration
        for name, config in cfg.items():
            mot = self.__dict__[name]
            mot.description(config["pv_descr"])
            # mot.stage_type(config["type"])
            mot.motor_parameters.sensor_type_num(config["sensor"])
            mot.direction(config["direction"])
            mot.motor_parameters.max_frequency(config["speed"])
            sleep(0.5)
            mot.calibrate_sensor()

    def home_smaract_stages(self, motor_configuration=None):
        if motor_configuration == None:
            motor_configuration = self.motor_configuration
        stages = motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = motor_configuration[name]
            mot = self.__dict__[name]
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.home_reverse(1)
                sleep(0.5)
                while not mot.flags.motion_complete():
                    sleep(1)
                if not mot.flags.is_homed():
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.home_forward(1)
            elif config["home_direction"] == "forward":
                mot.home_forward(1)
                sleep(0.5)
                while not mot.flags.motion_complete():
                    sleep(1)
                if not mot.flags.is_homed():
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.home_reverse(1)

    def fit_funvction(self, t, t0, w, tau):
        from scipy.special import erf

        tt = t - t0
        y = (
            0.5
            * exp(-(2 * tau * tt - w**2) / (2 * tau**2))
            * (1 - erf((-tau * tt + w**2) / (sqrt(2) * tau * w)))
        )
        return y

    def get_adjustable_positions_str(self):
        ostr = "*****EOS motor positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def loaddata(self, runno, diode_channels=None):
        json_dir = Path(self.basepath)
        fname = list(json_dir.glob(f"run{runno:04}*"))[0]
        data = sf.parseScanEco_v01(fname)
        # print(data)
        dat = {name: data[ch].data.compute() for name, ch in diode_channels.items()}
        ch = list(diode_channels.values())[0]
        xlab = list(data[ch].scan.parameter.keys())[0]
        x = data[ch].scan.parameter[xlab]["values"]
        shots = len(list(dat.values())[0]) // len(x)
        numsteps = len(list(dat.values())[0]) // shots
        daton = np.ndarray((numsteps,))
        datoff = np.ndarray((numsteps,))
        datmean = {
            name: np.array(
                [dat[name][n * shots : (n + 1) * shots].mean() for n in range(numsteps)]
            )
            for name in dat.keys()
        }
        return {xlab: x}, datmean, dat

    def plotEOS_list(
        self, runlist, what="diff", diode_channels=None, t0_corr=True, offset_sub=False
    ):
        """
        what = 'diff' the read out from the channel 3 of the balanced diode
               'diff/sum'  (diode1 - diode2)/(diode1+diode2)
        t0_corr: True or False
                it finds the position the maximum / minimum peak and correct time zero in the time axis
        """
        fig, ax = plt.subplots(1, 2, figsize=(10, 5), num="Runlist")
        ax[1].set_xlabel("Frequency [THz]")
        ax[1].set_ylabel("normalized ampl")

        for rr in runlist:
            if diode_channels is None:
                diode_channels = self.diode_channels
            x, datmean, dat = self.loaddata(rr, diode_channels)
            x_motor, x = list(x.items())[0]
            if "delay" in x_motor:
                x = np.array(x) * 1e12  # covert to ps
                x_motor = x_motor + " [ps]"
            dat1 = datmean["d1"]
            dat2 = datmean["d2"]

            if what == "diff":
                diff = datmean["diff"]
            elif what == "ratio":
                diff = dat1 / dat2
            elif what == "sum":
                diff = dat1 + dat2
            elif what == "diff":
                diff = dat1 - dat2
            elif what == "diff/sum":
                diff = (dat1 - dat2) / (dat1 + dat2)
            if "delay" in x_motor:
                freq, ampl = self.calcFFT(x, diff.T)

            else:
                freq, ampl = 0, 0
            if offset_sub:
                diff = diff - np.mean(diff[:5])
            max_pos = np.argmax(abs(diff))
            t0_pos = x[int(max_pos)]
            if t0_corr:
                x = x - t0_pos
            np.savetxt(f"eos_data/eos_Scan{rr}.txt", [x, diff])
            ax[0].plot(x, diff, label=f"Run_{rr}: t0={t0_pos:0.2f}")
            ax[0].legend()
            ax[1].plot(freq, ampl)
        ax[0].set_xlabel(x_motor)

    def plotEOS(self, runno, diode_channels=None):
        if diode_channels is None:
            diode_channels = self.diode_channels
        x, datmean, dat = self.loaddata(runno, diode_channels)
        x_motor, x = list(x.items())[0]
        if "delay" in x_motor:
            x = np.array(x) * 1e12  # covert to ps
            x_motor = x_motor + " [ps]"
        fig, ax = plt.subplots(2, 2, figsize=(9, 7), num=f"Run_{runno}")
        dat1 = datmean["d1"]
        dat2 = datmean["d2"]
        diff = datmean["diff"]
        diffOverSum = (dat1 - dat2) / (dat1 + dat2)
        if "delay_thz" in x_motor:
            freq_0, ampl_0 = self.calcFFT(x, diff.T)
            freq_1, ampl_1 = self.calcFFT(x, diffOverSum.T)
        else:
            freq_0 = 0
            freq_1 = 0
            ampl_0 = 0
            ampl_1 = 0
        ax[0, 0].set_title(f"Run_{runno}")
        ax[0, 0].plot(x, diff, "k-", label="Channel3 (diff)")
        ax[1, 0].plot(x, diffOverSum, "r-", label="Diff / Sum ")
        ax[0, 1].plot(x, dat1, label="Diode1")
        ax[0, 1].plot(x, dat2, label="Diode2")
        ax[0, 1].set_xlabel(x_motor)
        ax[1, 1].plot(freq_0, ampl_0, "k-", label="Channel3(diff)")
        ax[1, 1].plot(freq_1, ampl_1, "r-", label="Diff/Sum")
        ax[1, 1].set_xlabel("Frequency [THz]")
        ax[1, 1].set_ylabel("normalized ampl")
        for ii in range(2):
            ax[ii, 0].legend()
            ax[ii, 0].set_xlabel(x_motor)
            ax[0, ii].legend()
        return x, diffOverSum

    def calcFFT(self, x, y, norm=True, lim=[0.1, 15]):
        # lim: min and max in THz for normalization and plotting
        x = abs(x)
        N = x.size
        T = x[N - 1] - x[0]
        # def __repr__(self):
        #     return self.get_adjustable_positions_str()
        te = x[1] - x[0]
        fe = 1.0 / te
        fft_cal = np.fft.fft(y) / N
        ampl = np.absolute(fft_cal)
        freq = np.arange(N) / T
        ind0 = int(np.argmin(abs(freq - lim[0])))
        ind1 = int(np.argmin(abs(freq - lim[1])))
        ampl = ampl[ind0:ind1]
        freq = freq[ind0:ind1]
        if norm:
            ampl = ampl / np.max(ampl[2:])
        return freq, ampl

    def calcField(self, DiffoverSum, L=100e-6, wl=800e-9, r41=0.97e-12, n0=3.19):
        # Parameters: L: GaP thickness, lambda: EOS wavelength, r41: EO coefficient, n0: refractive index of EOS sampling
        # Field transmission assuming that n(THz) = n(opt)
        # Angle between THz polarization and (001)
        alpha = 90.0 / 180 * np.pi
        # angle between probe polarization and (001)
        th = 0.0 / 180 * np.pi
        t = 2.0 / (n0 + 1)
        geoSens = np.cos(alpha) * np.sin(2 * th) + 2 * np.sin(alpha) * np.cos(2 * th)
        E = np.arcsin(DiffoverSum) * wl / (np.pi * L * r41 * n0**3 * t) / geoSens

        return E

    # def __repr__(self):
    #     return self.get_adjustable_positions_str()


def calc_otti(
    ottinu,
    ottidel,
    ottidet=None,
    ottidetdist=179.287,
    Nxpix=1024,
    Nzpix=512,
    pixsize=75e-3,
    rotcencoo=np.asarray([46, 77.436, 0]),
    plotit=False,
):

    x = np.arange(Nxpix) * pixsize
    z = np.arange(Nzpix) * pixsize
    y = np.asarray([ottidetdist])
    x = x - np.mean(x.ravel())
    z = z - np.mean(z.ravel())
    X, Y, Z = np.meshgrid(x, y, z)

    pixcoo = np.vstack([X.ravel(), Y.ravel(), Z.ravel()])
    detdir = np.asarray([0, -1, 0])

    rotcencoo = np.asarray([46, 77.436, 0])
    samplecoo = np.asarray([0, 0, 0])

    ottidel = np.radians(ottidel)
    ottinu = np.radians(ottinu)
    if ottidet is None:
        ottidet = -ottinu
    else:
        ottidet = np.radians(ottidet)

    pixcoo_ottidet = np.asarray(
        Rotation.from_rotvec(np.asarray([0, ottidet, 0])).as_matrix()
        * np.asmatrix(pixcoo)
    )
    pixcoo_ottidel = np.asarray(
        Rotation.from_rotvec(np.asarray([ottidel, 0, 0])).as_matrix()
        * np.asmatrix(pixcoo_ottidet)
    )
    pixcoo_ottinu = np.asarray(
        Rotation.from_rotvec(np.asarray([0, ottinu, 0])).as_matrix()
        * np.asmatrix(pixcoo_ottidel)
    )
    pixcoo_otti = pixcoo_ottinu + rotcencoo[:, np.newaxis]

    detdir_otti = np.asarray(
        Rotation.from_rotvec(np.asarray([0, ottinu, 0])).as_matrix()
        * (
            Rotation.from_rotvec(np.asarray([ottidel, 0, 0])).as_matrix()
            * np.asmatrix(detdir[:, np.newaxis])
        )
    )
    rxz = pixcoo_otti[0] ** 2 + pixcoo_otti[2] ** 2

    pixdist = np.sqrt(rxz + pixcoo_otti[1] ** 2).reshape(X.shape[1:])
    nu = np.arctan2(pixcoo_otti[0], pixcoo_otti[2]).reshape(X.shape[1:])
    delta = np.arctan2(pixcoo_otti[1], np.sqrt(rxz)).reshape(X.shape[1:])

    if plotit:
        fig = plt.figure(figsize=[8, 4])

        ax = fig.add_subplot(1, 3, 1, projection="3d")

        plpixcoo = np.roll(pixcoo + rotcencoo[:, np.newaxis], 1, axis=0)
        plpixcoo_otti = np.roll(pixcoo_otti, 1, axis=0)
        plrotcencoo = np.roll(rotcencoo, 1, axis=0)
        plsamplecoo = np.roll(samplecoo, 1, axis=0)

        ax.set_box_aspect(
            np.ptp(
                np.concatenate(
                    [
                        plpixcoo,
                        plpixcoo_otti,
                        plrotcencoo[:, np.newaxis],
                        plsamplecoo[:, np.newaxis],
                    ],
                    axis=1,
                ),
                axis=1,
            )
        )
        # ax.set_box_aspect(np.ptp(pixcoo,axis=1))
        ax.plot(*plrotcencoo, "om")

        ax.plot(*plsamplecoo, "sr")
        ax.plot(*[get_array_frame(ta.reshape(X.shape[1:])) for ta in plpixcoo], ":b")
        # ax.plot(*plpixcoo,'.b')
        # ax.plot(*plpixcoo_otti,'xg')
        ax.plot(
            *[get_array_frame(ta.reshape(X.shape[1:])) for ta in plpixcoo_otti], "g"
        )

        ax.set_xlabel("z / mm")
        ax.set_ylabel("x / mm")
        ax.set_zlabel("y / mm")
        # ax.set_box_aspect((np.ptp(np.concatenate([pixcoo,pixcoo_ottidel,pixcoo_ottidet],axis=0),axis=1)

        axn = fig.add_subplot(1, 3, 2)
        axn.set_xlabel("nu / °")
        axn.set_ylabel("delta / °")

        i1 = axn.plot(
            np.degrees(get_array_frame(nu)), np.degrees(get_array_frame(delta)), "-g"
        )
        axp = fig.add_subplot(1, 3, 3, projection="polar")
        axp.set_ylabel("nu / °")
        axp.set_xlabel("delta / °")
        axp.set_rlim(bottom=90, top=45)
        axp.set_theta_zero_location("N", offset=0.0)

        ip = plt.plot(get_array_frame(nu), np.degrees(get_array_frame(delta)), "-g")
        plt.tight_layout()

        print(f"Average detector distance: {np.mean(pixdist)} mm")
        print(f"Average nu angle: {np.degrees(np.mean(nu))}°")
        print(f"Average delta angle: {np.degrees(np.mean(delta))}°")
        print(detdir_otti)
    return nu, delta, pixdist


def get_array_frame(a):
    return np.concatenate([a[:, 0], a[-1, 1:], a[-2::-1, -1], a[0, -2::-1]])


class LowtemperatureSurfaceDiffraction(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self.name = name

        ### SmarAct stages ###
        self.motor_configuration = {
            "beam_block": {
                "id": "SARES23-USR:MOT_18",
                "pv_descr": "6:3 LSD Chamber Beam Block",
                "direction": 0,
                "sensor": 1,
                "speed": 200,
                "home_direction": "back",
                "kwargs": {"accuracy": 0.000001},
            },
            "interferrometer_paddle": {
                "id": "SARES23-USR:MOT_16",
                "pv_descr": "6:1 LSD Interferrometer Paddle",
                "direction": 0,
                "sensor": 1,
                "speed": 200,
                "home_direction": "front",
                "kwargs": {"accuracy": 0.000001},
            },
        }
        self.motor_configuration_openloop = {
            "interferrometer_ver": {
                "id": "SARES23-USR:asyn",
                "pv_descr": "5:1 LSD interferrometer hor",
                "channel": 13,
            },
            "interferrometer_hor": {
                "id": "SARES23-USR:asyn",
                "pv_descr": "5:2 LSD interferrometer ver",
                "channel": 14,
            },
        }
        for name, config in self.motor_configuration.items():
            self._append(
                SmaractRecord,
                pvname=config["id"],
                name=name,
                is_setting=True,
            )

        for name, config in self.motor_configuration_openloop.items():
            self._append(
                SmarActOpenLoopRecord,
                pvname=config["id"],
                name=name,
                channel=config["channel"],
                is_setting=False,
            )
        ### lakeshore temperatures ####
        self._append(
            AdjustablePv,
            pvsetname="SARES20-LS336:LOOP1_SP",
            pvreadbackname="SARES20-LS336:A_RBV",
            accuracy=0.1,
            name="temp_sample",
            is_setting=False,
        )
        self._append(
            AdjustablePv,
            pvsetname="SARES20-LS336:LOOP2_SP",
            pvreadbackname="SARES20-LS336:B_RBV",
            accuracy=0.1,
            name="temp_coldfinger",
            is_setting=False,
        )

        ### Transl_eta Feedback PVs
        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LTIM01-EVR0:CALCZ.C",
            name="feedback_setpoint",
            accuracy=10,
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LTIM01-EVR0:CALCZ.B",
            name="feedback_enabled",
            accuracy=10,
            is_setting=True,
        )
        self._append(
            DetectorPvDataStream,
            "SLAAR21-LTIM01-EVR0:CALCZ",
            name="interferrometer_value",
        )

        self._append(
            MpodChannel,
            pvbase="SARES21-PS7071",
            channel_number=3,
            name="illumination",
        )

    def beam_block_in(self, target=7):
        self.beam_block.set_target_value(target)

    def beam_block_out(self, target=0):
        self.beam_block.set_target_value(target)

    def interferrometer_in(self, target=13.35):
        self.interferrometer_paddle.set_target_value(target)

    def interferrometer_out(self, target=-10):
        self.interferrometer_paddle.set_target_value(target)

    def set_stage_config(self):
        cfg = self.motor_configuration
        for name, config in cfg.items():
            mot = self.__dict__[name]
            mot.description(config["pv_descr"])
            # mot.stage_type(config["type"])
            mot.motor_parameters.sensor_type_num(config["sensor"])
            mot.direction(config["direction"])
            mot.motor_parameters.max_frequency(config["speed"])
            sleep(0.5)
            mot.calibrate_sensor()
        cfg_openloop = self.motor_configuration_openloop
        for name, config in cfg_openloop.items():
            mot = self.__dict__[name]
            mot.description(config["pv_descr"])

    def home_smaract_stages(self, motor_configuration=None):
        if motor_configuration == None:
            motor_configuration = self.motor_configuration
        stages = motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = motor_configuration[name]
            mot = self.__dict__[name]
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.home_reverse(1)
                sleep(0.5)
                while not mot.flags.motion_complete():
                    sleep(1)
                if not mot.flags.is_homed():
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.home_forward(1)
            elif config["home_direction"] == "forward":
                mot.home_forward(1)
                sleep(0.5)
                while not mot.flags.motion_complete():
                    sleep(1)
                if not mot.flags.is_homed():
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.home_reverse(1)
