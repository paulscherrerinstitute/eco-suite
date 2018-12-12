from epics import PV
from ..aliases import Alias
from ..utilities.lazy_proxy import Proxy


# temporary mapping of Ids to codes, be aware of changes!
eventcodes = [1,  2,  3,  4,  5,  0,  6,  7,  8,  12,  0,  11,  9,  10,  13,  14,  15,  16,  17,  18,  19,  20,  21,  22,  23,  24,  25,  26,  27,  28,  29,  30,  31,  32,  33,  34,  35,  36,  37,  38,  39,  40,  41,  42,  43,  44,  45,  46,  47,  48,  49] 

class MasterEventSystem:
    def __init__(self,pvname,name=None):
        self.name = name
        self.pvname = pvname
        self._pvs = {}yy

    def _get_pv(self,pvname):
        if not pvname in self._pvs:
            self._pvs[pvname] = PV(pvname)
        return self._pvs[pvname]

    def _get_Id_code(self,intId):
        return self._get_pv(f"{self.pvname}:Evt-{intId}-Code-SP").get()
    
    def _get_Id_freq(self,intId):
        return self._get_pv(f"{self.pvname}:Evt-{intId}-Freq-I").get()

    def _get_Id_period(self,intId):
        return self._get_pv(f"{self.pvname}:Evt-{intId}-Period-I").get()

    def _get_Id_delay(self,intId,inticks=False):
        """in seconds if not ticks"""
        if inticks:
            return self._get_pv(f"{self.pvname}:Evt-{intId}-Delay-RB.A").get()
        else:
            return self._get_pv(f"{self.pvname}:Evt-{intId}-Delay-RB").get()/1000000

    def _get_Id_description(self,intId):
        return self._get_pv(f"{self.pvname}:Evt-{intId}.DESC").get()

    def _get_evtcode_Id(self,evtcode):
        if not evtcode in eventcodes:
            raise Exception(f"Eventcode mapping not defined for {evtcode}")
        Id = eventcodes.index(evtcode)+1
        if not self._get_Id_code(Id) == evtcode:
            raise Exception(f"Eventcode mapping has apparently changed!")
        return Id

    def get_evtcode_delay(self,evtcode,**kwargs):
        Id = self._get_evtcode_Id(evtcode)
        return self._get_Id_delay(Id,**kwargs)

    def get_evtcode_description(self,evtcode):
        Id = self._get_evtcode_Id(evtcode)
        return self._get_Id_description(Id)
       
    def get_evtcode_frequency(self,evtcode):
        """ in Hz"""
        Id = self._get_evtcode_Id(evtcode)
        return self._get_Id_freq(Id)

    def get_evtcode_period(self,evtcode):
        """ in s"""
        Id = self._get_evtcode_Id(evtcode)
        return self._get_Id_period(Id)/1000


class EvrPulser:
    def __init__(self,pvname,name=None):
        self.pvname = pvname
        self.name = name
        self._pvs = {}

    def _get_pv(self,pvname):
        if not pvname in self._pvs:
            self._pvs[pvname] = PV(pvname)
        return self._pvs[pvname]
    
    def get_delay(self):
        """ in seconds """
        return self._get_pv(f"{self.pvname}-Delay-RB").get()/int(1e6)

    def set_delay(self,value):
        """ in seconds """
        return self._get_pv(f"{self.pvname}-Delay-SP").set(value*int(1e6))

    def get_width(self):
        """ in seconds """
        return self._get_pv(f"{self.pvname}-Width-RB").get()/int(1e6)

    def set_width(self,value):
        """ in seconds """
        return self._get_pv(f"{self.pvname}-Width-SP").set(value*int(1e6))

    def get_evtcode(self):
        return self._get_pv(f"{self.pvname}-Evt-Trig0-SP").get()

    def set_evtcode(self,value):
        return self._get_pv(f"{self.pvname}-Evt-Trig0-SP").set(value)

    def get_polarity(self):
        return self._get_pv(f"{self.pvname}-Polarity-Sel").get()

    def set_polarity(self,value):
        return self._get_pv(f"{self.pvname}-Polarity-Sel").set(value)

class EvrOutput:
    def __init__(self,pvname,name=None):
        self.pvname = pvname
        self.name = name



class EventReceiver:
    def __init__(self,pvname,name=None):
        self.name = name
        self.pvname = pvname
    







