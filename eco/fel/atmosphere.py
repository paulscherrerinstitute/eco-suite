import logging

from ..epics_utils.detector import DetectorGls, DetectorPvDataStream
from ..epics_utils.gls import guess_to_gls_channels

from ..elements.assembly import Assembly

_logger = logging.getLogger(__name__)


class ControlRoomEnv(Assembly):
    """Bernina control room temperature/humidity. Not wired to real channels
    yet: there is no known EPICS PV for either, and neither GLS candidate id
    suggested so far -- "OSFA-IKLTK-1701-EB05301" (humidity) /
    "OSFA-EH.050a-Z574.01" (location code) -- resolves to anything, live or
    in datahub's sf-archiver search (checked 2026-11-08): "IKLTK-1701" isn't
    a bridged room at all (confirmed bridged IKLTK rooms are 0201/0501/0601
    only -- see `eco.epics_utils.gls.list_gls_channels()` for the current,
    authoritative list, and `find_gls_channels(building="IKLTK")` to browse
    just those). `temperature`/`humidity` are only appended once a real
    channel id is confirmed and put in the candidates below.
    """

    TEMPERATURE_CANDIDATES = ("OSFA-IKLTK-1701-EB05301-M01-A",)
    HUMIDITY_CANDIDATES = ("OSFA-IKLTK-1701-EB05301-M02-A",)

    def __init__(self, name=None):
        super().__init__(name=name)
        self._append_resolved("temperature", self.TEMPERATURE_CANDIDATES)
        self._append_resolved("humidity", self.HUMIDITY_CANDIDATES)

    def _append_resolved(self, name, candidates):
        for guess in candidates:
            matches = guess_to_gls_channels(guess)
            if matches:
                self._append(DetectorGls, matches[0].channel, name=name)
                return
        _logger.warning(
            "ControlRoomEnv: no GLS channel resolved for %r (tried %s) -- "
            "see eco.epics_utils.gls.find_gls_channels/list_gls_channels to "
            "look up the correct building/room/device and update "
            "ControlRoomEnv's candidates",
            name,
            candidates,
        )


class BerninaEnv(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        # These were never real D_OSFA PV names: confirmed 2026-11-08 that
        # none of IKLTK building 2401, device EB06501 (control room), or
        # IKLUM-8701 device EB01921 (hutch humidity/door) resolve, either live
        # (`caget` -> "not found") or in datahub's sf-archiver channel search
        # -- there is no PSI OSFA building-climate channel under those ids.
        # Left disabled until the correct ids are known.
        # self._append(DetectorPvDataStream, 'D_OSFA_IKLTK_2401_EB06501_M01_A', name='control_room_temperature')
        # self._append(DetectorPvDataStream, 'D_OSFA_IKLTK_2401_EB06501_M02_A', name='control_room_humidity')
        # self._append(DetectorPvDataStream, 'D_OSFA_IKLUM_8701_EB01921_M01_A', name='hutch_humdity_door')

        self._append(ControlRoomEnv, name="env_controlroom")

        # Bernina hutch AC temperature: this one *does* exist, just not as an
        # EPICS PV -- it's a PSI OSFA building-climate channel, archived only,
        # unreachable by `caget`. History depth matches whatever sf-archiver's
        # retention currently covers (same as any EPICS-archived channel
        # there, ~2024-06 onward as of 2026-11-08 -- not itself limited);
        # sampling is coarser than a comparable EPICS channel (~110s median
        # here vs ~30s for e.g. las_sens1_temperature below). No known EPICS
        # PV exists for it, so this is GLS-only in practice, but built via
        # DetectorGls (pvname=None) so an EPICS twin can be added later
        # without changing the channel's name or history API. See
        # eco.epics_utils.detector.DetectorGls / eco.epics_utils.gls.
        self._append(
            DetectorGls,
            "IKLUM-8701-EB01904:M01_A_D-OSFA",
            name="hutch_temperature_ac",
        )
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
