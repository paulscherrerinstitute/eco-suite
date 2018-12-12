from epics import PV
from ..aliases import Alias
from ..utilities.lazy_proxy import Proxy

#EVR output mapping
evr_mapping = {
        0: "Pulser 0",
        1: "Pulser 1",
        2: "Pulser 2",
        3: "Pulser 3",
        4: "Pulser 4",
        5: "Pulser 5",
        6: "Pulser 6",
        7: "Pulser 7",
        8: "Pulser 8",
        9: "Pulser 9",
        10: "Pulser 10",
        11: "Pulser 11",
        12: "Pulser 12",
        13: "Pulser 13",
        14: "Pulser 14",
        15: "Pulser 15",
        16: "Pulser 16",
        17: "Pulser 17",
        18: "Pulser 18",
        19: "Pulser 19",
        20: "Pulser 20",
        21: "Pulser 21",
        22: "Pulser 22",
        23: "Pulser 23",
        32: "Distributed bus bit 0",
        33: "Distributed bus bit 1",
        34: "Distributed bus bit 2",
        35: "Distributed bus bit 3",
        36: "Distributed bus bit 4",
        37: "Distributed bus bit 5",
        38: "Distributed bus bit 6",
        39: "Distributed bus bit 7",
        40: "Prescaler 0",
        41: "Prescaler 1",
        42: "Prescaler 2",
        62: "Logic High",
        63: "Logic low"
        }


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
    







