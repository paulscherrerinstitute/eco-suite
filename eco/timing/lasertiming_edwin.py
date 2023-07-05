from epics import PV
import os
import numpy as np
import time

from eco.elements.detector import DetectorVirtual
from ..devices_general.utilities import Changer
from ..elements.adjustable import (
    spec_convenience,
    AdjustableFS,
    AdjustableVirtual,
    tweak_option,
    value_property,
)
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum
from ..epics.detector import DetectorPvData
from ..aliases import append_object_to_object, Alias
from ..elements.assembly import Assembly


# @spec_convenience
# class XltEpics:
#     def __init__(self, pvname="SLAAR02-LTIM-PDLY", name="lxt_epics"):
#         self.pvname = pvname
#         self.alias = Alias(name)
#         append_object_to_object(
#             self,
#             AdjustablePvEnum,
#             self.pvname + ":SHOTDELAY",
#             name="oscialltor_pulse_offset",
#         )
#         append_object_to_object(
#             self,
#             AdjustablePvEnum,
#             self.pvname + ":SHOTMOFFS_ENA",
#             name="modulo_offset_mode",@spec_convenience
# class XltEpics:
#     def __init__(self, pvname="SLAAR02-LTIM-PDLY", name="lxt_epics"):
#         self.pvname = pvname
#         self.alias = Alias(name)
#         append_object_to_object(
#             self,
#             AdjustablePvEnum,
#             self.pvname + ":SHOTDELAY",
#             name="oscialltor_pulse_offset",
#         )
#         append_object_to_object(
#             self,
#             AdjustablePvEnum,
#             self.pvname + ":SHOTMOFFS_ENA",
#             name="modulo_offset_mode",
#         )
#         append_object_to_object(
#             self, AdjustablePv, self.pvname + ":DELAY_Z_OFFS", name="_offset"
#         )
#         self.offset = AdjustableVirtual(
#             [self._offset],
#             lambda offset: offset * 1e-12,
#             lambda offset: offset / 1e-12,
#             name="offset",
#         )
#         append_object_to_object(
#             self, AdjustablePv, self.pvname + ":DELAY", name="_set_user_delay_value"
#         )
#         self._delay_dial_rb = PV("SLAAR-LGEN:DLY_OFFS2")
#         self.alias.append(
#             Alias("delay_dial_rb", "SLAAR-LGEN:DLY_OFFS2", channeltype="CA")
#         )
#         self.waiting_for_change = PV(self.pvname + ":WAITING")

#     def get_current_dial_value(self):
#         return self._delay_dial_rb.get() * 1e-6

#     def get_current_value(self):
#         return self.get_current_dial_value() - self.offset.get_current_value()

#     def change_user_and_wait(self, value, check_interval=0.03):
#         if np.abs(value) > 0.1:
#             raise Exception("Very large value! This value is counted in seconds!")
#         if not self.waiting_for_change.get():
#             raise Exception("lxt is still moving!")
#         self.is_moving = False
#         self.is_stopped = False

#         def set_is_stopped(**kwargs):
#             old_status = self.is_moving
#             new_status = not bool(kwargs["value"])
#             if (not new_status) and old_status:
#                 self.is_stopped = True
#             self.is_moving = new_status

#         self.waiting_for_change.add_callback(callback=set_is_stopped)
#         self._set_user_delay_value.set_target_value(value / 1e-12)

#         while not self.is_stopped:
#             time.sleep(check_interval)
#         self.waiting_for_change.clear_callbacks()

#     def set_target_value(self, value, hold=False):
#         return Changer(
#             target=value,
#             parent=self,
#             changer=self.change_user_and_wait,
#             hold=hold,
#             stopper=None,
#         )

#     def reset_current_value_to(self, value):
#         self.offset.set_target_value((self.get_current_dial_value() - value)).wait()
#         )
#         append_object_to_object(
#             self, AdjustablePv, self.pvname + ":DELAY_Z_OFFS", name="_offset"
#         )
#         self.offset = AdjustableVirtual(
#             [self._offset],
#             lambda offset: offset * 1e-12,
#             lambda offset: offset / 1e-12,
#             name="offset",
#         )
#         append_object_to_object(
#             self, AdjustablePv, self.pvname + ":DELAY", name="_set_user_delay_value"
#         )
#         self._delay_dial_rb = PV("SLAAR-LGEN:DLY_OFFS2")
#         self.alias.append(
#             Alias("delay_dial_rb", "SLAAR-LGEN:DLY_OFFS2", channeltype="CA")
#         )
#         self.waiting_for_change = PV(self.pvname + ":WAITING")

#     def get_current_dial_value(self):
#         return self._delay_dial_rb.get() * 1e-6

#     def get_current_value(self):
#         return self.get_current_dial_value() - self.offset.get_current_value()

#     def change_user_and_wait(self, value, check_interval=0.03):
#         if np.abs(value) > 0.1:
#             raise Exception("Very large value! This value is counted in seconds!")
#         if not self.waiting_for_change.get():
#             raise Exception("lxt is still moving!")
#         self.is_moving = False
#         self.is_stopped = False

#         def set_is_stopped(**kwargs):
#             old_status = self.is_moving
#             new_status = not bool(kwargs["value"])
#             if (not new_status) and old_status:
#                 self.is_stopped = True
#             self.is_moving = new_status

#         self.waiting_for_change.add_callback(callback=set_is_stopped)
#         self._set_user_delay_value.set_target_value(value / 1e-12)

#         while not self.is_stopped:
#             time.sleep(check_interval)
#         self.waiting_for_change.clear_callbacks()

#     def set_target_value(self, value, hold=False):
#         return Changer(
#             target=value,
#             parent=self,
#             changer=self.change_user_and_wait,
#             hold=hold,
#             stopper=None,
#         )

#     def reset_current_value_to(self, value):
#         self.offset.set_target_value((self.get_current_dial_value() - value)).wait()


@spec_convenience
@tweak_option
@value_property
class XltEpics(Assembly):
    def __init__(self, pvname="SLAAR02-LTIM-PDLY", name="lxt_epics"):
        super().__init__(name=name)
        self.settings_collection.append(self, force=True)
        self.pvname = pvname
        # self.settings_collection.append(self, force=True)
        # self.status_collection.append(self, force=True)
        # self.display_collection.append(self, force=True)
        self._append(
            AdjustablePv,
            self.pvname + ":DELAY_Z_OFFS",
            name="_offset",
            is_setting=True,
            is_display=False,
        )  # in picoseconds
        self._append(
            DetectorPvData,
            "SLAAR-LGEN:DLY_OFFS2",
            unit="ps",
            name="delay_dial_rb",
            is_setting=False,
            is_display=False,
        )
        # SLAAR-LGEN:DLY_OFFS2
        self._append(
            AdjustableVirtual,
            [self._offset],
            lambda offset: offset * 1e-12,
            lambda offset: offset / 1e-12,
            name="offset",
            unit="s",
            is_setting=False,
            is_display=False,
        )
        self._append(
            DetectorVirtual,
            [self.delay_dial_rb, self.offset],
            lambda dialrb,offset: dialrb * 1e-6 - offset,
            unit="s",
            name="readback",
        )
        self._append(
            AdjustablePv,
            self.pvname + ":WINDOW_REQ",
            name="phase_shifter_window_start",
            is_setting=True,
            is_display=True,
            unit="ps",
        )
        self._append(
            AdjustablePv,
            self.pvname + ":LONG_DELAY_THRESH",
            name="_long_delay_threshold",
            is_setting=True,
            is_display=False,
            unit="ps",
        )
        self._append(
            AdjustableVirtual,
            [self._long_delay_threshold],
            lambda offset: offset * 1e-12,
            lambda offset: offset / 1e-12,
            name="long_delay_threshold",
            unit="s",
            is_setting=True,
            is_display=True,
        )

        # self._append(
        #     AdjustablePvEnum,
        #     self.pvname + ":MODE_SET1",
        #     pvname_set = self.pvname + ':MODESELECT',
        #     name="reference_mode",
        #     is_setting=True,
        #     is_display=True,
        # )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":SHOTDELAY",
            name="oscillator_pulse_offset",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":SHOTMOFFS_ENA",
            name="modulo_offset_mode",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":ONEINN_MODE",
            name="reference_mode",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":USE_EXT_EVT",
            name="use_ext_reference_event",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":ALT_EXT_EVT",
            name="ext_reference_event",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":DELAY",
            name="_set_user_delay_value",
            is_setting=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":P_RATIO",
            name="ref_pattern_len",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            "SIN-TIMAST-TMA:Evt-22-Freq-SP",
            name="laser_frequency",
            unit="Hz",
            is_setting=True,
            is_display=True,
        )


        # self._append(
        #     DetectorPvData,
        #     "SLAAR-LGEN:DLY_OFFS2",
        #     name="delay_dial",
        #     is_setting=False,
        #     is_display=True,
        # )

        # self._delay_dial_rb = PV("SLAAR-LGEN:DLY_OFFS2")
        # self.alias.append(
        #     Alias("delay_dial_rb", "SLAAR-LGEN:DLY_OFFS2", channeltype="CA")
        # )
        self.waiting_for_change = PV(self.pvname + ":WAITING")

    # def get_current_dial_value(self):
    #     return self.delay_dial_rb.get_current_value() * 1e-6

    def get_current_value(self):
        return self.readback.get_current_value()

    # def get_current_dial_value(self):
    #     return self.delay_dial_rb.get_current_value() * 1e-6

    # def get_current_user_value(self):
    #     return (
    #         self.delay_dial_rb.get_current_value() * 1e-6
    #         - self.offset.get_current_value()
    #     )

    def change_user_and_wait(self, value, check_interval=0.03, evr_wait_time=0.01):
        if np.abs(value) > 0.1:
            raise Exception("Very large value! This value is counted in seconds!")
        if not self.waiting_for_change.get():
            raise Exception("lxt is still moving!")
        self.is_moving = False
        self.is_stopped = False

        def set_is_stopped(**kwargs):
            old_status = self.is_moving
            new_status = not bool(kwargs["value"])
            if (not new_status) and old_status:
                self.is_stopped = True
            self.is_moving = new_status

        is_phasshift = not (
            self.long_delay_threshold.get_current_value() < np.abs(value)
        )
        if is_phasshift:
            self.waiting_for_change.add_callback(callback=set_is_stopped)

        self._set_user_delay_value.set_target_value(
            (value) / 1e-12
        )
        if not is_phasshift:
            time.sleep(evr_wait_time)
            self.is_stopped = True

        while not self.is_stopped:
            time.sleep(check_interval)
        self.waiting_for_change.clear_callbacks()

    def set_target_value(self, value, hold=False):
        return Changer(
            target=value,
            parent=self,
            changer=self.change_user_and_wait,
            hold=hold,
            stopper=None,
        )

    def reset_current_value_to(self, value):
        self.offset.set_target_value(self.offset.get_current_value() + self.get_current_value() - value).wait()

        # caqtdm -noMsg  -macro S=SLAAR02-LTIM-PDLY  /sf/laser/config/qt/SLAAR02-L-SET_DELAY.ui

    def gui(self):
        self._run_cmd(
            f"caqtdm -noMsg  -macro S=SLAAR02-LTIM-PDLY  /sf/laser/config/qt/SLAAR02-L-SET_DELAY.ui"
        )

@spec_convenience
@tweak_option
@value_property
class LaserRateControl(Assembly):
    def __init__(self, pvname="SLAAR02-LTIM-PDLY", name=None):
        super().__init__(name=name)
        self.settings_collection.append(self, force=True)
        self.pvname = pvname
        # self.settings_collection.append(self, force=True)
        # self.status_collection.append(self, force=True)
        # self.display_collection.append(self, force=True)
        self._append(
            AdjustablePvEnum,
            self.pvname + ":ONEINN_MODE",
            name="reference_mode",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            "SIN-TIMAST-TMA:Evt-22-Freq-SP",
            name="laser_frequency",
            unit="Hz",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":SHOTDELAY",
            name="oscillator_pulse_offset",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":SHOTMOFFS_ENA",
            name="modulo_offset_mode",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":USE_EXT_EVT",
            name="use_ext_reference_event",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":ALT_EXT_EVT",
            name="ext_reference_event",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":P_RATIO",
            name="ref_pattern_len",
            is_setting=True,
            is_display=True,
        )


        # self._append(
        #     DetectorPvData,
        #     "SLAAR-LGEN:DLY_OFFS2",
        #     name="delay_dial",
        #     is_setting=False,
        #     is_display=True,
        # )

        # self._delay_dial_rb = PV("SLAAR-LGEN:DLY_OFFS2")
        # self.alias.append(
        #     Alias("delay_dial_rb", "SLAAR-LGEN:DLY_OFFS2", channeltype="CA")
        # )
    

    def gui(self):
        self._run_cmd(
            f"caqtdm -noMsg  -macro S=SLAAR02-LTIM-PDLY  /sf/laser/config/qt/SLAAR02-L-SET_DELAY.ui"
        )