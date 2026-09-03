from .box import ManualControlBox
from .jog import Jogger
from .navigator import TreeNavigator
from .tweak_action import tweak_action


def start_box_server(*args, **kwargs):
    """Serve a namespace to the physical control box (lazy import: the remote
    stack is only needed when a box is actually used).

        from eco.manual_control import start_box_server
        box = start_box_server(bernina.namespace)
    """
    from .remote.serve import start_box_server as _start

    return _start(*args, **kwargs)
