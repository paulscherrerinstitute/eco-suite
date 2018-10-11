from ..utilities.config import initFromConfigList
from epics import PV
from .config import components
from .. import ecocnf
from ..aliases import NamespaceCollection

itglobals = globals()

alias_namespaces = NamespaceCollection()


for key, value in initFromConfigList(components, lazy=ecocnf.startup_lazy).items():
    globals()[key] = value

    if not ecocnf.startup_lazy:
        try:
            for ta in value.alias.get_all():
                alias_namespaces.bernina.update(ta['alias'],ta['channel'],ta['channeltype'])
        except:
            pass
    alias_namespaces.bernina.store()

