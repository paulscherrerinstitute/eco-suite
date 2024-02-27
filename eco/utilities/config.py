import json
import importlib
from pathlib import Path
from eco.elements.protocols import InitialisationWaitable
import sys
from time import sleep, time
from colorama import Fore as _color
from functools import partial

# from .lazy_proxy import Proxy
from ..aliases import Alias
from ..elements.assembly import Assembly
import getpass
import colorama
import socket
from importlib import import_module
from lazy_object_proxy import Proxy as Proxy_orig
from tabulate import tabulate
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread
from tqdm import tqdm
from rich import progress
from inspect import signature

import traceback


class Component:
    def __init__(self, namestring):
        self.name = namestring


class NamespaceComponent:
    def __init__(self, namespace, namestring):
        self.namespace = namespace
        comps = namestring.split(".")
        for n, comp in enumerate(comps):
            tn = ".".join(comps[: n + 1])
            if tn in self.namespace.all_names:
                self.obj_name = tn
                self.sub_name = ".".join(comps[n + 1 :])
                break
            if n == len(comps) - 1:
                print(f"could not find {namestring} in namespace!")

    def get(self):
        # if not self.obj_name in self.namespace.initialized_names:
        #     self.namespace.init_name(self.obj_name)
        obj = self.namespace.get_obj(self.obj_name)
        if self.sub_name:
            return eval(f"obj.{self.sub_name}")
        else:
            return obj


def replace_NamespaceComponents(*args, **kwargs):
    args_out = []
    kwargs_out = {}

    for arg in args:
        if isinstance(arg, NamespaceComponent):
            args_out.append(Proxy(arg.get))
        else:
            args_out.append(arg)
            pass
    for name, value in kwargs.items():
        if isinstance(value, NamespaceComponent):
            kwargs_out[name] = Proxy(value.get)
        else:
            kwargs_out[name] = value

    return args_out, kwargs_out


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


class IsInitialisingError(Exception):
    """Raised exception when an object is already initializing.

    Args:
        Exception (_type_): _description_
    """

    pass


class Namespace(Assembly):
    def __init__(self, name=None, root_module=None, alias_namespace=None):
        super().__init__(name)
        # self.name = name
        self.lazy_items = {}
        self.initialized_items = {}
        self.failed_items = {}
        self.failed_items_excpetion = {}
        self.initialisation_times_lazy = {}
        self.initialisation_times = {}
        self._initialisation_start_time = {}
        self._init_priority = {}

        self.names_without_alias = []
        self._initializing = []
        self.root_module = root_module
        self.alias_namespace = alias_namespace
    
    @property
    def initialisation_times_sorted(self):
        return dict(sorted(self.initialisation_times.items(), key=lambda w: w[1]))

    @property
    def initialisation_times_lazy_sorted(self):
        return dict(sorted(self.initialisation_times_lazy.items(), key=lambda w: w[1]))

    @property
    def initialized_names(self):
        return set(self.initialized_items.keys())

    @property
    def lazy_names(self):
        return set(self.lazy_items.keys())

    @property
    def failed_names(self):
        return set(self.failed_items.keys())

    @property
    def all_names(self):
        return self.initialized_names | self.lazy_names | self.failed_names
    
    def move_failed_to_lazy(self,*names):
        if not names:
            names = self.failed_names
        for name in names:
            self.lazy_items[name] = self.failed_items.pop(name)
            try:
                self.failed_items_excpetion.pop(name)
            except KeyError:
                pass
            try:
                self._initializing.pop(self._initializing.index(name))
            except KeyError:
                pass
            except ValueError:
                pass




    def init_name(self, name, verbose=True, raise_errors=False):
        # for name in self.all_names:
        # if verbose:
        #     print(("Configuring %s " % (name)).ljust(25), end="")
        #     sys.stdout.flush()
        # if verbose:
        #     print(("(%s)" % (name)).ljust(25), end="")
        #     sys.stdout.flush()
        starttime = time()
        try:
            titem = self.get_obj(name)
            if isinstance(titem, InitialisationWaitable):
                titem._wait_for_initialisation()
            else:
                dir(titem)
            self.initialisation_times[name] = time() - starttime
            if verbose:
                print(
                    ("Init %s " % (name)).ljust(40) + f"{round(1000*(time()-starttime)): 6d} ms "
                    + (_color.GREEN + "OK" + _color.RESET).rjust(5)
                )
                sys.stdout.flush()

        except Exception as expt:
            # if isinstance(expt, IsInitialisingError):
            #     raise IsInitialisingError(f"{name} is being initialized already")
            # tb = traceback.format_exc()
            self.initialisation_times[name] = time() - starttime
            if verbose:
                print(
                    ("Init %s " % (name)).ljust(40) + f"{round(1000*(time()-starttime)): 6d} ms "
                    + (_color.RED + "FAILED" + _color.RESET).rjust(5)
                )
                # print(sys.exc_info())
            if raise_errors:
                raise expt

    def init_all(
        self,
        verbose=False,
        raise_errors=False,
        print_summary=True,
        print_times=True,
        max_workers=5,
        N_cycles=4,
        silent=True,
        giveup_failed=True,
        exclude_names=[],
    ):
        starttime = time()

        if self.failed_names:
            print(
                f"WARNING - previously hard failed items are NOT initialized:\n{self.failed_names} "
            )
        if silent:
            self.silently_initializing = True
            print(
                f"Initializing all items in namespace {self.name} silently in background.\n Be aware of unrelated output!"
            )

            def init():
                self.exc_init = ThreadPoolExecutor(max_workers=max_workers)
                jobs = [
                    self.exc_init.submit(
                        self.init_name, name, verbose=verbose, raise_errors=raise_errors
                    )
                    for name in (self.all_names - set(exclude_names))
                ]
                self.exc_init.shutdown(wait=True)
                self.exc_init = ThreadPoolExecutor(max_workers=1)
                jobs = [
                    self.exc_init.submit(
                        self.init_name, name, verbose=verbose, raise_errors=raise_errors
                    )
                    for name in (
                        self.all_names - self.initialized_names - set(exclude_names)
                    )
                ]
                self.exc_init.shutdown(wait=True)
                self.silently_initializing = False
                if giveup_failed:
                    failed_names = self.lazy_names
                    for k in failed_names:
                        self.failed_items[k] = self.lazy_items.pop(k)
                if print_summary:
                    print(
                        f"Initialized {len(self.initialized_names)} of {len(self.all_names)}."
                    )
                    print(
                        "Failed objects: "
                        + ", ".join(self.lazy_names.union(self.failed_names))
                    )
                    print(f"Initialisation took {time()-starttime} seconds")

            Thread(target=init).start()
        else:
            if hasattr(self, "exc_init"):
                self.exc_init.shutdown(wait=False)
            with ThreadPoolExecutor(max_workers=max_workers) as exc:
                list(
                    progress.track(
                        exc.map(
                            lambda name: self.init_name(
                                name, verbose=verbose, raise_errors=raise_errors
                            ),
                            self.all_names
                            - self.initialized_names
                            - set(exclude_names),
                        ),
                        description="Initializing ...",
                        total=len(
                            self.all_names - self.initialized_names - set(exclude_names)
                        ),
                        transient=True,
                    )
                )
            print("Initializing in single thread...")
            self.move_failed_to_lazy()
            with ThreadPoolExecutor(max_workers=1) as exc:
                list(
                    progress.track(
                        exc.map(
                            lambda name: self.init_name(
                                name, verbose=verbose, raise_errors=raise_errors
                            ),
                            self.all_names
                            - self.initialized_names
                            - set(exclude_names),
                        ),
                        description="Initializing ...",
                        total=len(
                            self.all_names - self.initialized_names - set(exclude_names)
                        ),
                        transient=True,
                    )
                )
                # )
                #     # )
            if giveup_failed:
                failed_names = self.lazy_names
                for k in failed_names:
                    self.failed_items[k] = self.lazy_items.pop(k)
            if print_summary:
                print(
                    f"Initialized {len(self.initialized_names)} of {len(self.all_names)}."
                )
                print(
                    "Failed objects: "
                    + ", ".join(self.lazy_names.union(self.failed_names))
                )
                print(f"Initialisation took {time()-starttime} seconds")

            if (not silent) and print_times:
                from ascii_graph import Pyasciigraph

                gr = Pyasciigraph()
                for line in gr.graph(
                    "Initialisation times",
                    [(tk, tv) for tk, tv in self.initialisation_times_sorted.items()],
                ):
                    print(line)

            # if verbose:
            #     print(("Configuring %s " % (name)).ljust(25), end="")
            #     sys.stdout.flush()
            # # if verbose:
            # #     print(("(%s)" % (name)).ljust(25), end="")
            # #     sys.stdout.flush()
            # try:
            #     dir(self.get_obj(name))

            #     if verbose:
            #         print((_color.GREEN + "OK" + _color.RESET).rjust(5))
            #         sys.stdout.flush()

            # except Exception as expt:
            #     # tb = traceback.format_exc()
            #     if verbose:
            #         print((_color.RED + "FAILED" + _color.RESET).rjust(5))
            #         # print(sys.exc_info())
            #     if raise_errors:
            #         raise expt

    def init_all_new(
        self,
        verbose=False,
        raise_errors=False,
        print_summary=True,
        print_times=True,
        max_workers=5,
        N_cycles=4,
        silent=True,
        giveup_failed=True,
        exclude_names=[],
    ):
        starttime = time()

        if self.failed_names:
            print(
                f"WARNING - previously hard failed items are NOT initialized:\n{self.failed_names} "
            )
        if silent:
            self.silently_initializing = True
            print(
                f"Initializing all items in namespace {self.name} silently in background.\n Be aware of unrelated output!"
            )

            def init():
                self.exc_init = ThreadPoolExecutor(max_workers=max_workers)
                jobs = [
                    self.exc_init.submit(
                        self.init_name, name, verbose=verbose, raise_errors=raise_errors
                    )
                    for name in (self.all_names - set(exclude_names))
                ]
                self.exc_init.shutdown(wait=True)
                self.exc_init = ThreadPoolExecutor(max_workers=1)
                jobs = [
                    self.exc_init.submit(
                        self.init_name, name, verbose=verbose, raise_errors=raise_errors
                    )
                    for name in (
                        self.all_names - self.initialized_names - set(exclude_names)
                    )
                ]
                self.exc_init.shutdown(wait=True)
                self.silently_initializing = False
                if giveup_failed:
                    failed_names = self.lazy_names
                    for k in failed_names:
                        self.failed_items[k] = self.lazy_items.pop(k)
                if print_summary:
                    print(
                        f"Initialized {len(self.initialized_names)} of {len(self.all_names)}."
                    )
                    print(
                        "Failed objects: "
                        + ", ".join(self.lazy_names.union(self.failed_names))
                    )
                    print(f"Initialisation took {time()-starttime} seconds")

            Thread(target=init).start()
        else:
            if hasattr(self, "exc_init"):
                self.exc_init.shutdown(wait=False)
            with ThreadPoolExecutor(max_workers=max_workers) as exc:
                names = self.all_names - self.initialized_names - set(exclude_names)

                def tinit(name):
                    try:
                        self.init_name(name, verbose=verbose, raise_errors=raise_errors)
                    except IsInitialisingError:
                        return ["postpone", name]

                futs = []
                for tname in names:
                    futs.append(exc.submit(tinit, tname))

                while futs:
                    print(f">>>>>>>>>>>  {len(futs)} initialisations to wait for")
                    for fut in as_completed(futs):
                        futs.pop(futs.index(fut))
                        try:
                            if fut.result()[0] == "postpone":
                                tname = fut.result()[1]
                                futs.append(exc.submit(tinit, tname))
                        except:
                            pass

            print("Initializing in single thread...")
            with ThreadPoolExecutor(max_workers=1) as exc:
                list(
                    progress.track(
                        exc.map(
                            lambda name: self.init_name(
                                name, verbose=verbose, raise_errors=raise_errors
                            ),
                            self.all_names
                            - self.initialized_names
                            - set(exclude_names),
                        ),
                        description="Initializing ...",
                        total=len(
                            self.all_names - self.initialized_names - set(exclude_names)
                        ),
                        transient=True,
                    )
                )

            if giveup_failed:
                failed_names = self.lazy_names
                for k in failed_names:
                    self.failed_items[k] = self.lazy_items.pop(k)
            if print_summary:
                print(
                    f"Initialized {len(self.initialized_names)} of {len(self.all_names)}."
                )
                print(
                    "Failed objects: "
                    + ", ".join(self.lazy_names.union(self.failed_names))
                )
                print(f"Initialisation took {time()-starttime} seconds")

            if (not silent) and print_times:
                from ascii_graph import Pyasciigraph

                gr = Pyasciigraph()
                for line in gr.graph(
                    "Initialisation times",
                    [(tk, tv) for tk, tv in self.initialisation_times_sorted.items()],
                ):
                    print(line)

    def get_initialized_aliases(self, channeltypes=[]):
        aliases = []
        has_no_aliases = []
        for tn, tv in self.initialized_items.items():
            try:
                aliases += tv.alias.get_all()
            except:
                has_no_aliases.append(tn)
        aliases_out = []
        for channeltype in channeltypes:
            for alias in aliases:
                if alias["channeltype"] == channeltype:
                    aliases_out.append(alias)
        if not channeltypes:
            aliases_out = aliases
        return aliases, has_no_aliases

    def append_obj(
        self, obj_factory, *args, lazy=False, name=None, module_name=None, init_timeout=30, **kwargs
    ):
        if lazy:

            def init_local():

                if name in self.failed_names:
                    raise IsInitialisingError(f'{name} failed previously to initialize.')
                
                if name in self._initializing:
                    self._init_priority[name] += 1
                    while name in self._initializing:
                        if (time()-self._initialisation_start_time[name]) <= init_timeout:
                            sleep(5)
                        else:
                    #     print(f'{name} waiting init since {time()-self._initialisation_start_time[name]} s')
                    #     sleep(5)
                    # # pass
                            self._initializing.pop(self._initializing.index(name))
                            raise IsInitialisingError(f"NB: {name} initialization timed out!")
                    


                else:
                    self._initializing.append(name)
                    self._init_priority[name] = 0
                    self._initialisation_start_time[name] = time()
                

                # args, kwargs = replace_NamespaceComponents(*args, **kwargs)

                if module_name:
                    obj_maker = getattr(import_module(module_name), obj_factory)
                else:
                    obj_maker = obj_factory

                try:
                    if "name" in signature(obj_maker).parameters:
                        obj_initialized = obj_maker(
                            *replace_NamespaceComponents(*args)[0],
                            name=name,
                            **replace_NamespaceComponents(**kwargs)[1],
                        )
                    else:
                        obj_initialized = obj_maker(
                            *replace_NamespaceComponents(*args)[0],
                            **replace_NamespaceComponents(**kwargs)[1],
                        )
                except Exception as e:
                    self.failed_items[name] = self.lazy_items.pop(name)
                    self.failed_items_excpetion[name] = e
                    self._initializing.pop(self._initializing.index(name))
                    raise Exception


                try:
                    self.initialized_items[name] = self.lazy_items.pop(name)
                except KeyError:
                    self.initialized_items[name] = self.failed_items.pop(name)
                self._initializing.pop(self._initializing.index(name))
                # if name in self.initialisation_times_lazy.keys():
                #     self.initialisation_times_lazy[name] += time() - starttime
                # else:
                self.initialisation_times_lazy[name] = time() - self._initialisation_start_time[name] 
                if hasattr(obj_initialized, "alias"):
                    self._append(
                        obj_initialized,
                        name=name,
                        is_setting=True,
                        is_display="recursive",
                        call_obj=False,
                    )
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
                    self.names_without_alias.append(name)
                return obj_initialized

            obj_lazy = Proxy(init_local)
            self.lazy_items[name] = obj_lazy
            if self.root_module:
                sys.modules[self.root_module].__dict__[name] = obj_lazy
            return obj_lazy

        else:
            starttime = time()
            args, kwargs = replace_NamespaceComponents(*args, **kwargs)
            if module_name:
                obj_maker = getattr(import_module(module_name), obj_factory)
            else:
                obj_maker = obj_factory
            try:
                obj = obj_maker(*args, name=name, **kwargs)
            except TypeError:
                obj = obj_maker(*args, **kwargs)
            self.initialized_items[name] = obj
            self.initialisation_times_lazy[name] = time() - starttime
            if self.root_module:
                sys.modules[self.root_module].__dict__[name] = obj
            if hasattr(obj, "alias"):
                self._append(
                    obj,
                    name=name,
                    is_setting=True,
                    is_display="recursive",
                    call_obj=False,
                )
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
                self.names_without_alias.append(name)
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
