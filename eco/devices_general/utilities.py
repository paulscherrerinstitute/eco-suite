from threading import Thread
from ..utilities import PropagatingThread


class Changer:
    def __init__(self, target=None, parent=None, changer=None, hold=True, stopper=None):
        self.target = target
        self._changer = changer
        self._stopper = stopper
        self._thread = PropagatingThread(target=self._changer, args=(target,))
        if not hold:
            self._thread.start()

    def wait(self):
        self._thread.join()

    def start(self):
        self._thread.start()

    def status(self):
        if self._thread.ident is None:
            return "waiting"
        else:
            if self._thread.is_alive():
                return "changing"
            else:
                return "done"

    def is_alive(self):
        if self._thread.ident is None:
            return True
        else:
            if self._thread.is_alive():
                return True
            else:
                return False

    def isAlive(self):
        return self.is_alive()

    def stop(self):
        self._stopper()
