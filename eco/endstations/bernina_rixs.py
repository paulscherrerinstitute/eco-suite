import sys

sys.path.append("..")
from ..devices_general.motors import MotorRecord, MotorRecord_new
from ..devices_general.adjustable import PvRecord, AdjustableVirtual, AdjustableMemory

from epics import PV
from ..aliases import Alias, append_object_to_object
from ..endstations.hexapod import HexapodPI
from pathlib import Path
import subprocess
from ..elements.assembly import Assembly
from ..detector.jungfrau import Jungfrau
from .kappa_conversion import kappa2you, you2kappa
import numpy as np




class RIXS(Assembly):
    def __init__(
        self,
        name=None,
        pvname='SARES20-RIXS',
        alias_namespace=None,
        config={},
    ):
        super().__init__(name=name)
        self.pvname = pvname
        self.config = config
        self.config.update({
            'rowland': {
                'r': 1000,
                'alpha_lin': 90-69,
                'tth_0': 0,
                'x0':999.85,
            },
        })


        ### detector raw motors ###
        self._append(
            MotorRecord,
            pvname + ":MOT_1",
            name="_det_t",
            is_setting=True,
            is_status=True,
        )
        self._append(
            MotorRecord,
            pvname + ":MOT_2",
            name="_det_rz",
            is_setting=False,
            is_status=False,
        )
        self._append(
            MotorRecord,
            pvname + ":MOT_3",
            name="_det_x",
            is_setting=False,
            is_status=False,
        )
#        self._append(
#            MotorRecord,
#            pvname + ":MOT_YU",
#            name="crystal_theta",
#            is_setting=True,
#            is_status=False,
#            backlash_definition=True,
#        )
#        self._append(
#            MotorRecord,
#            pvname + ":MOT_YU",
#            name="crystal_y",
#            is_setting=True,
#            is_status=False,
#            backlash_definition=True,
#        )
#        self._append(
#            MotorRecord,
#            pvname + ":MOT_YD",
#            name="crystal_transl",
#            is_setting=True,
#            is_status=False,
#            backlash_definition=True,
#        )
#        self._append(
#            MotorRecord,
#            pvname + ":MOT_YU",
#            name="crystal_y,
#            is_setting=True,
#            is_status=False,
#            backlash_definition=True,
#        )

    def rowland_intersection(self,beta,tbeta):
        cfg = self.config['rowland']
        r = cfg['r']
        b=np.deg2rad(beta)
        tb=np.deg2rad(tbeta)
        x0 = r/2*np.cos(b)
        y0 = r/2*np.sin(b)
        p = np.cos(tb)*x0+np.sin(tb)*y0
        d = p+np.sqrt(p**2+(r/2)**2-x0**2-y0**2)
        x,y = np.array([d*np.cos(tb), d*np.sin(tb)])
        return x,y

    def calc_crystal_detector_positions(self,tth):
        """
        This function returns the crystal and the detector positions in (x,y) assuming that the sample is at (0,0)
        """
        tbeta = 180-tth
        x_d,y_d = self.rowland_intersection(tbeta/2,tbeta)
        x_c,y_c = self.rowland_intersection(tbeta/2,0)

        return np.array([-x_c,y_c]), np.array([x_d-x_c, y_d])

    def ct_det_t_h_rz_from_tth(self,tth):
        cfg = self.config['rowland']
        r = cfg['r']
        a0 = np.deg2rad(cfg['alpha_lin'])
        [x_s, y_s], [x_d,y_d] = self.calc_crystal_detector_positions(tth)
        det_t_set = np.sin(a0)*y_d+np.cos(a0)*x_d
        det_h_set = np.cos(a0)*y_d-np.sin(a0)*x_d
        det_rz_set = -(180-tth)/2
        cryst_t_set =abs(x_s)
        return cryst_t_set, det_t_set, det_h_set, det_rz_set

    def tth_from_t_h_rz(self, t, h, rz):
        cfg = self.config['rowland']
        a0 = np.deg2rad(cfg['alpha_lin'])
        y = np.cos(a0)*h + np.sin(a0)*t
        tth = np.rad2deg(np.arcsin(y/cfg['r']))
        return tth



    def gui(self, guiType="xdm"):
        """ Adjustable convention"""
        cmd = ["caqtdm", "-macro"]
        cmd += [
            "-noMsg",
            "-stylefile",
            "sfop.qss",
            "-macro",
            "P=SARES22-GPS",
            "ESB_GPS_exp.ui",
        ]
        return self._run_cmd(" ".join(cmd))

    # def get_adjustable_positions_str(self):
    #     ostr = "*****GPS motor positions******\n"

    #     for tkey, item in self.__dict__.items():
    #         if hasattr(item, "get_current_value"):
    #             pos = item.get_current_value()
    #             ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
    #     return ostr

    # def __repr__(self):
    #     return self.get_adjustable_positions_str()


