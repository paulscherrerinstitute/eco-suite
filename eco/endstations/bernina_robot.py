from eco import Assembly
from eco.detector.jungfrau import Jungfrau

class DetectorRobot(Assembly):
    def __init__(self,JF_detector_id=None, JF_detector_name='det_diff', name='robot', pgroup_adj=None):
        super().__init__(name=name)
        self._append(Jungfrau,JF_detector_id,pgroup_adj=pgroup_adj, name=JF_detector_name)

        