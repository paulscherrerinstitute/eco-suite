from numbers import Number
import os
import json
import numpy as np
from time import sleep, time
import traceback
from pathlib import Path
import colorama
from ..elements.adjustable import DummyAdjustable
from IPython import get_ipython
from .daq_client import Daq
from eco.elements.assembly import Assembly
from rich.progress import Progress


inval_chars = [" ", "/"]
ScanNameError = Exception(
    f"invalid character in acquisition name, please use a name without {inval_chars}"
)


class NumpyEncoder(json.JSONEncoder):
    """Special json encoder for numpy types"""

    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return json.JSONEncoder.default(self, obj)


class RunList(Assembly):
    def __init__(self, scan_info_dir, name=None):
        super().__init__(name=name)
        self.scan_info_dir = scan_info_dir

    def get_run_list(self):
        ...


class Scan:
    def __init__(
        self,
        adjustables,
        values,
        counterCallers,
        fina,
        Npulses=100,
        basepath="",
        scan_info_dir="",
        settling_time=0,
        checker=None,
        scan_directories=False,
        callbackStartStep=None,
        callbacks_start_scan=[],
        callbacks_end_scan=[],
        checker_sleep_time=2,
        return_at_end="question",
        run_table=None,
        run_number=None,
        elog=None,
        **kwargs_callbacks,
    ):
        if np.any([char in fina for char in inval_chars]):
            raise ScanNameError
        self.Nsteps = len(values)
        self._run_table = run_table
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
        self.counterCallers = counterCallers
        self.settling_time = settling_time
        self.fina = fina
        self.nextStep = 0
        self.basepath = basepath
        self.scan_info_dir = scan_info_dir
        self.scan_info = {
            "scan_parameters": {
                "name": [ta.name for ta in adjustables],
                "Id": [ta.Id if hasattr(ta, "Id") else "noId" for ta in adjustables],
            },
            "scan_values_all": values,
            "scan_values": [],
            "scan_readbacks": [],
            "scan_files": [],
            "scan_step_info": [],
        }
        self.scan_info_filename = os.path.join(self.scan_info_dir, fina)
        self._scan_directories = scan_directories
        self.checker = checker
        self.initial_values = []
        self.return_at_end = return_at_end
        self._checker_sleep_time = checker_sleep_time
        self._elog = elog
        self.run_number = run_number
        self.remaining_tasks = []
        self.callbacks_end_scan = callbacks_end_scan
        self.callbacks_kwargs = kwargs_callbacks
        print(f"Scan info in file {self.scan_info_filename}.")
        for adj in self.adjustables:
            tv = adj.get_current_value()
            self.initial_values.append(adj.get_current_value())
            print("Initial value of %s : %g" % (adj.name, tv))

        if callbacks_start_scan:
            for caller in callbacks_start_scan:
                caller(self, **self.callbacks_kwargs)

    def get_filename(self, stepNo, Ndigits=4):
        fina = os.path.join(self.basepath, Path(self.fina).stem)
        if self._scan_directories:
            fina = os.path.join(fina, self.fina)
        fina += "_step%04d" % stepNo
        return fina

    def doNextStep(self, step_info=None, verbose=True):
        # for call in self.callbacks_start_step:
        # call()
        if self.checker:
            first_check = time()
            checker_unhappy = False
            while not self.checker.check_now():
                print(
                    colorama.Fore.RED
                    + f"Condition checker is not happy, waiting for OK conditions since {time()-first_check:5.1f} seconds."
                    + colorama.Fore.RESET,
                    # end="\r",
                )
                sleep(self._checker_sleep_time)
                checker_unhappy = True
            if checker_unhappy:
                print(
                    colorama.Fore.RED
                    + f"Condition checker was not happy and waiting for {time()-first_check:5.1f} seconds."
                    + colorama.Fore.RESET
                )
            self.checker.clear_and_start_counting()

        if not len(self.values_todo) > 0:
            return False
        values_step = self.values_todo[0]
        if verbose:
            print(
                "Starting scan step %d of %d"
                % (self.nextStep + 1, len(self.values_todo) + len(self.values_done))
            )
        ms = []
        fina = self.get_filename(self.nextStep)
        for adj, tv in zip(self.adjustables, values_step):
            ms.append(adj.set_target_value(tv))
        for tm in ms:
            tm.wait()

        # settling
        sleep(self.settling_time)

        readbacks_step = []
        adjs_name = []
        adjs_offset = []
        adjs_id = []

        for adj in self.adjustables:
            readbacks_step.append(adj.get_current_value())
            try:
                if hasattr(adj, "name"):
                    adjs_name.append(adj.name)
            except:
                print("acquiring metadata failed")
                pass

        if verbose:
            print("Moved variables, now starting acquisition")
        acs = []
        for ctr in self.counterCallers:
            if isinstance(ctr, Daq):
                acq_pars = {
                    "scan_info": {
                        "scan_name": Path(fina).stem,
                        "scan_values": values_step,
                        "scan_readbacks": readbacks_step,
                        "scan_step_info": {
                            "step_number": self.nextStep + 1,
                        },
                        "name": [adj.name for adj in self.adjustables],
                        "expected_total_number_of_steps": len(self.values_todo)
                        + len(self.values_done),
                    },
                    "run_number": self.run_number,
                    "user_tag": self.fina,
                }
                acq = ctr.acquire(
                    file_name=fina, Npulses=self.pulses_per_step[0], acq_pars=acq_pars
                )
            else:
                acq = ctr.acquire(file_name=fina, Npulses=self.pulses_per_step[0])
            acs.append(acq)
        filenames = []
        for ta in acs:
            ta.wait()
            filenames.extend(ta.file_names)
        if verbose:
            print("Done with acquisition")

        if self.checker:
            if not self.checker.stop_and_analyze():
                return True
        if callable(step_info):
            tstepinfo = step_info()
        else:
            tstepinfo = step_info
        self.values_done.append(self.values_todo.pop(0))
        self.pulses_done.append(self.pulses_per_step.pop(0))
        self.readbacks.append(readbacks_step)
        self.appendScanInfo(
            values_step, readbacks_step, step_files=filenames, step_info=tstepinfo
        )
        self.writeScanInfo()
        self.nextStep += 1
        return True

    def appendScanInfo(
        self, values_step, readbacks_step, step_files=None, step_info=None
    ):
        self.scan_info["scan_values"].append(values_step)
        self.scan_info["scan_readbacks"].append(readbacks_step)
        self.scan_info["scan_files"].append(step_files)
        self.scan_info["scan_step_info"].append(step_info)

    def writeScanInfo(self):
        if not Path(self.scan_info_filename).exists():
            with open(self.scan_info_filename, "w") as f:
                json.dump(self.scan_info, f, sort_keys=True, cls=NumpyEncoder)
        else:
            with open(self.scan_info_filename, "r+") as f:
                f.seek(0)
                json.dump(self.scan_info, f, sort_keys=True, cls=NumpyEncoder)
                f.truncate()

    def scanAll(self, step_info=None):
        done = False
        steps_remaining = len(self.values_todo)
        with Progress() as self._progress:
            pr_task = self._progress.add_task(
                "[green]Scanning...", total=steps_remaining
            )
            try:
                while not done:
                    done = not self.doNextStep(step_info=step_info)
                    self._progress.update(pr_task, advance=1)
            except:
                tb = traceback.format_exc()
            else:
                tb = "Ended all steps without interruption."
            finally:
                self._progress.stop()
                print(tb)

                if self.callbacks_end_scan:
                    for caller in self.callbacks_end_scan:
                        caller(self, **self.callbacks_kwargs)
                if self.return_at_end == "question":
                    if input("Change back to initial values? (y/n)")[0] == "y":
                        chs = self.changeToInitialValues()
                        print("Changing back to value(s) before scan.")
                        for ch in chs:
                            ch.wait()
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
    def __init__(
        self,
        data_base_dir="",
        scan_info_dir="",
        default_counters=[],
        checker=None,
        scan_directories=False,
        callbacks_start_scan=[],
        callbacks_end_scan=[],
        run_table=None,
        elog=None,
    ):
        self._run_table = run_table
        self.callbacks_start_scan = callbacks_start_scan
        self.callbacks_end_scan = callbacks_end_scan
        self.data_base_dir = data_base_dir
        scan_info_dir = Path(scan_info_dir)
        if not scan_info_dir.exists():
            print(
                f"Path {scan_info_dir.absolute().as_posix()} does not exist, will try to create it..."
            )
            scan_info_dir.mkdir(parents=True)
            print(f"Tried to create {scan_info_dir.absolute().as_posix()}")
            scan_info_dir.chmod(0o775)
            print(f"Tried to change permissions to 775")

        for counter in default_counters:
            if not (counter._default_file_path is None):
                data_dir = Path(counter._default_file_path + self.data_base_dir)
                if not data_dir.exists():
                    print(
                        f"Path {data_dir.absolute().as_posix()} does not exist, will try to create it..."
                    )
                    data_dir.mkdir(parents=True)
                    print(f"Tried to create {data_dir.absolute().as_posix()}")
                    data_dir.chmod(0o775)
                    print(f"Tried to change permissions to 775")

        self.scan_info_dir = scan_info_dir
        self.filename_generator = RunFilenameGenerator(self.scan_info_dir)
        self._default_counters = default_counters
        self.checker = checker
        self._scan_directories = scan_directories
        self._elog = elog

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
        file_name=None,
        counters=[],
        start_immediately=True,
        step_info=None,
        return_at_end="question",
        **kwargs_callbacks,
    ):
        positions0 = np.linspace(start0_pos, end0_pos, N_intervals + 1)
        positions1 = np.linspace(start1_pos, end1_pos, N_intervals + 1)
        values = [[tp0, tp1] for tp0, tp1 in zip(positions0, positions1)]
        if not counters:
            counters = self._default_counters
        s = Scan(
            [adjustable0, adjustable1],
            values,
            self.counters,
            file_name,
            Npulses=N_pulses,
            basepath=self.data_base_dir,
            scan_info_dir=self.scan_info_dir,
            checker=self.checker,
            scan_directories=self._scan_directories,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_end_scan=self.callbacks_end_scan,
            run_table=self._run_table,
            elog=self._elog,
            return_at_end=return_at_end,
            **kwargs_callbacks,
        )
        if start_immediately:
            s.scanAll(step_info=step_info)
        return s

    def acquire(
        self,
        N_pulses,
        N_repetitions=1,
        file_name="",
        counters=[],
        start_immediately=True,
        settling_time=0,
        step_info=None,
        return_at_end=True,
        **kwargs_callbacks,
    ):

        adjustable = DummyAdjustable()

        positions = list(range(N_repetitions))
        values = [[tp] for tp in positions]
        file_name = self.filename_generator.get_nextrun_filename(file_name)
        run_number = self.filename_generator.get_nextrun_number()
        if not counters:
            counters = self._default_counters
        s = Scan(
            [adjustable],
            values,
            counters,
            file_name,
            Npulses=N_pulses,
            basepath=self.data_base_dir,
            scan_info_dir=self.scan_info_dir,
            settling_time=settling_time,
            checker=self.checker,
            scan_directories=self._scan_directories,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_end_scan=self.callbacks_end_scan,
            run_table=self._run_table,
            elog=self._elog,
            run_number=run_number,
            return_at_end=return_at_end,
            **kwargs_callbacks,
        )
        if start_immediately:
            s.scanAll(step_info=step_info)
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
        s = Scan(
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
            callbacks_end_scan=self.callbacks_end_scan,
            run_table=self._run_table,
            elog=self._elog,
            run_number=run_number,
            **kwargs_callbacks,
        )
        if start_immediately:
            s.scanAll(step_info=step_info)
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
        s = Scan(
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
            callbacks_end_scan=self.callbacks_end_scan,
            run_table=self._run_table,
            elog=self._elog,
            run_number=run_number,
            **kwargs_callbacks,
        )
        if start_immediately:
            s.scanAll(step_info=step_info)
        return s

    def rscan(self, *args, **kwargs):
        print(
            "Warning: This is not implemented, should be reflectivity scan. \n for relative/differential scan please use dscan ."
        )
        # return self.rscan(*args, **kwargs)

    def ascanList(
        self,
        adjustable,
        posList,
        N_pulses,
        file_name=None,
        counters=[],
        start_immediately=True,
        settling_time=0,
        step_info=None,
        return_at_end="question",
        **kwargs_callbacks,
    ):
        positions = posList
        values = [[tp] for tp in positions]
        file_name = self.filename_generator.get_nextrun_filename(file_name)
        run_number = self.filename_generator.get_nextrun_number()
        if not counters:
            counters = self._default_counters
        s = Scan(
            [adjustable],
            values,
            counters,
            file_name,
            Npulses=N_pulses,
            basepath=self.data_base_dir,
            scan_info_dir=self.scan_info_dir,
            checker=self.checker,
            scan_directories=self._scan_directories,
            settling_time=settling_time,
            return_at_end=return_at_end,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_end_scan=self.callbacks_end_scan,
            run_table=self._run_table,
            elog=self._elog,
            run_number=run_number,
            **kwargs_callbacks,
        )
        if start_immediately:
            s.scanAll(step_info=step_info)
        return s

    def a2scanList(
        self,
        adjustable0,
        start0_pos,
        end0_pos,
        adjustable1,
        start1_pos,
        end1_pos,
        N_intervals,
        N_pulses,
        file_name=None,
        counters=[],
        start_immediately=True,
        step_info=None,
        return_at_end="question",
        **kwargs_callbacks,
    ):
        positions0 = np.linspace(start0_pos, end0_pos, N_intervals + 1)
        positions1 = np.linspace(start1_pos, end1_pos, N_intervals + 1)
        # self.prefix
        #     + f"{runno:{self.Ndigits}0d}"
        #     + self.separator
        #     + "*."
        #     + self.suffix
        values = [[tp0, tp1] for tp0, tp1 in zip(positions0, positions1)]
        if not counters:
            counters = self._default_counters
        s = Scan(
            [adjustable0, adjustable1],
            values,
            self.counters,
            file_name,
            Npulses=N_pulses,
            basepath=self.data_base_dir,
            scan_info_dir=self.scan_info_dir,
            checker=self.checker,
            scan_directories=self._scan_directories,
            return_at_end=return_at_end,
            callbacks_start_scan=self.callbacks_start_scan,
            callbacks_end_scan=self.callbacks_end_scan,
            run_table=self._run_table,
            elog=self._elog,
            **kwargs_callbacks,
        )
        if start_immediately:
            s.scanAll(step_info=step_info)
        return s


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
