import subprocess
from threading import Thread
from epics import PV
from .utilities import Changer
from ..aliases import Alias
from enum import IntEnum, auto




# wrappers for adjustables >>>>>>>>>>>
def default_representation(Obj):
    def get_name(Obj):
        if Obj.name:
            return Obj.name
        else:
            return Obj.Id

    def get_repr(Obj):
        return f"{Obj._get_name()} is at: {repr(Obj.get_current_value())}"
        if Obj.name:
            return Obj.name
        else:
            return Obj.Id

    Obj._get_name = get_name
    Obj.__repr__  = get_repr
    return Obj
# wrappers for adjustables <<<<<<<<<<<


def _keywordChecker(kw_key_list_tups):
    for tkw, tkey, tlist in kw_key_list_tups:
        assert tkey in tlist, "Keyword %s should be one of %s" % (tkw, tlist)


class PvRecord:
    def __init__(
        self,
        pvsetname,
        pvreadbackname = None,
        accuracy = None,
        name=None,
        elog=None,
    ):

#        alias_fields={"setpv": pvsetname, "readback": pvreadbackname},
#    ):
        self.Id = pvsetname
        self.name = name
        self.alias = Alias(name)
#        for an, af in alias_fields.items():
#            self.alias.append(
#                Alias(an, channel=".".join([pvname, af]), channeltype="CA")
#            )

        self._pv = PV(self.Id) 
        self._currentChange = None
        self.accuracy = accuracy

        if pvreadbackname is None:
            self._pvreadback = PV(self.Id)
        else:
            self._pvreadback = PV(pvreadbackname)


    def get_current_value(self, readback=True,): 
        if readback:
            currval = self._pvreadback.get()
        if not readback:
            currval = self._pv.get()
        return currval
 

    def get_moveDone(self):
        """ Adjustable convention"""
        """ 0: moving 1: move done"""
        movedone = 1 
        if self.accuracy is not None:
            if( np.abs(self.get_current_value(readback=False)-self.get_current_value(readback=True)) > self.accuracy ): 
                movedone = 0
        return movedone


    def move(self,value): 
        self._pv.put(value) 
        time.sleep(0.1)
        while( self.get_moveDone() == 0 ): 
                time.sleep(0.1)


    def changeTo(self, value, hold=False):
        """ Adjustable convention"""

        changer = lambda value: self.move(\
                value)
        return Changer(
                target=value,
                parent=self,
                changer=changer,
                hold=hold,
                stopper=None)


    # spec-inspired convenience methods
    def mv(self,value):
        self._currentChange = self.changeTo(value)
    def wm(self,*args,**kwargs):
        return self.get_current_value(*args,**kwargs)
    def mvr(self,value,*args,**kwargs):

        if(self.get_moveDone == 1):
            startvalue = self.get_current_value(readback=True,*args,**kwargs)
        else:
            startvalue = self.get_current_value(readback=False,*args,**kwargs)
        self._currentChange = self.changeTo(value+startvalue,*args,**kwargs)
    def wait(self):
        self._currentChange.wait()

    def __repr__(self):
        return "%s is at: %s"%(self.Id,self.get_current_value())



@default_representation
class PvEnum:
    def __init__(self,pvname,name=None):
        self.Id = pvname
        self._pv = PV(pvname)
        self.name = name
        self.enum_strs = self._pv.enum_strs
        if name:
            enumname = self.name
        else:
            enumname = self.Id
        self.PvEnum = IntEnum(enumname,{tstr:n for n,tstr in enumerate(self.enum_strs)})
    
    def validate(self,value):
        if type(value) is str:
            return self.PvEnum.__members__[value]
        else:
            return self.PvEnum(value)
    
    def get_current_value(self):
        return self.validate(self._pv.get())

    def changeTo(self, value, hold=False):
        """ Adjustable convention"""
        value = self.validate(value)

        changer = lambda value: self._pv.put(value,wait=True)
        return Changer(
                target=value,
                parent=self,
                changer=changer,
                hold=hold,
                stopper=None)




        





