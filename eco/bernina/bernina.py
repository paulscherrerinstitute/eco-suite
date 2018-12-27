from ..utilities.config import initFromConfigList
from epics import PV
from .. import ecocnf
from ..aliases import NamespaceCollection
import logging

from .config import components, config
_namespace = globals()
_scope_name = 'bernina'

alias_namespaces = NamespaceCollection()


for key, value in initFromConfigList(components, lazy=ecocnf.startup_lazy).items():
    _namespace[key] = value

    if not ecocnf.startup_lazy:
        try:
            for ta in value.alias.get_all():
                alias_namespaces.bernina.update(
                    ta["alias"], ta["channel"], ta["channeltype"]
                )
        except:
            pass
    alias_namespaces.bernina.store()


def initDevice(name):
    if name=='all':
        logging.info(f'initializing all components from {_scope_name}.')
        name = list(components.keys())
    if not name in components.keys():
        raise KeyError(f'Could not find {name} in configuration!')
    if type(name) is list:
        for tname in name:
            initDevice(tname)
    else:
        initFromConfigList
    






