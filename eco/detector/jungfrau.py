from tkinter import W
from ..elements.adjustable import AdjustableVirtual, AdjustableGetSet
from ..epics.adjustable import AdjustablePv
from ..elements.assembly import Assembly
from ..aliases import Alias
from pathlib import Path
from ..elements import memory
from datetime import datetime


class Jungfrau(Assembly):
    def __init__(
        self,
        jf_id,
        pv_trigger="SAR-CVME-TIFALL5-EVG0:SoftEvt-EvtCode-SP",
        trigger_on=254,
        trigger_off=255,
        broker_address="http://sf-daq:10002",
        name=None,
    ):
        super().__init__(name=name)
        self.alias = Alias(name, channel=jf_id, channeltype="JF")

        self.jf_id = jf_id
        self.broker_address = broker_address
        self._append(
            AdjustablePv,
            pv_trigger,
            is_display=True,
            is_setting=False,
            name="trigger",
        )
        self._trigger_on = trigger_on
        self._trigger_off = trigger_off
        self._append(
            AdjustableVirtual,
            [self.trigger],
            lambda value: value == self._trigger_on,
            self._set_trigger_enable,
            name="trigger_enable",
            append_aliases=False,
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            self.get_present_pedestal_filename,
            lambda value: NotImplementedError(
                "Can not set the pedestal file manually yet."
            ),
            name="pedestal_file",
            is_display=True,
        )
        self._append(
            AdjustableGetSet,
            self.get_present_gain_filename,
            lambda value: NotImplementedError(
                "Can not set the pedestal file manually yet."
            ),
            name="gain_file",
            is_display=True,
        )

    def _set_trigger_enable(self, value):
        if value:
            self.trigger.set_target_value(self._trigger_on).wait()
        else:
            self.trigger.set_target_value(self._trigger_off).wait()

    def get_present_gain_filename(self):
        filepath = Path(f"/sf/jungfrau/config/gainMaps/{self.jf_id}/gains.h5")

        if filepath.exists():
            return filepath.as_posix()
        else:
            raise Exception(f"File {filepath.as_posix()} seems not to exist!")

    def get_present_pedestal_filename(self):
        searchpath = Path(f"/sf/jungfrau/data/pedestal/{self.jf_id}")
        filelist = list(searchpath.glob("*.h5"))
        times = [datetime.strptime(f.stem, "%Y%m%d_%H%M%S") for f in filelist]
        return filelist[times.index(max(times))].as_posix()

    def get_detector_frequency(self):
        return self._event_master.event_codes[
            self._detectors_event_code
        ].frequency.get_current_value()

    def get_availability(self):
        is_available = (
            self.jf_id
            in requests.get(f"{self.broker_address}/get_allowed_detectors_list").json()[
                "detectors"
            ]
        )
        return is_available

    def get_isrunning(self):
        is_running = (
            self.jf_id
            in requests.get(f"{self.broker_address}/get_running_detectors_list").json()[
                "detectors"
            ]
        )
        return is_running

    def power_on(self):
        JF_channel = self.jf_id
        par = {"detector_name": JF_channel}
        return requests.post(
            f"{self.broker_address}/power_on_detector", json=par
        ).json()

    # def take_pedestal(self, JF_list=None, pgroup=None):
    #     if pgroup is None:
    #         pgroup = self.pgroup
    #     if not JF_list:
    #         JF_list = self.get_JFs_running()
    #     parameters = {
    #         "pgroup": pgroup,
    #         "rate_multiplicator": 1,
    #         "detectors": {tJF: {} for tJF in JF_list},
    #     }
    #     return requests.post(
    #         f"{self.broker_address}/take_pedestal", json=parameters
    #     ).json()
