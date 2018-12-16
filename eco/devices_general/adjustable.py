import subprocess
from threading import Thread
from epics import PV
from .utilities import Changer


def _keywordChecker(kw_key_list_tups):
    for tkw, tkey, tlist in kw_key_list_tups:
        assert tkey in tlist, "Keyword %s should be one of %s" % (tkw, tlist)


class ValueRdback:
    def __init__(self, pv_value, pv_readback, name=None, elog=None):
        self.Id = pv_value
        self._PV_value = PV(pv_value)
        self._PV_readback = PV(pv_readback)
        self._elog = elog
        self.name = name
        self._currentChange = None

    # Conventional methods and properties for all Adjustable objects
    def changeTo(self, value, hold=False, check=True):
        """ Adjustable convention"""

        def changer(value):
            self._status = self._motor.move(value, ignore_limits=(not check), wait=True)
            self._status_message = _status_messages[self._status]
            if not self._status == 0:
                print(self._status_message)

        #        changer = lambda value: self._motor.move(\
        #                value, ignore_limits=(not check),
        #                wait=True)
        return Changer(
            target=value,
            parent=self,
            changer=changer,
            hold=hold,
            stopper=self._motor.stop,
        )

    def stop(self):
        """ Adjustable convention"""
        try:
            self._currentChange.stop()
        except:
            self._motor.stop()
        pass

    def get_current_value(self):
        """ Adjustable convention"""
        return self._PV_readback.get()

    def set_current_value(self, value):
        """ Adjustable convention"""
        print("not implemented: depends on a defined offset")

    def get_precision(self):
        """ Adjustable convention"""
        if isinstance(self._precision, PV):
            return self._precision.get()
        else:
            return self._precision

    def set_precision(self, value):
        """ Adjustable convention"""
        if isinstance(self._precision, PV):
            self._precision.put(value)
        else:
            self._precision = value

    precision = property(get_precision, set_precision)

    def set_speed(self):
        """ Adjustable convention"""
        pass

    def get_speed(self):
        """ Adjustable convention"""
        pass

    def set_speedMax(self):
        """ Adjustable convention"""
        pass

    def get_moveDone(self):
        """ Adjustable convention"""
        """ 0: moving 1: move done"""
        return PV(str(self.Id + ".DMOV")).value

    def set_limits(self, values, posType="user", relative_to_present=False):
        """ Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        ll_name, hl_name = "LLM", "HLM"
        if posType is "dial":
            ll_name, hl_name = "DLLM", "DHLM"
        if relative_to_present:
            v = self.get_current_value(posType=posType)
            values = [v - values[0], v - values[1]]
        self._motor.put(ll_name, values[0])
        self._motor.put(hl_name, values[1])

    def get_limits(self, posType="user"):
        """ Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        ll_name, hl_name = "LLM", "HLM"
        if posType is "dial":
            ll_name, hl_name = "DLLM", "DHLM"
        return self._motor.get(ll_name), self._motor.get(hl_name)

    # return string with motor value as variable representation
    def __str__(self):
        return "Motor is at %s" % self.wm()

    def __repr__(self):
        return self.__str__()

    def __call__(self, value):
        self._currentChange = self.changeTo(value)


class ChangerOld:
    def __init__(self, target=None, parent=None, mover=None, hold=True, stopper=None):
        self.target = target
        self._mover = mover
        self._stopper = stopper
        self._thread = Thread(target=self._mover, args=(target,))
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
            if self._isAlive:
                return "changing"
            else:
                return "done"

    def stop(self):
        self._stopper()
