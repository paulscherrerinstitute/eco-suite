from eco.elements.assembly import Assembly
from ..aliases import Alias, append_object_to_object
from ..devices_general.motors import MotorRecord
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum


class Xspect(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)

        self._append(MotorRecord, "SARFE10-PSSS055:MOTOR_X1", name="x_grating")
        self._append(MotorRecord, "SARFE10-PSSS055:MOTOR_Y1", name="y_grating")
        self._append(MotorRecord, "SARFE10-PSSS055:MOTOR_ROT_X1", name="rx_grating")
        self._append(MotorRecord, "SARFE10-PSSS055:MOTOR_PROBE", name="transl_probe")
        self._append(MotorRecord, "SARFE10-PSSS059:MOTOR_X2", name="x_girder")
        self._append(MotorRecord, "SARFE10-PSSS059:MOTOR_X3", name="x_crystal")
        self._append(MotorRecord, "SARFE10-PSSS059:MOTOR_Y3", name="y_crystal")
        self._append(MotorRecord, "SARFE10-PSSS059:MOTOR_ROT_X3", name="rx_crystal")
        self._append(MotorRecord, "SARFE10-PSSS059:MOTOR_Y4", name="y_alignment")
        self._append(MotorRecord, "SARFE10-PSSS059:MOTOR_ROT_X4", name="rx_camera")
        self._append(MotorRecord, "SARFE10-PSSS059:MOTOR_X5", name="x_camera")
        self._append(AdjustablePvEnum, "SARFE10-PSSS055:GRATING_SP", name="grid_type")
        self._append(
            AdjustablePvEnum, "SARFE10-PSSS059:CRYSTAL_SP", name="crystal_type"
        )
        self._append(AdjustablePvEnum, "SARFE10-PSSS055:PROBE_SP", name="probe")
        self._append(
            AdjustablePv, "SARFE10-PSSS059:ENERGY", name="energy_center_setpoint"
        )
        self._append(AdjustablePv, "SARFE10-PSSS059:MOTOR_Z5", name="camera_z")
