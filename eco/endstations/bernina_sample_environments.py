import sys

sys.path.append("..")
from ..devices_general.motors import MotorRecord, SmaractStreamdevice
from ..devices_general.smaract import SmarActRecord
from ..epics.adjustable import AdjustablePv
import numpy as np
from epics import PV
from ..aliases import Alias, append_object_to_object
from time import sleep
import escape.parse.swissfel as sf
from ..bernina import config
import pylab as plt
import escape
from pathlib import Path
from ..elements.adjustable import AdjustableVirtual


def addMotorRecordToSelf(self, name=None, Id=None):
    try:
        self.__dict__[name] = MotorRecord(Id, name=name)
        self.alias.append(self.__dict__[name].alias)
    except:
        print(f"Warning! Could not find motor {name} (Id:{Id})")


def addSmarActRecordToSelf(self, Id=None, name=None):
    self.__dict__[name] = SmaractStreamdevice(Id, name=name)
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


class Organic_crystal_breadboard:
    def __init__(self, name=None, Id=None, alias_namespace=None):
        self.Id = Id
        self.name = name
        self.alias = Alias(name)

        self.motor_configuration = {
            "mirr2_x": {
                "id": "-ESB1",
                "pv_descr": "Motor3:1 THz mirror x ",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "mirr2_rz": {
                "id": "-ESB2",
                "pv_descr": "Motor3:2 THz mirror rz ",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "mirr2_ry": {
                "id": "-ESB3",
                "pv_descr": "Motor3:3 THz mirror ry ",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
            "mirr2_z": {
                "id": "-ESB4",
                "pv_descr": "Motor4:1 THz mirror z",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "par2_x": {
                "id": "-ESB5",
                "pv_descr": "Motor4:2 THz parabola2 x",
                "type": 1,
                "sensor": 0,
                "speed": 250,
                "home_direction": "back",
            },
            "mirr1_x": {
                "id": "-ESB7",
                "pv_descr": "Motor5:1 near IR mirror x",
                "type": 1,
                "sensor": 0,
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

        ### smaract motors ###
        for name, config in self.motor_configuration.items():
            addSmarActRecordToSelf(self, Id=Id + config["id"], name=name)

        addSmarActRecordToSelf(self, Id="SARES23-LIC7", name="polarizer_stg")
        addSmarActRecordToSelf(self, Id="SARES23-LIC14", name="crystal_stg")
        addSmarActRecordToSelf(self, Id="SARES23-LIC13", name="wp_stg")

        self.polarizer = AdjustableVirtual(
            [self.polarizer_stg], self.pol_get, self.pol_set, name="polarizer"
        )
        self.crystal = AdjustableVirtual(
            [self.crystal_stg], self.xtal_wp_get, self.xtal_wp_set, name="crystal"
        )
        self.wp = AdjustableVirtual(
            [self.wp_stg], self.xtal_wp_get, self.xtal_wp_set, name="wp"
        )
        self.thz_polarization = AdjustableVirtual(
            [self.crystal_stg, self.wp_stg],
            self.thz_pol_get,
            self.thz_pol_set,
            name="thz_polarization",
        )

    def pol_set(self, val):
        return 204 / 360 * val

    def pol_get(self, val):
        return 360 / 204 * val

    def thz_pol_set(self, val):
        return 1.0 / 5 * val, 1 / 2.5 * val

    def thz_pol_get(self, val, val2):
        return 5 / 1.0 * val

    def xtal_wp_set(self, val):
        return 1 / 5.0 * val

    def xtal_wp_get(self, val):
        return 5 / 1.0 * val

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
            mot = self.__dict__[name]._device
            mot.put("NAME", config["pv_descr"])
            mot.put("STAGE_TYPE", config["type"])
            mot.put("SET_SENSOR_TYPE", config["sensor"])
            mot.put("CL_MAX_FREQ", config["speed"])
            sleep(0.5)
            mot.put("CALIBRATE.PROC", 1)

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
            * exp(-(2 * tau * tt - w ** 2) / (2 * tau ** 2))
            * (1 - erf((-tau * tt + w ** 2) / (sqrt(2) * tau * w)))
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
        E = np.arcsin(DiffoverSum) * wl / (np.pi * L * r41 * n0 ** 3 * t) / geoSens

        return E

    def __repr__(self):
        return self.get_adjustable_positions_str()
