import copy
from datetime import datetime
import functools
from itertools import product
from numbers import Number
import os
import json
import numpy as np
from time import sleep, time
import threading
import traceback
from pathlib import Path
import colorama

from eco.elements.protocols import Adjustable
from eco.utilities.utilities import (
    NumpyEncoder,
    foo_get_kwargs,
    get_eco_name,
    linlog_intervals,
)
from ..elements.adjustable import AdjustableMemory, DummyAdjustable
from IPython import get_ipython
from .daq_client import Daq
from .counter_protocol import Counter
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
        counters: "list[Counter]",
        description="",
        Npulses=100,
        basepath="",
        settling_time=0,
        repetitions=1,
        callbacks_start_scan=[],
        callbacks_start_step=[],
        callbacks_step_counting=[],
        callbacks_end_step=[],
        callbacks_end_scan=[],
        return_at_end="timeout",
        timeout_adjustables=60,
        gridspecs=None,
        # elog=None,
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
        # try:
        #     iter(counters)

        #     self.counters = counters
        # except TypeError:
        #     self.counters = [counters]
        self.counters = counters

        self._append(
            DetectorGet, lambda: self._get_names(self.counters), name="counters_names"
        )
        self._append(
            DetectorGet,
            lambda: self._get_counter_description(),
            name="counter_description",
        )

        self._append(DetectorMemory, len(values), name="number_of_steps")
        try:
            scan_command = get_ipython().user_ns["In"][-1]
        except:
            scan_command = "unknown"
        self._append(DetectorMemory, scan_command, name="scan_command")

        # TODO: make Npulses and pulses_per_step general counter arguments that are eihter interpreted by the counter or that are replaced by counter depedent kwargs.
        Npulses = copy.deepcopy(Npulses)
        values = copy.deepcopy(values)
        if not isinstance(Npulses, Number):
            if not len(Npulses) == len(values):
                raise ValueError("steps for Number of pulses and values must match!")
            self.pulses_per_step = Npulses
        else:
            self.pulses_per_step = [Npulses] * len(values)

        values = list(values)
        self.pulses_per_step = list(self.pulses_per_step)
        if repetitions > 1:
            values = values * repetitions
            self.pulses_per_step = self.pulses_per_step * repetitions
            print(
                f"Repeating scan {repetitions} times. Total steps: {len(values)}, {len(values)/repetitions} per repetition."
            )

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
        self.timeout_adjustables = timeout_adjustables
        # self._elog = elog
        self.remaining_tasks = []
        # scratch space for counters to attach their own scan-scoped state
        # (e.g. Daq's run number/monitors/namespace status) without poking
        # ad hoc attributes directly onto this scan instance -- namespaced by
        # counter name so multiple counters (or concurrently running scans
        # sharing one counter) can't collide. See counter_scratch().
        self.counter_state = {}
        self.callbacks_start_scan = callbacks_start_scan
        self.callbacks_start_step = callbacks_start_step
        self.callbacks_step_counting = callbacks_step_counting
        self.callbacks_end_step = callbacks_end_step
        self.callbacks_end_scan = callbacks_end_scan
        # deliberately NOT threaded through Scans.xxx() as a session-level
        # default like the five lists above -- that's exactly the "three
        # unranked callback sources" pattern that's already hard to reason
        # about (see counter_description's docstring). A counter that wants
        # in (e.g. to keep an elog message in sync, via set_description())
        # just defines `self.callbacks_description_changed = [...]` on
        # itself, the same opt-in `hasattr` mechanism as the others.
        self.callbacks_description_changed = []
        self.callbacks_kwargs = kwargs_callbacks

        self._have_run_callbacks_start_scan = False

        # Pause/resume: a checkpoint boundary between steps, reusing the
        # _values_todo/_values_done split that already exists -- resuming
        # is just clearing the event; scan_all()'s loop picks up exactly
        # where it left off with no extra bookkeeping. See pause()/resume().
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_requested = False

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

    def _get_counter_description(self):
        """Optional per-counter description(s), so anyone inspecting a
        running/finished scan can see what each attached counter actually
        does (`scan.counter_description()`), not just its bare name. A
        counter may expose `description` as a plain string or a
        callable/DetectorGet (matching how `description` is exposed on
        `StepScan`/`Scans` themselves); counters without one are silently
        skipped -- purely optional, nothing to opt into for existing
        counters."""
        parts = []
        for ctr in self.counters:
            desc = getattr(ctr, "description", None)
            if desc is None:
                continue
            if callable(desc):
                try:
                    desc = desc()
                except Exception:
                    continue
            if not desc:
                continue
            name = getattr(ctr, "name", None) or repr(ctr)
            parts.append(f"{name}: {desc}")
        return "; ".join(parts)

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
            tm.wait(timeout=self.timeout_adjustables)
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
                    scan=self, Npulses=self.pulses_per_step[0], **self.callbacks_kwargs
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
                ctr.start(scan=self, **self.callbacks_kwargs)
                try:
                    if hasattr(ctr, "name"):
                        statstr += f"{ctr.name}, "
                except:
                    pass
            self.run_callbacks_step_counting()

            filenames = []
            for ctr in self.counters:
                resp = ctr.stop(scan=self, **self.callbacks_kwargs)
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

        gridspecs = self.grid_specs.get_current_value()
        if gridspecs:
            tstepinfo["grid_index"] = gridspecs["index_plan"][self.next_step]

        tstepinfo["times"] = {
            "callbacks_step_start": dt_callbacks_step_start,
            "adjustables": dt_adj,
            "counters": dt_ctr,
        }
        # Preliminary appending info for end step callbacks
        self.append_scan_info(
            self.values_current_step,
            self.readbacks_current_step,
            step_files=filenames,
            step_info=tstepinfo,
        )
        self.run_callbacks_end_step()
        dt_callbacks_step_end = time() - t_callbacks_step_end
        ### <<<< Callback end

        # hack to update the times
        self.scan_info["scan_step_info"][-1]["times"][
            "callbacks_step_end"
        ] = dt_callbacks_step_end

        if self._current_step_ok:
            self._values_done.append(self._values_todo.pop(0))
            self.pulses_done.append(self.pulses_per_step.pop(0))
            self.readbacks.append(self.readbacks_current_step)
            self.next_step += 1
        else:
            # removing scan step info again if step was not ok
            self.remove_last_scan_info_entry()

        return True

    def counter_scratch(self, counter_name):
        """Per-counter scratch dict on this scan instance (see
        ``self.counter_state``): ``scan.counter_scratch(self.name)[...]``
        instead of ``scan.<ad hoc attribute> = ...``."""
        return self.counter_state.setdefault(counter_name, {})

    def set_scan_parameter(self, key, value):
        """Attach one extra entry to ``scan_info["scan_parameters"]``
        (e.g. a pointer to an uploaded aux file) without reaching into the
        dict directly."""
        self.scan_info["scan_parameters"][key] = value

    # -- pause / resume --------------------------------------------------
    #
    # A checkpoint sits between steps, not inside one: pausing mid-move or
    # mid-acquisition would leave hardware/counters in an undefined state,
    # so pause() only takes effect at the top of the next loop iteration in
    # scan_all(). Resuming needs no bookkeeping of its own -- _values_todo/
    # _values_done (populated by do_next_step() on every completed step
    # already) already say exactly what's left, so "resume" is just
    # un-blocking the same loop.

    def pause(self):
        """Pause before the next step. Takes effect at the next checkpoint,
        not immediately -- a step already in flight always finishes."""
        self._pause_event.clear()

    def resume(self):
        """Undo pause(): scan_all()'s loop continues from wherever
        _values_todo currently starts, nothing is redone."""
        self._pause_event.set()

    def is_paused(self):
        return not self._pause_event.is_set()

    def request_stop(self):
        """Stop before the next step (not mid-step) instead of pausing.
        Wakes the scan first if it was paused, so a stopped-while-paused
        scan actually exits rather than hanging forever waiting to resume."""
        self._stop_requested = True
        self._pause_event.set()

    def snapshot_remaining(self):
        """A JSON-serializable description of the *not-yet-done* part of
        this scan -- enough to reconstruct a StepScan that finishes it
        elsewhere (see ``eco.acquisition.scan_queue`` store/resume).

        Deliberately does not attempt to capture live counter/adjustable
        state (open CA monitors, in-flight acquisitions, ...) -- resuming
        from this re-resolves fresh adjustable/counter objects by name and
        starts a fresh StepScan for the remaining values, it does not
        revive the original StepScan's live state.
        """
        return {
            "adjustable_names": self._get_names(self.adjustables),
            "counter_names": self._get_names(self.counters),
            "values_remaining": copy.deepcopy(self._values_todo),
            "pulses_remaining": copy.deepcopy(self.pulses_per_step),
            "description": self._description,
            "grid_specs": self.grid_specs.get_current_value(),
        }

    # -- live description --------------------------------------------------

    def set_description(self, description):
        """Change the scan's description while it's running (e.g. to add a
        finding mid-scan). Keeps `scan_info` in sync (it's a snapshot taken
        at construction otherwise) and runs
        ``callbacks_description_changed`` on any counter that defines one --
        e.g. to push the new text into an elog message already posted for
        this scan, instead of only ever seeing the text as it was when the
        scan started."""
        old = self._description
        self._description = description
        self.scan_info["scan_description"] = description
        self.run_callbacks_description_changed(old, description)

    def run_callbacks_description_changed(self, old_description, new_description):
        for ctr in self.counters:
            if (
                hasattr(ctr, "callbacks_description_changed")
                and ctr.callbacks_description_changed
            ):
                for tcb in ctr.callbacks_description_changed:
                    tcb(self, old_description, new_description, **self.callbacks_kwargs)

    def append_scan_info(
        self, values_step, readbacks_step, step_files=None, step_info=None
    ):
        self.scan_info["scan_values"].append(values_step)
        self.scan_info["scan_readbacks"].append(readbacks_step)
        self.scan_info["scan_files"].append(step_files)
        self.scan_info["scan_step_info"].append(step_info)

    def remove_last_scan_info_entry(
        self,
    ):
        self.scan_info["scan_values"].pop(-1)
        self.scan_info["scan_readbacks"].pop(-1)
        self.scan_info["scan_files"].pop(-1)
        self.scan_info["scan_step_info"].pop(-1)

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
                    self._pause_event.wait()
                    if self._stop_requested:
                        break
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
        # elog=None,
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
        # self._elog = elog
        self._queues_container = None
        self._augment_docstrings()

    _DOCUMENTED_SCAN_METHODS = (
        "acquire",
        "ascan",
        "ascan_position_list",
        "dscan",
        "snakescan",
        "a2scan",
        "meshscan",
        "scan",
    )

    def _collect_callback_keywords_quiet(self):
        """Same introspection as get_callback_keywords(), without the
        print-per-keyword side effect -- get_callback_keywords() is kept
        as-is (chatty) for its existing interactive use; this is the quiet
        variant _augment_docstrings() needs so constructing a Scans
        instance doesn't spam stdout."""
        kws = set()
        for cb_list in (
            self.callbacks_start_scan,
            self.callbacks_start_step,
            self.callbacks_step_counting,
            self.callbacks_end_step,
            self.callbacks_end_scan,
        ):
            for cb in cb_list:
                k = foo_get_kwargs(cb)
                if k:
                    kws.update(k)
        for ctr in self._default_counters:
            for attr in (
                "callbacks_start_scan",
                "callbacks_start_step",
                "callbacks_step_counting",
                "callbacks_end_step",
                "callbacks_end_scan",
            ):
                for cb in getattr(ctr, attr, None) or []:
                    k = foo_get_kwargs(cb)
                    if k:
                        kws.update(k)
        return kws

    def _augment_docstrings(self):
        """Best-effort: append the extra keyword arguments this instance's
        *default* counters' callbacks actually accept to each scan method's
        docstring -- e.g. ``help(scans.ascan)`` shows them without a
        separate call to get_callback_keywords(). These are the free-form
        ``**kwargs_callbacks`` every scan method already accepts and passes
        through untyped; this doesn't change that, it just makes what a
        *particular* Scans instance's counters happen to read out of that
        bag discoverable instead of implicit.

        Re-run this (safe to call again) whenever default_counters changes
        after construction -- e.g. eco.acquisition.decorators.scannable's
        ``.scans`` property does, since a fresh CounterValue may accept
        different keywords than the one before it.

        A no-op when no default counters are set at all -- nothing to
        discover, and leaving the class's plain method/docstring alone in
        that case means a Scans instance with no default counters costs
        nothing extra (no functools.partial shadowing every method).
        """
        if not self._default_counters:
            return
        try:
            kws = sorted(self._collect_callback_keywords_quiet())
        except Exception:
            return
        counter_names = ", ".join(self._get_counter_names()) or "none"
        extra = "\n\nCounter keywords (from default counters: " + counter_names + "):\n"
        if kws:
            extra += "\n".join(f"    {k}" for k in kws)
        else:
            extra += "    (none discovered)"
        for method_name in self._DOCUMENTED_SCAN_METHODS:
            unbound = getattr(type(self), method_name, None)
            if unbound is None:
                continue
            base_doc = getattr(unbound, "__doc__", None) or ""
            wrapper = functools.partial(unbound, self)
            wrapper.__doc__ = base_doc + extra
            wrapper.__name__ = method_name
            setattr(self, method_name, wrapper)

    @property
    def queues(self):
        """This ``Scans`` instance's own, private named queues --
        ``scans.queues.default``, ``scans.queues.alignment``, etc, each with
        its own worker thread (so two different lanes, or two different
        ``Scans`` instances, run concurrently). See
        ``eco.acquisition.scan_queue.ScanQueueContainer``. Every scan method
        also accepts ``scan_queue=`` to route a single call through here
        without touching ``.queues`` directly -- see ``_route_to_queue``."""
        if self._queues_container is None:
            from eco.acquisition.scan_queue import ScanQueueContainer

            self._queues_container = ScanQueueContainer()
        return self._queues_container

    def _route_to_queue(self, scan_queue, method_name, args, kwargs):
        """Called at the top of every queueable Scans method. Returns a
        QueueItem (the caller should return it immediately) if `scan_queue`
        requests queuing, or None if the caller should just run normally.

        `scan_queue`: None -> run normally (today's behaviour, unchanged).
        True or 1 -> the "default" lane. Any other value -> that named lane
        (auto-created via .queues)."""
        if scan_queue is None:
            return None
        if scan_queue is True or scan_queue == 1:
            scan_queue = "default"
        q = self.queues[scan_queue]
        method = getattr(self, method_name)
        return q.submit(method, *args, **kwargs)

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
        scan_queue=None,
        **kwargs_callbacks,
    ):
        queued = self._route_to_queue(
            scan_queue,
            "acquire",
            (N_pulses,),
            dict(
                N_repetitions=N_repetitions,
                description=description,
                counters=counters,
                start_immediately=start_immediately,
                settling_time=settling_time,
                return_at_end=return_at_end,
                step_info=step_info,
                **kwargs_callbacks,
            ),
        )
        if queued is not None:
            return queued

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
            # elog=self._elog,
            return_at_end=return_at_end,
            name="acquiring_scan",
            **kwargs_callbacks,
        )
        self._append(s, name="acquiring_scan", overwrite=True, delete_old=True)
        if start_immediately:
            s.scan_all(step_info=step_info)
        return s

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
        repetitions=1,
        scan_queue=None,
        **kwargs_callbacks,
    ):
        queued = self._route_to_queue(
            scan_queue,
            "ascan",
            (adjustable, start_pos, end_pos, N_intervals, N_pulses),
            dict(
                description=description,
                counters=counters,
                start_immediately=start_immediately,
                return_at_end=return_at_end,
                settling_time=settling_time,
                step_info=step_info,
                repetitions=repetitions,
                **kwargs_callbacks,
            ),
        )
        if queued is not None:
            return queued

        positions = interpret_step_specification((start_pos, end_pos, N_intervals))

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
            # elog=self._elog,
            name="acquiring_scan",
            repetitions=repetitions,
            **kwargs_callbacks,
        )
        self._append(s, name="acquiring_scan", overwrite=True, delete_old=True)
        if start_immediately:
            s.scan_all(step_info=step_info)
        return s

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
        repetitions=1,
        scan_queue=None,
        **kwargs_callbacks,
    ):
        queued = self._route_to_queue(
            scan_queue,
            "ascan_position_list",
            (adjustable, position_list, N_pulses),
            dict(
                description=description,
                counters=counters,
                start_immediately=start_immediately,
                settling_time=settling_time,
                step_info=step_info,
                return_at_end=return_at_end,
                name=name,
                repetitions=repetitions,
                **kwargs_callbacks,
            ),
        )
        if queued is not None:
            return queued

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
            repetitions=repetitions,
            # elog=self._elog,
            name="acquiring_scan",
            **kwargs_callbacks,
        )
        self._append(s, name="acquiring_scan", overwrite=True, delete_old=True)
        if start_immediately:
            s.scan_all(step_info=step_info)
        return s

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
        repetitions=1,
        scan_queue=None,
        **kwargs_callbacks,
    ):
        """Differential scan, i.e. the adjustable is moved to the start position and then moved in steps of the interval size."""
        queued = self._route_to_queue(
            scan_queue,
            "dscan",
            (adjustable, start_pos, end_pos, N_intervals, N_pulses),
            dict(
                description=description,
                counters=counters,
                start_immediately=start_immediately,
                settling_time=settling_time,
                step_info=step_info,
                return_at_end=return_at_end,
                repetitions=repetitions,
                **kwargs_callbacks,
            ),
        )
        if queued is not None:
            return queued

        positions = interpret_step_specification((start_pos, end_pos, N_intervals))
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
            repetitions=repetitions,
            # elog=self._elog,
            name="acquiring_scan",
            **kwargs_callbacks,
        )
        self._append(
            s, name="acquiring_scan", overwrite=True, status=True, delete_old=True
        )
        if start_immediately:
            s.scan_all(step_info=step_info)
        return s

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
        repetitions=1,
        scan_queue=None,
        **kwargs_callbacks,
    ):
        queued = self._route_to_queue(
            scan_queue,
            "snakescan",
            (adjustable_slow, step_interval, Nrows, adjustable_fast, interval),
            dict(
                description=description,
                counters=counters,
                start_immediately=start_immediately,
                settling_time=settling_time,
                step_info=step_info,
                return_at_end=return_at_end,
                repetitions=repetitions,
                **kwargs_callbacks,
            ),
        )
        if queued is not None:
            return queued

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
            repetitions=repetitions,
            # elog=self._elog,
            name="acquiring_scan",
            **kwargs_callbacks,
        )
        self._append(s, name="acquiring_scan", overwrite=True, delete_old=True)
        if start_immediately:
            s.scan_all(step_info=step_info)
        return s

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
        **kwargs_callbacks,
    ):
        """Two adjustables moved simultaneously over the same number of steps.

        Thin wrapper around the general ``scan`` using a single simultaneous
        (a2scan-like) axis.
        """
        return self.scan(
            [
                (adjustable0, start0_pos, end0_pos, N_intervals),
                (adjustable1, start1_pos, end1_pos, N_intervals),
            ],
            N_pulses=N_pulses,
            **kwargs_callbacks,
        )

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
        repetitions=1,
        scan_queue=None,
        **kwargs_callbacks,
    ):
        """
        Mesh scan, i.e. a scan in multiple dimensions, where the last adjustable is moved first.
        The scanning order can be changed by setting the `scanning_order` parameter.
        """
        queued = self._route_to_queue(
            scan_queue,
            "meshscan",
            adj_specs,
            dict(
                scanning_order=scanning_order,
                N_pulses=N_pulses,
                description=description,
                counters=counters,
                start_immediately=start_immediately,
                return_at_end=return_at_end,
                settling_time=settling_time,
                step_info=step_info,
                repetitions=repetitions,
                **kwargs_callbacks,
            ),
        )
        if queued is not None:
            return queued

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
            "grid_dimension_names": [get_eco_name(adj) for adj in adjustables],
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
            # elog=self._elog,
            gridspecs=gridspecs,
            repetitions=repetitions,
            name="acquiring_scan",
            **kwargs_callbacks,
        )

        self._append(s, name="acquiring_scan", overwrite=True, delete_old=True)
        if start_immediately:
            s.scan_all(step_info=step_info)

        return s

    def scan(
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
        repetitions=1,
        scan_queue=None,
        **kwargs_callbacks,
    ):
        """
        Most general scan, i.e. a scan of multiple adjustable in multiple dimensions, where the last adjustable is moved first.
        The scanning order can be changed by setting the `scanning_order` parameter.

        Each positional ``adj_spec`` is one grid dimension; the cartesian product runs
        across dimensions (mesh). A dimension is either

        - single (mesh) axis: ``(adjustable, *step_spec)`` where ``adj_spec[0]`` is an
          Adjustable, or
        - simultaneous (a2scan-like) axis: a nested list of specs
          ``[(adjA, *step_specA), (adjB, *step_specB), ...]`` whose adjustables move
          together and must share the same number of steps.

        ``*step_spec`` is anything understood by ``interpret_step_specification``.

        ``scan_queue``: None (default) runs synchronously as above and returns the
        StepScan, unchanged from before. Any other value (True/1 for the "default"
        lane, or a name) routes this call through ``self.queues`` instead and returns
        a ``QueueItem`` -- see ``eco.acquisition.scan_queue``.
        """
        queued = self._route_to_queue(
            scan_queue,
            "scan",
            adj_specs,
            dict(
                scanning_order=scanning_order,
                N_pulses=N_pulses,
                description=description,
                counters=counters,
                start_immediately=start_immediately,
                return_at_end=return_at_end,
                settling_time=settling_time,
                step_info=step_info,
                repetitions=repetitions,
                **kwargs_callbacks,
            ),
        )
        if queued is not None:
            return queued

        adjustables = []
        positions = []
        for adj_spec in adj_specs:
            # simultaneous (a2scan-like) axis: nested list of specs, one per co-moving adjustable
            if not isinstance(adj_spec[0], Adjustable):
                s_adjustables = [ts[0] for ts in adj_spec]
                s_positions = [interpret_step_specification(ts[1:]) for ts in adj_spec]
                if len(set(map(len, s_positions))) != 1:
                    raise Exception(
                        "Simultaneous scan adjustables must have the same number of step positions!"
                    )
                adjustables.append(s_adjustables)
                positions.append(np.asarray(s_positions).T)  # shape (Nsteps, Nadj)

            # single mesh axis
            else:
                adjustables.append(adj_spec[0])
                positions.append(interpret_step_specification(adj_spec[1:]))

        shape = [len(tp) for tp in positions]

        if scanning_order == "last_fastest":
            index_plan = list(product(*[range(n) for n in shape]))
        elif scanning_order == "first_fastest":
            index_plan = [tc[::-1] for tc in product(*[range(n) for n in shape][::-1])]

        # StepScan is flat: one inner list per step, holding one target per flat
        # adjustable. A simultaneous axis contributes several values to each step.
        values = []
        for ixs in index_plan:
            step_values = []
            for ti, tp in zip(ixs, positions):
                tpos = tp[ti]
                if np.iterable(tpos):  # simultaneous vector
                    step_values.extend(np.asarray(tpos).tolist())
                else:
                    step_values.append(tpos)
            values.append(step_values)

        # per-dimension names, nested where a dimension holds co-moving adjustables
        grid_dimension_names = [
            [get_eco_name(a) for a in ta] if isinstance(ta, list) else get_eco_name(ta)
            for ta in adjustables
        ]

        gridspecs = {
            "shape": shape,
            "positions": positions,
            "index_plan": index_plan,
            "grid_dimension_names": grid_dimension_names,
        }

        # flat adjustable list matching the per-step value order
        adjustables_flat = []
        for ta in adjustables:
            if isinstance(ta, list):
                adjustables_flat.extend(ta)
            else:
                adjustables_flat.append(ta)

        if not counters:
            counters = self._default_counters

        s = StepScan(
            adjustables_flat,
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
            # elog=self._elog,
            gridspecs=gridspecs,
            repetitions=repetitions,
            name="acquiring_scan",
            **kwargs_callbacks,
        )

        self._append(s, name="acquiring_scan", overwrite=True, delete_old=True)
        if start_immediately:
            s.scan_all(step_info=step_info)

        return s

    def flyscan_test(
        self,
        adjustable,
        start_pos,
        end_pos,
        counters=[],
        description="",
        settling_time=0.1,
        plot=True,
    ):
        """EXPERIMENTAL continuous ("fly") scan -- a proof of concept, not
        integrated into StepScan (see the field-survey artifact's §4.2/§5
        and ESRF BLISS's AcquisitionChain, whose continuous-scan support
        being just a different trigger node in the *same* engine rather
        than a separate scan-type class is the direction this points
        towards). Moves `adjustable` to `start_pos`, starts continuous
        monitoring on one counter, moves once, continuously, to `end_pos`
        (no step/settle/count loop), stops monitoring, and plots what
        streamed in during the move.

        Only supports a single ``CounterValue``-shaped counter (one that
        has ``start_monitoring()``/``stop_monitoring()``, exactly the
        machinery ``eco.acquisition.counters.CounterValue`` already uses
        for its own step scans -- confirmed live this session via a
        `scans.dscan()`'s `scan.monitor_scan_arrays`). Multiple counters
        would need `start_monitoring`'s scan.monitors bookkeeping to merge
        instead of overwrite, which it doesn't do today -- left for when
        this graduates past "test implementation."
        """
        if not counters:
            counters = self._default_counters
        if not counters:
            raise ValueError("flyscan_test needs at least one counter")
        counter = counters[0]
        if len(counters) > 1:
            print(
                f"flyscan_test only supports one counter for now, using {getattr(counter, 'name', counter)!r}"
            )
        if not (hasattr(counter, "start_monitoring") and hasattr(counter, "stop_monitoring")):
            raise TypeError(
                f"{getattr(counter, 'name', counter)!r} has no start_monitoring()/"
                "stop_monitoring() -- flyscan_test only supports CounterValue-shaped "
                "counters today"
            )

        class _FlyScanContext:
            pass

        ctx = _FlyScanContext()
        ctx.adjustables = [adjustable]
        ctx.timestamp_intervals = []
        ctx._description = description
        adj_name = getattr(adjustable, "name", repr(adjustable))
        ctx.scan_info = {"scan_parameters": {"name": [adj_name]}}

        print(f"flyscan_test: moving {adj_name} to start position {start_pos}")
        adjustable.set_target_value(start_pos).wait()
        sleep(settling_time)

        counter.start_monitoring(scan=ctx)
        t0 = time()
        print(f"flyscan_test: continuous move {adj_name}: {start_pos} -> {end_pos}")
        adjustable.set_target_value(end_pos).wait()
        t1 = time()
        ctx.timestamp_intervals.append((t0, t1))
        counter.stop_monitoring(scan=ctx)

        from escape import ArrayTimestamps

        # escape's ArrayTimestamps wants exactly one parameter value per
        # timestamp_interval -- there's one interval here (the whole
        # continuous move), so one value, not [start_pos, end_pos]. The
        # position is continuously varying *within* that interval; using
        # start_pos as its single nominal value is a placeholder -- per-
        # pulse interpolated position (from the move's own timing) would
        # be the real fix, left for when this is more than a test.
        parameter = {adj_name: {"values": [start_pos]}}
        ctx.monitor_scan_arrays = {
            monname: ArrayTimestamps(
                data=copy.copy(mon.data["values"]),
                timestamps=mon.data["timestamps"],
                timestamp_intervals=ctx.timestamp_intervals,
                parameter=parameter,
                name=monname,
            )
            for monname, mon in ctx.monitors.items()
        }

        if plot:
            import matplotlib.pyplot as plt

            plt.close("flyscan_test")
            names = list(ctx.monitor_scan_arrays.keys())
            fig, axs = plt.subplots(len(names), 1, sharex=True, num="flyscan_test")
            if len(names) == 1:
                axs = [axs]
            for ax, monname in zip(axs, names):
                arr = ctx.monitor_scan_arrays[monname]
                ax.plot(arr.timestamps, arr.data, ".-")
                ax.set_ylabel(monname)
            axs[-1].set_xlabel("time (s)")
            plt.show(block=False)
            ctx.fig = fig

        return ctx


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
    """Create a list of step positions from a specification.

    Args:
        spec (tuple/list): interable of different format:
            - 3 numbers: start, end, N_intervals or interval size (int or float)
            - 1 iterable of positions
            - anchor positions with linear or logarithmic filled spaces.
              i.e. odd number of elements, where every second element is an iterable ('lin',<float size or int intervals>), interpreted as lin-log intervals

    Returns:
        array: array of positions
    """
    # normal linear scan
    if len(spec) == 3 and all(isinstance(ta, Number) for ta in spec):
        start_pos, end_pos, N_intervals = spec
        if type(N_intervals) is float:
            print("Interval size defined as float, interpreting as interval size.")
            positions = np.arange(start_pos, end_pos + N_intervals, N_intervals)
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
    elif len(spec) % 2 and all([np.iterable(ts) for ts in spec[1::2]]):
        return linlog_intervals(*spec)
    else:
        raise Exception(
            "Step position specification is not understood, should be 3 numbers or a list of positions."
        )
