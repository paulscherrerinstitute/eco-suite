import time
import weakref
from eco.acquisition.utilities import Acquisition
from eco.elements.protocols import Detector, MonitorableValueUpdate
from collections import namedtuple
from escape import ArrayTimestamps
from matplotlib.animation import FuncAnimation
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime
from escape import DataSet

DEFAULT_STORAGE_DIR = Path("./")

StepTime = namedtuple("StepTime", "start stop")


class CounterValue:
    def __init__(self, *detectors, name="value_counter"):
        self.detectors = []
        self.detector_values = []
        self.monitorables = []
        self.append_detectors(*detectors)
        self.callbacks_start_scan = [self.start_scan]
        self.callbacks_start_step = []
        self.callbacks_step_counting = []
        self.callbacks_end_step = [self.create_arrays, self.plot_arrays]

        def stopani(scan, **kwargs):
            scan.animation.event_source.stop()

        self.callbacks_end_scan = [
            self.create_arrays,
            self.stop_monitoring,
            self.clear_detectors,
            stopani,
            self.store_arrays,
        ]
        self.name = name

    def append_detectors(self, *detectors):
        for detector in detectors:
            if not isinstance(detector, MonitorableValueUpdate) and not isinstance(
                detector, Detector
            ):
                raise TypeError(
                    f"Expected Detector or MonitorableValueUpdate, got {type(detector)}"
                )

            if (
                isinstance(detector, MonitorableValueUpdate)
                and detector not in self.monitorables
            ):
                self.monitorables.append(detector)
            elif isinstance(detector, Detector) and detector not in self.detectors:
                self.detectors.append(detector)

                # self.detectors = detectors

    def start_scan(self, scan=None, detectors=[], **kwargs):
        self.append_detectors(*detectors)
        scan.detector_values = []
        scan.detector_names = self.get_detector_names()
        self.start_monitoring(scan=scan)
        scan.timestamp_intervals = []
        # scan.moniitorable_names = self.get_monitorable_names()

    def start_monitoring(self, scan=None, **kwargs):
        monitors = [tm.set_current_value_callback() for tm in self.monitorables]
        for tm in monitors:
            tm.start()
        if scan is not None:
            scan.monitors = {
                tn: tm for (tn, tm) in zip(self.get_monitorable_names(), monitors)
            }
        else:
            self.monitors = monitors

    def stop_monitoring(self, scan=None, **kwargs):
        if scan is not None:
            for tm in scan.monitors.values():
                tm.stop()
            del scan.monitors
        else:
            for tm in self.monitors:
                tm.stop()
            del self.monitors

    def clear_detectors(self, scan, **kwargs):
        for det in self.detectors:
            tmpref = weakref.ref(det)
            del det
            if tmpref() is not None:
                print(
                    f"Warning: Detector {tmpref().name} could not be deleted properly!"
                )

    def get_monitorable_names(self):
        names = []
        for tm in self.monitorables:
            try:
                names.append(tm.alias.get_full_name())
            except:
                names.append(tm.name)
        return names

    def get_detector_names(self):
        names = []
        for detector in self.detectors:
            try:
                names.append(detector.alias.get_full_name())
            except:
                names.append(detector.name)
        return names

    def get_detector_values(self):
        detector_values = []

        for detector in self.detectors:
            try:
                detector_values.append(detector.get_current_value())
            except Exception as e:
                print(f"Error getting value from {detector.name}: {e}")
                detector_values.append(None)
        return detector_values

    def acquire(self, scan=None, collection_time=1.0, Npulses=None, **kwargs):
        if Npulses is not None:
            collection_time = Npulses
        t_start = time.time()
        acq_pars = {}

        if scan:
            scan_wr = weakref.ref(scan)
            acq_pars = {
                "scan_info": {
                    "scan_name": scan.description(),
                    "scan_values": scan.values_current_step,
                    "scan_readbacks": scan.readbacks_current_step,
                    "name": [adj.name for adj in scan.adjustables],
                    "expected_total_number_of_steps": scan.number_of_steps(),
                    "scan_step_info": {
                        "step_number": scan.next_step + 1,
                    },
                },
            }

        acquisition = Acquisition(
            acquire=None,
            acquisition_kwargs={"Npulses": Npulses},
        )

        def acquire():
            t_tmp = time.time()
            det_val = self.get_detector_values()
            scan_wr().detector_values.append(det_val)
            time.sleep(collection_time - (time.time() - t_tmp))
            t_stop = time.time()
            scan_wr().timestamp_intervals.append(StepTime(t_tmp, t_stop))

        acquisition.set_acquire_foo(acquire, hold=False)

        return acquisition

    def create_arrays(self, scan, **kwargs):
        scan.monitor_scan_arrays = {}
        for monname, mon in scan.monitors.items():
            scan.monitor_scan_arrays[monname] = ArrayTimestamps(
                data=mon.data["values"],
                timestamps=mon.data["timestamps"],
                timestamp_intervals=scan.timestamp_intervals,
                parameter=parameter_from_scan(scan),
                name=monname,
            )

    def plot_arrays(self, scan, **kwargs):
        if not hasattr(scan, "animation"):

            plt.close("CounterValue")

            f, axs = plt.subplots(
                len(scan.monitor_scan_arrays), 1, sharex=True, num="CounterValue"
            )
            scan.fig = f
            if isinstance(axs, plt.Axes):
                axs = [axs]

            def plotdat(n, *args):
                for ma, ax in zip(scan.monitor_scan_arrays.values(), axs):
                    ax.cla()
                    ma.scan.plot(axis=ax, fmt="o-")

            scan.animation = FuncAnimation(
                fig=f, func=plotdat, cache_frame_data=False, interval=500
            )
            plt.show(block=False)
        else:
            scan.fig.tight_layout()
            scan.fig.canvas.draw()
            scan.fig.canvas.flush_events()

    def store_arrays(
        self, scan, filename="auto", directory="auto", elog=None, **kwargs
    ):

        if directory == "auto":
            directory = DEFAULT_STORAGE_DIR
        if callable(directory):
            directory = directory()
        directory = Path(directory)
        if not directory.exists():
            try:
                directory.mkdir(parents=True)
            except:
                print(f"Warning: Could not create directory {directory.resolve()} !")

        if filename == "auto":
            filename = datetime.now().strftime("%Y-%m-%d_%H:%M:%S") + ".esc.h5"

        try:
            d = DataSet.create_with_new_result_file(
                Path(directory) / Path(filename), force_overwrite=False
            )
            names = []
            for k, v in scan.monitor_scan_arrays.items():
                names.append(k)
                d.append(v, name=k)
                v.store()
            d.results_file.close()
            scan.stored_filename = (
                (Path(directory) / Path(filename)).resolve().as_posix()
            )
            print(
                f"Stored filename {(Path(directory) / Path(filename)).resolve().as_posix()}"
            )

            d = DataSet.load_from_result_file(Path(directory) / Path(filename))
            for name in names:
                scan.monitor_scan_arrays[name] = d.datasets[name]
        except:
            print("Could not create dataset file!")

        files = []
        try:
            # import mpld3

            plotfilename = Path(directory) / Path(
                Path(filename).stem.split(".")[0] + ".png"
            )
            scan.fig.savefig(
                plotfilename.as_posix(),
            )
            files.append(plotfilename)
            # print(plotfilename, plotfilename.as_posix())

        except Exception:
            pass

        files.append(Path(directory) / Path(filename))

        if elog:
            if elog == True:
                elog = None
            scan.status_to_elog(
                text=f"### Quick scan: {scan.description()}\nData stored in {filename}.",
                auto_title=False,
                elog=elog,
                files=files,
            )

    # TODO
    def start(self):
        pass

    def stop(self):
        pass


def parameter_from_scan(scan):
    parameter = {
        parname: {"values": [tvs[n] for tvs in scan.scan_info["scan_values"]]}
        for n, parname in enumerate(
            scan.scan_info["scan_parameters"]["name"],
        )
    }
    return parameter


# class Monitor:
#     def __init__(self, pvname, start_immediately=True):
#         self.data = {}
#         self.print = False
#         self.pv = PV(pvname)
#         self.cb_index = None
#         if start_immediately:
#             self.start_callback()

#     def start_callback(self):
#         self.cb_index = self.pv.add_callback(self.append)

#     def stop_callback(self):
#         self.pv.remove_callback(self.cb_index)

#     def append(self, pvname=None, value=None, timestamp=None, **kwargs):
#         if not (pvname in self.data):
#             self.data[pvname] = []
#         ts_local = time()
#         self.data[pvname].append(
#             {"value": value, "timestamp": timestamp, "timestamp_local": ts_local}
#         )
#         if self.print:
#             print(
#                 f"{pvname}:  {value};  time: {timestamp}; time_local: {ts_local}; diff: {ts_local-timestamp}"
#             )
