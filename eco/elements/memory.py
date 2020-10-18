from pathlib import Path
from datetime import datetime
from ..devices_general.adjustable import AdjustableFS
from ..utilities.KeyPress import KeyPress
from tabulate import tabulate
import sys, colorama

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

    def setup_path(self):
        name = self.obj_parent.alias.get_full_name(joiner=None)
        self.dir = Path(self.base_dir) / Path("/".join(reversed(name)))
        self.memories = AdjustableFS(self.dir / Path("memories.json"), default_value={})
        try:
            self.dir.mkdir(exist_ok=True)
        except:
            print("Could not create memory directory")

    def __str__(self):
        self.setup_path()
        mem = self.memories()
        a = []
        for n, (key, content) in enumerate(mem.items()):
            row = [n]
            t = datetime.fromisoformat(key)
            row.append(t.strftime("%Y-%m-%d: %a %-H:%M"))
            row.append(content["message"])
            a.append(row)
        return tabulate(a, headers=["Index", "Time", "Message"])

    def __call__(self, index):
        # print(self.get_memory_difference_str(index))
        self.recall(index)

    def memorize(self, message=None, attributes={}, force_message=True):
        self.setup_path()
        stat_now = self.obj_parent.get_status(base=self.obj_parent)
        stat_now["memorized_attributes"] = attributes
        key = datetime.now().isoformat()
        mem = self.memories()
        if force_message:
            while not message:
                message = input(
                    "Please enter a message associated to this memory entry:\n>>> "
                )
        mem[key] = {"message": message, "categories": self.categories}
        tmp = AdjustableFS(self.dir / Path(key + ".json"))
        tmp(stat_now)
        self.memories(mem)

    def get_memory(self, index=None, key=None):
        self.setup_path()
        if not (index is None):
            key = list(self.memories().keys())[index]
        tmp = AdjustableFS(self.dir / Path(key + ".json"))
        return tmp()

    def recall(self, memory_index=None, key=None, wait=True, show_changes_only=True):
        select = self.select_from_memory(
            memory_index, show_changes_only=show_changes_only
        )
        if not select:
            return
        mem = self.get_memory(index=memory_index)
        rec = mem["settings"]
        if not input("would you really like to do the change? (y/n):") == "y":
            return
        changes = []
        for sel, (key, val) in zip(select, rec.items()):
            if sel:
                to = name2obj(self.obj_parent, key)
                print(f"Changing {key} from {to.get_current_value()} to {val}")
                changes.append(to.set_target_value(val))
        if wait:
            for change in changes:
                change.wait()
            return
        else:
            return changes

    def get_memory_difference_str(
        self, memory_index, select=None, ask_select=True, show_changes_only=False
    ):
        mem = self.get_memory(index=memory_index)
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
                    comp_indicator = (
                        colorama.Fore.RED
                        + colorama.Style.BRIGHT
                        + f"--({recall_value-present_value:+g})-->"
                        + colorama.Style.RESET_ALL
                    )
            if show_changes_only and (not changed):
                continue
            table.append([n, tselstr, key, present_value, comp_indicator, recall_value])

        return tabulate(
            table,
            headers=[
                "",
                "",
                "name",
                "present",
                "",
                "memory",
            ],
            colalign=("decimal", "center", "left", "decimal", "center", "decimal"),
        )

    def select_from_memory(self, memory_index, show_changes_only=True):
        mem = self.get_memory(index=memory_index)
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
                        memory_index,
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


def name2obj(obj_parent, name, delimiter="."):
    if type(name) is str:
        name = name.split(delimiter)
    obj = obj_parent
    for tn in name:
        if not tn:
            obj = obj
        else:
            obj = obj.__dict__[tn]

    return obj