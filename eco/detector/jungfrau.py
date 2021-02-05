from ..devices_general.adjustable import PvRecord, AdjustableVirtual
from ..elements.assembly import Assembly
from ..aliases import Alias
from ..elements import memory


class Jungfrau(Assembly):
    def __init__(
        self,
        jf_id,
        pv_trigger="SAR-CVME-TIFALL5-EVG0:SoftEvt-EvtCode-SP",
        trigger_on=254,
        trigger_off=255,
        name=None,
    ):
        super().__init__(name=name)
        self.alias = Alias(name, channel=jf_id, channeltype="JF")

        self.jf_id = jf_id
        self._append(
            PvRecord,
            pv_trigger,
            is_status=True,
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

    def _set_trigger_enable(self, value):
        if value:
            self.trigger.set_target_value(self._trigger_on).wait()
        else:
            self.trigger.set_target_value(self._triggeroff).wait()
