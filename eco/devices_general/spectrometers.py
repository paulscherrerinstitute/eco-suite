

from eco.elements.assembly import Assembly
from eco.epics.adjustable import AdjustablePv, AdjustablePvEnum
from eco.epics.detector import DetectorPvData


class SpectrometerAndor(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname=pvname
        self._append(DetectorPvData, pvname + ":SERIAL_NUM", unit='', name="sno_spectrometer")
        self._append(
            AdjustablePv, pvname + ":SEND_WL", pvreadbackname= pvname + ":WAVELENGTH", name="wavelength", is_setting=True, unit='nm'
        )
        self._append(
            AdjustablePv, pvname + ":SEND_SW", pvreadbackname= pvname + ":SLIT_WIDTH", name="slit_width", is_setting=True
        )
        
        self._append(DetectorPvData, pvname + ":MIN_WL", unit='nm', name="min_wavelength")
        self._append(DetectorPvData, pvname + ":MAX_WL", unit='nm', name="max_wavelength")
        self._append(DetectorPvData, pvname + ":FOCAL_POS", unit='mm', name="focal_position")
        self._append(DetectorPvData, pvname + ":TURRET", unit='', name="turret_number")
        self._append(DetectorPvData, pvname + ":NUM_GRATINGS", unit='', name="noof_installed_gratings")
        self._append(DetectorPvData, pvname + ":GRATING_NUM", unit='', name="active_grating")
        self._append(DetectorPvData, pvname + ":LPMM", unit='lns/mm', name="line_density")
                

