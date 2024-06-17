from pathlib import Path
from datetime import datetime
from .adjustable import AdjustableFS
from ..utilities.keypress import KeyPress
from tabulate import tabulate
import sys, colorama
from inspect import getargspec
import eco
from ansi2html import Ansi2HTMLConverter
from simple_term_menu import TerminalMenu

conv = Ansi2HTMLConverter()

global_memory_dir = None


def set_global_memory_dir(dirpath, mode="w"):
    globals()["global_memory_dir"] = Path(dirpath).expanduser()


def get_memory(name):
    if not (global_memory_dir is None):
        return Memory(name)


class Memory:
    def __init__(
        self,
        obj,
        memory_dir=global_memory_dir,
        categories={"recall": ["settings"], "track": ["status_indicators"]},
    ):
        self.obj_parent = obj
        self.categories = categories
        if not memory_dir:
            memory_dir = global_memory_dir
        self.base_dir = Path(memory_dir)
        self.obj_parent.presets = Presets(self)

    def setup_path(self):
        name = self.obj_parent.alias.get_full_name(joiner=None)
        self.dir = Path(self.base_dir) / Path("/".join(reversed(name)))
        try:
            self.dir.mkdir(exist_ok=True)
            try:
                self.dir.chmod(0o775)
            except:
                pass
        except:
            print("Could not create memory directory")
        self._memories = AdjustableFS(
            self.dir / Path("memories.json"), default_value={}
        )
        self._presets = AdjustableFS(self.dir / Path("presets.json"), default_value={})

    def __str__(self):
        self.setup_path()
        mem = self._memories()
        a = []
        for n, (key, content) in enumerate(mem.items()):
            row = [n]
            t = datetime.fromisoformat(key)
            row.append(t.strftime("%Y-%m-%d: %a %-H:%M"))
            row.append(content["message"])
            a.append(row)

        return tabulate(a, headers=["Index", "Time", "Message"])

    def __call__(self, index=None, **kwargs):
        # print(self.get_memory_difference_str(index))

        if index is None:
            self.setup_path()
            mem = self._memories()
            a = []
            for n, (key, content) in enumerate(mem.items()):
                row = ""
                t = datetime.fromisoformat(key)
                row += t.strftime("%Y-%m-%d: %a %H:%M")
                row += "   "
                row += content["message"]
                a.append(row)
            ind_cancel = len(a)
            a.append("--> do nothing")
            menu = TerminalMenu(a, cursor_index=ind_cancel)
            print("Select memory to recall")
            index = menu.show()
            if index == ind_cancel:
                return
        self.recall(memory_index=index, **kwargs)

    def _get_elog(self):
        if hasattr(self, "_elog") and self._elog:
            return self._elog
        elif hasattr(self, "__elog") and self.__elog:
            return self.__elog
        elif eco.defaults.ELOG:
            return eco.defaults.ELOG
        else:
            return None

    def memorize(
        self,
        message=None,
        attributes={},
        force_message=True,
        preset_varname=None,
        to_elog=True,
    ):
        self.setup_path()
        stat_now = self.obj_parent.get_status(base=self.obj_parent)
        stat_now["memorized_attributes"] = attributes
        key = datetime.now().isoformat()
        stat_now["date"] = key
        mem = self._memories()
        if force_message:
            while not message:
                message = input(
                    "Please enter a message associated to this memory entry:\n>>> "
                )
                mem[key] = {
                    "message": message,
                    "categories": self.categories,
                    "date": key,
                }
                if preset_varname:
                    mem[key].update({"presetname": preset_varname})
        tmp = AdjustableFS(self.dir / Path(key + ".json"))
        tmp(stat_now)
        self._memories(mem)
        print(f"Saved memory for {self.obj_parent.alias.get_full_name()}: {message}")
        print(f"memory file:  {tmp.file_path.as_posix()}")
        if to_elog:
            elog = self._get_elog()
            elog.post(
                f"Saved memory for {self.obj_parent.alias.get_full_name()}: {message}",
                tmp.file_path,
                text_encoding="markdown",
            )

    def get_memory(self, input_obj=None, index=None, key=None, filter_existing=True):
        if not input_obj is None:
            if type(input_obj) is dict:
                mem_full = input_obj
            else:
                tmp = AdjustableFS(Path(input_obj))
                mem_full = tmp()
        else:
            self.setup_path()
            if not (index is None):
                key = list(self._memories().keys())[index]
            tmp = AdjustableFS(self.dir / Path(key + ".json"))
            mem_full = tmp()
        if filter_existing:
            mem_filt = {}
            for tkey, tval in mem_full.items():
                if tkey in ["settings", "status_indicators"]:
                    mem_filt[tkey] = {}
                    for ttkey, ttval in tval.items():
                        try:
                            name2obj(self.obj_parent, ttkey)
                            mem_filt[tkey][ttkey] = ttval
                        except KeyError:
                            ...
                else:
                    mem_filt[tkey] = tval

            return mem_filt
        else:
            return mem_full

    def clear_memory(self, index=None, key=None):
        if not (index is None):
            key = list(self._memories().keys())[index]
        if key is None:
            raise Exception("memory key or index to be deleted needs to be specified!")
        mem = self._memories.get_current_value()
        mem.pop(key)
        self._memories.set_target_value(mem).wait()

    def recall(
        self,
        memory_index=None,
        input_obj=None,
        key=None,
        wait=True,
        show_changes_only=True,
        set_changes_only=True,
        check_limits=True,
        change_serially=False,
        force=False,
    ):
        """Recall a memory_index, from an index in the default meory list, from a
        dictionary containing the memory information, or from a path to a file containing the memory.

        Args:
            memory_index (integer, optional): index in memory list. Defaults to None.
            input_obj (dictionary or string, optional): direct passing memory as dict or s filepath (string) to the memory file. Defaults to None.
            key (string, optional): key of memory in memory list (if not defined by the index). Defaults to None.
            wait (bool, optional): Wait for the memory recall changes to complete. Defaults to True.
            show_changes_only (bool, optional): in rpreview show only changes that are different to present setting. Defaults to True.
            set_changes_only (bool, optional): setting only the changes that changed. Defaults to True.
            check_limits (bool, optional): check limits before changing. Defaults to True.
            change_serially (bool, optional): change and wait each change after each other, not simultaneously. Defaults to False.
            force (bool, optional): force the change without previous preview. Defaults to False.

        Returns:
            _type_: _description_
        """
        # if input_obj:
        mem = self.get_memory(
            index=memory_index,
            key=key,
            input_obj=input_obj,
        )
        rec = mem["settings"]
        if force:
            select = [True] * len(rec.items())
        else:
            select = self.select_from_memory(
                memory_index=memory_index,
                key=key,
                show_changes_only=show_changes_only,
                input_obj=input_obj,
            )
            if not select:
                return
            if not input("would you really like to do the change? (y/n):") == "y":
                return

        changes = []
        for sel, (key, val) in zip(select, rec.items()):
            if sel:
                to = name2obj(self.obj_parent, key)
                if set_changes_only:
                    if to.get_current_value() == val:
                        continue
                print(f"Changing {key} from {to.get_current_value()} to {val}")
                if "check" in getargspec(to.set_target_value).args:
                    changes.append(to.set_target_value(val, check=check_limits))
                else:
                    changes.append(to.set_target_value(val))
                if change_serially:
                    changes[-1].wait()
        if wait:
            for change in changes:
                change.wait()
            return
        else:
            return changes

    def recall_from_runtable(self): ...

    def get_memory_difference_str(
        self,
        memory,
        select=None,
        ask_select=True,
        show_changes_only=False,
        tablefmt="plain",
    ):
        # mem = self.get_memory(index=memory_index)
        mem = memory
        rec = mem["settings"]
        if not select:
            select = [True] * len(rec)
        table = []
        for n, (tsel, (key, recall_value)) in enumerate(zip(select, rec.items())):
            present_value = name2obj(self.obj_parent, key).get_current_value()
            if tsel:
                tselstr = "x"
            else:
                tselstr = " "
            if present_value == recall_value:
                changed = False
                if tablefmt == "html":
                    comp_indicator = "=="
                else:
                    comp_indicator = (
                        colorama.Fore.GREEN
                        + colorama.Style.BRIGHT
                        + "=="
                        + colorama.Style.RESET_ALL
                    )
            else:
                changed = True
                if not tsel:
                    comp_indicator = f"not changed ({recall_value-present_value:+g})"
                else:
                    try:
                        tdiff = f"{recall_value - present_value:+g}"
                    except TypeError:
                        tdiff = "special"
                    if tablefmt == "html":
                        comp_indicator = f"{tdiff:s}"
                    else:
                        comp_indicator = (
                            colorama.Fore.RED
                            + colorama.Style.BRIGHT
                            + f"{tdiff:s}"
                            + colorama.Style.RESET_ALL
                        )
            if show_changes_only and (not changed):
                continue

            table.append([n, tselstr, key, present_value, comp_indicator, recall_value])

        if len(table) == 0:
            return "No changes compared to memory!"
        return tabulate(
            table,
            headers=[
                "",
                "",
                "name",
                "present",
                "difference",
                "memory",
            ],
            colalign=("decimal", "center", "left", "decimal", "center", "decimal"),
            tablefmt=tablefmt,
        )

    def select_from_memory(
        self, input_obj=None, key=None, memory_index=None, show_changes_only=True
    ):
        mem = self.get_memory(input_obj=input_obj, key=key, index=memory_index)
        rec = mem["settings"]
        k = KeyPress()
        # cll = colorama.ansi.clear_line()

        help = "Change selection pressing keys followed by numbered seelection \n"
        help += "  o : Select only (enter comma-separated row numbers)\n"
        help += "  a : Select additionally (enter comma-separated row numbers)\n"
        help += "  e : Exclude from selection (enter comma-separated row numbers)\n"
        help += "  r : recall selected memory\n"
        help += "  q : quit\n"

        class Printer:
            def __init__(self, o=self):
                self.o = o
                self.len = len(rec)
                self.select = [True] * self.len

            def print(self, **kwargs):
                print(
                    self.o.get_memory_difference_str(
                        mem,
                        select=self.select,
                        show_changes_only=show_changes_only,
                    )
                )
                print(help)

            def select_only(self):
                v = self.get_array()
                self.select = [False] * self.len
                for tv in v:
                    self.select[tv] = True

            def select_additional(self):
                v = self.get_array()
                for tv in v:
                    self.select[tv] = True

            def exclude(self):
                v = self.get_array()
                for tv in v:
                    self.select[tv] = False

            def get_array(self):
                sys.stdout.flush()
                v = sys.stdin.readline()
                try:
                    v = v.split(",")
                    v = [int(tv) for tv in v]
                    print(v)
                    return v
                except:
                    print(
                        "value cannot be converted to listed integers, please try again!"
                    )
                    sys.stdout.flush()
                    return self.get_array()

        p = Printer()
        while k.isq() is False:
            p.print()
            k.waitkey()
            if k.iskey("o"):
                print("Select only: ")
                p.select_only()
            elif k.iskey("a"):
                print("Append to selection: ")
                p.select_additional()
            elif k.iskey("e"):
                print("Exclude from selection: ")
                p.exclude()
            elif k.isq():
                return
            elif k.iskey("r"):
                return p.select
            else:
                # print(help)
                pass

        # stat_now = self.obj_parent.get_status()
        # for mem

    def __repr__(self):
        return self.__str__()


class Presets:
    def __init__(
        self,
        memory,
    ):
        self._memory = memory
        self._setup_presets()

    def __dir__(self):
        return self._setup_presets()

    def __getattr__(self, name):
        self._setup_presets()
        if not name in self.__dict__.keys():
            raise AttributeError
        return self.__dict__[name]

    def _setup_presets(self):
        self._memory.setup_path()
        mem = self._memory._memories()
        presets = []
        for key, dat in mem.items():
            if "presetname" in dat.keys():
                self.__dict__[dat["presetname"]] = Preset(
                    self._memory, key, name=dat["presetname"]
                )
                presets.append(dat["presetname"])
        return presets

    def __str__(self):
        self._memory.setup_path()
        mem = self._memory._memories()
        table = []
        for key, dat in mem.items():
            if "presetname" in dat.keys():
                table.append([dat["presetname"], key, dat["message"]])

        return tabulate(
            table,
            headers=[
                "Preset",
                "Date",
                "Message",
            ],
            colalign=("left", "left", "left"),
        )

    def __repr__(self):
        return self.__str__()


class Preset:
    def __init__(self, memory, key, name=None):
        self._memory = memory
        self._key = key
        self._name = name

    def get_memory(self):
        return self._memory.get_memory(key=self._key)

    def __call__(self, force=True):
        self._memory.recall(key=self._key, force=force)

    def __str__(self):
        s = f"Preset {self._name} - saved values compared to the present status\n"
        tmem = self._memory.get_memory(key=self._key)
        s += self._memory.get_memory_difference_str(tmem)
        return s

    def __repr__(self):
        return self.__str__()


def name2obj(obj_parent, name, delimiter="."):
    if type(name) is str:
        name = name.split(delimiter)
    obj = obj_parent
    for tn in name:
        if not tn or tn == "self":
            obj = obj
        else:
            obj = obj.__dict__[tn]

    return obj
