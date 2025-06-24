from ..utilities import PropagatingThread
from epics import PV
from asyncio import Future


class Acquisition:
    def __init__(
        self,
        parent=None,
        acquire=lambda: None,
        acquisition_kwargs={},
        hold=True,
        stopper=None,
        get_result=lambda: None,
    ):
        self.acquisition_kwargs = acquisition_kwargs
        for key, val in acquisition_kwargs.items():
            self.__dict__[key] = val
        self._stopper = stopper
        self._get_result = get_result
        if acquire:
            self.set_acquire_foo(acquire, hold=hold)

    def set_acquire_foo(self, acquire, hold=True):
        self._acquire = acquire
        self._thread = PropagatingThread(target=self._acquire)
        if not hold:
            self._thread.start()

    def wait(self):
        self._thread.join()
        return self._get_result()

    def start(self):
        self._thread.start()

    def status(self):
        if self._thread.ident is None:
            return "waiting"
        else:
            if self._thread.is_alive():
                return "acquiring"
            else:
                return "done"

    def stop(self):
        self._stopper()


def getPVchecker(pvname, config_file=None):
    checkerPV = PV(pvname)

    def checker_function(limits):
        cv = checkerPV.get()
        if cv > limits[0] and cv < limits[1]:
            return True
        else:
            return False

    checker = {}
    checker["checker_call"] = checker_function
    checker["args"] = [[100, 700]]
    checker["kwargs"] = {}
    checker["wait_time"] = 3
    return checker


def checker_function(limits):
    cv = checkerPV.get()
    if cv > limits[0] and cv < limits[1]:
        return True
    else:
        print(f"Gas detector intensity {cv} outside limits {limits} !")
        return False


class Checker_obj:
    def __init__(self, PV):
        self.PV = PV
        self.data = []

    def append_to_data(self, **kwargs):
        self.data.append(kwargs["value"])

    def clear_and_start_counting(self):
        self.data = []
        self.PV.add_callback(self.append_to_data)

    def stopcounting(self):
        self.PV.clear_callbacks()

    def stop_and_analyze(self, limits, fraction_min):
        self.stopcounting()
        data = np.asarray(self.data)
        good = np.logical_and(data > np.min(limits), data < np.max(limits))
        fraction = good.sum() / len(good)
        print(f"Gas detector intensity was {fraction*100}% inside limits {limits},")
        print(f"given limit was {fraction_min*100}%.")

        return fraction >= fraction_min
