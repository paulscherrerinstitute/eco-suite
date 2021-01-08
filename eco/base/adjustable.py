from abc import ABC, abstractmethod

class Adjustable(ABC):
    @abstractmethod
    def get_current_value(self):
        pass
    @abstractmethod
    def set_target_value_to(self,value):
        pass


class Changer:
    def __init__(self, target=None, parent=None, changer=None, hold=True, stopper=None):
        self.target = target
        self._changer = changer
        self._stopper = stopper
        self._thread = Thread(target=self._changer, args=(target,))
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

    def stop(self):
        self._stopper()
    