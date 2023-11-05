from epics import PV
import numpy as np
from ..elements.adjustable import AdjustableFS
from ..epics.adjustable import AdjustablePv
from ..epics.detector import DetectorPvDataStream
from ..detector.detectors_psi import DetectorBsStream

from ..elements.assembly import Assembly


class CheckerCA(Assembly):
    def __init__(
        self,
        pvname=None,
        thresholds=None,
        required_fraction=None,
        filepath_thresholds="/photonics/home/gac-bernina/eco/configuration/checker_thresholds",
        filepath_fraction="/photonics/home/gac-bernina/eco/configuration/checker_required_fraction",
        name=None,
    ):
        super().__init__(name=name)
        self._append(DetectorPvDataStream, pvname, name="monitor")
        self._append(
            AdjustableFS,
            filepath_thresholds,
            default_value=sorted(thresholds),
            name="thresholds",
        )
        self._append(
            AdjustableFS,
            filepath_fraction,
            default_value=required_fraction,
            name="required_fraction",
        )

    def check_now(self):
        cv = self.monitor.get_current_value()
        thresholds = self.thresholds()
        if cv > thresholds[0] and cv < thresholds[1]:
            return True
        else:
            return False

    # def append_to_data(self, **kwargs):
    #     self.data.append(kwargs["value"])

    def clear_and_start_counting(self):
        self.monitor.accumulate_start()

    # def stopcounting(self):
    #     self.PV.clear_callbacks()

    def stop_and_analyze(self):
        data = np.asarray(self.monitor.accumulate_stop())
        thresholds = self.thresholds()
        good = np.logical_and(data > thresholds[0], data < thresholds[1])
        fraction = good.sum() / len(good)
        isgood = fraction >= self.required_fraction()
        if not isgood:
            print(f"Checker: {fraction*100}% inside limits {self.thresholds()},")
            print(f"         given limit was {self.required_fraction()*100}%.")
        return fraction >= self.required_fraction()


class CheckerBS(Assembly):
    def __init__(
        self,
        bs_channel=None,
        thresholds=None,
        required_fraction=None,
        filepath_thresholds="/photonics/home/gac-bernina/eco/configuration/checker_thresholds",
        filepath_fraction="/photonics/home/gac-bernina/eco/configuration/checker_required_fraction",
        name=None,
    ):
        super().__init__(name=name)
        self._append(DetectorBsStream, bs_channel, name="monitor")
        self._append(
            AdjustableFS,
            filepath_thresholds,
            default_value=sorted(thresholds),
            name="thresholds",
        )
        self._append(
            AdjustableFS,
            filepath_fraction,
            default_value=required_fraction,
            name="required_fraction",
        )

    def check_now(self):
        cv = self.monitor.get_current_value()
        thresholds = self.thresholds()
        if cv > thresholds[0] and cv < thresholds[1]:
            return True
        else:
            return False

    # def append_to_data(self, **kwargs):
    #     self.data.append(kwargs["value"])

    def clear_and_start_counting(self):
        self.monitor.accumulate_start()

    # def stopcounting(self):
    #     self.PV.clear_callbacks()

    def stop_and_analyze(self):
        data = np.asarray(self.monitor.accumulate_stop())
        thresholds = self.thresholds()
        good = np.logical_and(data > thresholds[0], data < thresholds[1])
        fraction = good.sum() / len(good)
        isgood = fraction >= self.required_fraction()
        if not isgood:
            print(f"Checker: {fraction*100}% inside limits {self.thresholds()},")
            print(f"         given limit was {self.required_fraction()*100}%.")
        return fraction >= self.required_fraction()


# checker_obj = Checker_obj(checkerPV)


# checker_ready = {}
# checker_ready["checker_call"] = checker_function
# checker_ready["args"] = [[60, 700]]
# checker_ready["kwargs"] = {}
# checker_ready["wait_time"] = 3

# checker_init = {}
# checker_init["checker_call"] = checker_obj.clear_and_start_counting
# checker_init["args"] = []
# checker_init["kwargs"] = {}
# checker_init["wait_time"] = None

# checker_end = {}
# checker_end["checker_call"] = checker_obj.stop_and_analyze
# checker_end["args"] = [[60, 700], .7]
# checker_end["kwargs"] = {}
# checker_end["wait_time"] = None
