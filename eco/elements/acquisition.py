


# single acquisition class
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
