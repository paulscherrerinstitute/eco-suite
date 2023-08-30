import sys

sys.path.append("..")
from ..devices_general.motors import MotorRecord, MotorRecord_new
from ..elements.adjustable import AdjustableMemory, AdjustableVirtual
from ..epics.adjustable import AdjustablePv

from epics import PV
from ..aliases import Alias, append_object_to_object
from ..endstations.hexapod import HexapodPI
from pathlib import Path
import subprocess
from ..elements.assembly import Assembly
from ..detector.jungfrau import Jungfrau
from .kappa_conversion import kappa2you, you2kappa
import numpy as np
from ..utilities.recspace import Crystals, DiffGeometryYou


def addMotorRecordToSelf(self, name=None, Id=None):
    try:
        self.__dict__[name] = MotorRecord(Id, name=name)
        self.alias.append(self.__dict__[name].alias)
    except:
        print(f"Warning! Could not find motor {name} (Id:{Id})")


class GPS(Assembly):
    def __init__(
        self,
        name=None,
        pvname=None,
        configuration=["base"],
        alias_namespace=None,
        fina_hex_angle_offset=None,
        diffcalc=False,
    ):
        super().__init__(name=name)
        self.pvname = pvname
        self.configuration = configuration

        if "base" in self.configuration:
            ### motors base platform ###
            self._append(
                MotorRecord,
                pvname + ":MOT_TX",
                name="xbase",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_TY",
                name="_ybase_deltatau",
                is_setting=False,
                is_display=False,
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_RX",
                name="_rxbase_deltatau",
                is_setting=False,
                is_display=False,
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_YU",
                name="_ybase_upstream",
                is_setting=True,
                is_display=False,
                backlash_definition=True,
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_YD",
                name="_ybase_downstream",
                is_setting=True,
                is_display=False,
                backlash_definition=True,
            )
            self._append(
                AdjustableVirtual,
                [self._ybase_upstream, self._ybase_downstream],
                lambda u, d: np.mean([u, d]),
                lambda v: [
                    i.get_current_value() + (v - self.ybase.get_current_value())
                    for i in [self._ybase_upstream, self._ybase_downstream]
                ],
                name="ybase",
                is_setting=False,
                is_display=True,
                unit="mm",
            )
            self._append(
                AdjustableVirtual,
                [self._ybase_upstream, self._ybase_downstream],
                lambda u, d: np.arctan(np.diff([d, u])[0] / 1146) * 180 / np.pi,
                lambda v: [
                    self.ybase.get_current_value()
                    + i * np.tan(v * np.pi / 180) * 1146 / 2
                    for i in [1, -1]
                ],
                name="rxbase",
                is_setting=False,
                is_display=True,
                unit="deg",
            )

            self._append(
                MotorRecord, pvname + ":MOT_NY_RY2TH", name="nu", is_setting=True
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_NY_RY2TH",
                name="gamma",
                is_setting=False,
                is_display=False,
            )
            self._append(
                MotorRecord, pvname + ":MOT_MY_RYTH", name="mu", is_setting=True
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_MY_RYTH",
                name="alpha",
                is_setting=False,
                is_display=False,
            )
            self.set_base_off = DeltaTauCurrOff("SARES22-GPS:asyn2.AOUT")

        if "phi_table" in self.configuration:
            ### motors phi table ###
            self._append(
                MotorRecord, pvname + ":MOT_HEX_RX", name="eta", is_setting=True
            )
            self._append(
                MotorRecord, pvname + ":MOT_HEX_TX", name="transl_eta", is_setting=True
            )

        if "phi_hex" in self.configuration:

            ### motors PI hexapod ###
            if fina_hex_angle_offset:
                fina_hex_angle_offset = Path(fina_hex_angle_offset).expanduser()

            self._append(
                HexapodPI,
                "SARES20-HEX_PI",
                name="hex",
                fina_angle_offset=fina_hex_angle_offset,
                is_setting=True,
                is_display="recursive",
            )

        if "hlxz" in self.configuration:
            ### motors heavy load goniometer ###
            self._append(
                MotorRecord, pvname + ":MOT_TBL_TX", name="xhl", is_setting=True
            )
            self._append(
                MotorRecord, pvname + ":MOT_TBL_TZ", name="zhl", is_setting=True
            )

        if "hly" in self.configuration:
            self._append(
                MotorRecord, pvname + ":MOT_TBL_TY", name="yhl", is_setting=True
            )

        if "hlrxrz" in self.configuration:
            self._append(
                MotorRecord, pvname + ":MOT_TBL_RX", name="rxhl", is_setting=True
            )
            self._append(
                MotorRecord, pvname + ":MOT_TBL_RZ", name="rzhl", is_setting=True
            )
        self.set_samplestg_off = DeltaTauCurrOff("SARES22-GPS:asyn1.AOUT")
        if "kappa" in self.configuration:
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_KRX",
                name="eta_kap",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_KAP",
                name="kappa",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_KPH",
                name="phi_kap",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_DTY",
                name="zkap",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_DTX",
                name="xkap",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_DTZ",
                name="ykap",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_DRX",
                name="rxkap",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_DRZ",
                name="rykap",
                is_setting=True,
                is_display=True,
            )
            self.set_kappa_off = DeltaTauCurrOff(self.pvname + ":asyn1.AOUT")

            def get_current_kappa2you():
                return self.calc_kappa2you(
                    self.eta_kap.get_current_value(),
                    self.kappa.get_current_value(),
                    self.phi_kap.get_current_value(),
                )

            def set_youvar_value_to_current_kappa(value, varind):
                vars = list(get_current_kappa2you())
                vars[varind] = value
                return self.calc_you2kappa(*vars)

            self._append(
                AdjustableVirtual,
                [self.eta_kap, self.kappa, self.phi_kap],
                lambda eta_kap, kappa, phi_kap: self.calc_kappa2you(
                    eta_kap, kappa, phi_kap
                )[0],
                lambda value_eta: set_youvar_value_to_current_kappa(value_eta, 0),
                name="eta",
                unit="deg",
            )
            self._append(
                AdjustableVirtual,
                [self.eta_kap, self.kappa, self.phi_kap],
                lambda eta_kap, kappa, phi_kap: self.calc_kappa2you(
                    eta_kap, kappa, phi_kap
                )[1],
                lambda value_chi: set_youvar_value_to_current_kappa(value_chi, 1),
                name="chi",
                unit="deg",
            )
            self._append(
                AdjustableVirtual,
                [self.eta_kap, self.kappa, self.phi_kap],
                lambda eta_kap, kappa, phi_kap: self.calc_kappa2you(
                    eta_kap, kappa, phi_kap
                )[2],
                lambda value_phi: set_youvar_value_to_current_kappa(value_phi, 2),
                name="phi",
                unit="deg",
            )

        if diffcalc:
            self._append(
                Crystals,
                diffractometer_you=self,
                name="diffcalc",
                is_setting=False,
                is_display=False,
            )


    def gui(self, guiType="xdm"):
        """Adjustable convention"""
        cmd = ["caqtdm", "-macro"]
        cmd += [
            "-noMsg",
            "-stylefile",
            "sfop.qss",
            "-macro",
            "P=SARES22-GPS",
            "/ioc/modules/qt/ESB_GPS_exp.ui",
        ]
        return self._run_cmd(" ".join(cmd))
        # bash -c 'caqtdm -noMsg  -stylefile sfop.qss -macro P=SARES22-GPS  /ioc/modules/qt/ESB_GPS_exp.ui'

    def calc_you2kappa(
        self,
        eta,
        chi,
        phi,
        kappa_angle=60,
        degrees=True,
        bernina_kappa=True,
        invert_elbow=False,
    ):
        """tool to convert from you definition angles to kappa angles, in
        particular the bernina kappa where the"""
        if bernina_kappa:
            eta = -eta
        if degrees:
            eta, chi, phi, kappa_angle = np.deg2rad([eta, chi, phi, kappa_angle])
        if invert_elbow:
            delta_angle = np.pi - np.arcsin(-np.tan(chi / 2) / np.tan(kappa_angle))
        else:
            delta_angle = np.arcsin(-np.tan(chi / 2) / np.tan(kappa_angle))
        eta_k = eta - delta_angle
        if invert_elbow:
            kappa = -2 * np.arcsin(np.sin(chi / 2) / np.sin(kappa_angle))
        else:
            kappa = 2 * np.arcsin(np.sin(chi / 2) / np.sin(kappa_angle))

        phi_k = phi - delta_angle

        if bernina_kappa:
            eta_k = eta_k - np.pi / 2
            kappa = -kappa
            phi_k = phi_k

        if True:

            def flip_ang(ang):
                if 1 < abs(ang // np.pi):
                    return ang - np.sign(ang) * abs(ang) // (2 * np.pi) * np.pi * 2
                else:
                    return ang

            # phi_k = flip_ang(phi_k)
            eta_k = flip_ang(eta_k)
            kappa = flip_ang(kappa)

        if degrees:
            eta_k, kappa, phi_k = np.rad2deg([eta_k, kappa, phi_k])
        return eta_k, kappa, phi_k

    def calc_kappa2you(
        self,
        eta_k,
        kappa,
        phi_k,
        kappa_angle=60,
        degrees=True,
        bernina_kappa=True,
        invert_elbow=False,
    ):
        if degrees:
            eta_k, kappa, phi_k, kappa_angle = np.deg2rad(
                [eta_k, kappa, phi_k, kappa_angle]
            )
        if bernina_kappa:
            eta_k = eta_k + np.pi / 2
            kappa = -kappa
            phi_k = phi_k
        if invert_elbow:
            kappa = -kappa
            delta_angle = np.pi - np.arctan(np.tan(kappa / 2) * np.cos(kappa_angle))
        else:
            delta_angle = np.arctan(np.tan(kappa / 2) * np.cos(kappa_angle))
        eta = eta_k - delta_angle
        chi = 2 * np.arcsin(np.sin(kappa / 2) * np.sin(kappa_angle))
        phi = phi_k - delta_angle
        if degrees:
            eta, chi, phi = np.rad2deg([eta, chi, phi])
        if bernina_kappa:
            eta = -eta
        return eta, chi, phi

    # def get_adjustable_positions_str(self):
    #     ostr = "*****GPS motor positions******\n"

    #     for tkey, item in self.__dict__.items():
    #         if hasattr(item, "get_current_value"):
    #             pos = item.get_current_value()
    #             ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
    #     return ostr

    # def __repr__(self):
    #     return self.get_adjustable_positions_str()


class DeltaTauCurrOff:
    def __init__(self, pvname, name=None):
        self.pvname = pvname
        self.PV = PV(pvname)

    def set_off(self):
        self.PV.put("#1..8k")

    def __call__(self):
        self.set_off()


class XRDYou(Assembly):
    def __init__(
        self,
        name=None,
        Id=None,
        configuration=["base"],
        diff_detector=None,
        invert_kappa_ellbow=True,
        pgroup_adj=None,
        fina_hex_angle_offset=None,
        diffcalc=True,
    ):
        """X-ray diffractometer platform in AiwssFEL Bernina.\
                <configuration> : list of elements mounted on 
                the plaform, options are kappa, nutable, hlgonio, polana"""
        # self.Id = Id
        self.pvname = Id
        pvname = Id
        super().__init__(name=name)
        self.configuration = configuration
        self.invert_kappa_ellbow = invert_kappa_ellbow

        if "base" in self.configuration:
            ### motors base platform ###
            self._append(
                MotorRecord,
                pvname + ":MOT_TX",
                name="xbase",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_TY",
                name="_ybase_deltatau",
                is_setting=False,
                is_display=False,
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_RX",
                name="_rxbase_deltatau",
                is_setting=False,
                is_display=False,
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_YU",
                name="_ybase_upstream",
                is_setting=True,
                is_display=False,
                backlash_definition=True,
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_YD",
                name="_ybase_downstream",
                is_setting=True,
                is_display=False,
                backlash_definition=True,
            )
            self._append(
                AdjustableVirtual,
                [self._ybase_upstream, self._ybase_downstream],
                lambda u, d: np.mean([u, d]),
                lambda v: [
                    i.get_current_value() + (v - self.ybase.get_current_value())
                    for i in [self._ybase_upstream, self._ybase_downstream]
                ],
                name="ybase",
                is_setting=False,
                is_display=True,
                unit="mm",
            )
            self._append(
                AdjustableVirtual,
                [self._ybase_upstream, self._ybase_downstream],
                lambda u, d: np.arctan(np.diff([d, u])[0] / 1146) * 180 / np.pi,
                lambda v: [
                    self.ybase.get_current_value()
                    + i * np.tan(v * np.pi / 180) * 1146 / 2
                    for i in [1, -1]
                ],
                name="rxbase",
                is_setting=False,
                is_display=True,
                unit="deg",
            )
            self._append(
                MotorRecord_new,
                Id + ":MOT_MY_RYTH",
                name="mu",
                is_setting=True,
                is_display=True,
            )
            self.set_base_off = DeltaTauCurrOff("SARES21-XRD:asyn4.AOUT")

        if "arm" in self.configuration:
            ### motors XRD detector arm ###
            self._append(
                MotorRecord_new,
                Id + ":MOT_NY_RY2TH",
                name="nu",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                Id + ":MOT_DT_RX2TH",
                name="delta",
                is_setting=True,
                is_display=True,
            )
            ### motors XRD area detector branch ###
            self._append(
                MotorRecord_new,
                Id + ":MOT_D_T",
                name="tdet",
                is_setting=True,
                is_display=True,
            )

            ### motors XRD polarisation analyzer branch ###
            self._append(
                MotorRecord_new,
                Id + ":MOT_P_T",
                name="tpol",
                is_setting=True,
                is_display=True,
            )
            # missing: slits of flight tube
            self.set_detarm_off = DeltaTauCurrOff("SARES21-XRD:asyn3.AOUT")

        if "hlxz" in self.configuration:
            ### motors heavy load goniometer ###
            self._append(
                MotorRecord_new,
                Id + ":MOT_TBL_TX",
                name="xhl",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                Id + ":MOT_TBL_TZ",
                name="zhl",
                is_setting=True,
                is_display=True,
            )
            self.set_phi_off = DeltaTauCurrOff("SARES21-XRD:asyn1.AOUT")
        if "hly" in self.configuration:
            self._append(
                MotorRecord_new,
                Id + ":MOT_TBL_TY",
                name="yhl",
                is_setting=True,
                is_display=True,
            )
            self.set_phi_off = DeltaTauCurrOff("SARES21-XRD:asyn1.AOUT")

        if "hlrxrz" in self.configuration:
            try:
                self._append(
                    MotorRecord_new,
                    Id + ":MOT_TBL_RX",
                    name="rxhl",
                    is_setting=True,
                    is_display=True,
                )
            except:
                print("XRD.rxhl not found")
                pass
            try:
                self._append(
                    MotorRecord_new,
                    Id + ":MOT_TBL_RZ",
                    name="rzhl",
                    is_setting=True,
                    is_display=True,
                )
            except:
                print("XRD.rzhl not found")
            self.set_phi_off = DeltaTauCurrOff("SARES21-XRD:asyn1.AOUT")

        if "phi_table" in self.configuration:
            ### motors nu table ###
            self._append(
                MotorRecord_new,
                Id + ":MOT_HEX_TX",
                name="transl_eta",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                Id + ":MOT_HEX_RX",
                name="eta",
                is_setting=True,
                is_display=True,
            )

        if "phi_hex" in self.configuration:

            ### motors PI hexapod ###
            if fina_hex_angle_offset:
                fina_hex_angle_offset = Path(fina_hex_angle_offset).expanduser()

            self._append(
                HexapodPI,
                "SARES20-HEX_PI",
                name="hex",
                fina_angle_offset=fina_hex_angle_offset,
                is_setting=True,
                is_display="recursive",
            )
#        if "phi_hex" in self.configuration:
#            ### motors PI hexapod ###
#            append_object_to_object(
#                self,
#                AdjustablePv,
#                "SARES20-HEX_PI:SET-POSI-X",
#                pvreadbackname="SARES20-HEX_PI:POSI-X",
#                name="xhex",
#            )
#            append_object_to_object(
#                self,
#                AdjustablePv,
#                "SARES20-HEX_PI:SET-POSI-Y",
#                pvreadbackname="SARES20-HEX_PI:POSI-Y",
#                name="yhex",
#            )
#            append_object_to_object(
#                self,
#                AdjustablePv,
#                "SARES20-HEX_PI:SET-POSI-Z",
#                pvreadbackname="SARES20-HEX_PI:POSI-Z",
#                name="zhex",
#            )
#            append_object_to_object(
#                self,
#                AdjustablePv,
#                "SARES20-HEX_PI:SET-POSI-U",
#                pvreadbackname="SARES20-HEX_PI:POSI-U",
#                name="uhex",
#            )
#            append_object_to_object(
#                self,
#                AdjustablePv,
#                "SARES20-HEX_PI:SET-POSI-V",
#                pvreadbackname="SARES20-HEX_PI:POSI-V",
#                name="vhex",
#            )
#            append_object_to_object(
#                self,
#                AdjustablePv,
#                "SARES20-HEX_PI:SET-POSI-W",
#                pvreadbackname="SARES20-HEX_PI:POSI-W",
#                name="whex",
#            )

        if "kappa" in self.configuration:
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_KRX",
                name="eta_kap",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_KAP",
                name="kappa",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_KPH",
                name="phi_kap",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_DTY",
                name="zkap",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_DTX",
                name="xkap",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_DTZ",
                name="ykap",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_DRX",
                name="rxkap",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord_new,
                self.pvname + ":MOT_KAP_DRZ",
                name="rykap",
                is_setting=True,
                is_display=True,
            )
            self.set_kappa_off = DeltaTauCurrOff(self.pvname + ":asyn1.AOUT")

            def get_current_kappa2you():
                return self.calc_kappa2you(
                    self.eta_kap.get_current_value(),
                    self.kappa.get_current_value(),
                    self.phi_kap.get_current_value(),
                    bernina_kappa=True,
                    invert_elbow=self.invert_kappa_ellbow,
                )

            def set_youvar_value_to_current_kappa(value, varind):
                vars = list(get_current_kappa2you())
                vars[varind] = value
                return self.calc_you2kappa(
                    *vars,
                    bernina_kappa=True,
                    invert_elbow=self.invert_kappa_ellbow,
                )

            self._append(
                AdjustableVirtual,
                [self.eta_kap, self.kappa, self.phi_kap],
                lambda eta_kap, kappa, phi_kap: self.calc_kappa2you(
                    eta_kap,
                    kappa,
                    phi_kap,
                    invert_elbow=self.invert_kappa_ellbow,
                    bernina_kappa=True,
                )[0],
                lambda value_eta: set_youvar_value_to_current_kappa(value_eta, 0),
                name="eta",
                unit="deg",
            )
            self._append(
                AdjustableVirtual,
                [self.eta_kap, self.kappa, self.phi_kap],
                lambda eta_kap, kappa, phi_kap: self.calc_kappa2you(
                    eta_kap,
                    kappa,
                    phi_kap,
                    invert_elbow=self.invert_kappa_ellbow,
                    bernina_kappa=True,
                )[1],
                lambda value_chi: set_youvar_value_to_current_kappa(value_chi, 1),
                name="chi",
                unit="deg",
            )
            self._append(
                AdjustableVirtual,
                [self.eta_kap, self.kappa, self.phi_kap],
                lambda eta_kap, kappa, phi_kap: self.calc_kappa2you(
                    eta_kap,
                    kappa,
                    phi_kap,
                    invert_elbow=self.invert_kappa_ellbow,
                    bernina_kappa=True,
                )[2],
                lambda value_phi: set_youvar_value_to_current_kappa(value_phi, 2),
                name="phi",
                unit="deg",
            )

        if diff_detector:
            self._append(
                Jungfrau,
                diff_detector["jf_id"],
                name="det_diff",
                is_setting=False,
                is_display=False,
                pgroup_adj=pgroup_adj,
                view_toplevel_only=True,
            )
        if diffcalc:
            self._append(
                Crystals,
                diffractometer_you=self,
                name="diffcalc",
                is_setting=False,
                is_display=False,
            )

    def get_adjustable_positions_str(self):
        ostr = "*****XRD motor positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def gui(self, guiType="xdm"):
        """Adjustable convention"""
        cmd = ["caqtdm", "-macro"]
        cmd += [
            "-noMsg",
            "-stylefile",
            "sfop.qss",
            "-macro",
            "P=SARES21-XRD",
            "ESB_XRD_exp.ui",
        ]
        return self._run_cmd(" ".join(cmd))

    # def calc_kappa2you(self, eta_k, kappa, phi_k):
    #     return kappa2you(eta_k, kappa, phi_k)

    # def calc_you2kappa(self, eta, chi, phi):
    #     return you2kappa(eta, chi, phi)
    #################

    def calc_you2kappa(
        self,
        eta,
        chi,
        phi,
        kappa_angle=60,
        degrees=True,
        bernina_kappa=True,
        invert_elbow=False,
    ):
        """tool to convert from you definition angles to kappa angles, in
        particular the bernina kappa where the"""
        if bernina_kappa:
            eta = -eta
            phi = -phi
        if degrees:
            eta, chi, phi, kappa_angle = np.deg2rad([eta, chi, phi, kappa_angle])
        if invert_elbow:
            delta_angle = np.pi - np.arcsin(-np.tan(chi / 2) / np.tan(kappa_angle))
        else:
            delta_angle = np.arcsin(-np.tan(chi / 2) / np.tan(kappa_angle))
        eta_k = eta - delta_angle
        if invert_elbow:
            kappa = -2 * np.arcsin(np.sin(chi / 2) / np.sin(kappa_angle))
        else:
            kappa = 2 * np.arcsin(np.sin(chi / 2) / np.sin(kappa_angle))

        phi_k = phi - delta_angle

        if bernina_kappa:
            eta_k = eta_k - np.pi / 2
            kappa = -kappa
        if False:

            def flip_ang(ang):
                if 1 < abs(ang // np.pi):
                    return ang - np.sign(ang) * np.pi * 2
                else:
                    return ang

            # phi_k = flip_ang(phi_k)
            phi_k = phi_k + np.pi * 2
            eta_k = flip_ang(eta_k)
            kappa = flip_ang(kappa)
        if degrees:
            eta_k, kappa, phi_k = np.rad2deg([eta_k, kappa, phi_k])
        return eta_k, kappa, phi_k

    def calc_kappa2you(
        self,
        eta_k,
        kappa,
        phi_k,
        kappa_angle=60,
        degrees=True,
        bernina_kappa=True,
        invert_elbow=False,
    ):
        if degrees:
            eta_k, kappa, phi_k, kappa_angle = np.deg2rad(
                [eta_k, kappa, phi_k, kappa_angle]
            )
        if bernina_kappa:
            eta_k = eta_k + np.pi / 2
            kappa = -kappa
            # phi_k = -phi_k
        if invert_elbow:
            kappa = -kappa
            delta_angle = np.pi - np.arctan(np.tan(kappa / 2) * np.cos(kappa_angle))
        else:
            delta_angle = np.arctan(np.tan(kappa / 2) * np.cos(kappa_angle))
        eta = eta_k - delta_angle
        chi = 2 * np.arcsin(np.sin(kappa / 2) * np.sin(kappa_angle))
        phi = phi_k - delta_angle
        if degrees:
            eta, chi, phi = np.rad2deg([eta, chi, phi])
        if bernina_kappa:
            eta = -eta
            phi = -phi
        return eta, chi, phi


#################
# def calc_you2kappa(
#     self, eta, chi, phi, kappa_angle=60, degrees=True, bernina_kappa=True
# ):
#     """tool to convert from you definition angles to kappa angles, in
#     particular the bernina kappa where the"""
#     if bernina_kappa:
#         eta = -eta
#     if degrees:
#         eta, chi, phi, kappa_angle = np.deg2rad([eta, chi, phi, kappa_angle])
#     delta_angle = np.arcsin(-np.tan(chi / 2) / np.tan(kappa_angle))
#     eta_k = eta - delta_angle
#     kappa = 2 * np.arcsin(np.sin(chi / 2) / np.sin(kappa_angle))
#     phi_k = phi - delta_angle
#
#     if bernina_kappa:
#         eta_k = eta_k - np.pi / 2
#         kappa = -kappa
#         phi_k = phi_k
#     if degrees:
#         eta_k, kappa, phi_k = np.rad2deg([eta_k, kappa, phi_k])
#     return eta_k, kappa, phi_k
#
# def calc_kappa2you(
#     self, eta_k, kappa, phi_k, kappa_angle=60, degrees=True, bernina_kappa=True
# ):
#     if degrees:
#         eta_k, kappa, phi_k, kappa_angle = np.deg2rad(
#             [eta_k, kappa, phi_k, kappa_angle]
#         )
#     if bernina_kappa:
#         eta_k = eta_k + np.pi / 2
#         kappa = -kappa
#         phi_k = phi_k
#     delta_angle = np.arctan(np.tan(kappa / 2) * np.cos(kappa_angle))
#     eta = eta_k - delta_angle
#     chi = 2 * np.arcsin(np.sin(kappa / 2) * np.sin(kappa_angle))
#     phi = phi_k - delta_angle
#     if degrees:
#         eta, chi, phi = np.rad2deg([eta, chi, phi])
#     if bernina_kappa:
#         eta = -eta
#     return eta, chi, phi
#
# # def __repr__(self):
# #     return self.get_adjustable_positions_str()


class XRD(Assembly):
    def __init__(
        self,
        name=None,
        Id=None,
        configuration=["base"],
        diff_detector=None,
        pgroup_adj=None,
    ):
        """X-ray diffractometer platform in AiwssFEL Bernina.\
                <configuration> : list of elements mounted on 
                the plaform, options are kappa, nutable, hlgonio, polana"""
        # self.Id = Id
        super().__init__(name=name)
        self.configuration = configuration

        if "base" in self.configuration:
            ### motors base platform ###

            self._append(
                MotorRecord,
                pvname + ":MOT_TX",
                name="xbase",
                is_setting=True,
                is_display=True,
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_TY",
                name="_ybase_deltatau",
                is_setting=False,
                is_display=False,
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_RX",
                name="_rxbase_deltatau",
                is_setting=False,
                is_display=False,
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_YU",
                name="_ybase_upstream",
                is_setting=True,
                is_display=False,
                backlash_definition=True,
            )
            self._append(
                MotorRecord,
                pvname + ":MOT_YD",
                name="_ybase_downstream",
                is_setting=True,
                is_display=False,
                backlash_definition=True,
            )
            self._append(
                AdjustableVirtual,
                [self._ybase_upstream, self._ybase_downstream],
                lambda u, d: np.mean([u, d]),
                lambda v: [
                    i.get_current_value() + (v - self.ybase.get_current_value())
                    for i in [self._ybase_upstream, self._ybase_downstream]
                ],
                name="ybase",
                is_setting=False,
                is_display=True,
                unit="mm",
            )
            self._append(
                AdjustableVirtual,
                [self._ybase_upstream, self._ybase_downstream],
                lambda u, d: np.arctan(np.diff([d, u])[0] / 1146) * 180 / np.pi,
                lambda v: [
                    self.ybase.get_current_value()
                    + i * np.tan(v * np.pi / 180) * 1146 / 2
                    for i in [1, -1]
                ],
                name="rxbase",
                is_setting=False,
                is_display=True,
                unit="deg",
            )

            self._append(
                MotorRecord_new, Id + ":MOT_MY_RYTH", name="alpha", is_setting=True
            )
            self.set_base_off = DeltaTauCurrOff("SARES21-XRD:asyn4.AOUT")

        if "arm" in self.configuration:
            ### motors XRD detector arm ###
            self._append(
                MotorRecord_new, Id + ":MOT_NY_RY2TH", name="gamma", is_setting=True
            )
            self._append(
                MotorRecord_new, Id + ":MOT_DT_RX2TH", name="delta", is_setting=True
            )
            ### motors XRD area detector branch ###
            self._append(MotorRecord_new, Id + ":MOT_D_T", name="tdet", is_setting=True)

            ### motors XRD polarisation analyzer branch ###
            self._append(MotorRecord_new, Id + ":MOT_P_T", name="tpol", is_setting=True)
            # missing: slits of flight tube
            self.set_detarm_off = DeltaTauCurrOff("SARES21-XRD:asyn3.AOUT")

        if "hlxz" in self.configuration:
            ### motors heavy load goniometer ###
            self._append(
                MotorRecord_new, Id + ":MOT_TBL_TX", name="xhl", is_setting=True
            )
            self._append(
                MotorRecord_new, Id + ":MOT_TBL_TZ", name="zhl", is_setting=True
            )
            self.set_phi_off = DeltaTauCurrOff("SARES21-XRD:asyn1.AOUT")
        if "hly" in self.configuration:
            self._append(
                MotorRecord_new, Id + ":MOT_TBL_TY", name="yhl", is_setting=True
            )
            self.set_phi_off = DeltaTauCurrOff("SARES21-XRD:asyn1.AOUT")

        if "hlrxrz" in self.configuration:
            try:
                self._append(
                    MotorRecord_new, Id + ":MOT_TBL_RX", name="rxhl", is_setting=True
                )
            except:
                print("XRD.rxhl not found")
                pass
            try:
                self._append(
                    MotorRecord_new, Id + ":MOT_TBL_RY", name="rzhl", is_setting=True
                )
            except:
                print("XRD.rzhl not found")
            self.set_phi_off = DeltaTauCurrOff("SARES21-XRD:asyn1.AOUT")

        if "phi_table" in self.configuration:
            ### motors nu table ###
            self._append(
                MotorRecord_new, Id + ":MOT_HEX_TX", name="tphi", is_setting=True
            )
            self._append(
                MotorRecord_new, Id + ":MOT_HEX_RX", name="phi", is_setting=True
            )

        if "phi_hex" in self.configuration:
            ### motors PI hexapod ###
            append_object_to_object(
                self,
                AdjustablePv,
                "SARES20-HEX_PI:SET-POSI-X",
                pvreadbackname="SARES20-HEX_PI:POSI-X",
                name="xhex",
            )
            append_object_to_object(
                self,
                AdjustablePv,
                "SARES20-HEX_PI:SET-POSI-Y",
                pvreadbackname="SARES20-HEX_PI:POSI-Y",
                name="yhex",
            )
            append_object_to_object(
                self,
                AdjustablePv,
                "SARES20-HEX_PI:SET-POSI-Z",
                pvreadbackname="SARES20-HEX_PI:POSI-Z",
                name="zhex",
            )
            append_object_to_object(
                self,
                AdjustablePv,
                "SARES20-HEX_PI:SET-POSI-U",
                pvreadbackname="SARES20-HEX_PI:POSI-U",
                name="uhex",
            )
            append_object_to_object(
                self,
                AdjustablePv,
                "SARES20-HEX_PI:SET-POSI-V",
                pvreadbackname="SARES20-HEX_PI:POSI-V",
                name="vhex",
            )
            append_object_to_object(
                self,
                AdjustablePv,
                "SARES20-HEX_PI:SET-POSI-W",
                pvreadbackname="SARES20-HEX_PI:POSI-W",
                name="whex",
            )

        if "kappa" in self.configuration:
            self._append(
                MotorRecord_new, "SARES21-XRD:MOT_KAP_KRX", name="eta", is_setting=True
            )
            self._append(
                MotorRecord_new,
                "SARES21-XRD:MOT_KAP_KAP",
                name="kappa",
                is_setting=True,
            )
            self._append(
                MotorRecord_new, "SARES21-XRD:MOT_KAP_KPH", name="phi", is_setting=True
            )
            self._append(
                MotorRecord_new, "SARES21-XRD:MOT_KAP_DTY", name="zkap", is_setting=True
            )
            self._append(
                MotorRecord_new, "SARES21-XRD:MOT_KAP_DTX", name="xkap", is_setting=True
            )
            self._append(
                MotorRecord_new, "SARES21-XRD:MOT_KAP_DTZ", name="ykap", is_setting=True
            )
            self._append(
                MotorRecord_new,
                "SARES21-XRD:MOT_KAP_DRX",
                name="rxkap",
                is_setting=True,
            )
            self._append(
                MotorRecord_new,
                "SARES21-XRD:MOT_KAP_DRZ",
                name="rykap",
                is_setting=True,
            )
            self.set_kappa_off = DeltaTauCurrOff("SARES21-XRD:asyn1.AOUT")

        if diff_detector:
            self._append(
                Jungfrau,
                diff_detector["jf_id"],
                name="det_diff",
                is_setting=False,
                is_display=True,
                pgroup_adj=pgroup_adj,
                view_toplevel_only=True,
            )

    def get_adjustable_positions_str(self):
        ostr = "*****XRD motor positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def gui(self, guiType="xdm"):
        """Adjustable convention"""
        cmd = ["caqtdm", "-macro"]
        cmd += [
            "-noMsg",
            "-stylefile",
            "sfop.qss",
            "-macro",
            "P=SARES21-XRD",
            "ESB_XRD_exp.ui",
        ]
        return self._run_cmd(" ".join(cmd))

    def calc_kappa2you(self, eta_k, kappa, phi_k):
        return kappa2you(eta_k, kappa, phi_k)

    def calc_you2kappa(self, eta, chi, phi):
        return you2kappa(eta, chi, phi)

    # def __repr__(self):
    #     return self.get_adjustable_positions_str()


class XRD_old:
    def __init__(self, name=None, Id=None, configuration=["base"]):
        """X-ray diffractometer platform in AiwssFEL Bernina.\
                <configuration> : list of elements mounted on 
                the plaform, options are kappa, nutable, hlgonio, polana"""
        self.Id = Id
        self.name = name
        self.alias = Alias(name)
        self.configuration = configuration

        if "base" in self.configuration:
            ### motors base platform ###
            ### motors base platform ###
            addMotorRecordToSelf(self, Id=Id + ":MOT_TX", name="xbase")
            addMotorRecordToSelf(self, Id=Id + ":MOT_TY", name="ybase")
            addMotorRecordToSelf(self, Id=Id + ":MOT_RX", name="rxbase")
            addMotorRecordToSelf(self, Id=Id + ":MOT_MY_RYTH", name="alpha")

        if "arm" in self.configuration:
            ### motors XRD detector arm ###
            addMotorRecordToSelf(self, Id=Id + ":MOT_NY_RY2TH", name="gamma")
            addMotorRecordToSelf(self, Id=Id + ":MOT_DT_RX2TH", name="delta")
            ### motors XRD area detector branch ###
            addMotorRecordToSelf(self, Id=Id + ":MOT_D_T", name="tdet")

            ### motors XRD polarisation analyzer branch ###
            addMotorRecordToSelf(self, Id=Id + ":MOT_P_T", name="tpol")
            # missing: slits of flight tube

        if "hlxz" in self.configuration:
            ### motors heavy load goniometer ###
            addMotorRecordToSelf(self, Id=Id + ":MOT_TBL_TX", name="xhl")
            addMotorRecordToSelf(self, Id=Id + ":MOT_TBL_TZ", name="zhl")
        if "hly" in self.configuration:
            addMotorRecordToSelf(self, Id=Id + ":MOT_TBL_TY", name="yhl")

        if "hlrxrz" in self.configuration:
            try:
                addMotorRecordToSelf(self, Id=Id + ":MOT_TBL_RX", name="rxhl")
            except:
                print("XRD.rxhl not found")
                pass
            try:
                addMotorRecordToSelf(self, Id=Id + ":MOT_TBL_RY", name="rzhl")
            except:
                print("XRD.rzhl not found")
                pass

        if "phi_table" in self.configuration:
            ### motors nu table ###
            addMotorRecordToSelf(self, Id=Id + ":MOT_HEX_TX", name="tphi")
            addMotorRecordToSelf(self, Id=Id + ":MOT_HEX_RX", name="phi")

        if "phi_hex" in self.configuration:
            ### motors PI hexapod ###
            append_object_to_object(
                self,
                AdjustablePv,
                "SARES20-HEX_PI:SET-POSI-X",
                pvreadbackname="SARES20-HEX_PI:POSI-X",
                name="xhex",
            )
            append_object_to_object(
                self,
                AdjustablePv,
                "SARES20-HEX_PI:SET-POSI-Y",
                pvreadbackname="SARES20-HEX_PI:POSI-Y",
                name="yhex",
            )
            append_object_to_object(
                self,
                AdjustablePv,
                "SARES20-HEX_PI:SET-POSI-Z",
                pvreadbackname="SARES20-HEX_PI:POSI-Z",
                name="zhex",
            )
            append_object_to_object(
                self,
                AdjustablePv,
                "SARES20-HEX_PI:SET-POSI-U",
                pvreadbackname="SARES20-HEX_PI:POSI-U",
                name="uhex",
            )
            append_object_to_object(
                self,
                AdjustablePv,
                "SARES20-HEX_PI:SET-POSI-V",
                pvreadbackname="SARES20-HEX_PI:POSI-V",
                name="vhex",
            )
            append_object_to_object(
                self,
                AdjustablePv,
                "SARES20-HEX_PI:SET-POSI-W",
                pvreadbackname="SARES20-HEX_PI:POSI-W",
                name="whex",
            )

        if "kappa" in self.configuration:
            append_object_to_object(
                self, MotorRecord, "SARES21-XRD:MOT_KAP_KRX", name="eta"
            )
            append_object_to_object(
                self, MotorRecord, "SARES21-XRD:MOT_KAP_KAP", name="kappa"
            )
            append_object_to_object(
                self, MotorRecord, "SARES21-XRD:MOT_KAP_KPH", name="phi"
            )
            append_object_to_object(
                self, MotorRecord, "SARES21-XRD:MOT_KAP_DTY", name="zkap"
            )
            append_object_to_object(
                self, MotorRecord, "SARES21-XRD:MOT_KAP_DTX", name="xkap"
            )
            append_object_to_object(
                self, MotorRecord, "SARES21-XRD:MOT_KAP_DTZ", name="ykap"
            )
            append_object_to_object(
                self, MotorRecord, "SARES21-XRD:MOT_KAP_DRX", name="rxkap"
            )
            append_object_to_object(
                self, MotorRecord, "SARES21-XRD:MOT_KAP_DRZ", name="rykap"
            )

    def get_adjustable_positions_str(self):
        ostr = "*****XRD motor positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def gui(self, guiType="xdm"):
        """Adjustable convention"""
        cmd = ["caqtdm", "-macro"]
        cmd = [
            "-noMsg",
            "-stylefile",
            "sfop.qss",
            "-macro",
            "P=SARES21-XRD",
            "/sf/common/config/qt/ESB_XRD_exp.ui",
        ]
        return subprocess.Popen(" ".join(cmd), shell=True)

    def __repr__(self):
        return self.get_adjustable_positions_str()
