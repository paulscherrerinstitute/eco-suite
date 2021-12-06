from os import lseek
import numpy as np
from scipy import constants
from ..elements.assembly import Assembly
from ..devices_general.motors import MotorRecord
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum
from .kb_mirrors import KbVer, KbHor
from time import sleep
from numbers import Number
from tabulate import tabulate


class KBMirrorBernina_new(Assembly):
    def __init__(
        self,
        pvname_ver,
        pvname_hor,
        usd_table=None,
        diffractometer=None,
        downstream_diag=None,
        d_kbver=3350.0,
        d_kbhor=2600.0,
        d_hex=1600.0,
        d_win1=1945.0,
        d_win2=1330.0,
        d_target=1520.0,
        d_att=1420.0,
        d_prof_dsd=3725.0,
        name=None,
    ):
        """All distances are from sample interaction point at straight beam (no kb deflection), the units are expected in mm"""
        super().__init__(name=name)

        self._append(
            KbVer, pvname_ver, name="ver", is_setting=True, is_status="recursive"
        )
        self._append(
            KbHor, pvname_hor, name="hor", is_setting=True, is_status="recursive"
        )
        self.diffractometer = diffractometer

        self.usd_table = usd_table
        self.d_kbver = d_kbver
        self.d_kbhor = d_kbhor
        self.d_hex = d_hex
        self.d_att = d_att
        self.d_win2 = d_win2
        self.d_win1 = d_win1
        self.d_target = d_target
        self.d_prof_dsd = d_prof_dsd

    def calc_positions(self, the_kbver, the_kbhor):
        """angles in rad"""
        pos_calc = {}
        pos_calc["y_kbhor"] = np.tan(2 * the_kbver) * np.abs(
            self.d_kbver - self.d_kbhor
        )
        pos_calc["rx_kbhor"] = -2 * the_kbver
        pos_calc["y_hex"] = np.tan(2 * the_kbver) * np.abs(self.d_kbver - self.d_hex)
        pos_calc["x_hex"] = np.tan(2 * the_kbhor) * np.abs(self.d_kbhor - self.d_hex)
        pos_calc["rx_hex"] = -2 * the_kbver
        pos_calc["ry_hex"] = 2 * the_kbhor
        pos_calc["x_diff"] = np.tan(2 * the_kbhor) * np.abs(self.d_kbhor)
        pos_calc["y_diff"] = np.tan(2 * the_kbver) * np.abs(self.d_kbver)
        pos_calc["x_dsd"] = np.tan(2 * the_kbhor) * np.abs(
            self.d_kbhor + self.d_prof_dsd
        )
        pos_calc["y_dsd"] = np.tan(2 * the_kbver) * np.abs(
            self.d_kbver + self.d_prof_dsd
        )
        return pos_calc

    def calc_fwhm(self, fwhm_hor, fwhm_ver, z_focver=0, z_fochor=0, E_phot=None):
        """E_phot in eV, length units in mm."""
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

    def get_current_kb_theta_from_table_usd(self):
        """Return values in radians"""
        coo = self.usd_table.get_coordinates()
        return -coo[3] * np.pi / 180 / 2, coo[4] * np.pi / 180 / 2

    def move_hex_and_diff_for_kb_angles(
        self, the_kbver, the_kbhor, angle_interval_Rad=30e-6
    ):
        """input angles are thetas and in rad and only positive."""
        the_kbver_0, the_kbhor_0 = self.get_current_kb_theta_from_table_usd()
        dthe_kbver = the_kbver - the_kbver_0
        dthe_kbhor = the_kbhor - the_kbhor_0
        N_intervals = int(np.ceil(max(dthe_kbver, dthe_kbhor) / angle_interval_Rad))
        thes_path = zip(
            np.linspace(the_kbver_0, the_kbver, N_intervals + 1)[1:],
            np.linspace(the_kbhor_0, the_kbhor, N_intervals + 1)[1:],
        )
        pos0 = self.calc_positions(the_kbver_0, the_kbhor_0)

        ocoo = self.usd_table.get_coordinates()
        odiffx = self.diffractometer.xbase.get_current_value()
        odiffy = self.diffractometer.ybase.get_current_value()
        odiffrx = self.diffractometer.rxbase.get_current_value()
        odiffnu = self.diffractometer.nu.get_current_value()
        odiffmu = self.diffractometer.mu.get_current_value()

        try:
            for thever, thehor in thes_path:
                tpos = self.calc_positions(thever, thehor)
                dx = tpos["x_hex"] - pos0["x_hex"]
                dy = tpos["y_hex"] - pos0["y_hex"]
                drx = (tpos["rx_hex"] - pos0["rx_hex"]) * 180 / np.pi
                dry = (tpos["ry_hex"] - pos0["ry_hex"]) * 180 / np.pi
                dz = drz = 0.0
                dcoo = (dx, dy, dz, drx, dry, drz)
                coo_moveto = [td + to for td, to in zip(dcoo, ocoo)]

                diffx_moveto = (tpos["x_diff"] - pos0["x_diff"]) + odiffx
                diffy_moveto = (tpos["y_diff"] - pos0["y_diff"]) + odiffy

                print("will move to relative from start:")
                print(
                    dcoo,
                    tpos["x_diff"] - pos0["x_diff"],
                    tpos["y_diff"] - pos0["y_diff"],
                )

                self.usd_table.move_to_coordinates(*coo_moveto)
                self.diffractometer.xbase.mv(diffx_moveto)
                self.diffractometer.ybase.mv(diffy_moveto)

                print("finished all motions.")
                sleep(0.2)
                # if input("continue move? (y/n) ") == "y":
                #     continue
                # else:
                #     break
        except KeyboardInterrupt:
            pass

    def calc_beamsizes_at_elements(
        self, size_before_kb, size_sample=None, size_prof_kb=None
    ):
        if isinstance(size_before_kb, Number):
            size_before_kb = [size_before_kb, size_before_kb]

        if not size_sample is None:
            # from each mirror
            focpos_hor = calc_focus_pos_intercept(
                size_before_kb[0], size_sample[0], self.d_kbhor
            )
            focpos_ver = calc_focus_pos_intercept(
                size_before_kb[1], size_sample[1], self.d_kbver
            )
            sz_win1_hor = (
                size_before_kb[0]
                / focpos_hor
                * (focpos_hor - self.d_kbhor + self.d_win1)
            )
            sz_win1_ver = (
                size_before_kb[1]
                / focpos_ver
                * (focpos_ver - self.d_kbver + self.d_win1)
            )
            sz_win2_hor = (
                size_before_kb[0]
                / focpos_hor
                * (focpos_hor - self.d_kbhor + self.d_win2)
            )
            sz_win2_ver = (
                size_before_kb[1]
                / focpos_ver
                * (focpos_ver - self.d_kbver + self.d_win2)
            )
            sz_tt_hor = (
                size_before_kb[0]
                / focpos_hor
                * (focpos_hor - self.d_kbhor + self.d_target)
            )
            sz_tt_ver = (
                size_before_kb[1]
                / focpos_ver
                * (focpos_ver - self.d_kbver + self.d_target)
            )
            sz_attusd_hor = (
                size_before_kb[0]
                / focpos_hor
                * (focpos_hor - self.d_kbhor + self.d_att)
            )
            sz_attusd_ver = (
                size_before_kb[1]
                / focpos_ver
                * (focpos_ver - self.d_kbver + self.d_att)
            )

            out = [
                [sz_win1_hor, sz_win1_ver],
                [sz_tt_hor, sz_tt_ver],
                [sz_attusd_hor, sz_attusd_ver],
                [sz_win2_hor, sz_win2_ver],
            ]
        if True:
            names = ["Window kb-usd", "Timetool target", "att_usd", "Window usd-lic"]
            strg = tabulate(
                [[tn, tx, ty] for tn, (tx, ty) in zip(names, out)],
                headers=["Element", "horizontal", "vertical"],
            )
            print(strg)

        return focpos_hor, focpos_ver, out


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
        """E_phot in eV, length units in mm."""
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


def calc_focus_pos_intercept(size0, size1, distance):
    """calculates the position of the focus based on simple intercept theorem.
    position is from where size 0 was measured, and the focus will be between the
     two positions, in case sign of size0 and size1 differ."""

    return distance / (-size1 / size0 + 1)
