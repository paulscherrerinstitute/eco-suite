import json
import importlib
from pathlib import Path
import sys
from colorama import Fore as _color
from functools import partial

# from .lazy_proxy import Proxy
from ..aliases import Alias
import getpass
import colorama
import socket
from importlib import import_module
from lazy_object_proxy import Proxy as Proxy_orig
from tabulate import tabulate


import traceback


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


def get_dependencies(inp):
    outp = []
    if isinstance(inp, dict):
        inp = inp.values()
    for ta in inp:
        if isinstance(ta, Component):
            outp.append(ta.name)
        elif isinstance(ta, dict) or isinstance(ta, list):
            outp.append(get_dependencies(ta))
    return outp


def replaceComponent(inp, dict_all, config_all, lazy=False):
    if isinstance(inp, list):
        outp = []
        for ta in inp:
            if isinstance(ta, Component):
                if ta.name in dict_all.keys():
                    outp.append(dict_all[ta.name])
                else:
                    ind = [ta.name == tca["name"] for tca in config_all].index(True)
                    outp.append(
                        initFromConfigList(
                            config_list[ind : ind + 1], config_all, lazy=lazy
                        )
                    )
            elif isinstance(ta, dict) or isinstance(ta, list):
                outp.append(replaceComponent(ta, dict_all, config_all, lazy=lazy))
            else:
                outp.append(ta)
    elif isinstance(inp, dict):
        outp = {}
        for tk, ta in inp.items():
            if isinstance(ta, Component):
                if ta.name in dict_all.keys():
                    outp[tk] = dict_all[ta.name]
                else:
                    ind = [tk.name == tca["name"] for tca in config_all].index(True)
                    outp[tk] = initFromConfigList(
                        config_list[ind : ind + 1], config_all, lazy=lazy
                    )
            elif isinstance(ta, dict) or isinstance(ta, list):
                outp[tk] = replaceComponent(ta, dict_all, config_all, lazy=lazy)
            else:
                outp[tk] = ta
    else:
        return inp
    return outp


def initFromConfigList(config_list, config_all, lazy=False):
    op = {}
    for td in config_list:
        # args = [op[ta.name] if isinstance(ta, Component) else ta for ta in td["args"]]
        # kwargs = {
        # tkwk: op[tkwv.name] if isinstance(tkwv, Component) else tkwv
        # for tkwk, tkwv in td["kwargs"].items()
        # }
        try:
            tlazy = td["lazy"]
        except:
            tlazy = lazy
        op[td["name"]] = init_device(
            td["type"],
            td["name"],
            replaceComponent(td["args"], op, config_all, lazy=lazy),
            replaceComponent(td["kwargs"], op, config_all, lazy=lazy),
            lazy=tlazy,
        )
    return op


class Configuration:
    """Configuration collector object collecting important settings for arbitrary use,
    linking to one or few standard config files in the file system. Sould also be used
    for config file writing."""

    def __init__(self, configFile, name=None):
        self.name = name
        self.configFile = Path(configFile)
        self._config = {}
        if self.configFile:
            self.readConfigFile()

    def readConfigFile(self):
        self._config = loadConfig(self.configFile)
        assert (
            type(self._config) is dict
        ), f"Problem reading {self.configFile} json file, seems not to be a valid dictionary structure!"
        # self.__dict__.update(self._config)

    def __setitem__(self, key, item):
        self._config[key] = item
        # self.__dict__.update(self._config)
        self.saveConfigFile()

    def __getitem__(self, key):
        return self._config[key]

    def saveConfigFile(self, filename=None, force=False):
        if not filename:
            filename = self.configFile
        if (not force) and filename.exists():
            if (
                not input(
                    f"File {filename.absolute().as_posix()} exists,\n would you like to overwrite? (y/n)"
                ).strip()
                == "y"
            ):
                return
        writeConfig(filename, self._config)

    def _ipython_key_completions_(self):
        return list(self._config.keys())

    def __repr__(self):
        return json.dumps(self._config, indent=4)


def loadConfig(fina):
    with open(fina, "r") as f:
        return json.load(f)


def writeConfig(fina, obj):
    with open(fina, "w") as f:
        json.dump(obj, f, indent=4)


class ChannelList(list):
    def __init__(self, *args, **kwargs):
        self.file_name = kwargs.pop("file_name")
        # list.__init__(*args,**kwargs)
        self.load()

    def load(self):
        self.clear()
        self.extend(parseChannelListFile(self.file_name))


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


def append_to_path(*args):
    for targ in args:
        sys.path.append(targ)


def prepend_to_path(*args):
    for targ in args:
        sys.path.insert(0, targ)


class Terminal:
    def __init__(self, title="eco", scope=None):
        self.title = title
        self.scope = scope

    @property
    def user(self):
        return getpass.getuser()

    @property
    def host(self):
        return socket.gethostname()

    @property
    def user(self):
        return getpass.getuser()

    def get_string(self):
        s = f"{self.title}"
        if self.scope:
            s += f"-{self.scope}"
        s += f" ({self.user}@{self.host})"
        return s

    def set_title(self, extension=""):
        print(colorama.ansi.set_title("♻️ " + self.get_string() + extension))


class Namespace(object):
    def __init__(self, name=None, root_module=None, alias_namespace=None):
        self.name = name
        self.lazy_items = {}
        self.initialized_items = {}
        self.root_module = root_module
        self.alias_namespace = alias_namespace

    @property
    def initialized_names(self):
        return set(self.initialized_items.keys())

    @property
    def lazy_names(self):
        return set(self.lazy_items.keys())

    @property
    def all_names(self):
        return self.initialized_names | self.lazy_names

    def append_obj(
        self, obj_factory, *args, lazy=False, name=None, module_name=None, **kwargs
    ):
        if lazy:

            def init_local():
                if module_name:
                    obj_maker = getattr(import_module(module_name), obj_factory)
                else:
                    obj_maker = obj_factory
                try:
                    obj_initialized = obj_maker(*args, name=name, **kwargs)
                except TypeError:
                    obj_initialized = obj_maker(*args, **kwargs)

                self.initialized_items[name] = self.lazy_items.pop(name)
                if self.alias_namespace and hasattr(obj_initialized, "alias"):
                    for ta in obj_initialized.alias.get_all():
                        try:
                            self.alias_namespace.update(
                                ta["alias"], ta["channel"], ta["channeltype"]
                            )
                        except Exception as e:
                            print(f'could not init alias {ta["alias"]}')
                            print("error message", e)
                            # traceback.print_tb(e)
                else:
                    print(f"object {name} has no alias!")
                return obj_initialized

            obj_lazy = Proxy(init_local)
            self.lazy_items[name] = obj_lazy
            if self.root_module:
                sys.modules[self.root_module].__dict__[name] = obj_lazy
            return obj_lazy

        else:
            if module_name:
                obj_maker = getattr(import_module(module_name), obj_factory)
            else:
                obj_maker = obj_factory
            try:
                obj = obj_maker(*args, name=name, **kwargs)
            except TypeError:
                obj = obj_maker(*args, **kwargs)
            self.initialized_items[name] = obj
            if self.root_module:
                sys.modules[self.root_module].__dict__[name] = obj
            if self.alias_namespace and hasattr(obj, "alias"):
                for ta in obj.alias.get_all():
                    try:
                        self.alias_namespace.update(
                            ta["alias"], ta["channel"], ta["channeltype"]
                        )
                    except Exception as e:
                        print(f'could not init alias {ta["alias"]}')
                        print("error message", e)
            else:
                print(f"object {name} has no alias!")
            return obj

    def get_obj(self, name):
        if name in self.lazy_names:
            return self.lazy_items[name]
        elif name in self.initialized_names:
            return self.initialized_items[name]
        else:
            raise Exception("Name is not initialized!")

    def append_obj_from_config(self, cnf, lazy=False):
        module_name, obj_factory = cnf["type"].split(":")
        args = []
        for targ in cnf["args"]:
            if isinstance(targ, Component):
                args.append(self.get_obj(targ.name))
            else:
                args.append(targ)
        kwargs = {}
        for tk, tv in cnf["kwargs"].items():
            if isinstance(tv, Component):
                kwargs[tk] = self.get_obj(tv.name)
            else:
                kwargs[tk] = tv
        if "lazy" in cnf.keys():
            lazy = cnf["lazy"]

        self.append_obj(
            obj_factory,
            *args,
            lazy=lazy,
            name=cnf["name"],
            module_name=module_name,
            **kwargs,
        )

    def print_status(self):
        tab = []
        for name in self.initialized_names:
            tab.append([name, "initialized"])
        for name in self.lazy_names:
            tab.append([name, "lazy"])
        print(tabulate(tab))


class Proxy(Proxy_orig):
    def __repr__(self, __getattr__=object.__getattribute__):
        try:
            target = __getattr__(self, "__target__")
        except AttributeError:
            target = self.__wrapped__

        return target.__repr__()
