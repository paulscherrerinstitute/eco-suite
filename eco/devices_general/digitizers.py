from ..detector.detectors_psi import DetectorBsStream
from eco.elements.assembly import Assembly
from eco.epics.detector import DetectorPvDataStream
from eco.epics.adjustable import AdjustablePv, AdjustablePvEnum


class DigitizerKeysightBoxcarChannel(Assembly):
    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        self._append(DetectorBsStream, self.pvbase + "_VAL_GET", name="value")
        self._append(
            DetectorPvDataStream,
            self.pvbase + "_BOXCAR.VALF",
            name="waveform_slow",
            is_display=False,
            is_status=True,
        )

        self.status_collection.append(self.waveform_slow, force=True)
        self._append(
            DetectorPvDataStream,
            self.pvbase + "_BOXCAR.VALH",
            name="background_average",
        )
        self._append(
            DetectorPvDataStream,
            self.pvbase + "_BOXCAR.VALI",
            name="signal_average",
        )
        self._append(
            DetectorPvDataStream,
            self.pvbase + "_BOXCAR.VALG",
            name="difference_average",
        )
        self._append(
            DetectorPvDataStream,
            self.pvbase + "_BOXCAR.VALP",
            name="background_integral",
        )
        self._append(
            DetectorPvDataStream, self.pvbase + "_BOXCAR.VALO", name="signal_integral"
        )
        self._append(
            DetectorPvDataStream,
            self.pvbase + "_BOXCAR.VALQ",
            name="difference_integral",
        )
        self._append(
            AdjustablePv,
            self.pvbase + "_BSTART",
            name="bgregion_start",
            is_setting="auto",
        )
        self._append(
            AdjustablePv, self.pvbase + "_BEND", name="bgregion_end", is_setting="auto"
        )
        self._append(
            AdjustablePv,
            self.pvbase + "_START",
            name="sigregion_start",
            is_setting="auto",
        )
        self._append(
            AdjustablePv, self.pvbase + "_END", name="sigregion_end", is_setting="auto"
        )
        self._append(
            AdjustablePv, self.pvbase + "_LEVEL", name="cross_level", is_setting="auto"
        )
        self._append(
            AdjustablePv,
            self.pvbase + "_CALIB",
            name="calibration_gain",
            is_setting="auto",
        )
        self._append(
            AdjustablePv,
            self.pvbase + "_CALIB_OFFS",
            name="calibration_offset",
            is_setting="auto",
        )
        self._append(
            AdjustablePvEnum,
            self.pvbase + "_WHICH_CHAN",
            name="output_mode",
            is_setting="auto",
        )

    def get_current_value(self):
        return self.value.get_current_value()


class DigitizerKeysight(Assembly):
    def __init__(self, pvbase="SARES20-LSCP9-FNS", name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        for chno in range(2):
            chno = chno + 1
            self._append(
                DigitizerKeysightBoxcarChannel,
                f"{self.pvbase}:PR1_CH{chno}",
                name=f"channel_{chno:d}",
                is_setting=True,
            )
        self._append(
            AdjustablePv,
            self.pvbase + ":A_SCANRATERB",
            name="sample_rate",
            is_setting="auto",
        )
        self._append(
            AdjustablePv,
            self.pvbase + ":A_TRIGGERDELAYNS",
            pvreadbackname=self.pvbase + ":A_TRIGGERDELAYRB",
            name="trigger_delay",
            is_setting="auto",
        )


class DigitizerIoxos(Assembly):
    def __init__(self, pvbase="SARES20-LSCP9-FNS", name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        for chno in range(8):
            self._append(
                DigitizerIoxosBoxcarChannel,
                f"{self.pvbase}:CH{chno}",
                name=f"channel_{chno:d}",
                is_setting=True,
            )


class DigitizerIoxosBoxcarChannel(Assembly):
    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        self._append(DetectorBsStream, self.pvbase + ":VAL_GET", name="value")
        self._append(
            AdjustablePv, self.pvbase + ":VAL_GET.EGU", name="unit", is_setting="auto"
        )
        self._append(
            DetectorPvDataStream,
            self.pvbase + ":WFM",
            name="waveform_slow",
            is_display=False,
            is_status=True,
        )
        self.status_collection.append(self.waveform_slow, force=True)
        self._append(
            DetectorPvDataStream,
            self.pvbase + ":BOXCAR.VALH",
            name="background",
        )
        self._append(
            DetectorPvDataStream,
            self.pvbase + ":BOXCAR.VALI",
            name="signal",
        )
        self._append(
            DetectorPvDataStream,
            self.pvbase + ":BOXCAR.VALG",
            name="difference",
        )
        self._append(
            DetectorPvDataStream, self.pvbase + ":BOXCAR.VALO", name="signal_integral"
        )
        self._append(
            DetectorPvDataStream, self.pvbase + ":BOXCAR.VALE", name="signal_average"
        )
        self._append(
            DetectorPvDataStream,
            self.pvbase + ":BOXCAR.VALP",
            name="background_integral",
        )
        self._append(
            DetectorPvDataStream,
            self.pvbase + ":BOXCAR.VALD",
            name="background_average",
            is_setting="auto",
        )
        self._append(
            AdjustablePv,
            self.pvbase + ":BSTART",
            name="bgregion_start",
            is_setting="auto",
        )
        self._append(
            AdjustablePv, self.pvbase + ":BEND", name="bgregion_end", is_setting="auto"
        )
        self._append(
            AdjustablePv,
            self.pvbase + ":START",
            name="sigregion_start",
            is_setting="auto",
        )
        self._append(
            AdjustablePv, self.pvbase + ":END", name="sigregion_end", is_setting="auto"
        )
        self._append(
            AdjustablePv,
            self.pvbase + ":CALIB",
            name="calibration_gain",
            is_setting="auto",
        )
        self._append(
            AdjustablePv,
            self.pvbase + ":CALIB_OFFS",
            name="calibration_offset",
            is_setting="auto",
        )
        self._append(
            AdjustablePvEnum,
            self.pvbase + ":BOXCAR.SCAN",
            name="scan_mode",
            is_setting="auto",
        )
        self._append(
            AdjustablePvEnum,
            self.pvbase + ":WHICH_CHAN",
            name="output_mode",
            is_setting="auto",
        )

    def get_current_value(self):
        return self.value.get_current_value()
