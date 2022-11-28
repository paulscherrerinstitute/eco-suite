import sys

sys.path.append("..")
from ..devices_general.motors import MotorRecord, SmaractRecord
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


class High_field_thz_chamber(Assembly):
    def __init__(self, name=None, alias_namespace=None, configuration=[]):
        super().__init__(name=name)
        self.name = name
        self.alias = Alias(name)
        self.par_out_pos = [35, -9.5]
        self.motor_configuration = {
            "rx": {
                "id": "SARES23:ESB13",
                "pv_descr": "Motor7:1 THz Chamber Rx",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
                "kwargs": {"accuracy": 0.01},
            },
            "x": {
                "id": "SARES23:ESB14",
                "pv_descr": "Motor7:2 THz Chamber x ",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "z": {
                "id": "SARES23:ESB10",
                "pv_descr": "Motor6:1 THz Chamber z ",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "ry": {
                "id": "SARES23:ESB11",
                "pv_descr": "Motor6:2 THz Chamber Ry",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "rz": {
                "id": "SARES23:ESB12",
                "pv_descr": "Motor6:3 THz Chamber Rz",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
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

        ### in vacuum smaract motors ###
        #for name, config in self.motor_configuration.items():
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

    def set_stage_config(self):
        for name, config in self.motor_configuration.items():
            mot = self.__dict__[name]
            mot.description(config["pv_descr"])
            #mot.stage_type(config["type"])
            mot.sensor_type(config["sensor"])
            mot.max_frequency(config["speed"])
            sleep(0.5)
            mot.calibrate_sensor()

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
                mot.home_reverse(1)
                sleep(.5)
                while not mot.flags.motion_complete():
                    sleep(1)
                if not mot.flags.is_homed():
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.home_forward(1)
            elif config["home_direction"] == "forward":
                mot.home_forward(1)
                sleep(.5)
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
    def __init__(self, name=None, Id=None, alias_namespace=None):
        super().__init__(name=name)
        self.Id = Id
        self.name = name
        self.alias = Alias(name)

        self.motor_configuration = {
            "mirr2_x": {
                "id": "-LIC17",
                "pv_descr": "Motor8:2 THz mirror x ",
                "type": 1,
                "sensor": 13,
                "speed": 250,
                "home_direction": "back",
            },
            "mirr2_rz": {
                "id": "-LIC18",
                "pv_descr": "Motor8:3 THz mirror rz ",
                "type": 1,
                "sensor": 13,
                "speed": 250,
                "home_direction": "back",
            },
            "mirr2_ry": {
                "id": "-ESB1",
                "pv_descr": "Motor3:1 THz mirror ry ",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "forward",
            },
            "mirr2_z": {
                "id": "-LIC16",
                "pv_descr": "Motor8:1 THz mirror z",
                "type": 1,
                "sensor": 13,
                "speed": 250,
                "home_direction": "back",
            },
            "par2_x": {
                "id": "-ESB3",
                "pv_descr": "Motor3:3 THz parabola2 x",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "delaystage_thz": {
                "id": "-ESB18",
                "pv_descr": "Motor8:3 NIR delay stage",
                "type": 1,
                "sensor": 0,
                "speed": 100,
                "home_direction": "back",
            },
            "nir_mirr1_ry": {
                "id": "-ESB17",
                "pv_descr": "Motor8:2 near IR mirror 1 ry",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "nir_mirr1_rx": {
                "id": "-ESB16",
                "pv_descr": "Motor8:1 near IR mirror 1 rx",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "nir_mirr2_ry": {
                "id": "-ESB9",
                "pv_descr": "Motor5:3 near IR mirror 2 ry",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "nir_mirr2_rx": {
                "id": "-ESB4",
                "pv_descr": "Motor4:1 near IR mirror 2 rx",
                "type": 1,
                "sensor": 13,
                "speed": 250,
                "home_direction": "back",
            },
            "crystal": {
                "id": "-ESB2",
                "pv_descr": "Motor3:2 crystal rotation",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "wp": {
                "id": "-ESB7",
                "pv_descr": "Motor5:1 waveplate rotation",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
                "direction": 1,
            },
        }



        ### smaract motors ###
        for name, config in self.motor_configuration.items():
            self._append(
                SmaractStreamdevice,
                pvname=Id + config["id"],
                name=name,
                is_setting=True,
            )

        self.delay_thz = DelayTime(self.delaystage_thz, name="delay_thz")

        self.thz_polarization = AdjustableVirtual(
            [self.crystal, self.wp],
            self.thz_pol_get,
            self.thz_pol_set,
            name="thz_polarization",
        )

    def thz_pol_set(self, val):
        return 1.0 * val, 1.0 / 2 * val

    def thz_pol_get(self, val, val2):
        return 1.0 * val

    def set_stage_config(self):
        for name, config in self.motor_configuration.items():
            mot = self.__dict__[name]
            mot.caqtdm_name(config["pv_descr"])
            mot.stage_type(config["type"])
            mot.sensor_type(config["sensor"])
            mot.speed(config["speed"])
            if "direction" in config.keys():
                mot.direction(config["direction"])
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
        ostr = "*****Organic Crystal Breadboard positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def __repr__(self):
        return self.get_adjustable_positions_str()


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


class Electro_optic_sampling:
    def __init__(
        self, name=None, Id=None, alias_namespace=None, pgroup=None, diode_channels=None
    ):
        self.Id = Id
        self.name = name
        self.alias = Alias(name)
        self.diode_channels = diode_channels
        self.basepath = f"/sf/bernina/data/p18915/res/scan_info/"
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
                "id": "-ESB5",
                "pv_descr": "Motor4:1 EOS prism rx ",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "x": {
                "id": "-ESB4",
                "pv_descr": "Motor4:2 EOS prism x ",
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
