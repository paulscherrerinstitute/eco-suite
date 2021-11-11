from ..epics.detector import DetectorPvDataStream

from ..elements.assembly import Assembly


class BerninaEnv(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        # self._append(DetectorPvDataStream, 'D_OSFA_IKLTK_2401_EB06501_M01_A', name='control_room_temperature')
        # self._append(DetectorPvDataStream, 'D_OSFA_IKLTK_2401_EB06501_M02_A', name='control_room_humidity')
        # self._append(DetectorPvDataStream, 'D_OSFA_IKLUM_8701_EB01904_M01_A', name='hutch_temperature_ac')
        # self._append(DetectorPvDataStream, 'D_OSFA_IKLUM_8701_EB01921_M01_A', name='hutch_humdity_door')
        self._append(
            DetectorPvDataStream,
            "SLAAR21-LI2C01_CH1:TEMP",
            name="las_sens1_temperature",
        )
        self._append(
            DetectorPvDataStream,
            "SLAAR21-LI2C01_CH1:HUMIREL",
            name="las_sens1_humidity",
        )
        self._append(
            DetectorPvDataStream, "SLAAR21-LI2C01_CH1:PRES", name="las_sens1_pressure"
        )
        self._append(
            DetectorPvDataStream,
            "SLAAR21-LI2C01_CH2:TEMP",
            name="las_sens2_temperature",
        )
        self._append(
            DetectorPvDataStream,
            "SLAAR21-LI2C01_CH2:HUMIREL",
            name="las_sens2_humidity",
        )
        self._append(
            DetectorPvDataStream, "SLAAR21-LI2C01_CH2:PRES", name="las_sens2_pressure"
        )
        self._append(
            DetectorPvDataStream,
            "SLAAR02-LI2C02_CH1:TEMP",
            name="lhx_sens7_temperature",
        )
        self._append(
            DetectorPvDataStream,
            "SLAAR02-LI2C02_CH1:HUMIREL",
            name="lhx_sens7_humidity",
        )
        self._append(
            DetectorPvDataStream, "SLAAR02-LI2C02_CH1:PRES", name="lhx_sens7_pressure"
        )
        self._append(
            DetectorPvDataStream,
            "SLAAR02-LI2C02_CH2:TEMP",
            name="lhx_sens5_temperature",
        )
        self._append(
            DetectorPvDataStream,
            "SLAAR02-LI2C02_CH2:HUMIREL",
            name="lhx_sens5_humidity",
        )
        self._append(
            DetectorPvDataStream, "SLAAR02-LI2C02_CH2:PRES", name="lhx_sens5_pressure"
        )
        self._append(
            DetectorPvDataStream,
            "SLAAR02-LI2C02_CH3:TEMP",
            name="lhx_sens9_temperature",
        )
        self._append(
            DetectorPvDataStream,
            "SLAAR02-LI2C02_CH3:HUMIREL",
            name="lhx_sens9_humidity",
        )
        self._append(
            DetectorPvDataStream, "SLAAR02-LI2C02_CH3:PRES", name="lhx_sens9_pressure"
        )
