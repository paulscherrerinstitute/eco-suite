from ..utilities.config import initFromConfigList
from epics import PV
from .config import components
from .. import ecocnf

itglobals = globals()

for key, value in initFromConfigList(components, lazy=ecocnf.startup_lazy).items():
    globals()[key] = value
