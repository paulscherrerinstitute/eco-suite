# init()
import sys
from .config import config


    # {
        # "name": "elog",
        # "type": "eco.utilities.elog:Elog",
        # "args": ["https://elog-gfa.psi.ch/Bernina"],
        # "kwargs": {
            # "screenshot_directory": "/tmp",
        # },
    # },


@init_obj(lazy=is_globally_lazy)
def init_specific_device(*args,**kwargs):
    from whatever import something
    return something(*args,**kwargs)
specific_name = init_specific_device(name=specific_name)




