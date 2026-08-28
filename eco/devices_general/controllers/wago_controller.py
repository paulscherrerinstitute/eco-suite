"""One physical WAGO analog/digital I/O + thermosensor controller unit.

Mirrors the single caqtdm overview panel for the whole box
(`/ioc/modules/qt/SARES20_CWAG_GPS01-OVERVIEW.ui`, macros
`HOST=<prefix>,IOC=...,DEVICE=<prefix>` -- see the "Analog in and out,
Thermosensors (WAGO)" entry in
`/sf/bernina/config/launcher/S_Bernina_Components.json`), which shows 8
analog inputs, 8 analog outputs and 16 thermosensor channels
(`<prefix>:TEMP-T1`..`T16`) as one unit.

In `eco.bernina.bernina` today these are three *separately-registered*
things against the same prefix: `WagoAnalogInputs`/`WagoAnalogOutputs` as
two distinct top-level namespace names, plus three (of the 16 available)
`WagoSensor` channels (`T9`/`T10`/`T11`) hand-built inside the unrelated
`SampleHeaterJet` assembly. Those stay exactly as they are;
`WagoController` is a complete, additional view of the same physical unit
(including the 13 thermosensor channels nothing else exposes yet), living in
the separate `controllers` namespace branch.
"""

from eco.devices_general.env_sensors import WagoSensor
from eco.devices_general.wago import WagoAnalogInputs, WagoAnalogOutputs
from eco.elements.assembly import Assembly
from eco.epics_utils.ioc_mixin import IOCMixin


class WagoController(Assembly, IOCMixin):
    def __init__(self, prefix, name=None, ioc_name=None, n_temp_channels=16):
        super().__init__(name=name)
        self.prefix = prefix
        if ioc_name:
            self._ioc_name = ioc_name
        self._append(WagoAnalogInputs, prefix, name="analog_inputs")
        self._append(WagoAnalogOutputs, prefix, name="analog_outputs")
        for i in range(1, n_temp_channels + 1):
            self._append(WagoSensor, f"{prefix}:TEMP-T{i}", name=f"temp_{i}")
