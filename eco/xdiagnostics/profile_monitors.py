from eco.detector.detectors_psi import DetectorBsStream
from ..devices_general.motors import MotorRecord, SmaractStreamdevice, SmaractRecord
from ..devices_general.detectors import CameraCA, CameraBS
from ..devices_general.cameras_swissfel import CameraBasler, CameraPCO
from ..aliases import Alias
from ..elements.adjustable import AdjustableVirtual
from ..epics.adjustable import AdjustablePvEnum
from ..elements.assembly import Assembly

# from ..devices_general.epics_wrappers import EnumSelector


def addMotorRecordToSelf(self, Id=None, name=None):
    self.__dict__[name] = MotorRecord(Id, name=name)
    self.alias.append(self.__dict__[name].alias)


class Pprm(Assembly):
    def __init__(self, pvname, pvname_camera, name=None, in_target=1, bs_channels={}):
        super().__init__(name=name)
        self.pvname = pvname
        self.in_target = in_target

        self._append(
            AdjustablePvEnum, self.pvname + ":PROBE_SP", name="target", is_setting=True
        )

        self._append(
            MotorRecord,
            pvname_camera + ":MOTOR_PROBE",
            name="target_pos",
            is_setting=True,
        )
        self.camCA = CameraCA(pvname_camera)
        self._append(
            CameraBasler,
            pvname_camera,
            camserver_alias=f"{name} ({pvname_camera})",
            name="camera",
            is_setting=True,
            is_display="recursive",
        )
        self._append(
            AdjustablePvEnum, self.pvname + ":LED", name="led", is_setting=True
        )
        for bscn, bscc in bs_channels.items():
            self._append(
                DetectorBsStream,
                bscc,
                name=bscn,
                cachannel=None,
                is_setting=False,
            )

    def movein(self, target=None):
        if target == None:
            target = self.in_target
        self.target.set_target_value(target)

    def moveout(self, target=0):
        self.target.set_target_value(target)

    # def __repr__(self):
    #     s = f"**Profile Monitor {self.name}**\n"
    #     s += f"Target in beam: {self.target.get_current_value().name}\n"
    #     return s


class Target_xyz(Assembly):
    def __init__(
        self,
        pvname_x="SARES20-MF2:MOT_1",
        pvname_y="SARES20-MF2:MOT_2",
        pvname_z="SARES20-MF2:MOT_3",
        name=None,
    ):
        super().__init__(name=name)
        self._append(
            MotorRecord,
            pvname_x,
            name="x",
            is_setting=True,
        )
        self._append(
            MotorRecord,
            pvname_y,
            name="y",
            is_setting=True,
        )
        self._append(
            MotorRecord,
            pvname_z,
            name="z",
            is_setting=True,
        )
        # temporarily remove the offsets for the limit switch homed stages
        ix = self.settings_collection._list.index(self.x.offset)
        self.settings_collection._list.pop(ix)
        ix = self.settings_collection._list.index(self.y.offset)
        self.settings_collection._list.pop(ix)
        ix = self.settings_collection._list.index(self.z.offset)
        self.settings_collection._list.pop(ix)


class ProfKbBernina(Assembly):
    def __init__(
        self,
        pvname_target_x="SARES20-MF2:MOT_1",
        pvname_target_y="SARES20-MF2:MOT_2",
        pvname_target_z="SARES20-MF2:MOT_3",
        pvname_mirror="SARES20-MCS1:MOT_11",
        mirror_in=15,
        mirror_out=-5,
        pvname_zoom="SARES20-MF2:MOT_4",
        pvname_camera="SARES20-PROF141-M1",
        name=None,
    ):
        super().__init__(name=name)
        self.mirror_in_position = mirror_in
        self.mirror_out_position = mirror_out
        self._append(
            Target_xyz,
            pvname_x="SARES20-MF2:MOT_1",
            pvname_y="SARES20-MF2:MOT_2",
            pvname_z="SARES20-MF2:MOT_3",
            name="target_stages",
            is_display="recursive",
            is_setting=True,
        )
        self.target = self.target_stages.presets

        self._append(
            SmaractRecord,
            pvname_mirror,
            name="x_mirror_microscope",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustableVirtual,
            [self.x_mirror_microscope],
            lambda v: abs(v - self.mirror_in_position) < 0.003,
            lambda v: self.mirror_in_position if v else self.mirror_out_position,
            name="mirror_in",
            is_setting=True,
            is_display=True,
        )
        # self.camCA = CameraCA(pvname_camera)
        self._append(
            CameraBasler,
            pvname_camera,
            camserver_alias=f"{name} ({pvname_camera})",
            name="camera",
            is_setting=False,
            is_display="recursive",
        )
        self._append(
            MotorRecord, pvname_zoom, name="zoom", is_setting=True, is_display=True
        )
        ix = self.settings_collection._list.index(self.zoom.offset)
        self.settings_collection._list.pop(ix)

    def movein_keep_target(self, wait=False):
        ch = self.mirror_in.set_target_value(1)
        if wait:
            ch.wait()

    def moveout_keep_target(self, wait=False):
        ch = self.mirror_in.set_target_value(0)
        if wait:
            ch.wait()

    def movein(self, wait=False):
        ch = self.mirror_in.set_target_value(1)
        try:
            self.presets.movein()
        except:
            print("No movein preset found for prof_kb.")
        if wait:
            ch.wait()

    def moveout(self, wait=False):
        ch = self.mirror_in.set_target_value(0)
        try:
            self.presets.moveout()
        except:
            print("No moveout preset found for prof_kb.")
        if wait:
            ch.wait()


class Pprm_dsd(Assembly):
    def __init__(self, pvname, pvname_camera, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(
            MotorRecord,
            self.pvname + ":MOTOR_PROBE",
            name="target_pos",
            is_setting=True,
        )
        self.camCA = CameraCA(pvname_camera)
        self._append(
            CameraPCO,
            pvname_camera,
            camserver_alias=f"{name} ({pvname_camera})",
            name="camera",
            is_setting=False,
            is_display="recursive",
        )
        self._append(
            MotorRecord, self.pvname + ":MOTOR_ZOOM", name="zoom", is_setting=True
        )
        self._append(
            AdjustablePvEnum, self.pvname + ":PROBE_SP", name="target", is_setting=True
        )

    def movein(self, target=1):
        self.target.set_target_value(target)

    def moveout(self, target=0):
        self.target.set_target_value(target)


class Pprmold:
    def __init__(self, Id, name=None):
        self.Id = Id
        self.name = name
        self.target_pos = MotorRecord(Id + ":MOTOR_PROBE", name="target_pos")
        self.camCA = CameraCA(Id)
        # self.camCS = CameraCS(Id, name)
        self.led = AdjustablePvEnum(self.Id + ":LED", name="led")
        self.target = AdjustablePvEnum(self.Id + ":PROBE_SP", name="target")
        if name:
            self.alias = Alias(name)
            self.alias.append(self.target_pos.alias)
            self.alias.append(self.target.alias)
            self.alias.append(self.led.alias)

    def movein(self, target=1):
        self.target.set_target_value(target)

    def moveout(self, target=0):
        self.target.set_target_value(target)

    def __repr__(self):
        s = f"**Profile Monitor {self.name}**\n"
        s += f"Target in beam: {self.target.get_current_value().name}\n"
        return s


class Bernina_XEYE(Assembly):
    def __init__(
        self, camera_pv=None, zoomstage_pv=None, bshost=None, bsport=None, name=None
    ):
        super().__init__(name=name)
        if zoomstage_pv:
            self._append(MotorRecord, zoomstage_pv, name="zoom", is_setting=True)
        try:
            self.cam = CameraCA(camera_pv)
            self._append(
                CameraBasler,
                camera_pv,
                camserver_alias=f"{name} ({camera_pv})",
                name="camera",
                is_setting=True,
                is_display="recursive",
            )
        except:
            print("X-Ray eye Cam not found")
            pass

        if bshost:
            self.camBS = CameraBS(host=bshost, port=bsport)


#        self._led = PV(self.Id+':LED')


#    def illuminate(self,value=None):
#        if value:
#            self._led.put(value)
#        else:
#            self._led.put(
#                    not self.get_illumination_state())
#
#    def get_illumination_state(self):
#        return bool(self._led.get())
#
