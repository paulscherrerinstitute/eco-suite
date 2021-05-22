from epics import PV
import os
import numpy as np
import time
from ..devices_general.utilities import Changer
from ..elements.adjustable import spec_convenience, AdjustableFS, AdjustableVirtual
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum
from ..aliases import append_object_to_object, Alias
from ..elements import Assembly


@spec_convenience
class XltEpics:
    def __init__(self, pvname="SLAAR02-LTIM-PDLY", name="lxt_epics"):
        self.pvname = pvname
        self.alias = Alias(name)
        append_object_to_object(
            self,
            AdjustablePvEnum,
            self.pvname + ":SHOTDELAY",
            name="oscialltor_pulse_offset",
        )
        append_object_to_object(
            self,
            AdjustablePvEnum,
            self.pvname + ":SHOTMOFFS_ENA",
            name="modulo_offset_mode",
        )
        append_object_to_object(
            self, AdjustablePv, self.pvname + ":DELAY_Z_OFFS", name="_offset"
        )
        self.offset = AdjustableVirtual(
            [self._offset],
            lambda offset: offset * 1e-12,
            lambda offset: offset / 1e-12,
            name="offset",
        )
        append_object_to_object(
            self, AdjustablePv, self.pvname + ":DELAY", name="_set_user_delay_value"
        )
        self._delay_dial_rb = PV("SLAAR-LGEN:DLY_OFFS2")
        self.alias.append(
            Alias("delay_dial_rb", "SLAAR-LGEN:DLY_OFFS2", channeltype="CA")
        )
        self.waiting_for_change = PV(self.pvname + ":WAITING")

    def get_current_dial_value(self):
        return self._delay_dial_rb.get() * 1e-6

    def get_current_value(self):
        return self.get_current_dial_value() - self.offset.get_current_value()

    def change_user_and_wait(self, value, check_interval=0.03):
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

        self.waiting_for_change.add_callback(callback=set_is_stopped)
        self._set_user_delay_value.set_target_value(value / 1e-12)

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
        self.offset.set_target_value((self.get_current_dial_value() - value)).wait()


@spec_convenience
class XltEpics(Assembly):
    def __init__(self, pvname="SLAAR02-LTIM-PDLY", name="lxt_epics"):
        super().__init__(name=name)
        self.pvname = pvname
        self.settings_collection.append(self, force=True)
        self.status_indicators_collection.append(self, force=True)
        self._append(
            AdjustablePvEnum,
            self.pvname + ":SHOTDELAY",
            name="oscillator_pulse_offset",
            is_setting=True,
            is_status=True,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":SHOTMOFFS_ENA",
            name="modulo_offset_mode",
            is_setting=True,
            is_status=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":DELAY_Z_OFFS",
            name="_offset",
            is_setting=True,
            is_status=False,
        )
        self._append(
            AdjustableVirtual,
            [self._offset],
            lambda offset: offset * 1e-12,
            lambda offset: offset / 1e-12,
            name="offset",
            is_setting=False,
            is_status=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":DELAY",
            name="_set_user_delay_value",
            is_setting=False,
            is_status=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":P_RATIO",
            name="rep_len",
            is_setting=True,
            is_status=True,
        )
        self._append(
            AdjustablePv,
            "SIN-TIMAST-TMA:Evt-22-Freq-SP",
            name="laser_frequency",
            is_setting=True,
            is_status=True,
        )
        self._delay_dial_rb = PV("SLAAR-LGEN:DLY_OFFS2")
        self.alias.append(
            Alias("delay_dial_rb", "SLAAR-LGEN:DLY_OFFS2", channeltype="CA")
        )
        self.waiting_for_change = PV(self.pvname + ":WAITING")

    def get_current_dial_value(self):
        return self._delay_dial_rb.get() * 1e-6

    def get_current_value(self):
        return self.get_current_dial_value() - self.offset.get_current_value()

    def change_user_and_wait(self, value, check_interval=0.03):
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

        self.waiting_for_change.add_callback(callback=set_is_stopped)
        self._set_user_delay_value.set_target_value(value / 1e-12)

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
        self.offset.set_target_value((self.get_current_dial_value() - value)).wait()
