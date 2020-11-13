import numpy as np
from scipy import constants
from ..elements import Assembly
from ..devices_general.motors import MotorRecord
from ..devices_general.adjustable import PvRecord


class KBMirrorBernina:
    def __init__(
        self,
        kb_ver=None,
        kb_hor=None,
        usd_table=None,
        d_kbver=3350.0,
        d_kbhor=2600.0,
        d_hex=1600.0,
        d_win1=1945.0,
        d_win2=1330.0,
        d_target=1520.0,
        d_att=1420.0,
    ):
        """All distances are from sample interaction point at straight beam (no kb deflection), the units are expected in mm"""
        self.kb_ver = kb_ver
        self.kb_hor = kb_hor
        self.usd_table = usd_table
        self.d_kbver = d_kbver
        self.d_kbhor = d_kbhor
        self.d_hex = d_hex
        self.d_att = d_att
        self.d_win2 = d_win2
        self.d_win1 = d_win1
        self.d_target = d_target

    def calc_positions(self, the_kbver, the_kbhor):
        """angles in rad"""
        y_kbhor = np.tan(2 * the_kbver) * np.abs(self.d_kbver - self.d_kbhor)
        rx_kbhor = -2 * the_kbver
        y_hex = np.tan(2 * the_kbver) * np.abs(self.d_kbver - self.d_hex)
        x_hex = np.tan(2 * the_kbhor) * np.abs(self.d_kbhor - self.d_hex)
        rx_hex = rx_kbhor
        ry_hex = 2 * the_kbhor
        return {
            "y_kbhor": y_kbhor,
            "rx_kbhor": rx_kbhor,
            "x_hex": x_hex,
            "y_hex": y_hex,
            "rx_hex": rx_hex,
            "ry_hex": ry_hex,
        }

    def calc_fwhm(self, fwhm_hor, fwhm_ver, z_focver=0, z_fochor=0, E_phot=None):
        """ E_phot in eV, length units in mm."""
        lam = constants.c * constants.h / constants.electron_volt / E_phot
        print(lam * 1e10)
        fwhm_fac = 1 / np.sqrt(2 * np.log(2))  # w = fwhm_fac*fwhm
        # div_ver = np.arctan(fwhm_ver*fwhm_fac/2/(self.d_kbver+z_focver)) #half divergence
        # div_hor = np.arctan(fwhm_hor*fwhm_fac/2/(self.d_kbhor+z_fochor))
        c = lam / np.pi * 1e3

        w0 = lambda w, z: (
            w ** 2 - (w ** 4 - (2 * c * z) ** 2) ** 0.5
        ) ** 0.5 / np.sqrt(2)
        w = lambda w0, z: (w0 ** 2 + (c * z / w0) ** 2) ** 0.5
        zr = lambda w0: w0 ** 2 / c

        fwhm_z = lambda z, w0: w(w0, z) / fwhm_fac
        w0_hor = w0(fwhm_hor * fwhm_fac, self.d_kbhor + z_fochor)
        w0_ver = w0(fwhm_ver * fwhm_fac, self.d_kbver + z_focver)
        print(w0_hor)
        res = {}
        res["target"] = (
            fwhm_z(self.d_target + z_fochor, w0_hor),
            fwhm_z(self.d_target + z_focver, w0_ver),
        )
        res["win1"] = (
            fwhm_z(self.d_win1 + z_fochor, w0_hor),
            fwhm_z(self.d_win1 + z_focver, w0_ver),
        )
        res["win2"] = (
            fwhm_z(self.d_win2 + z_fochor, w0_hor),
            fwhm_z(self.d_win2 + z_focver, w0_ver),
        )
        res["att"] = (
            fwhm_z(self.d_att + z_fochor, w0_hor),
            fwhm_z(self.d_att + z_focver, w0_ver),
        )
        res["sample"] = (fwhm_z(z_fochor, w0_hor), fwhm_z(z_focver, w0_ver))
        # res['fwhm_kbver'] = (fwhm_z(self.d_kbver+z_fochor,w0_hor),fwhm_z(self.d_kbver+z_focver,w0_ver))
        # res['fwhm_kbhor'] = (fwhm_z(self.d_kbhor+z_fochor,w0_hor),fwhm_z(self.d_kbhor+z_focver,w0_ver))
        return res

    def move_hex_for_kb_angles(self, the_kbver, the_kbhor):
        pos = self.calc_positions(the_kbver, the_kbhor)
        x = pos["x_hex"]
        y = pos["y_hex"]
        rx = pos["rx_hex"] * 180 / np.pi
        ry = pos["ry_hex"] * 180 / np.pi
        z = rz = 0.0
        ax, ay, az, arx, ary, arz = self.usd_table.get_coordinates()
        print(
            f"present upstream large hexapod position is (x/mm,y/mm,z/mm,rx/°,ry/°,rz/°) = ({ax:g},{ay:g},{az:g},{arx:g},{ary:g},{arz:g})"
        )
        print(
            f"moving to (x/mm,y/mm,z/mm,rx/°,ry/°,rz/°) = ({x:g},{y:g},{z:g},{rx:g},{ry:g},{rz:g})"
        )
        if not input("start moving upstream large hexapod? (y/n)") == "y":
            print("did nothing")
            return
        else:
            self.usd_table.move_to_coordinates(x, y, z, rx, ry, rz)


class KBMirrorBernina_new(Assembly):
    def __init__(
        self,
        pvname_hor,
        pvname_ver,
        usd_table=None,
        d_kbver=3350.0,
        d_kbhor=2600.0,
        d_hex=1600.0,
        d_win1=1945.0,
        d_win2=1330.0,
        d_target=1520.0,
        d_att=1420.0,
    ):
        """All distances are from sample interaction point at straight beam (no kb deflection), the units are expected in mm"""
        super().__init__(name=name)

        self.kb_ver = kb_ver
        self.kb_hor = kb_hor
        self.usd_table = usd_table
        self.d_kbver = d_kbver
        self.d_kbhor = d_kbhor
        self.d_hex = d_hex
        self.d_att = d_att
        self.d_win2 = d_win2
        self.d_win1 = d_win1
        self.d_target = d_target

        self._add(MotorRecord, pvname_hor + ":W_X", name="x_hor")
        self._add(MotorRecord, pvname_hor + ":W_Y", name="y_hor")
        self._add(MotorRecord, pvname_hor + ":W_RY", name="y_hor")
        self._add(MotorRecord, pvname_hor + ":W_RY", name="pitch_hor")
        self._add(MotorRecord, pvname_hor + ":W_RZ", name="roll_hor")
        self._add(MotorRecord, pvname_hor + ":W_RX", name="yaw_hor")
        self._add(MotorRecord, pvname_hor + ":BU", name="bend_upstream_hor")
        self._add(MotorRecord, pvname_hor + ":BD", name="bend_downstream_hor")

        self._add(MotorRecord, pvname_hor + ":TY1", name="y1_phys_hor")
        self._add(MotorRecord, pvname_hor + ":TY2", name="y2_phys_hor")
        self._add(MotorRecord, pvname_hor + ":TY3", name="y3_phys_hor")
        self._add(MotorRecord, pvname_hor + ":TX1", name="x1_phys_hor")
        self._add(MotorRecord, pvname_hor + ":TX2", name="x2_phys_hor")

        self._add(
            PvRecord,
            pvsetname=Id + ":CURV_SP",
            pvreadbackname=Id + ":CURV",
            accuracy=0.002,
            name="curv",
        )
        addPvRecordToSelf(
            self,
            pvsetname=Id + ":ASYMMETRY_SP",
            pvreadbackname=Id + ":ASYMMETRY",
            accuracy=0.002,
            name="asym",
        )

    def calc_positions(self, the_kbver, the_kbhor):
        """angles in rad"""
        y_kbhor = np.tan(2 * the_kbver) * np.abs(self.d_kbver - self.d_kbhor)
        rx_kbhor = -2 * the_kbver
        y_hex = np.tan(2 * the_kbver) * np.abs(self.d_kbver - self.d_hex)
        x_hex = np.tan(2 * the_kbhor) * np.abs(self.d_kbhor - self.d_hex)
        rx_hex = rx_kbhor
        ry_hex = 2 * the_kbhor
        return {
            "y_kbhor": y_kbhor,
            "rx_kbhor": rx_kbhor,
            "x_hex": x_hex,
            "y_hex": y_hex,
            "rx_hex": rx_hex,
            "ry_hex": ry_hex,
        }

    def calc_fwhm(self, fwhm_hor, fwhm_ver, z_focver=0, z_fochor=0, E_phot=None):
        """ E_phot in eV, length units in mm."""
        lam = constants.c * constants.h / constants.electron_volt / E_phot
        print(lam * 1e10)
        fwhm_fac = 1 / np.sqrt(2 * np.log(2))  # w = fwhm_fac*fwhm
        # div_ver = np.arctan(fwhm_ver*fwhm_fac/2/(self.d_kbver+z_focver)) #half divergence
        # div_hor = np.arctan(fwhm_hor*fwhm_fac/2/(self.d_kbhor+z_fochor))
        c = lam / np.pi * 1e3

        w0 = lambda w, z: (
            w ** 2 - (w ** 4 - (2 * c * z) ** 2) ** 0.5
        ) ** 0.5 / np.sqrt(2)
        w = lambda w0, z: (w0 ** 2 + (c * z / w0) ** 2) ** 0.5
        zr = lambda w0: w0 ** 2 / c

        fwhm_z = lambda z, w0: w(w0, z) / fwhm_fac
        w0_hor = w0(fwhm_hor * fwhm_fac, self.d_kbhor + z_fochor)
        w0_ver = w0(fwhm_ver * fwhm_fac, self.d_kbver + z_focver)
        print(w0_hor)
        res = {}
        res["target"] = (
            fwhm_z(self.d_target + z_fochor, w0_hor),
            fwhm_z(self.d_target + z_focver, w0_ver),
        )
        res["win1"] = (
            fwhm_z(self.d_win1 + z_fochor, w0_hor),
            fwhm_z(self.d_win1 + z_focver, w0_ver),
        )
        res["win2"] = (
            fwhm_z(self.d_win2 + z_fochor, w0_hor),
            fwhm_z(self.d_win2 + z_focver, w0_ver),
        )
        res["att"] = (
            fwhm_z(self.d_att + z_fochor, w0_hor),
            fwhm_z(self.d_att + z_focver, w0_ver),
        )
        res["sample"] = (fwhm_z(z_fochor, w0_hor), fwhm_z(z_focver, w0_ver))
        # res['fwhm_kbver'] = (fwhm_z(self.d_kbver+z_fochor,w0_hor),fwhm_z(self.d_kbver+z_focver,w0_ver))
        # res['fwhm_kbhor'] = (fwhm_z(self.d_kbhor+z_fochor,w0_hor),fwhm_z(self.d_kbhor+z_focver,w0_ver))
        return res

    def move_hex_for_kb_angles(self, the_kbver, the_kbhor):
        pos = self.calc_positions(the_kbver, the_kbhor)
        x = pos["x_hex"]
        y = pos["y_hex"]
        rx = pos["rx_hex"] * 180 / np.pi
        ry = pos["ry_hex"] * 180 / np.pi
        z = rz = 0.0
        ax, ay, az, arx, ary, arz = self.usd_table.get_coordinates()
        print(
            f"present upstream large hexapod position is (x/mm,y/mm,z/mm,rx/°,ry/°,rz/°) = ({ax:g},{ay:g},{az:g},{arx:g},{ary:g},{arz:g})"
        )
        print(
            f"moving to (x/mm,y/mm,z/mm,rx/°,ry/°,rz/°) = ({x:g},{y:g},{z:g},{rx:g},{ry:g},{rz:g})"
        )
        if not input("start moving upstream large hexapod? (y/n)") == "y":
            print("did nothing")
            return
        else:
            self.usd_table.move_to_coordinates(x, y, z, rx, ry, rz)
