from typing import Protocol, runtime_checkable


@runtime_checkable
class Adjustable(Protocol):
    def get_current_value(self):
        ...

    def set_target_value(self, value):
        ...

    # def set_target_value(self,value) -> Changer:...


@runtime_checkable
class Detector(Protocol):
    def get_current_value(self):
        ...


@runtime_checkable
class ValueUpdateMonitorable(Protocol):
    def get_current_value_callback(self):
        ...


@runtime_checkable
class InitialisationWaitable(Protocol):
    def _wait_for_initialisation(self):
        ...

@runtime_checkable
class Counter:
    def acquire(self):
        ...
    def start(self):
        ...
    def stop(self):
        ...
 
        
        # file_name=fina, Npulses=self.pulses_per_step[0], acq_pars=acq_pars):
                )
        