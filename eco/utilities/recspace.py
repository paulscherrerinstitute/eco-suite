from diffcalc.hkl.calc import HklCalculation
from diffcalc.hkl.constraints import Constraints
from diffcalc.hkl.geometry import Position
from diffcalc.ub import calc as dccalc
# from diffcalc.ub import calc calc import UBCalculation, Crystal
from eco.elements import Assembly
from eco.elements.adjustable import AdjustableMemory

class CrystalNew(Assembly):
    def __init__(self,*args,name=None,**kwargs):
        Assembly.__init__(self,name=name)
        nam = 'a1'
        self._ucpars = ['a','b','c','alpha','beta','gamma']:
        for name in self._ucpars:
            self._append(AdjustableMemory,0,name=f'{name}')

    def set_unit_cell(self,a=None,b=None,c=None,alpha=None,beta=None,gamma=None):
        self.a(a)
        self.b(b)
        self.c(c)
        self.alpha(alpha)
        self.beta(beta)
        self.gamma(gamma)


    def _calc_cryst(self):
        self.crystal = dccalc.Crystal(self.name,{par:self.__dict__[par].get_current_value() for par in self._ucpars})






class DiffGeometryYou(Assembly):
    def __init__(self,diffractometer_you=None, name=None):
        Assembly.__init__(name=name)
        # self._append(diffractometer_you,call_obj=False, name='diffractometer')
        self._append(AdjustableMemory,{},name='contraints')
        self._append(AdjustableMemory,{},name='unit_cell')
        self._append(AdjustableMemory,{},name='U_matrix')
        self._append(AdjustableMemory,{},name='UB_matrix')

    def set_unit_cell(self,name_crystal,a=None,b=None,c=None,alpha=None,beta=None,gamma=None):
        self.ubcalc = dccalc.UBCalculation('you')
        self.ubcalc.set_lattice(name_crystal,a=a,b=b,c=c,alpha=alpha,beta=beta,gamma=gamma)

    def add_reflection(self,*args,**kwargs):
        self.ubcalc.add_reflection(*args,**kwargs)
    def add_orientation(self,*args,**kwargs):
        self.ubcalc.add_orientation(*args,**kwargs)
    def calc_ub(self,*args,**kwargs):
        self.ubcalc.calc_ub(*args,**kwargs)
    def fit_ub(self,*args,**kwargs):
        self.ubcalc.fit_ub(*args,**kwargs)

    pass
    # def __init__(sel):
