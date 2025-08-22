from datetime import datetime
from itertools import product
from numbers import Number
import os
import json
import numpy as np
from time import sleep, time
import traceback
from pathlib import Path
import colorama

from eco.elements.protocols import Adjustable
from eco.utilities.utilities import NumpyEncoder, foo_get_kwargs
from ..elements.adjustable import AdjustableMemory, DummyAdjustable
from IPython import get_ipython
from .daq_client import Daq
from eco.elements.assembly import Assembly
from rich.progress import Progress
import inputimeout


# TODO circular import issue
from eco.elements.detector import DetectorGet, DetectorMemory

inval_chars = [" ", "/"]
ScanNameError = Exception(
    f"invalid character in acquisition name, please use a name without {inval_chars}"
)


class RunList(Assembly):
    def __init__(self, scan_info_dir, name=None):
        super().__init__(name=name)
        self.scan_info_dir = scan_info_dir

    def get_run_list(self): ...


class StepScan(Assembly):
    def __init__(
        self,
        adjustables,
        values,
        counters,
        description="",
        Npulses=100,
        basepath="",
        settling_time=0,
        callbacks_start_scan=[],
        callbacks_start_step=[],
        callbacks_step_counting=[],
        callbacks_end_step=[],
        callbacks_end_scan=[],
        return_at_end="timeout",
        gridspecs=None,
        elog=None,
        name="current_scan",
        **kwargs_callbacks,
    ):
        # if np.any([char in fina for char in inval_chars]):
        #     raise ScanNameError

        super().__init__(name=name)
        self._append(
            DetectorMemory,
            datetime.now().strftime("%Y-%M-%d %H:%M:%S"),
            name="start_time",
        )
        self._description = description
        self._append(DetectorGet, lambda: self._description, name="description")
        self.adjustables = adjustables
        self._append(
            DetectorGet,
            lambda: self._get_names(self.adjustables),
            name="adjustables_names",
        )
        self.counters = counters
        self._append(
            DetectorGet, lambda: self._get_names(self.counters), name="counters_names"
        )

        self._append(DetectorMemory, len(values), name="number_of_steps")
        try:
            scan_command = get_ipython().user_ns["In"][-1]
        except:
            scan_command = "unknown"
        self._append(DetectorMemory, scan_command, name="scan_command")

        # TODO: make Npulses and pulses_per_step general counter arguments that are eihter interpreted by the counter or that are replaced by counter depedent kwargs.
        if not isinstance(Npulses, Number):
            if not len(Npulses) == len(values):
                raise ValueError("steps for Number of pulses and values must match!")
            self.pulses_per_step = Npulses
        else:
            self.pulses_per_step = [Npulses] * len(values)

        self._values_todo = values
        self._append(
            DetectorGet, lambda: self._values_todo, name="values_todo", is_display=False
        )
        self._values_done = []
        self._append(
            DetectorGet, lambda: self._values_done, name="values_done", is_display=False
        )
        self._append(DetectorMemory, gridspecs, name="grid_specs", is_display=False)

        self.pulses_done = []

        self.readbacks = []

        self._settling_time = settling_time
        self._append(
            DetectorGet,
            lambda: self._settling_time,
            name="settling_time",
            is_display=False,
        )
        self.next_step = 0

        self.scan_info = {
            "scan_parameters": {
                "name": self.adjustables_names.get_current_value(),
                "grid_specs": self.grid_specs.get_current_value(),
                # "Id": [ta.Id if hasattr(ta, "Id") else "noId" for ta in adjustables],
            },
            "scan_description": self._description,
            "scan_values_all": values,
            "scan_values": [],
            "scan_readbacks": [],
            "scan_files": [],
            "scan_step_info": [],
        }

        self._append(DetectorGet, lambda: self.scan_info, name="info", is_display=False)

        initial_values = []
        for adj in self.adjustables:
            tv = adj.get_current_value()
            initial_values.append(adj.get_current_value())
            print("Initial value of %s : %g" % (adj.name, tv))

        self._append(
            DetectorMemory, initial_values, name="initial_values", is_display=False
        )

        self.return_at_end = return_at_end
        self._elog = elog
        self.remaining_tasks = []
        self.callbacks_start_scan = callbacks_start_scan
        self.callbacks_start_step = callbacks_start_step
        self.callbacks_step_counting = callbacks_step_counting
        self.callbacks_end_step = callbacks_end_step
        self.callbacks_end_scan = callbacks_end_scan
        self.callbacks_kwargs = kwargs_callbacks

        self._have_run_callbacks_start_scan = False

    def _get_names(self, elements):
        """Get the names of the elements."""
        names = []
        for el in elements:
            if hasattr(el, "alias"):
                names.append(el.alias.get_full_name())
            elif hasattr(el, "name"):
                names.append(el.name)
            else:
                names.append("unknown")
        return names

    def run_callbacks_start_scan(self):
        if self.callbacks_start_scan:
            for caller in self.callbacks_start_scan:
                caller(self, **self.callbacks_kwargs)
        for ctr in self.counters:
            if hasattr(ctr, "callbacks_start_scan") and ctr.callbacks_start_scan:
                for tcb in ctr.callbacks_start_scan:
                    tcb(self, **self.callbacks_kwargs)

    def run_callbacks_start_step(self):
        if self.callbacks_start_step:
            for caller in self.callbacks_start_step:
                caller(self, **self.callbacks_kwargs)
        for ctr in self.counters:
            if hasattr(ctr, "callbacks_start_step") and ctr.callbacks_start_step:
                for tcb in ctr.callbacks_start_step:
                    tcb(self, **self.callbacks_kwargs)

    def run_callbacks_step_counting(self):
        if self.callbacks_step_counting:
            for caller in self.callbacks_step_counting:
                caller(self, **self.callbacks_kwargs)
        for ctr in self.counters:
            if hasattr(ctr, "callbacks_step_counting") and ctr.callbacks_step_counting:
                for tcb in ctr.callbacks_step_counting:
                    tcb(self, **self.callbacks_kwargs)

    def has_callbacks_step_counting(self):
        if self.callbacks_step_counting:
            return True
        for ctr in self.counters:
            if hasattr(ctr, "callbacks_step_counting") and ctr.callbacks_step_counting:
                return True
        return False

    def run_callbacks_end_step(self):
        if self.callbacks_end_step:
            for caller in self.callbacks_end_step:
                caller(self, **self.callbacks_kwargs)
        for ctr in self.counters:
            if hasattr(ctr, "callbacks_end_step") and ctr.callbacks_end_step:
                for tcb in ctr.callbacks_end_step:
                    tcb(self, **self.callbacks_kwargs)

    def run_callbacks_end_scan(self):
        if self.callbacks_end_scan:
            for caller in self.callbacks_end_scan:
                caller(self, **self.callbacks_kwargs)
        for ctr in self.counters:
            if hasattr(ctr, "callbacks_end_scan") and ctr.callbacks_end_scan:
                for tcb in ctr.callbacks_end_scan:
                    tcb(self, **self.callbacks_kwargs)

    # def get_filename(self, stepNo, Ndigits=4):
    #     fina = os.path.join(self.basepath, Path(self.fina).stem)
    #     if self._scan_directories:
    #         fina = os.path.join(fina, self.fina)
    #     fina += "_step%04d" % stepNo
    #     return fina

    def do_next_step(self, step_info=None, verbose=True):
        self._current_step_ok = True
        t_step_start = time()
        self.run_callbacks_start_step()

        dt_callbacks_step_start = time() - t_step_start

        if not len(self._values_todo) > 0:
            return False
        self.values_current_step = self._values_todo[0]

        statstr = "Step %d of %d" % (
            self.next_step + 1,
            len(self._values_todo) + len(self._values_done),
        )

        # fina = self.get_filename(self.nextStep)
        t_adj_start = time()
        ms = []
        for adj, tv in zip(self.adjustables, self.values_current_step):
            ms.append(adj.set_target_value(tv))
        for tm in ms:
            tm.wait()
        dt_adj = time() - t_adj_start

        # settling
        sleep(self._settling_time)

        # counters
        t_ctr_start = time()
        self.readbacks_current_step = []
        adjs_name = []
        adjs_offset = []
        adjs_id = []

        statstr += "   "
        for adj in self.adjustables:
            self.readbacks_current_step.append(adj.get_current_value())
            try:
                if hasattr(adj, "name"):
                    adjs_name.append(adj.name)
                    statstr += f"{adj.name} @ {adj.get_current_value():.3f}, "
            except:
                print("acquiring metadata failed")
                pass

        statstr += " ; Ctrs "
        if not self.has_callbacks_step_counting():
            acs = []
            for ctr in self.counters:
                acq = ctr.acquire(
                    scan=self, Npulses=self.pulses_per_step[0]
                )  # TODO make sure step-individual aquisition argument is possible.
                acs.append(acq)
                try:
                    if hasattr(ctr, "name"):
                        statstr += f"{ctr.name}, "
                except:
                    pass
            filenames = []
            for ta in acs:
                ta.wait()
                if hasattr(ta, "file_names"):
                    filenames.extend(ta.file_names)
        else:
            acs = []
            for ctr in self.counters:
                ctr.start(scan=self)
                try:
                    if hasattr(ctr, "name"):
                        statstr += f"{ctr.name}, "
                except:
                    pass
            self.run_callbacks_step_counting()

            filenames = []
            for ctr in self.counters:
                resp = ctr.stop(scan=self)
                filenames.extend(resp["files"])
        statstr = statstr[:-2] + " done."
        print(statstr, end="\n")

        dt_ctr = time() - t_ctr_start
        sleep(0.003)  # from display debugging, maybe unnecessary.

        ### >>> Callback end
        t_callbacks_step_end = time()
        # if self.checker:
        #     if not self.checker.stop_and_analyze():
        #         return True
        if callable(step_info):
            tstepinfo = step_info.get_current_value()
        else:
            tstepinfo = {}
        self._values_done.append(self._values_todo.pop(0))
        self.pulses_done.append(self.pulses_per_step.pop(0))
        self.readbacks.append(self.readbacks_current_step)

        gridspecs = self.grid_specs.get_current_value()
        if gridspecs:
            tstepinfo["grid_index"] = gridspecs["index_plan"][self.next_step]

        tstepinfo["times"] = {
            "callbacks_step_start": dt_callbacks_step_start,
            "adjustables": dt_adj,
            "counters": dt_ctr,
        }

        self.appendScanInfo(
            self.values_current_step,
            self.readbacks_current_step,
            step_files=filenames,
            step_info=tstepinfo,
        )
        # self.writeScanInfo()

        self.run_callbacks_end_step()
        dt_callbacks_step_end = time() - t_callbacks_step_end
        ### <<<< Callback end

        # hack to update the times
        self.scan_info["scan_step_info"][-1]["times"][
            "callbacks_step_end"
        ] = dt_callbacks_step_end

        if self._current_step_ok:
            self.next_step += 1
            return True
        else:
            return False

    def appendScanInfo(
        self, values_step, readbacks_step, step_files=None, step_info=None
    ):
        self.scan_info["scan_values"].append(values_step)
        self.scan_info["scan_readbacks"].append(readbacks_step)
        self.scan_info["scan_files"].append(step_files)
        self.scan_info["scan_step_info"].append(step_info)

    def get_callback_keywords(self):
        kws_all = set([])
        for cb in self.callbacks_start_scan:
            kws = foo_get_kwargs(cb)

            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for cb in self.callbacks_start_step:
            kws = foo_get_kwargs(cb)
            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for cb in self.callbacks_step_counting:
            kws = foo_get_kwargs(cb)
            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for cb in self.callbacks_end_step:
            kws = foo_get_kwargs(cb)
            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for cb in self.callbacks_end_scan:
            kws = foo_get_kwargs(cb)
            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for ctr in self.counters:
            if hasattr(ctr, "callbacks_start_scan"):
                for cb in ctr.callbacks_start_scan:
                    kws = foo_get_kwargs(cb)
                    if kws:
                        kws_all.update(set(kws))
                        print(cb.__name__, "has keywords:", kws)
            if hasattr(ctr, "callbacks_start_step"):
                for cb in ctr.callbacks_start_step:
                    kws = foo_get_kwargs(cb)
                    if kws:
                        kws_all.update(set(kws))
                        print(cb.__name__, "has keywords:", kws)
            if hasattr(ctr, "callbacks_step_counting"):
                for cb in ctr.callbacks_step_counting:
                    kws = foo_get_kwargs(cb)
                    if kws:
                        kws_all.update(set(kws))
                        print(cb.__name__, "has keywords:", kws)
            if hasattr(ctr, "callbacks_end_step"):
                for cb in ctr.callbacks_end_step:
                    kws = foo_get_kwargs(cb)
                    if kws:
                        kws_all.update(set(kws))
                        print(cb.__name__, "has keywords:", kws)
            if hasattr(ctr, "callbacks_end_scan"):
                for cb in ctr.callbacks_end_scan:
                    kws = foo_get_kwargs(cb)
                    if kws:
                        kws_all.update(set(kws))
                        print(cb.__name__, "has keywords:", kws)

        return kws_all

        # kws = set([])
        # kws.union(*[set(foo_get_kwargs(cb)) for cb in self.callbacks_start_scan])
        # kws.union(*[set(foo_get_kwargs(cb)) for cb in self.callbacks_start_step])
        # kws.union(*[set(foo_get_kwargs(cb)) for cb in self.callbacks_end_step])
        # kws.union(*[set(foo_get_kwargs(cb)) for cb in self.callbacks_end_scan])

        # for ctr in self.counters:
        #     kws.union(*[set(foo_get_kwargs(cb)) for cb in ctr.callbacks_start_scan if hasattr(ctr, "callbacks_start_scan")])
        #     kws.union(*[set(foo_get_kwargs(cb)) for cb in ctr.callbacks_start_step if hasattr(ctr, "callbacks_start_step")])
        #     kws.union(*[set(foo_get_kwargs(cb)) for cb in ctr.callbacks_end_step if hasattr(ctr, "callbacks_end_step")])
        #     kws.union(*[set(foo_get_kwargs(cb)) for cb in ctr.callbacks_end_scan if hasattr(ctr, "callbacks_end_step")])
        # return kws

    def writeScanInfo(self):
        if not Path(self.scan_info_filename).exists():
            with open(self.scan_info_filename, "w") as f:
                json.dump(self.scan_info, f, sort_keys=True, cls=NumpyEncoder)
        else:
            with open(self.scan_info_filename, "r+") as f:
                f.seek(0)
                json.dump(self.scan_info, f, sort_keys=True, cls=NumpyEncoder)
                f.truncate()

    def scan_all(self, step_info=None):
        if not self._have_run_callbacks_start_scan:
            self.run_callbacks_start_scan()
            self._have_run_callbacks_start_scan = True
        done = False
        steps_remaining = len(self._values_todo)
        with Progress() as self._progress:
            pr_task = self._progress.add_task(
                "[green]Scanning...", total=steps_remaining
            )
            try:
                while not done:
                    done = not self.do_next_step(step_info=step_info)
                    self._progress.update(pr_task, advance=1)
            except:
                tb = traceback.format_exc()
            else:
                tb = "Ended all steps without interruption."
            finally:
                self._progress.stop()
                print(tb)

                self.run_callbacks_end_scan()

                if self.return_at_end == "question":
                    if input("Change back to initial values? (y/n)")[0] == "y":
                        chs = self.changeToInitialValues()
                        print("Changing back to value(s) before scan.")
                        for ch in chs:
                            ch.wait()

                elif self.return_at_end == "timeout":
                    timeout = 10
                    try:
                        o = inputimeout.inputimeout(
                            prompt=f"Change back to initial values? (y/n) Changing back in {timeout} seconds.",
                            timeout=timeout,
                        )
                    except inputimeout.TimeoutOccurred:
                        chs = self.changeToInitialValues()
                        print("Changing back to value(s) before scan.")
                        for ch in chs:
                            ch.wait()
                    except KeyboardInterrupt:
                        raise Exception("User-requested cancelling!")
                    else:
                        if o == "y":
                            chs = self.changeToInitialValues()
                            print("Changing back to value(s) before scan.")
                            for ch in chs:
                                ch.wait()
                        if o == "n":
                            print("Staying at final scan value(s)!")
                elif self.return_at_end:
                    chs = self.changeToInitialValues()
                    print("Changing back to value(s) before scan.")
                    for ch in chs:
                        ch.wait()

                else:
                    print("Staying at final scan value(s)!")

    def changeToInitialValues(self):
        c = []
        for adj, iv in zip(self.adjustables, self.initial_values()):
            c.append(adj.set_target_value(iv))
        return c


class Scans(Assembly):
    """Convenience class to initialte typical scans with some default parameters the base StepScan and others."""

    def __init__(
        self,
        # data_base_dir="",
        # scan_info_dir="",
        default_counters=[],
        # checker=None,
        # scan_directories=False,
        callbacks_start_scan=[],
        callbacks_start_step=[],
        callbacks_step_counting=[],
        callbacks_end_step=[],
        callbacks_end_scan=[],
        # run_table=None,
        elog=None,
        name="scans",
    ):
        super().__init__(name=name)
        # self._run_table = run_table
        self.callbacks_start_scan = callbacks_start_scan
        self.callbacks_start_step = callbacks_start_step
        self.callbacks_step_counting = callbacks_step_counting

        self.callbacks_end_step = callbacks_end_step
        self.callbacks_end_scan = callbacks_end_scan
        # self.data_base_dir = data_base_dir
        # scan_info_dir = Path(scan_info_dir)
        # if not scan_info_dir.exists():
        #     print(
        #         f"Path {scan_info_dir.absolute().as_posix()} does not exist, will try to create it..."
        #     )
        #     scan_info_dir.mkdir(parents=True)
        #     print(f"Tried to create {scan_info_dir.absolute().as_posix()}")
        #     scan_info_dir.chmod(0o775)
        #     print(f"Tried to change permissions to 775")

        # for counter in default_counters:
        #     if not (counter._default_file_path is None):
        #         data_dir = Path(counter._default_file_path + self.data_base_dir)
        #         if not data_dir.exists():
        #             print(
        #                 f"Path {data_dir.absolute().as_posix()} does not exist, will try to create it..."
        #             )
        #             data_dir.mkdir(parents=True)
        #             print(f"Tried to create {data_dir.absolute().as_posix()}")
        #             data_dir.chmod(0o775)
        #             print(f"Tried to change permissions to 775")

        # self.scan_info_dir = scan_info_dir
        # self.filename_generator = RunFilenameGenerator(self.scan_info_dir)
        self._default_counters = default_counters
        self._append(
            DetectorGet, self._get_counter_names, name="default_counters_names"
        )
        self._append(DetectorMemory, "none since session start", name="acquiring_scan")
        # self.checker = checker
        # self._scan_directories = scan_directories
        self._elog = elog

    def _get_counter_names(self):
        """Get the names of the default counters."""
        return [tc.name for tc in self._default_counters]

    def get_callback_keywords(self):
        kws_all = set([])
        for cb in self.callbacks_start_scan:
            kws = foo_get_kwargs(cb)

            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for cb in self.callbacks_start_step:
            kws = foo_get_kwargs(cb)
            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for cb in self.callbacks_step_counting:
            kws = foo_get_kwargs(cb)
            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for cb in self.callbacks_end_step:
            kws = foo_get_kwargs(cb)
            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for cb in self.callbacks_end_scan:
            kws = foo_get_kwargs(cb)
            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for ctr in self._default_counters:
            if hasattr(ctr, "callbacks_start_scan"):
                for cb in ctr.callbacks_start_scan:
                    kws = foo_get_kwargs(cb)
                    if kws:
                        kws_all.update(set(kws))
                        print(cb.__name__, "has keywords:", kws)
            if hasattr(ctr, "callbacks_start_step"):
                for cb in ctr.callbacks_start_step:
                    kws = foo_get_kwargs(cb)
                    if kws:
                        kws_all.update(set(kws))
                        print(cb.__name__, "has keywords:", kws)
            if hasattr(ctr, "callbacks_step_counting"):
                for cb in ctr.callbacks_step_counting:
                    kws = foo_get_kwargs(cb)
                    if kws:
                        kws_all.update(set(kws))
                        print(cb.__name__, "has keywords:", kws)
            if hasattr(ctr, "callbacks_end_step"):
                for cb in ctr.callbacks_end_step:
                    kws = foo_get_kwargs(cb)
                    if kws:
                        kws_all.update(set(kws))
                        print(cb.__name__, "has keywords:", kws)
            if hasattr(ctr, "callbacks_end_scan"):
                for cb in ctr.callbacks_end_scan:
                    kws = foo_get_kwargs(cb)
                    if kws:
                        kws_all.update(set(kws))
                        print(cb.__name__, "has keywords:", kws)

        return kws_all

    def acquire(
        self,
        N_pulses,
        N_repetitions=1,
        description="",
        counters=[],
        start_immediately=True,
        settling_time=0,
        return_at_end=True,
        step_info=None,
        **kwargs_callbacks,
    ):
        adjustable = DummyAdjustable()
        positions = list(range(N_repetitions))
        values = [[tp] for tp in positions]
        # file_name = self.filename_generator.get_nextrun_filename(file_name)
        # run_number = self.filename_generator.get_nextrun_number()
        # if checker == "default":
        # checker = self.checker
        if not counters:
            counters = self._default_counters
        s = StepScan(
            [adjustable],
            values,
            counters=counters,
            description=description,
            Npulses=N_pulses,
            settling_time=settling_time,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_start_step=self.callbacks_start_step,
            callbacks_end_step=self.callbacks_end_step,
            callbacks_end_scan=self.callbacks_end_scan,
            elog=self._elog,
            return_at_end=return_at_end,
            name="acquiring_scan",
            **kwargs_callbacks,
        )
        self._append(s, name="acquiring_scan", overwrite=True)
        if start_immediately:
            s.scan_all(step_info=step_info)
        # return s

    def ascan(
        self,
        adjustable,
        start_pos,
        end_pos,
        N_intervals,
        N_pulses,
        description="",
        counters=[],
        start_immediately=True,
        return_at_end="timeout",
        settling_time=0,
        step_info=None,
        **kwargs_callbacks,
    ):

        if type(N_intervals) is float:
            print("Interval size defined as float, interpreting as interval size.")
            positions = np.arange(start_pos, end_pos + N_intervals, N_intervals)
        elif type(N_intervals) is int:
            print("Interval size defined as int, interpreting as number of intervals.")
            positions = np.linspace(start_pos, end_pos, N_intervals + 1)

        values = [[tp] for tp in positions]
        if not counters:
            counters = self._default_counters
        s = StepScan(
            [adjustable],
            values,
            counters=counters,
            description=description,
            Npulses=N_pulses,
            settling_time=settling_time,
            return_at_end=return_at_end,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_start_step=self.callbacks_start_step,
            callbacks_end_step=self.callbacks_end_step,
            callbacks_end_scan=self.callbacks_end_scan,
            elog=self._elog,
            name="acquiring_scan",
            **kwargs_callbacks,
        )
        self._append(s, name="acquiring_scan", overwrite=True)
        if start_immediately:
            s.scan_all(step_info=step_info)
        # return s

    def ascan_position_list(
        self,
        adjustable,
        position_list,
        N_pulses,
        description="",
        counters=[],
        # checker="default",
        start_immediately=True,
        settling_time=0,
        step_info=None,
        return_at_end="timeout",
        name="acquiring_scan",
        **kwargs_callbacks,
    ):
        positions = position_list
        values = [[tp] for tp in positions]

        if not counters:
            counters = self._default_counters

        s = StepScan(
            [adjustable],
            values,
            counters=counters,
            description=description,
            Npulses=N_pulses,
            settling_time=settling_time,
            return_at_end=return_at_end,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_start_step=self.callbacks_start_step,
            callbacks_end_step=self.callbacks_end_step,
            callbacks_end_scan=self.callbacks_end_scan,
            elog=self._elog,
            name="acquiring_scan",
            **kwargs_callbacks,
        )
        self._append(s, name="acquiring_scan", overwrite=True)
        if start_immediately:
            s.scan_all(step_info=step_info)
        # return s

    def dscan(
        self,
        adjustable,
        start_pos,
        end_pos,
        N_intervals,
        N_pulses,
        description="",
        counters=[],
        start_immediately=True,
        settling_time=0,
        step_info=None,
        return_at_end="timeout",
        **kwargs_callbacks,
    ):
        """Differential scan, i.e. the adjustable is moved to the start position and then moved in steps of the interval size."""

        if type(N_intervals) is float:
            print("Interval size defined as float, interpreting as interval size.")
            positions = np.arange(start_pos, end_pos + N_intervals, N_intervals)
        elif type(N_intervals) is int:
            print("Interval size defined as int, interpreting as number of intervals.")
            positions = np.linspace(start_pos, end_pos, N_intervals + 1)
        current = adjustable.get_current_value()
        values = [[tp + current] for tp in positions]

        if not counters:
            counters = self._default_counters

        s = StepScan(
            [adjustable],
            values,
            counters,
            Npulses=N_pulses,
            description=description,
            return_at_end=return_at_end,
            settling_time=settling_time,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_start_step=self.callbacks_start_step,
            callbacks_end_step=self.callbacks_end_step,
            callbacks_end_scan=self.callbacks_end_scan,
            elog=self._elog,
            name="acquiring_scan",
            **kwargs_callbacks,
        )
        self._append(s, name="acquiring_scan", overwrite=True, status=True)
        if start_immediately:
            s.scan_all(step_info=step_info)
        # return s

    def snakescan(
        self,
        adjustable_slow,
        step_interval,
        Nrows,
        adjustable_fast,
        interval,
        description="",
        counters=[],
        start_immediately=True,
        settling_time=0,
        step_info=None,
        return_at_end="timeout",
        **kwargs_callbacks,
    ):

        adj_slow_start = adjustable_slow.get_current_value()
        adj_fast_start = adjustable_fast.get_current_value()
        print(
            "Snakescan is relative, starting from here: %s, %s"
            % (adj_slow_start, adj_fast_start)
        )

        start_positions = [
            [adj_slow_start + step_interval * i, adj_fast_start + (i % 2) * interval]
            for i in range(Nrows)
        ]

        def counting_function(scan, **kwargs):
            cv = adjustable_fast.get_current_value()
            print(cv)
            if abs(cv - adj_fast_start) < abs(cv - adj_fast_start - interval):
                print("moving to interval")
                adjustable_fast.set_target_value(adj_fast_start + interval).wait()
            else:
                print("moving back")
                adjustable_fast.set_target_value(adj_fast_start).wait()

        if not counters:
            counters = self._default_counters

        s = StepScan(
            [adjustable_slow, adjustable_fast],
            start_positions,
            counters,
            Npulses=1,
            description=description,
            return_at_end=return_at_end,
            settling_time=settling_time,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_start_step=self.callbacks_start_step,
            callbacks_step_counting=[counting_function],
            callbacks_end_step=self.callbacks_end_step,
            callbacks_end_scan=self.callbacks_end_scan,
            elog=self._elog,
            name="acquiring_scan",
            **kwargs_callbacks,
        )
        self._append(s, name="acquiring_scan", overwrite=True)
        if start_immediately:
            s.scan_all(step_info=step_info)
        # return s

    def a2scan(
        self,
        adjustable0,
        start0_pos,
        end0_pos,
        adjustable1,
        start1_pos,
        end1_pos,
        N_intervals,
        N_pulses,
        file_name="",
        counters=[],
        start_immediately=True,
        step_info=None,
        checker="default",
        return_at_end="question",
        **kwargs_callbacks,
    ):
        positions0 = np.linspace(start0_pos, end0_pos, N_intervals + 1)
        positions1 = np.linspace(start1_pos, end1_pos, N_intervals + 1)
        values = [[tp0, tp1] for tp0, tp1 in zip(positions0, positions1)]
        if not counters:
            counters = self.default_counters.get_current_value()
        if checker == "default":
            checker = self.checker
        s = StepScan(
            [adjustable0, adjustable1],
            values,
            self.counters,
            file_name,
            Npulses=N_pulses,
            basepath=self.data_base_dir,
            scan_info_dir=self.scan_info_dir,
            checker=checker,
            scan_directories=self._scan_directories,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_start_step=self.callbacks_start_step,
            callbacks_end_step=self.callbacks_end_step,
            callbacks_end_scan=self.callbacks_end_scan,
            run_table=self._run_table,
            elog=self._elog,
            return_at_end=return_at_end,
            name="acquiring_scan",
            **kwargs_callbacks,
        )
        self._append(s, name="acquiring_scan", overwrite=True)
        if start_immediately:
            s.scan_all(step_info=step_info)
        # return s

    def meshscan(
        self,
        *adj_specs,
        scanning_order="last_fastest",
        N_pulses=None,
        description="",
        counters=[],
        start_immediately=True,
        return_at_end="timeout",
        settling_time=0,
        step_info=None,
        **kwargs_callbacks,
    ):
        """
        Mesh scan, i.e. a scan in multiple dimensions, where the last adjustable is moved first.
        The scanning order can be changed by setting the `scanning_order` parameter.
        """
        adjustables = []
        positions = []
        for adj_spec in adj_specs:
            adj = adj_spec[0]
            spec = adj_spec[1:]
            if isinstance(adj, Adjustable):
                adjustables.append(adj)
                positions.append(interpret_step_specification(spec))

        shape = [len(tp) for tp in positions]

        if scanning_order == "last_fastest":
            index_plan = list(product(*[range(n) for n in shape]))
        elif scanning_order == "fist_fastst":
            index_plan = [tc[::-1] for tc in product(*[range(n) for n in shape][::-1])]

        values = []
        for ixs in index_plan:
            values.append([tp[ti] for ti, tp in zip(ixs, positions)])

        gridspecs = {
            "shape": shape,
            "positions": positions,
            "index_plan": index_plan,
        }

        if not counters:
            counters = self._default_counters

        s = StepScan(
            adjustables,
            values,
            counters=counters,
            Npulses=N_pulses,
            description=description,
            return_at_end=return_at_end,
            settling_time=settling_time,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_start_step=self.callbacks_start_step,
            callbacks_end_step=self.callbacks_end_step,
            callbacks_end_scan=self.callbacks_end_scan,
            elog=self._elog,
            gridspecs=gridspecs,
            name="acquiring_scan",
            **kwargs_callbacks,
        )

        self._append(s, name="acquiring_scan", overwrite=True)
        if start_immediately:
            s.scan_all(step_info=step_info)


class RunFilenameGenerator:
    def __init__(self, path, prefix="run", Ndigits=4, separator="_", suffix="json"):
        self.separator = separator
        self.prefix = prefix
        self.Ndigits = Ndigits
        self.path = Path(path)
        self.suffix = suffix

    def get_existing_runnumbers(self):
        fl = self.path.glob(
            self.prefix + self.Ndigits * "[0-9]" + self.separator + "*." + self.suffix
        )
        fl = [tf for tf in fl if tf.is_file()]
        runnos = [
            int(tf.name.split(self.prefix)[1].split(self.separator)[0]) for tf in fl
        ]
        return runnos

    def get_run_info_file(self, runno):
        fl = self.path.glob(
            self.prefix
            + f"{runno:0{self.Ndigits}d}"
            + self.separator
            + "*."
            + self.suffix
        )
        fl = [tf for tf in fl if tf.is_file()]
        if len(fl) > 1:
            raise Exception(
                f"Found multiple files in {self.path} with run number {runno}"
            )
        return fl[0]

    def get_nextrun_number(self):
        runnos = self.get_existing_runnumbers()
        if runnos:
            return max(runnos) + 1
        else:
            return 0

    def get_nextrun_filename(self, name):
        runnos = self.get_existing_runnumbers()
        if runnos:
            runno = max(runnos) + 1
        else:
            runno = 0
        return (
            self.prefix
            + "{{:0{:d}d}}".format(self.Ndigits).format(runno)
            + self.separator
            + name
            + "."
            + self.suffix
        )


def interpret_step_specification(spec):
    # normal linear scan
    if len(spec) == 3 and all(isinstance(ta, Number) for ta in spec):
        start_pos, end_pos, N_intervals = spec
        if type(N_intervals) is float:
            print("Interval size defined as float, interpreting as interval size.")
            positions = np.arange(start_pos, N_intervals, end_pos)
        elif type(N_intervals) is int:
            print("Interval size defined as int, interpreting as number of intervals.")
            positions = np.linspace(start_pos, end_pos, N_intervals + 1)
        return positions
    elif len(spec) == 1 and np.iterable(spec[0]):
        if type(spec[0]) is str:
            raise Exception(
                "Step position specification is a string, interpreting as position list!"
            )
        positions = spec[0]
        return positions
    else:
        raise Exception(
            "Step position specification is not understood, should be 3 numbers or a list of positions."
        )
