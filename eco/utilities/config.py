import json
import importlib
import sys
from colorama import Fore as _color
from functools import partial
from .lazy_proxy import Proxy
from ..aliases import Alias


class Component:
    def __init__(self, namestring):
        self.name = namestring


def init_name_obj(obj, args, kwargs, name=None):
    try:
        return obj(*args, **kwargs, name=name)
    except TypeError:
        return obj(*args, **kwargs)


def init_device(type_string, name, args=[], kwargs={}, verbose=True, lazy=True):
    if verbose:
        print(("Configuring %s " % (name)).ljust(25), end="")
        sys.stdout.flush()
    imp_p, type_name = type_string.split(sep=":")
    imp_p = imp_p.split(sep=".")
    if verbose:
        print(("(%s)" % (type_name)).ljust(25), end="")
        sys.stdout.flush()
    try:
        tg = importlib.import_module(".".join(imp_p)).__dict__[type_name]

        if lazy:
            tdev = Proxy(partial(init_name_obj, tg, args, kwargs, name=name))
            if verbose:
                print((_color.YELLOW + "LAZY" + _color.RESET).rjust(5))
                sys.stdout.flush()
        else:
            tdev = init_name_obj(tg, args, kwargs, name=name)
            if verbose:
                print((_color.GREEN + "OK" + _color.RESET).rjust(5))
                sys.stdout.flush()
        return tdev
    except Exception as expt:
        # tb = traceback.format_exc()
        if verbose:
            print((_color.RED + "FAILED" + _color.RESET).rjust(5))
            # print(sys.exc_info())
        raise expt


def initFromConfigList(config_list, lazy=False):
    op = {}
    for td in config_list:
        args = [op[ta.name] if isinstance(ta, Component) else ta for ta in td["args"]]
        kwargs = {
            tkwk: op[tkwv.name] if isinstance(tkwv, Component) else tkwv
            for tkwk, tkwv in td["kwargs"].items()
        }
        op[td["name"]] = init_device(td["type"], td["name"], args, kwargs, lazy=lazy)
    return op


class Exp:
    def __init__(self,module):
        pass



def loadConfig(fina):
    with open(fina, "r") as f:
        return json.load(f)


def writeConfig(fina, obj):
    with open(fina, "w") as f:
        json.dump(obj, f)


def parseChannelListFile(fina):
    out = []
    with open(fina, "r") as f:
        done = False
        while not done:
            d = f.readline()
            if not d:
                done = True
            if len(d) > 0:
                if not d.isspace():
                    if not d[0] == "#":
                        out.append(d.strip())
    return out
