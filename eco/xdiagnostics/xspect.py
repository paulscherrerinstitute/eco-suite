from ..aliases import Alias, append_object_to_object
from ..devices_general.motors import MotorRecord
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum


class Xspect:
    def __init__(self, name=None):
        self.alias = Alias(name)
        append_object_to_object(
            self, MotorRecord, "SARFE10-PSSS055:MOTOR_X1", name="x_grating"
        )
        append_object_to_object(
            self, MotorRecord, "SARFE10-PSSS055:MOTOR_Y1", name="y_grating"
        )
        append_object_to_object(
            self, MotorRecord, "SARFE10-PSSS055:MOTOR_ROT_X1", name="rx_grating"
        )
        append_object_to_object(
            self, MotorRecord, "SARFE10-PSSS055:MOTOR_PROBE", name="transl_probe"
        )
        append_object_to_object(
            self, MotorRecord, "SARFE10-PSSS059:MOTOR_X2", name="x_girder"
        )
        append_object_to_object(
            self, MotorRecord, "SARFE10-PSSS059:MOTOR_X3", name="x_crystal"
        )
        append_object_to_object(
            self, MotorRecord, "SARFE10-PSSS059:MOTOR_Y3", name="y_crystal"
        )
        append_object_to_object(
            self, MotorRecord, "SARFE10-PSSS059:MOTOR_ROT_X3", name="rx_crystal"
        )
        append_object_to_object(
            self, MotorRecord, "SARFE10-PSSS059:MOTOR_Y4", name="y_alignment"
        )
        append_object_to_object(
            self, MotorRecord, "SARFE10-PSSS059:MOTOR_ROT_X4", name="rx_camera"
        )
        append_object_to_object(
            self, MotorRecord, "SARFE10-PSSS059:MOTOR_X5", name="x_camera"
        )
        append_object_to_object(
            self, AdjustablePvEnum, "SARFE10-PSSS055:GRATING_SP", name="grid_type"
        )
        append_object_to_object(
            self, AdjustablePvEnum, "SARFE10-PSSS059:CRYSTAL_SP", name="crystal_type"
        )
        append_object_to_object(
            self, AdjustablePvEnum, "SARFE10-PSSS055:PROBE_SP", name="probe"
        )
        append_object_to_object(
            self, AdjustablePv, "SARFE10-PSSS059:ENERGY", name="energy_center_setpoint"
        )
        append_object_to_object(
            self, AdjustablePv, "SARFE10-PSSS059:MOTOR_Z5", name="camera_z"
        )



