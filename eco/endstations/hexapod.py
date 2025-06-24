import time
from epics import PV
from ..elements.adjustable import AdjustableFS, AdjustableVirtual
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum
from ..epics.detector import DetectorPvData
from time import sleep
from ..aliases import append_object_to_object, Alias
from scipy.spatial.transform import Rotation
import datetime
from ..elements.assembly import Assembly
from eco.devices_general.motors import AdjustablePiHex

class Hexapod_PI:
    def __init__(self, Id):
        self.Id = Id
        self.x, self.y, self.z = [
            ValueRdback(self.id + f":SET-POSI-{i}", self.id + f":POSI-{i}")
            for i in "XYZ"
        ]
        self.dx, self.dy, self.dz = [
            ValueRdback(self.id + f":SET-POSI-{i}", self.id + f":POSI-{i}")
            for i in "UVW"
        ]
        self._piv_x, self._piv_y, self._piv_z = [
            ValueRdback(self.id + f":SET-PIVOT-{i}", self.id + f":PIVOT-R-{i}")
            for i in "RST"
        ]

class HexapodPI(Assembly):
    def __init__(self, pvname, name=None, fina_angle_offset=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(
            AdjustablePiHex,
            self.pvname + ":SET-POSI-X",
            pvreadbackname=self.pvname + ":POSI-X",
            accuracy=0.001,
            unit="mm",
            name="x_raw",
            is_setting=True,
        )
        self._append(
            AdjustablePiHex,
            self.pvname + ":SET-POSI-Y",
            pvreadbackname=self.pvname + ":POSI-Y",
            accuracy=0.001,
            unit="mm",
            name="y_raw",
            is_setting=True,
        )
        self._append(
            AdjustablePiHex,
            self.pvname + ":SET-POSI-Z",
            pvreadbackname=self.pvname + ":POSI-Z",
            accuracy=0.001,
            unit="mm",
            name="z_raw",
            is_setting=True,
        )
        self._append(
            AdjustablePiHex,
            self.pvname + ":SET-POSI-U",
            pvreadbackname=self.pvname + ":POSI-U",
            accuracy=0.001,
            unit="deg",
            name="rx_raw",
            is_setting=True,
        )
        self._append(
            AdjustablePiHex,
            self.pvname + ":SET-POSI-V",
            pvreadbackname=self.pvname + ":POSI-V",
            accuracy=0.001,
            unit="deg",
            name="ry_raw",
            is_setting=True,
        )
        self._append(
            AdjustablePiHex,
            self.pvname + ":SET-POSI-W",
            pvreadbackname=self.pvname + ":POSI-W",
            accuracy=0.001,
            unit="deg",
            name="rz_raw",
            is_setting=True,
        )
        self._append(
            AdjustablePiHex,
            self.pvname + ":SET-PIVOT-R",
            pvreadbackname=self.pvname + ":PIVOT-R",
            accuracy=0.001,
            unit="mm",
            name="pivot_x",
            is_setting=True,
        )
        self._append(
            AdjustablePiHex,
            self.pvname + ":SET-PIVOT-S",
            pvreadbackname=self.pvname + ":PIVOT-S",
            accuracy=0.001,
            unit="mm",
            name="pivot_y",
            is_setting=True,
        )
        self._append(
            AdjustablePiHex,
            self.pvname + ":SET-PIVOT-T",
            pvreadbackname=self.pvname + ":PIVOT-T",
            accuracy=0.001,
            unit="mm",
            name="pivot_z",
            is_setting=True,
        )
        if fina_angle_offset:
            self._append(
                AdjustableFS, fina_angle_offset, name="ref_frame_angle", is_setting=True
            )
            self._append(
                AdjustableVirtual,
                [self.x_raw, self.y_raw, self.z_raw],
                lambda xraw, yraw, zraw: self._calc_xyz(xraw, yraw, zraw)[0],
                lambda x: self._calc_xyzraw(
                    x, self.y.get_current_value(), self.z.get_current_value()
                ),
                reset_current_value_to=False,
                append_aliases=False,
                change_simultaneously=False,
                check_limits=True,
                unit="mm",
                name="x",
                is_setting=False,
            )
            self._append(
                AdjustableVirtual,
                [self.x_raw, self.y_raw, self.z_raw],
                lambda xraw, yraw, zraw: self._calc_xyz(xraw, yraw, zraw)[1],
                lambda y: self._calc_xyzraw(
                    self.x.get_current_value(), y, self.z.get_current_value()
                ),
                reset_current_value_to=False,
                change_simultaneously=False,
                check_limits=True,
                append_aliases=True,
                unit="mm",
                name="y",
                is_setting=False,
            )
            self._append(
                AdjustableVirtual,
                [self.x_raw, self.y_raw, self.z_raw],
                lambda xraw, yraw, zraw: self._calc_xyz(xraw, yraw, zraw)[2],
                lambda z: self._calc_xyzraw(
                    self.x.get_current_value(), self.y.get_current_value(), z
                ),
                reset_current_value_to=False,
                append_aliases=False,
                change_simultaneously=False,
                check_limits=True,
                unit="mm",
                name="z",
                is_setting=False,
            )

    @property
    def rotation(self):
        angs = self.ref_frame_angle.get_current_value()
        angs = [angs["rz"],angs["rx"], angs["ry"]]
        return Rotation.from_euler("zxy", angs, degrees=True)

    def _calc_xyz(self, xraw, yraw, zraw):
        return self.rotation.apply([xraw, yraw, zraw])

    def _calc_xyzraw(self, x, y, z):
        return self.rotation.inv().apply([x, y, z])

    # # def get_status(self):
    # #     s = f'Hexapod {self.alias.get_full_name()} status ({datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")})\n'
    # #     if hasattr(self, "ref_frame_angle"):
    # #         for var in ["x", "y", "z"]:
    # #             s += (
    # #                 " " * 4
    # #                 + var.ljust(16)
    # #                 + f"{self.__dict__[var].get_current_value():g}\n"
    # #             )
    # #         s += (
    # #             " " * 4
    # #             + "ref_frame_angle".ljust(16)
    # #             + str(self.ref_frame_angle.get_current_value())
    # #             + "\n"
    # #         )
    # #     for var in [
    # #         "x_raw",
    # #         "y_raw",
    # #         "z_raw",
    # #         "rx_raw",
    # #         "ry_raw",
    # #         "rz_raw",
    # #         "pivot_x",
    # #         "pivot_y",
    # #         "pivot_z",
    # #     ]:
    # #         s += (
    # #             " " * 4
    # #             + var.ljust(16)
    # #             + f"{self.__dict__[var].get_current_value():g}\n"
    # #         )
    # #     return s

    # def __str__(self):
    #     return self.get_status()

    # def __repr__(self):
    #     return self.__str__()


class HexapodPI_old:
    def __init__(self, pvname, name=None, fina_angle_offset=None):
        self.name = name
        self.alias = Alias(name)
        self.pvname = pvname
        append_object_to_object(
            self,
            AdjustablePv,
            self.pvname + ":SET-POSI-X",
            pvreadbackname=self.pvname + ":POSI-X",
            accuracy=0.001,
            name="x_raw",
        )
        append_object_to_object(
            self,
            AdjustablePv,
            self.pvname + ":SET-POSI-Y",
            pvreadbackname=self.pvname + ":POSI-Y",
            accuracy=0.001,
            name="y_raw",
        )
        append_object_to_object(
            self,
            AdjustablePv,
            self.pvname + ":SET-POSI-Z",
            pvreadbackname=self.pvname + ":POSI-Z",
            accuracy=0.001,
            name="z_raw",
        )
        append_object_to_object(
            self,
            AdjustablePv,
            self.pvname + ":SET-POSI-U",
            pvreadbackname=self.pvname + ":POSI-U",
            accuracy=0.001,
            name="rx_raw",
        )
        append_object_to_object(
            self,
            AdjustablePv,
            self.pvname + ":SET-POSI-V",
            pvreadbackname=self.pvname + ":POSI-V",
            accuracy=0.001,
            name="ry_raw",
        )
        append_object_to_object(
            self,
            AdjustablePv,
            self.pvname + ":SET-POSI-W",
            pvreadbackname=self.pvname + ":POSI-W",
            accuracy=0.001,
            name="rz_raw",
        )
        append_object_to_object(
            self,
            AdjustablePv,
            self.pvname + ":SET-PIVOT-R",
            pvreadbackname=self.pvname + ":PIVOT-R",
            accuracy=0.001,
            name="pivot_x",
        )
        append_object_to_object(
            self,
            AdjustablePv,
            self.pvname + ":SET-PIVOT-S",
            pvreadbackname=self.pvname + ":PIVOT-S",
            accuracy=0.001,
            name="pivot_y",
        )
        append_object_to_object(
            self,
            AdjustablePv,
            self.pvname + ":SET-PIVOT-T",
            pvreadbackname=self.pvname + ":PIVOT-T",
            accuracy=0.001,
            name="pivot_z",
        )
        if fina_angle_offset:
            self.ref_frame_angle = AdjustableFS(fina_angle_offset)
            self.x = AdjustableVirtual(
                [self.x_raw, self.y_raw, self.z_raw],
                lambda xraw, yraw, zraw: self._calc_xyz(xraw, yraw, zraw)[0],
                lambda x: self._calc_xyzraw(
                    x, self.y.get_current_value(), self.z.get_current_value()
                ),
                reset_current_value_to=False,
                append_aliases=False,
                change_simultaneously=False,
                name="x",
            )
            self.y = AdjustableVirtual(
                [self.x_raw, self.y_raw, self.z_raw],
                lambda xraw, yraw, zraw: self._calc_xyz(xraw, yraw, zraw)[1],
                lambda y: self._calc_xyzraw(
                    self.x.get_current_value(), y, self.z.get_current_value()
                ),
                reset_current_value_to=False,
                change_simultaneously=False,
                append_aliases=False,
                name="y",
            )
            self.z = AdjustableVirtual(
                [self.x_raw, self.y_raw, self.z_raw],
                lambda xraw, yraw, zraw: self._calc_xyz(xraw, yraw, zraw)[2],
                lambda z: self._calc_xyzraw(
                    self.x.get_current_value(), self.y.get_current_value(), z
                ),
                reset_current_value_to=False,
                append_aliases=False,
                change_simultaneously=False,
                name="z",
            )

    @property
    def rotation(self):
        angs = self.ref_frame_angle.get_current_value()
        angs = [angs["rx"], angs["ry"], angs["rz"]]
        return Rotation.from_euler("xyz", angs, degrees=True)

    def _calc_xyz(self, xraw, yraw, zraw):
        return self.rotation.apply([xraw, yraw, zraw])

    def _calc_xyzraw(self, x, y, z):
        print(self.rotation.inv().apply([x, y, z]))
        return self.rotation.inv().apply([x, y, z])

    def get_status(self):
        s = f'Hexapod {self.alias.get_full_name()} status ({datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")})\n'
        if hasattr(self, "ref_frame_angle"):
            for var in ["x", "y", "z"]:
                s += (
                    " " * 4
                    + var.ljust(16)
                    + f"{self.__dict__[var].get_current_value():g}\n"
                )
            s += (
                " " * 4
                + "ref_frame_angle".ljust(16)
                + str(self.ref_frame_angle.get_current_value())
                + "\n"
            )
        for var in [
            "x_raw",
            "y_raw",
            "z_raw",
            "rx_raw",
            "ry_raw",
            "rz_raw",
            "pivot_x",
            "pivot_y",
            "pivot_z",
        ]:
            s += (
                " " * 4
                + var.ljust(16)
                + f"{self.__dict__[var].get_current_value():g}\n"
            )
        return s

    def __str__(self):
        return self.get_status()

    def __repr__(self):
        return self.__str__()


class HexapodSymmetrie(Assembly):
    def __init__(
        self, pv_master="SARES20-HEXSYM", name="hex_usd", offset=[0, 0, 0, 0, 0, 0]
    ):
        super().__init__(name=name)
        self.offset = offset
        self.pvname = pv_master
        self.coordinate_switch = AdjustablePvEnum(
            f"{self.pvname}:MOVE#PARAM:CM", name="hex_usd_coordinate_switch"
        )

        self.pvs_setpos = {
            "x": PV(f"{self.pvname}:MOVE#PARAM:X.VAL"),
            "y": PV(f"{self.pvname}:MOVE#PARAM:Y.VAL"),
            "z": PV(f"{self.pvname}:MOVE#PARAM:Z.VAL"),
            "rx": PV(f"{self.pvname}:MOVE#PARAM:RX.VAL"),
            "ry": PV(f"{self.pvname}:MOVE#PARAM:RY.VAL"),
            "rz": PV(f"{self.pvname}:MOVE#PARAM:RZ.VAL"),
        }
        self.pvs_getpos = {
            "x": PV(f"{self.pvname}:POSMACH:X"),
            "y": PV(f"{self.pvname}:POSMACH:Y"),
            "z": PV(f"{self.pvname}:POSMACH:Z"),
            "rx": PV(f"{self.pvname}:POSMACH:RX"),
            "ry": PV(f"{self.pvname}:POSMACH:RY"),
            "rz": PV(f"{self.pvname}:POSMACH:RZ"),
        }

        self._append(DetectorPvData, f"{self.pvname}:POSMACH:X", name="x")
        self._append(DetectorPvData, f"{self.pvname}:POSMACH:Y", name="y")
        self._append(DetectorPvData, f"{self.pvname}:POSMACH:Z", name="z")
        self._append(DetectorPvData, f"{self.pvname}:POSMACH:RX", name="rx")
        self._append(DetectorPvData, f"{self.pvname}:POSMACH:RY", name="ry")
        self._append(DetectorPvData, f"{self.pvname}:POSMACH:RZ", name="rz")

        self._ctrl_pv = PV(f"{self.pvname}:STATE#PANEL:SET.VAL")

    def set_coordinates(self, x, y, z, rx, ry, rz, relative_to_eco_offset=True):
        if relative_to_eco_offset:
            x = x + self.offset[0]
            y = y + self.offset[1]
            z = z + self.offset[2]
            rx = rx + self.offset[3]
            ry = ry + self.offset[4]
            rz = rz + self.offset[5]
        self.pvs_setpos["x"].put(x)
        self.pvs_setpos["y"].put(y)
        self.pvs_setpos["z"].put(z)
        self.pvs_setpos["rx"].put(rx)
        self.pvs_setpos["ry"].put(ry)
        self.pvs_setpos["rz"].put(rz)

    def get_coordinates(self, relative_to_eco_offset=True):
        x = self.pvs_getpos["x"].get()
        y = self.pvs_getpos["y"].get()
        z = self.pvs_getpos["z"].get()
        rx = self.pvs_getpos["rx"].get()
        ry = self.pvs_getpos["ry"].get()
        rz = self.pvs_getpos["rz"].get()
        if relative_to_eco_offset:
            x = x - self.offset[0]
            y = y - self.offset[1]
            z = z - self.offset[2]
            rx = rx - self.offset[3]
            ry = ry - self.offset[4]
            rz = rz - self.offset[5]
        return x, y, z, rx, ry, rz

    def set_control_on(self):
        self._ctrl_pv.put(3)

    def set_control_off(self):
        self._ctrl_pv.put(4)

    def get_control_state(self):
        stat = self._ctrl_pv.get()
        if stat == 3:
            return "control on"
        elif stat == 4:
            return "control on"
        elif stat == 2:
            return "stopped"
        elif stat == 11:
            return "moving"

    def move_to_coordinates(
        self,
        x,
        y,
        z,
        rx,
        ry,
        rz,
        precision=[0.001, 0.001, 0.001, 0.001, 0.001, 0.001],
        coordinate_type="absolute",
        relative_to_eco_offset=True,
    ):
        self.coordinate_switch.set_target_value(coordinate_type).wait()
        self.set_coordinates(
            x, y, z, rx, ry, rz, relative_to_eco_offset=relative_to_eco_offset
        )
        sleep(0.1)
        self.start_move(
            target=(x, y, z, rx, ry, rz),
            precision=precision,
            coordinate_type=coordinate_type,
        )

    def start_move(
        self,
        target=None,
        precision=[0.001, 0.001, 0.001, 0.001, 0.001, 0.001],
        coordinate_type="absolute",
        relative_to_eco_offset=True,
    ):
        print("Starting to move... stop with Ctrl-C")
        self.set_control_on()
        sleep(0.2)
        self._ctrl_pv.put(11)  # this starts moving!
        while 1:
            try:
                if target:
                    coo = self.get_coordinates(
                        relative_to_eco_offset=relative_to_eco_offset
                    )
                    if all(
                        [
                            abs(ctarg - cnow) < cprec
                            for ctarg, cnow, cprec in zip(target, coo, precision)
                        ]
                    ):
                        self.stop_move()
                        print("Target position reached")
                        break
                sleep(0.01)
            except KeyboardInterrupt:
                self.stop_move()
                print("Motion stopped")
                break
        sleep(0.1)
        self.set_control_off()
        sleep(0.05)

    def stop_move(self):
        self._ctrl_pv.put(2)
