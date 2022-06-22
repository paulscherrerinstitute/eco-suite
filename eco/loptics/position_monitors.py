from eco.epics.detector import DetectorPvDataStream
from eco.epics.adjustable import AdjustablePv
from eco import Assembly

class CameraPositionMonitor(Assembly):
    def __init__(self,pvname,name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(DetectorPvDataStream,pvname+':FIT-XPOS',name='xpos_raw')
        self._append(DetectorPvDataStream,pvname+':FIT-YPOS',name='ypos_raw')
        self._append(DetectorPvDataStream,pvname+':FIT-XWID',name='xwidth_raw')
        self._append(DetectorPvDataStream,pvname+':FIT-YWID',name='ywidth_raw')
        self._append(DetectorPvDataStream,pvname+':FIT-XCOM',name='xcom_raw')
        self._append(DetectorPvDataStream,pvname+':FIT-YCOM',name='ycom_raw')
        self._append(DetectorPvDataStream,pvname+':FIT-XPOS_EGU',name='xpos')
        self._append(DetectorPvDataStream,pvname+':FIT-YPOS_EGU',name='ypos')
        self._append(DetectorPvDataStream,pvname+':FIT-XWID_EGU',name='xwidth')
        self._append(DetectorPvDataStream,pvname+':FIT-YWID_EGU',name='ywidth')
        self._append(AdjustablePv,pvname+':XCALIB',name='xcalib_gradient', is_setting=True)
        self._append(AdjustablePv,pvname+':YCALIB',name='ycalib_gradient', is_setting=True)
        self._append(AdjustablePv,pvname+':XCALIB-OFFS',name='xcalib_offset', is_setting=True)
        self._append(AdjustablePv,pvname+':YCALIB-OFFS',name='ycalib_offset', is_setting=True)


        