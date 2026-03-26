from eco import Assembly

from eco.epics.detector import DetectorPvData
from eco.epics.adjustable import AdjustablePv
from eco.elements.adjustable import AdjustableGetSet
from epics import PV


class OPA(Assembly):
    def __init__(self, pvbase, motor_definitions={}, name="none"):
        super().__init__(name=name)
        self.pvbase = pvbase

        self._pv_open = PV(pvbase + ":OPEN_SHUTTER.PROC")
        self._pv_close = PV(pvbase + ":CLOSE_SHUTTER.PROC")
        self._pv_shutter_status = PV(pvbase + ":GET_SHUTTER")

        self._append(
            AdjustablePv,
            pvbase + ":SET_WAVELENGTH",
            pvreadbackname=pvbase + ":GET_WAVELENGTH",
            name="wavelength",
            is_setting=True,
        )
        for mnum, mdef in motor_definitions.items():
            self._append(
                DetectorPvData,
                pvbase + f":MOT{mnum}_GET_POS_CAL",
                has_unit_pv=pvbase + f":MOT{mnum}_UNIT",
                name=f"{mdef['name']}_readback",
                is_setting=False,
            )
            self._append(
                AdjustablePv,
                pvbase + f":MOT{mnum}_SET_ABS_STEPS",
                pvreadbackname=pvbase + f":MOT{mnum}_GET_POS",
                name=mdef["name"],
                is_setting=True,
            )

        self._append(
            AdjustableGetSet, self._get_shutter, self._set_shutter, name="shutter"
        )

    def _set_shutter(self, value):
        if value:
            self._pv_open.put(1)
        else:
            self._pv_close.put(1)

    def _get_shutter(self):
        return self._pv_shutter_status.get()


class Prime(OPA):
    def __init__(self, pvbase, name="none"):
        super().__init__(
            pvbase,
            motor_definitions={
                1: {"name": "crystal1"},
                2: {"name": "delay1"},
                3: {"name": "crystal2"},
                4: {"name": "delay2"},
                5: {"name": "crystal3"},
                6: {"name": "delay3"},
                7: {"name": "nvr_1"},
                8: {"name": "nvr_2"},
                9: {"name": "nvr_3"},
                10: {"name": "ndfg_crystal"},
                11: {"name": "ndfg_mirror"},
                12: {"name": "ndfg_delay"},
            },
            name=name,
        )


class TwinsSeed(OPA):
    def __init__(self, pvbase, name="none"):
        super().__init__(
            pvbase,
            motor_definitions={
                1: {"name": "crystal1"},
                2: {"name": "delay1"},
                3: {"name": "crystal2"},
                4: {"name": "delay2"},
                5: {"name": "ndfg_mirror_h"},
                6: {"name": "ndfg_mirror_v"},
                7: {"name": "ndfg_delay"},
                8: {"name": "ndfg_crystal"},
            },
            name=name,
        )


class TwinsPump(OPA):
    def __init__(self, pvbase, name="none"):
        super().__init__(
            pvbase,
            motor_definitions={
                1: {"name": "crystal1"},
                2: {"name": "delay1"},
                3: {"name": "crystal2"},
                4: {"name": "delay2"},
                5: {"name": "crystal3"},
                6: {"name": "delay3"},
            },
            name=name,
        )


class White(OPA):
    def __init__(self, pvbase, name="none"):
        super().__init__(
            pvbase,
            motor_definitions={
                1: {"name": "crystal"},
                2: {"name": "delay1"},
                3: {"name": "delay2"},
                4: {"name": "compressor"},
            },
            name=name,
        )
