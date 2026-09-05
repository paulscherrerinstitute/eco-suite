import copy
import time
import weakref
from eco.acquisition.utilities import Acquisition
from eco.elements.protocols import Detector, MonitorableValueUpdate, resolve_lazy
from eco.utilities.datafiles import ensure_dir, ensure_group_writable
from eco.utilities.utilities import is_notebook
from collections import namedtuple
from escape import ArrayTimestamps
from matplotlib.animation import FuncAnimation
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime
from escape import DataSet


DEFAULT_STORAGE_DIR = Path("./")

StepTime = namedtuple("StepTime", "start stop")


def _build_fit_icon(size=24):
    """A small "scatter + fit line" QIcon for the toolbar button, drawn with
    QPainter instead of shipping a bitmap asset."""
    from qtpy import QtCore, QtGui

    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)

    pen = QtGui.QPen(QtGui.QColor("black"))
    pen.setWidthF(max(1.0, size / 10))
    pen.setCapStyle(QtCore.Qt.RoundCap)
    painter.setPen(pen)
    painter.drawLine(
        QtCore.QPointF(0.08 * size, 0.85 * size),
        QtCore.QPointF(0.92 * size, 0.15 * size),
    )

    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor("black"))
    dot_r = size * 0.06
    dots = [
        (0.10, 0.55), (0.14, 0.72), (0.18, 0.40), (0.22, 0.62), (0.26, 0.30),
        (0.30, 0.50), (0.34, 0.68), (0.38, 0.35), (0.42, 0.55), (0.46, 0.25),
        (0.54, 0.62), (0.58, 0.30), (0.62, 0.50), (0.66, 0.20),
        (0.70, 0.40), (0.74, 0.58), (0.78, 0.28), (0.82, 0.45), (0.86, 0.15),
        (0.90, 0.35), (0.20, 0.20), (0.60, 0.65), (0.35, 0.15),
    ]
    for fx, fy in dots:
        painter.drawEllipse(QtCore.QPointF(fx * size, fy * size), dot_r, dot_r)

    painter.end()
    return QtGui.QIcon(pixmap)



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
            self._add_fit_button(scan, axs)
        else:
            scan.fig.tight_layout()
            scan.fig.canvas.draw()
            scan.fig.canvas.flush_events()

    def _add_fit_button(self, scan, axs):
        """Add a UI trigger that opens `escape.fit_gui.AxesFitter` on demand,
        one per plotted axis -- a Qt toolbar icon if the figure has a Qt
        navigation toolbar, else an ipywidgets button if running in a
        Jupyter kernel. Nothing is added otherwise (e.g. inline backend).

        The fit GUI draws its span selector/preview on top of `axs`, which
        the live animation clears every frame (`ax.cla()`), so starting a
        fit also stops that animation -- same effect `stop_animation`
        already has once the scan itself ends.

        The whole thing runs inside a try/except: this is a Qt slot when a
        Qt toolbar is in use, and an unhandled Python exception inside a Qt
        slot aborts the whole interpreter under PySide6/PyQt (confirmed
        directly -- not a defensive-programming guess), so nothing here can
        be allowed to raise back into Qt no matter what goes wrong with the
        animation, the axes, or escape.fit_gui itself.
        """

        def start_fitters(*_):
            try:
                from escape.fit_gui import AxesFitter

                self.stop_animation(scan)
                for ax in axs:
                    if ax.lines:
                        AxesFitter(ax)
            except Exception:
                import traceback

                traceback.print_exc()

        toolbar = getattr(scan.fig.canvas.manager, "toolbar", None)
        if toolbar is not None and toolbar.__class__.__name__.startswith(
            "NavigationToolbar2"
        ):
            action = toolbar.addAction(_build_fit_icon(), "Fit", start_fitters)
            action.setToolTip("Open interactive fit (escape.fit_gui.AxesFitter)")
        elif is_notebook():
            import ipywidgets
            from IPython.display import display

            button = ipywidgets.Button(description="Fit", icon="line-chart")
            button.on_click(start_fitters)
            display(button)

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
