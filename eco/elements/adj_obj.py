from .assembly import Assembly

from .adjustable import AdjustableGetSet
from functools import partial


class AdjustableObject(Assembly):
    def __init__(self, adjustable_dict, name=None):
        super().__init__(name=name)
        self._base_dict = adjustable_dict
        self.init_object()

    def set_field(self, fieldname, value):
        d = self._base_dict.get_current_value()
        if fieldname not in d.keys():
            raise Exception(f"{fieldname} is not in dictionary")
        d[fieldname] = value
        self._base_dict.set_target_value(d)

    def get_field(self, fieldname):
        d = self._base_dict.get_current_value()
        if fieldname not in d.keys():
            raise Exception(f"{fieldname} is not in dictionary")
        return d[fieldname]

    def init_object(self):
        # super().__init__(name=self.name)
        for k, v in self._base_dict.get_current_value().items():
            tadj = AdjustableGetSet(
                partial(self.get_field, k), partial(self.set_field, k), name=k
            )
            if k in self.__dict__.keys():
                ln = f"{k}_"
            else:
                ln = f"{k}"
            if type(v) is dict:

                self._append(
                    AdjustableObject(tadj, name=k),
                    call_obj=False,
                    is_setting=False,
                    name=ln,
                    is_display="recursive",
                )
            else:
                self._append(
                    tadj, call_obj=False, is_setting=False, is_display=True, name=ln
                )
