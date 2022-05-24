from typing import Protocol, runtime_checkable


@runtime_checkable
class Adjustable(Protocol):
    def get_target_value(self):
        ...

    def set_target_value(self, value):
        ...

    # def set_target_value(self,value) -> Changer:...


@runtime_checkable
class Detector(Protocol):
    def get_target_value(self):
        ...
