class Adjustable:
    def __init__(self, f_get_value, f_change_value, f_set_value_to=None):

        self._f_get_value = f_get_value
        self._f_change_value = f_change_value
        self._f_set_value_to = f_set_value_to


def wrap_spec_convenience(obj):
    # spec-inspired convenience methods
    def mv(self, value):
        self._currentChange = self.changeTo(value)

    def wm(self, *args, **kwargs):
        return self.get_current_value(*args, **kwargs)

    def mvr(self, value, *args, **kwargs):

        if self.get_moveDone == 1:
            startvalue = self.get_current_value(readback=True, *args, **kwargs)
        else:
            startvalue = self.get_current_value(readback=False, *args, **kwargs)
        self._currentChange = self.changeTo(value + startvalue, *args, **kwargs)

    def wait(self):
        self._currentChange.wait()

    obj.wm = wm
    obj.mv = mv
    obj.mvr = mvr
    obj.wait = wait
