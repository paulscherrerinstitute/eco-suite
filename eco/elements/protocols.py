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
class MonitorableValueUpdate(Protocol):
    def set_current_value_callback(self):
        ...


@runtime_checkable
class InitialisationWaitable(Protocol):
    def _wait_for_initialisation(self):
        ...

@runtime_checkable
class Counter(Protocol):
    def acquire(self):
        ...
    def start(self):
        ...
    def stop(self):
        ...

    def __enter__(self):
        self.start()
    
    def __exit__(self, type, value, traceback):
        self.stop()
 
        
        # file_name=fina, Npulses=self.pulses_per_step[0], acq_pars=acq_pars):
                
        

# class Callback:
#     self.__init__(self, func=None, *args, **kwargs):
#         self.func = func
#         self.args = args
#         self.kwargs = kwargs
    
#     def start(self,func=None):
#         if func is not None:
#             self.func = func
        

    
    
#     def 
    