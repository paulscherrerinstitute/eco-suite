import copy
import time
import weakref
from eco.acquisition.utilities import Acquisition
from eco.elements.protocols import Detector, MonitorableValueUpdate, resolve_lazy
from eco.utilities.datafiles import ensure_dir, ensure_group_writable
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

        # Stopping the animation is the one step here that must never be
        # skipped -- a still-running FuncAnimation timer keeps firing after
        # the scan is "done" and everything else has been torn down
        # (monitors stopped, detectors cleared, the next scan's figure
        # having reused this one's "CounterValue" label via plt.close()),
        # which is how a lingering animation turns into a crash rather than
        # just a stale plot. It's listed first so an exception anywhere
        # else in this list (create_arrays, store_arrays, ...) can't leave
        # it un-run; stop_animation itself never raises (see its docstring).
        self.callbacks_end_scan = [
            self.stop_animation,
            self.create_arrays,
            self.stop_monitoring,
            self.clear_detectors,
            self.store_arrays,
        ]
        self.name = name

    def stop_animation(self, scan=None, **kwargs):
        """Stop `scan.animation`'s timer for good, without ever raising.

        Safe to call more than once and safe to call after the figure has
        already been closed: matplotlib's own `Animation` disconnects
        itself on the figure's close_event and sets `event_source = None`
        (see `matplotlib.animation.Animation._stop`), so a plain
        `scan.animation.event_source.stop()` raises `AttributeError` in
        that case. That used to be a real problem here in two ways: as the
        last-but-one entry in `callbacks_end_scan`, that AttributeError
        skipped `store_arrays` (the run's data silently never got saved);
        and when the same unguarded call ran from the Qt "Fit" toolbar
        button's click handler, an unhandled Python exception inside a Qt
        slot aborts the whole interpreter under PySide6/PyQt -- confirmed
        directly (`Aborted (core dumped)`), not a theoretical concern.
        """
        animation = getattr(scan, "animation", None)
        if animation is None:
            return
        try:
            if animation.event_source is not None:
                animation.event_source.stop()
        except Exception:
            pass

    def append_detectors(self, *detectors):
        for detector in detectors:
            # A detector taken straight off a namespace (`bernina.some_det`)
            # can still be an unresolved lazy proxy, which fails every
            # structural protocol check below and would be rejected as "not a
            # Detector" -- see eco.elements.protocols.resolve_lazy. Resolve
            # once here; it is about to be used for real anyway.
            detector = resolve_lazy(detector)
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
        if hasattr(scan, "monitors"):
            del scan.monitors
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
            # del scan.monitors
        else:
            for tm in self.monitors:
                tm.stop()
            # del self.monitors

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
            # TODO: why is this necessary and not assigned?
            # acq_pars = {
            #     "scan_info": {
            #         "scan_name": scan.description(),
            #         "scan_values": scan.values_current_step,
            #         "scan_readbacks": scan.readbacks_current_step,
            #         "name": [adj.name for adj in scan.adjustables],
            #         "expected_total_number_of_steps": scan.number_of_steps(),
            #         "scan_step_info": {
            #             "step_number": scan.next_step + 1,
            #         },
            #     },
            # }

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
            tdata = copy.copy(mon.data["values"]) # needed for array data, apparently 
            scan.monitor_scan_arrays[monname] = ArrayTimestamps(
                data=tdata,
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
            scan.axs = axs

            def plotdat(n, *args):
                for ma, ax in zip(scan.monitor_scan_arrays.values(), axs):
                    ax.cla()
                    ma.scan.plot(axis=ax, fmt="o-")

            scan.animation = FuncAnimation(
                fig=f, func=plotdat, cache_frame_data=False, interval=500
            )
            plt.show(block=False)
            self._attach_escape_buttons(scan)
        else:
            scan.fig.tight_layout()
            scan.fig.canvas.draw()
            scan.fig.canvas.flush_events()

    def _attach_escape_buttons(self, scan):
        """Attach escape's own Fit / Peak / Peak-params toolbar buttons
        (`escape.plot_utilities.attach_escape_buttons`) to this scan's live
        figure, rather than this class hand-rolling its own Fit-only
        button -- so every interactive escape figure (a static one, an
        `escape.stream` live plot, or this one) offers the same tools the
        same way, including the peak/step analysis (`find_peak`) that only
        the bs-stream-backed live plots had until now.

        `before_click=self.stop_animation` (bound to `scan`) -- any of
        these buttons draws persistent overlay/panel artists directly onto
        `scan.axs`, which the live `FuncAnimation` (`plotdat`, above)
        clears every frame (`ax.cla()`); without stopping it first, the
        next frame just wipes the overlay/panel straight back off. Same
        effect `stop_animation` already has once the scan itself ends --
        see its own docstring.

        Import of `escape.plot_utilities` (and so ipywidgets/dask/IPython)
        is deferred to here rather than done at module level, matching
        `escape.stream`'s own lazy-import pattern for the same reason; any
        failure is swallowed with a printed note, matching
        `attach_escape_buttons`'s own never-break-the-caller contract.
        """
        try:
            from escape.plot_utilities import attach_escape_buttons

            attach_escape_buttons(scan.fig, before_click=lambda: self.stop_animation(scan))
        except Exception as exc:
            print(f"{self.name}: couldn't attach escape's Fit/Peak buttons: {exc}")

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
                # group-writable at every level, so the rest of the pgroup can
                # add to this run's data -- see eco.utilities.datafiles
                ensure_dir(directory)
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
            # the h5 comes from escape's DataSet, i.e. created with the umask
            # (0o644); the pgroup has to be able to rewrite it too
            ensure_group_writable(Path(directory) / Path(filename))
            scan.stored_filename = (
                (Path(directory) / Path(filename)).resolve().as_posix()
            )
            print(
                f"Stored filename {(Path(directory) / Path(filename)).resolve().as_posix()}"
            )

            d = DataSet.load_from_result_file(Path(directory) / Path(filename))
            for name in names:
                scan.monitor_scan_arrays[name] = d.datasets[name]
            d.results_file.close()
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
            ensure_group_writable(plotfilename)  # savefig also uses the umask
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
