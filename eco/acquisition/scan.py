from numbers import Number
import os
import json
import numpy as np
from time import sleep, time
import traceback
from pathlib import Path
import colorama

from eco.utilities.utilities import NumpyEncoder, foo_get_kwargs
from ..elements.adjustable import DummyAdjustable
from IPython import get_ipython
from .daq_client import Daq
from eco.elements.assembly import Assembly
from rich.progress import Progress
import inputimeout



inval_chars = [" ", "/"]
ScanNameError = Exception(
    f"invalid character in acquisition name, please use a name without {inval_chars}"
)



class RunList(Assembly):
    def __init__(self, scan_info_dir, name=None):
        super().__init__(name=name)
        self.scan_info_dir = scan_info_dir

    def get_run_list(self): ...


class StepScan:
    def __init__(
        self,
        adjustables,
        values,
        counters,
        description='',
        Npulses=100,
        basepath="",
        # scan_in1fo_dir="",
        settling_time=0,
        # checker=None,
        # scan_directories=False,
        callbacks_start_scan=[],
        callbacks_start_step=[],
        callbacks_end_step=[],
        callbacks_end_scan=[],
        # checker_sleep_time=2,
        return_at_end="question",
        # run_number=None,
        elog=None,
        **kwargs_callbacks,
    ):
        # if np.any([char in fina for char in inval_chars]):
        #     raise ScanNameError
        self.number_of_steps = len(values)
        if not isinstance(Npulses, Number):
            if not len(Npulses) == len(values):
                raise ValueError("steps for Number of pulses and values must match!")
            self.pulses_per_step = Npulses
        else:
            self.pulses_per_step = [Npulses] * len(values)
        self.adjustables = adjustables
        self.values_todo = values
        self.values_done = []
        self.pulses_done = []
        self.readbacks = []
        self.counters = counters
        self.settling_time = settling_time
        self.next_step = 0
        self.description = description
        # self.scan_info_dir = scan_info_dir

        anames = []
        for ta in adjustables:
            try:
                anames.append(ta.alias.get_full_name())
            except:
                anames.append(ta.name)

        self.scan_info = {
            "scan_parameters": {
                "name": anames,
                
                # "Id": [ta.Id if hasattr(ta, "Id") else "noId" for ta in adjustables],
            },
            "scan_description": self.description,
            "scan_values_all": values,
            "scan_values": [],
            "scan_readbacks": [],
            "scan_files": [],
            "scan_step_info": [],
        }
        # self.scan_info_filename = os.path.join(self.scan_info_dir, fina)
        # self._scan_directories = scan_directories
        # self.checker = checker
        self.initial_values = []
        self.return_at_end = return_at_end
        # self._checker_sleep_time = checker_sleep_time
        self._elog = elog
        # self.run_number = run_number
        self.remaining_tasks = []
        self.callbacks_start_scan = callbacks_start_scan
        self.callbacks_start_step = callbacks_start_step
        self.callbacks_end_step = callbacks_end_step
        self.callbacks_end_scan = callbacks_end_scan
        self.callbacks_kwargs = kwargs_callbacks
        # print(f"Scan info in file {self.scan_info_filename}.")
        for adj in self.adjustables:
            tv = adj.get_current_value()
            self.initial_values.append(adj.get_current_value())
            print("Initial value of %s : %g" % (adj.name, tv))

        self.run_callbacks_start_scan()

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
        
        t_step_start = time()
        self.run_callbacks_start_step()

        dt_callbacks_step_start = time()-t_step_start

        if not len(self.values_todo) > 0:
            return False
        self.values_current_step = self.values_todo[0]
        statstr = "Step %d of %d" % (self.next_step + 1, len(self.values_todo) + len(self.values_done))
                

        # fina = self.get_filename(self.nextStep)
        t_adj_start = time()
        ms = []
        for adj, tv in zip(self.adjustables, self.values_current_step):
            ms.append(adj.set_target_value(tv))
        for tm in ms:
            tm.wait()
        dt_adj = time()-t_adj_start

        # settling
        sleep(self.settling_time)

        # counters
        t_ctr_start = time()
        self.readbacks_current_step = []
        adjs_name = []
        adjs_offset = []
        adjs_id = []
        
        statstr += '   '
        for adj in self.adjustables:
            self.readbacks_current_step.append(adj.get_current_value())
            try:
                if hasattr(adj, "name"):
                    adjs_name.append(adj.name)
                    statstr += f"{adj.name} @ {adj.get_current_value():.3f}, "
            except:
                print("acquiring metadata failed")
                pass
        
        

        
        statstr += ' ; Ctrs '
        acs = []
        for ctr in self.counters:
            # if isinstance(ctr, Daq):
            #     acq_pars = {
            #         "scan_info": {
            #             "scan_name": self.description,
            #             "scan_values": values_step,
            #             "scan_readbacks": readbacks_step,
            #             "scan_step_info": {
            #                 "step_number": self.nextStep + 1,
            #             },
            #             "name": [adj.name for adj in self.adjustables],
            #             "expected_total_number_of_steps": len(self.values_todo)
            #             + len(self.values_done),
            #         },
            #         "run_number": self.run_number,
            #         "user_tag": self.fina,
            #     }
            #     acq = ctr.acquire(
            #         file_name=fina, Npulses=self.pulses_per_step[0], acq_pars=acq_pars
            #     )
            # else:
            acq = ctr.acquire(scan=self, Npulses=self.pulses_per_step[0])
            acs.append(acq)
            try:
                if hasattr(ctr, "name"):
                    statstr += f"{ctr.name}, "
            except:
                pass
        filenames = []
        for ta in acs:
            ta.wait()
            filenames.extend(ta.file_names)
        statstr = statstr[:-2] + ' done.'
        print(statstr, end='\n')
        
        dt_ctr = time() - t_ctr_start
        sleep(.003)

        

        ### >>> Callback end
        t_callbacks_step_end = time()
        # if self.checker:
        #     if not self.checker.stop_and_analyze():
        #         return True
        if callable(step_info):
            tstepinfo = step_info()
        else:
            tstepinfo = {}
        self.values_done.append(self.values_todo.pop(0))
        self.pulses_done.append(self.pulses_per_step.pop(0))
        self.readbacks.append(self.readbacks_current_step)

        self.run_callbacks_end_step()
        dt_callbacks_step_end = time() - t_callbacks_step_end
        ### <<<< Callback end

        tstepinfo['times'] = {
            "callbacks_step_start":dt_callbacks_step_start,
            "adjustables" : dt_adj,
            "counters" : dt_ctr,
            "callbacks_step_end": dt_callbacks_step_end,
        }

        self.appendScanInfo(
            self.values_current_step, self.readbacks_current_step, step_files=filenames, step_info=tstepinfo
        )
        # self.writeScanInfo()

        self.next_step += 1

        return True

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
        done = False
        steps_remaining = len(self.values_todo)
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
        for adj, iv in zip(self.adjustables, self.initial_values):
            c.append(adj.set_target_value(iv))
        return c


class Scans:
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
        callbacks_end_step=[],
        callbacks_end_scan=[],
        # run_table=None,
        elog=None,
    ):
        # self._run_table = run_table
        self.callbacks_start_scan = callbacks_start_scan
        self.callbacks_start_step = callbacks_start_step
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
        # self.checker = checker
        # self._scan_directories = scan_directories
        self._elog = elog
    
    def acquire(
        self,
        N_pulses,
        N_repetitions=1,
        description='',
        counters=[],
        start_immediately=True,
        settling_time=0,
        step_info=None,
        return_at_end=True,
        # checker="default",
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
            counters = counters,
            description = description,
            Npulses=N_pulses,
            settling_time=settling_time,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_start_step=self.callbacks_start_step,
            callbacks_end_step=self.callbacks_end_step,
            callbacks_end_scan=self.callbacks_end_scan,
            elog=self._elog,
            return_at_end=return_at_end,
            **kwargs_callbacks,
        )
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
        file_name="",
        counters=[],
        checker="default",
        start_immediately=True,
        step_info=None,
        return_at_end="question",
        settling_time=0,
        **kwargs_callbacks,
    ):
        positions = np.linspace(start_pos, end_pos, N_intervals + 1)
        values = [[tp] for tp in positions]
        file_name = self.filename_generator.get_nextrun_filename(file_name)
        run_number = self.filename_generator.get_nextrun_number()
        if not counters:
            counters = self._default_counters
        if checker == "default":
            checker = self.checker
        s = StepScan(
            [adjustable],
            values,
            counters,
            file_name,
            Npulses=N_pulses,
            basepath=self.data_base_dir,
            scan_info_dir=self.scan_info_dir,
            checker=self.checker,
            settling_time=settling_time,
            scan_directories=self._scan_directories,
            return_at_end=return_at_end,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_start_step=self.callbacks_start_step,
            callbacks_end_step=self.callbacks_end_step,
            callbacks_end_scan=self.callbacks_end_scan,
            run_table=self._run_table,
            elog=self._elog,
            run_number=run_number,
            **kwargs_callbacks,
        )
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
        return_at_end="question",
        **kwargs_callbacks,
    ):
        positions = position_list
        values = [[tp] for tp in positions]
        # description = self.filename_generator.get_nextrun_filename(description)
        # run_number = self.filename_generator.get_nextrun_number()
        if not counters:
            counters = self._default_counters
        # if checker == "default":
        #     checker = self.checker
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
            **kwargs_callbacks,
        )
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
        file_name="",
        counters=[],
        checker="default",
        start_immediately=True,
        settling_time=0,
        step_info=None,
        return_at_end="question",
        **kwargs_callbacks,
    ):
        positions = np.linspace(start_pos, end_pos, N_intervals + 1)
        current = adjustable.get_current_value()
        values = [[tp + current] for tp in positions]
        file_name = self.filename_generator.get_nextrun_filename(file_name)
        run_number = self.filename_generator.get_nextrun_number()
        if not counters:
            counters = self._default_counters
        if checker == "default":
            checker = self.checker
        s = StepScan(
            [adjustable],
            values,
            counters,
            file_name,
            Npulses=N_pulses,
            basepath=self.data_base_dir,
            scan_info_dir=self.scan_info_dir,
            checker=self.checker,
            scan_directories=self._scan_directories,
            return_at_end=return_at_end,
            settling_time=settling_time,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_start_step=self.callbacks_start_step,
            callbacks_end_step=self.callbacks_end_step,
            callbacks_end_scan=self.callbacks_end_scan,
            run_table=self._run_table,
            elog=self._elog,
            run_number=run_number,
            **kwargs_callbacks,
        )
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
            counters = self._default_counters
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
            **kwargs_callbacks,
        )
        if start_immediately:
            s.scan_all(step_info=step_info)
        return s

    

    

    

    # def rscan(self, *args, **kwargs):
    #     print(
    #         "Warning: This is not implemented, should be reflectivity scan. \n for relative/differential scan please use dscan ."
    #     )
    #     # return self.rscan(*args, **kwargs)

    

    # def a2scanList(
    #     self,
    #     adjustable0,
    #     start0_pos,
    #     end0_pos,
    #     adjustable1,
    #     start1_pos,
    #     end1_pos,
    #     N_intervals,
    #     N_pulses,
    #     file_name=None,
    #     counters=[],
    #     checker="default",
    #     start_immediately=True,
    #     step_info=None,
    #     return_at_end="question",
    #     **kwargs_callbacks,
    # ):
    #     positions0 = np.linspace(start0_pos, end0_pos, N_intervals + 1)
    #     positions1 = np.linspace(start1_pos, end1_pos, N_intervals + 1)
    #     # self.prefix
    #     #     + f"{runno:{self.Ndigits}0d}"
    #     #     + self.separator
    #     #     + "*."
    #     #     + self.suffix
    #     values = [[tp0, tp1] for tp0, tp1 in zip(positions0, positions1)]
    #     if not counters:
    #         counters = self._default_counters
    #     if checker == "default":
    #         checker = self.checker
    #     s = Scan(
    #         [adjustable0, adjustable1],
    #         values,
    #         self.counters,
    #         file_name,
    #         Npulses=N_pulses,
    #         basepath=self.data_base_dir,
    #         scan_info_dir=self.scan_info_dir,
    #         checker=self.checker,
    #         scan_directories=self._scan_directories,
    #         return_at_end=return_at_end,
    #         callbacks_start_scan=self.callbacks_start_scan,
    #         callbacks_start_step=self.callbacks_start_step,
    #         callbacks_end_step=self.callbacks_end_step,
    #         callbacks_end_scan=self.callbacks_end_scan,
    #         run_table=self._run_table,
    #         elog=self._elog,
    #         **kwargs_callbacks,
    #     )
    #     if start_immediately:
    #         s.scan_all(step_info=step_info)
    #     return s


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
