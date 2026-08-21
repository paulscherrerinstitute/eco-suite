from threading import Thread
from ..utilities import PropagatingThread


def _access_gate(parent):
    """Central write-access guardrail for eco. Every adjustable write builds a
    Changer with ``parent`` set to the adjustable, so this is the single
    chokepoint that covers (almost) all writes at once - see
    ``eco.elements.access``. It is OFF by default (access.enforce is False), so
    normal usability is unchanged; it only ever raises once a beamline opts in.

    An access-layer problem must never break device motion, so anything other
    than an actual AccessDenied is swallowed.
    """
    try:
        from eco.elements.access import check_write
    except Exception:
        return
    try:
        check_write(parent)
    except PermissionError:
        raise
    except Exception:
        pass


def _recent_gate(parent):
    """Record `parent` as a recently-used component (eco.elements.recent),
    reusing the same write chokepoint as _access_gate -- so the component
    picker's "Recent" list reflects real usage (e.g. `mono.mv(5)` typed in a
    shell), not just prior picks made through the picker itself. Cheap
    (in-memory, debounced disk write) and, like _access_gate, must never be
    allowed to affect device motion.
    """
    try:
        from eco.elements.recent import touch_from_write
    except Exception:
        return
    try:
        touch_from_write(parent)
    except Exception:
        pass


class Changer:
    def __init__(self, target=None, parent=None, changer=None, hold=True, stopper=None):
        _access_gate(parent)
        _recent_gate(parent)
        self.target = target
        self._changer = changer
        self._stopper = stopper
        self._thread = PropagatingThread(target=self._changer, args=(target,))
        # self._thread = Thread(target=self._changer, args=(target,))
        if not hold:
            self._thread.start()

    def wait(self, timeout=None):
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise TimeoutError(
                f"Changer did not finish within timeout period {timeout}."
            )

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
