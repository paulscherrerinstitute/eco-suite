from eco.elements.detector import DetectorGet
from .assembly import Assembly

from .adjustable import AdjustableGetSet
from functools import partial


class AdjustableObject(Assembly):
    def __init__(self, adjustable_dict, is_setting_children=False, name=None):
        super().__init__(name=name)
        self._append(adjustable_dict, name="_base_dict", is_setting=False)
        self.init_object(is_setting_children=is_setting_children)

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

    def update_base_dict(self, updatedict):
        tmp = self._base_dict.get_current_value()
        tmp.update(updatedict)
        self._base_dict.set_target_value(tmp)
        self.__init__(self._base_dict, name=self.name)

    def init_object(self, is_setting_children=False):
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
                    is_setting=is_setting_children,
                    name=ln,
                    is_display="recursive",
                )
            else:
                self._append(
                    tadj,
                    call_obj=False,
                    is_setting=is_setting_children,
                    is_display=True,
                    name=ln,
                )


class DetectorObject(Assembly):
    def __init__(self, detector_dict, name=None):
        super().__init__(name=name)
        self._base_dict = detector_dict
        self.init_object()

    def get_field(self, fieldname):
        d = self._base_dict.get_current_value()
        if fieldname not in d.keys():
            raise Exception(f"{fieldname} is not in dictionary")
        return d[fieldname]

    def init_object(self):
        # super().__init__(name=self.name)
        for k, v in self._base_dict.get_current_value().items():
            tdet = DetectorGet(partial(self.get_field, k), name=k)
            if k in self.__dict__.keys():
                ln = f"{k}_"
            else:
                ln = f"{k}"
            if type(v) is dict:

                self._append(
                    DetectorObject(tdet, name=k),
                    call_obj=False,
                    is_setting=False,
                    name=ln,
                    is_display="recursive",
                )
            else:
                self._append(
                    tdet, call_obj=False, is_setting=False, is_display=True, name=ln
                )
