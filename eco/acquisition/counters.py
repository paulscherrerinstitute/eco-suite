import inspect
import time
import traceback
import weakref
import numpy as np
from eco.acquisition.utilities import Acquisition, as_numeric_array, mean_sem
from eco.acquisition import peak_analysis as _peak
from eco.elements.protocols import Detector, MonitorableValueUpdate, resolve_lazy
from eco.utilities.datafiles import ensure_dir, ensure_group_writable
from eco.utilities import mainthread
from collections import namedtuple
from types import SimpleNamespace
from escape import ArrayTimestamps
from matplotlib.animation import FuncAnimation
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime
from escape import DataSet


DEFAULT_STORAGE_DIR = Path("./")

StepTime = namedtuple("StepTime", "start stop")


class CounterValue:
    """Counter for the plain (EPICS-monitor / polled) detectors.

    peak_analysis : True, False or dict, default True
        Run escape's `find_peak` on the per-step medians of a 1-D scan after
        every step (and once more at the end) and keep the result in
        `scan.peak_analysis` -- see `eco.acquisition.peak_analysis` for the
        fields and for what is not analysed (n-d scans, several channels).
        A dict passes `n_bg`, `bg_model`, `fixed_offset` and/or `mode` on to
        `find_peak`.
    """

    def __init__(self, *detectors, name="value_counter", peak_analysis=True):
        self.detectors = []
        self.detector_values = []
        self.monitorables = []
        self.append_detectors(*detectors)
        self._peak_settings = _peak.parse_settings(peak_analysis)
        self.callbacks_start_scan = [self.start_scan]
        self.callbacks_start_step = []
        self.callbacks_step_counting = []
        # before plot_arrays: it is the plot that shows the same data, and
        # the analysis must not depend on a figure existing (headless, or
        # its animation stopped by a toolbar button)
        self.callbacks_end_step = [
            self.create_arrays,
            self.update_peak_analysis,
            self.plot_arrays,
        ]

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
            self.finish_peak_analysis,
            self.stop_monitoring,
            self.clear_detectors,
            self.store_arrays,
        ]
        # a scan resumed after it ended unfinished (StepScan.scan_all again):
        # the end callbacks above stopped the monitors and the plot
        self.callbacks_resume_scan = [self.resume_monitoring, self.resume_animation]
        self.name = name

    @mainthread.on_main  # the animation's timer lives on the main thread
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
        animation = None if scan is None else getattr(self._state(scan), "animation", None)
        if animation is None:
            return
        try:
            if animation.event_source is not None:
                animation.event_source.stop()
        except Exception:
            pass

    @mainthread.on_main  # the animation's timer lives on the main thread
    def resume_animation(self, scan=None, **kwargs):
        """Restart the live plot stopped by `stop_animation`; if its window
        was closed meanwhile, the next step opens a new one."""
        state = self._state(scan)
        animation = getattr(state, "animation", None)
        if animation is None:
            return
        figure = getattr(state, "fig", None)
        if (
            animation.event_source is not None
            and figure is not None
            and plt.fignum_exists(figure.number)
        ):
            animation.event_source.start()
        else:
            for attribute in ("animation", "fig", "axs"):
                if hasattr(state, attribute):
                    delattr(state, attribute)

    def resume_monitoring(self, scan=None, **kwargs):
        """Restart the monitors stopped at the end of the scan; they keep
        what they collected and add to it."""
        for tm in self._state(scan).monitors.values():
            tm.start(add_current_value=False)

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

    # This counter's data of a scan live on the scan under the counter's own
    # name (scan.counter_scratch), so several CounterValues in one scan keep
    # apart. The scan's familiar attributes (scan.monitors,
    # scan.monitor_scan_arrays, scan.fig, ...) show the first CounterValue's.
    _SCAN_ATTRIBUTES = (
        "monitors", "detector_values", "detector_names", "timestamp_intervals",
        "monitor_scan_arrays", "fig", "axs", "animation", "stored_filename",
    )

    def _state(self, scan):
        """This counter's data of `scan`: monitors, values, step windows,
        arrays, figure, file."""
        counter_scratch = getattr(scan, "counter_scratch", None)
        if counter_scratch is not None:
            scratch = counter_scratch(self.name)
        else:
            scratch = vars(scan).setdefault("counter_state", {}).setdefault(self.name, {})
        state = scratch.get("values")
        if state is None:
            state = scratch["values"] = SimpleNamespace()
        return state

    def _owns_scan_attributes(self, scan):
        return vars(scan).setdefault("_value_counter_owner", self.name) == self.name

    def _publish(self, scan):
        """The scan's own attributes, for the scan's first CounterValue."""
        if scan is None or not self._owns_scan_attributes(scan):
            return
        state = self._state(scan)
        for attr in self._SCAN_ATTRIBUTES:
            if hasattr(state, attr):
                setattr(scan, attr, getattr(state, attr))

    def start_scan(self, scan=None, detectors=[], **kwargs):
        self.append_detectors(*detectors)
        self._state(scan).detector_values = []
        self._state(scan).detector_names = self.get_detector_names()
        self.start_monitoring(scan=scan)
        self._state(scan).timestamp_intervals = []
        # scan.moniitorable_names = self.get_monitorable_names()
        _peak.attach(
            scan, self.name, self._peak_settings, n_channels=len(self._state(scan).monitors)
        )
        self._publish(scan)

    def start_monitoring(self, scan=None, **kwargs):
        monitors = [tm.set_current_value_callback() for tm in self.monitorables]
        for tm in monitors:
            tm.start()
        if scan is not None:
            self._state(scan).monitors = {
                tn: tm for (tn, tm) in zip(self.get_monitorable_names(), monitors)
            }
        else:
            self.monitors = monitors

    def stop_monitoring(self, scan=None, **kwargs):
        if scan is not None:
            for tm in self._state(scan).monitors.values():
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

    def acquire(self, scan=None, collection_time=None, Npulses=None, **kwargs):
        # collection_time, if given (counter_options), else the scan's
        # exposure, else 1 s
        if collection_time is None:
            collection_time = Npulses if Npulses is not None else 1.0
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
            self._state(scan_wr()).detector_values.append(det_val)
            time.sleep(max(0.0, collection_time - (time.time() - t_tmp)))
            t_stop = time.time()
            self._state(scan_wr()).timestamp_intervals.append(StepTime(t_tmp, t_stop))
            set_window = getattr(scan_wr(), "set_step_window", None)
            if set_window is not None:  # only taken if this counter leads
                set_window(self, time_start=t_tmp, time_stop=t_stop)

        acquisition.set_acquire_foo(acquire, hold=False)

        return acquisition

    def create_arrays(self, scan, **kwargs):
        self._state(scan).monitor_scan_arrays = {}
        for monname, mon in self._state(scan).monitors.items():
            # Snapshot of the still-growing monitor lists (the CA callback
            # appends timestamp then value, so the two can differ by one),
            # as numpy arrays: escape >= 0.2.13's ArrayTimestamps keeps
            # `data` as passed, and its h5 storage only writes numpy/dask
            # data -- given a list, `.store()` silently wrote just the
            # timestamps and left a file with no `data_0000` (unreadable,
            # "Corrupt escape ArrayH5Dataset", and `.store()` itself then
            # failed with KeyError: data_0000).
            values = list(mon.data["values"])
            timestamps = list(mon.data["timestamps"])
            n = min(len(values), len(timestamps))
            self._state(scan).monitor_scan_arrays[monname] = ArrayTimestamps(
                data=as_numeric_array(values[:n]),
                timestamps=np.asarray(timestamps[:n]),
                timestamp_intervals=self._state(scan).timestamp_intervals,
                parameter=parameter_from_scan(scan),
                name=monname,
            )
        self._publish(scan)

    def update_peak_analysis(self, scan, **kwargs):
        """Re-run the peak/step analysis on the steps taken so far, from the
        per-step medians of the (single) monitor -- the same numbers
        `scan.plot` draws -- against the scan variable's own values. Result
        in `scan.peak_analysis`, see `eco.acquisition.peak_analysis`.

        Not in the plot's redraw: that runs off a GUI timer (stopped by the
        toolbar buttons, absent headless), this runs with the data. Never
        raises, so a problem here cannot cost a scan.
        """
        analysis = _peak.get_attached(scan, self.name)
        arrays = getattr(self._state(scan), "monitor_scan_arrays", {})
        if analysis is None or len(arrays) != 1:
            return analysis
        try:
            (array,) = arrays.values()
            y = _peak.step_medians(
                array.timestamps, array.data, self._state(scan).timestamp_intervals
            )
            x = _peak.scan_positions(scan)
            n = min(len(x), len(y))
            analysis.update(x[:n], y[:n])
        except Exception as exc:
            _peak.note_once(
                f"counter_failed_{type(exc).__name__}",
                f"{self.name}: peak analysis failed: {type(exc).__name__}: {exc}",
            )
        return analysis

    def step_feedback(self, scan, channel=None):
        """(mean, standard error, n) of one monitored channel (default: the
        first) over the last step's acquisition interval -- the live
        feedback an adaptive scan steers by (eco.acquisition.adaptive).
        None before the first step."""
        monitors = getattr(self._state(scan), "monitors", None)
        intervals = getattr(self._state(scan), "timestamp_intervals", None)
        if not monitors or not intervals:
            return None
        name = channel if channel is not None else next(iter(monitors))
        data = monitors[name].data
        values = list(data["values"])
        timestamps = np.asarray(list(data["timestamps"])[: len(values)])
        values = values[: len(timestamps)]
        start, stop = intervals[-1]
        inside = (timestamps >= start) & (timestamps <= stop)
        return mean_sem([v for v, keep in zip(values, inside) if keep])

    def finish_peak_analysis(self, scan, **kwargs):
        """Last pass at the end of the scan, and the one-line result."""
        analysis = self.update_peak_analysis(scan)
        if analysis is not None and analysis.summary():
            print(analysis.summary())

    # figures are created/drawn on the main thread, also when the scan runs
    # on a queue's worker thread (see eco.utilities.task_queue)
    @mainthread.on_main
    def plot_arrays(self, scan, **kwargs):
        if not hasattr(self._state(scan), "animation"):
            # one window reused scan after scan; a second CounterValue in
            # the scan gets its own
            label = "CounterValue"
            if not self._owns_scan_attributes(scan):
                label = f"CounterValue {self.name}"
            plt.close(label)

            f, axs = plt.subplots(
                len(self._state(scan).monitor_scan_arrays), 1, sharex=True, num=label
            )
            self._state(scan).fig = f
            if isinstance(axs, plt.Axes):
                axs = [axs]
            self._state(scan).axs = axs

            def plotdat(n, *args):
                for ma, ax in zip(self._state(scan).monitor_scan_arrays.values(), axs):
                    ax.cla()
                    ma.scan.plot(axis=ax, fmt="o-")

            self._state(scan).animation = FuncAnimation(
                fig=f, func=plotdat, cache_frame_data=False, interval=500
            )
            plt.show(block=False)
            self._attach_escape_buttons(scan)
            self._publish(scan)
        else:
            self._state(scan).fig.tight_layout()
            self._state(scan).fig.canvas.draw()
            self._state(scan).fig.canvas.flush_events()

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

            attach_escape_buttons(self._state(scan).fig, before_click=lambda: self.stop_animation(scan))
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
            # a second CounterValue in the scan: its own file
            own = "" if self._owns_scan_attributes(scan) else f".{self.name}"
            filename = datetime.now().strftime("%Y-%m-%d_%H:%M:%S") + own + ".esc.h5"
        else:
            # escape's DataSet refuses any other name ("Expecting esc suffix
            # in filename"), so a plain `filename="myfile"` used to complete
            # the whole scan and then store nothing. A name without any
            # escape suffix is completed silently; an incomplete one
            # (".h5", ".esc", ".zarr") says what it was completed to, the
            # same rule escape's own DataSet applies.
            esc_filename = _with_esc_suffix(filename)
            if esc_filename != str(filename) and {".esc", ".h5", ".zarr"} & set(
                Path(filename).suffixes
            ):
                print(
                    f"Note: dataset filename '{filename}' has an incomplete "
                    f"escape suffix ('.esc.h5' expected), storing as "
                    f"'{esc_filename}'."
                )
            filename = esc_filename

        filepath = Path(directory) / Path(filename)
        stored = False
        refused = False  # file exists and was deliberately not overwritten
        d = None
        # which step is running, so a failure can say where it happened
        stage = "creating the file"
        try:
            d = DataSet.create_with_new_result_file(filepath, force_overwrite=False)
            if d is None:
                # escape asked "...would you like to overwrite? (y/n)" and got "n"
                raise FileExistsError(f"{filepath} exists and overwriting was declined")
            names = []
            for k, v in self._state(scan).monitor_scan_arrays.items():
                names.append(k)
                stage = f"appending '{k}' to the dataset"
                d.append(v, name=k)
                stage = f"writing '{k}' ({type(v).__name__})"
                v.store()
            stage = "closing the file"
            d.results_file.close()
            # the h5 comes from escape's DataSet, i.e. created with the umask
            # (0o644); the pgroup has to be able to rewrite it too
            stage = "setting group permissions"
            ensure_group_writable(filepath)
            self._state(scan).stored_filename = filepath.resolve().as_posix()
            stored = True
            print(f"Stored filename {self._state(scan).stored_filename}")

            stage = "reloading the stored file"
            d = DataSet.load_from_result_file(filepath)
            for name in names:
                array = d.datasets[name]
                # escape >= 0.2.13 loads lazily: without pulling the numbers
                # into memory while the file is still open, every later access
                # (`.data`, `.timestamps`, `.scan.plot`) hits the closed h5
                # file and raises a ValueError deep inside h5py.
                array.data = np.asarray(array.data)
                array.timestamps
                self._state(scan).monitor_scan_arrays[name] = array
            d.results_file.close()
            self._publish(scan)
        except Exception as exc:
            # A failure after the file was opened must not leave it open:
            # h5py then keeps a half-written, unflushed file that other
            # readers see as corrupt until this process exits.
            try:
                d.results_file.close()
            except Exception:
                pass
            print(
                f"{self.name}: dataset file {filepath} "
                + (
                    f"was written, but {stage} failed"
                    if stored
                    else f"NOT stored - failed while {stage}"
                )
                + f": {type(exc).__name__}: {exc}"
            )
            # An OSError (permissions, missing directory, existing file, ...)
            # already says what is wrong; anything else is unexpected and
            # gets its full traceback.
            if not isinstance(exc, OSError):
                traceback.print_exc()
            if stored:
                print(f"{self.name}: the in-memory arrays of this scan are kept.")
            elif isinstance(exc, FileExistsError):
                refused = True
                print(f"{self.name}: the existing file was left untouched.")
            elif filepath.exists():
                print(
                    f"{self.name}: {filepath} exists on disk but is probably "
                    f"incomplete."
                )
            else:
                print(f"{self.name}: no file was written to {filepath}.")

        files = []
        plotfilename = Path(directory) / Path(
            Path(filename).stem.split(".")[0] + ".png"
        )
        try:
            # import mpld3

            if refused:
                # the plot shares its name with the existing data file: leave
                # it too, or it would show a scan whose data was not kept
                print(f"{self.name}: not writing {plotfilename} either.")
            else:
                mainthread.on_main(self._state(scan).fig.savefig)(plotfilename.as_posix())
                ensure_group_writable(plotfilename)  # savefig also uses the umask
                files.append(plotfilename)
            # print(plotfilename, plotfilename.as_posix())

        except Exception as exc:
            print(
                f"{self.name}: could not save plot {plotfilename}: "
                f"{type(exc).__name__}: {exc}"
            )

        # only attach the dataset if it was actually written
        if stored:
            files.append(filepath)

        if elog:
            if elog == True:
                elog = None
            analysis = _peak.get_attached(scan, self.name)
            summary = analysis.summary() if analysis is not None else ""
            scan.status_to_elog(
                text=f"### Quick scan: {scan.description()}\n"
                + (
                    f"Data stored in {filename}."
                    if stored
                    else f"Data NOT stored ({filename}, see console)."
                )
                + (f"\n\n{summary}" if summary else ""),
                auto_title=False,
                elog=elog,
                files=files,
            )

    # -- following the scan's leading counter (StepScan.do_next_step) --------
    # The monitors run for the whole scan; a step is only the time window cut
    # out of them afterwards (scan.timestamp_intervals). Following: start()
    # reads the polled detectors and notes the time, stop() closes the
    # window -- the leading counter's own times if it recorded them
    # (scan.step_window), else the start()/stop() calls.
    follows = True
    exposure_doc = "collection time per step, in seconds"
    exposure_keyword = "collection_time"

    def start(self, scan=None, **kwargs):
        if scan is None:
            return
        scan.counter_scratch(self.name)["follow_start"] = time.time()
        self._state(scan).detector_values.append(self.get_detector_values())

    def stop(self, scan=None, **kwargs):
        if scan is None:
            return {"files": []}
        t_stop = time.time()
        t_start = scan.counter_scratch(self.name).pop("follow_start", t_stop)
        window = getattr(scan, "step_window", None) or {}
        self._state(scan).timestamp_intervals.append(
            StepTime(window.get("time_start", t_start), window.get("time_stop", t_stop))
        )
        return {"files": []}


def _with_esc_suffix(filename):
    """`filename` as an escape results filename, which needs both `.esc` and
    `.h5`/`.zarr` in its suffixes. Only a trailing, partial escape suffix is
    replaced (`run`, `run.h5`, `run.esc` -> `run.esc.h5`); any other dot in
    the name is part of it (`scan_0.5V` -> `scan_0.5V.esc.h5`)."""
    p = Path(filename)
    if ".esc" in p.suffixes and (".h5" in p.suffixes or ".zarr" in p.suffixes):
        return str(filename)
    name = p.name
    stripped = True
    while stripped:
        stripped = False
        for ext in (".h5", ".zarr", ".esc"):
            if name.endswith(ext):
                name = name[: -len(ext)]
                stripped = True
    return str(p.with_name(name + ".esc.h5"))


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


# -- what a scan accepts as a counter -----------------------------------------
def _is_counter(obj):
    """A scan counter: an ``acquire`` that takes the scan. Several detectors
    have an ``acquire(hold, seconds, samples, **kwargs)`` of their own, which
    is not one."""
    acquire = getattr(obj, "acquire", None)
    if acquire is None:
        return False
    try:
        return "scan" in inspect.signature(acquire).parameters
    except (TypeError, ValueError):
        return False


def _counter_name(obj, default):
    alias = getattr(obj, "alias", None)
    try:
        return alias.get_full_name() if alias is not None else getattr(obj, "name", default)
    except Exception:
        return getattr(obj, "name", default)


def _bs_counter_of(obj):
    """The live bs counter for a ``DetectorBsStream``-like object (``.stream``
    is an escape Stream) or for an escape Stream itself, built once and kept
    on the object (``_bs_counter``, which ``.scans`` and
    ``get_current_value(force_bsstream=True)`` use too): it holds the bs
    subscription. None if `obj` is neither."""
    try:
        from escape.stream import Stream
    except ImportError:
        return None
    if not isinstance(obj, Stream) and not isinstance(getattr(obj, "stream", None), Stream):
        return None
    counter = getattr(obj, "_bs_counter", None)
    if counter is None:
        from eco.detector.bs_counter import BsStreamCounter

        counter = BsStreamCounter(obj, name=_counter_name(obj, "bs_counter"))
        obj._bs_counter = counter
    return counter


def _bs_available(obj):
    """False only if the dispatcher says the channel is not there; an object
    that cannot tell (a Stream, no dispatcher access) counts as available."""
    bs_avail = getattr(obj, "bs_avail", None)
    if bs_avail is None:
        return True
    try:
        return bool(bs_avail())
    except Exception:
        return True


def _plain_detector(obj):
    return isinstance(obj, (Detector, MonitorableValueUpdate))


def as_counter(obj):
    """What a scan uses for `obj` in ``counters=`` (or ``feedback=``):

    1. ``obj._scan_counter()`` if the object says what its counter is (e.g. an
       Assembly that wants one counter for several of its channels);
    2. `obj` itself if it is a counter (``acquire(scan=...)``);
    3. for a bs detector or an escape Stream: its live bs counter, kept on the
       object and reused by every scan -- unless the dispatcher does not have
       the channel and there is an EPICS PV to fall back to;
    4. any other detector: a fresh ``CounterValue`` (EPICS monitor/polling).

    For a whole list use ``as_counters``, which puts all detectors of case 4
    into one ``CounterValue``.
    """
    obj = resolve_lazy(obj)
    hook = getattr(obj, "_scan_counter", None)
    if callable(hook):
        return hook()
    if _is_counter(obj):
        return obj
    has_pv = getattr(obj, "_pv", None) is not None
    if not (has_pv and not _bs_available(obj)):
        counter = _bs_counter_of(obj)
        if counter is not None:
            return counter
    elif getattr(obj, "stream", None) is not None:
        print(
            f"{_counter_name(obj, obj)}: bs channel not on the dispatcher, "
            "using its EPICS PV for the scan."
        )
    if _plain_detector(obj):
        return CounterValue(obj, name=_counter_name(obj, "value_counter"))
    raise TypeError(
        f"{_counter_name(obj, type(obj).__name__)} is neither a counter nor a "
        "detector; pass one of its detectors (e.g. a child with "
        "get_current_value) instead."
    )


def exposure_doc(obj):
    """What a scan's exposure_def means with `obj` as the first counter --
    without building a counter for it."""
    obj = resolve_lazy(obj)
    doc = getattr(obj, "exposure_doc", None)
    if doc:
        return doc
    if _is_counter(obj):
        return "passed to the counter as Npulses"
    try:
        from escape.stream import Stream

        if isinstance(obj, Stream) or isinstance(getattr(obj, "stream", None), Stream):
            return "number of bs shots per step"
    except ImportError:
        pass
    return CounterValue.exposure_doc


def exposure_keyword(obj):
    """The keyword under which `obj`, as a scan's first counter, takes the
    exposure: its declared ``exposure_keyword``, else the first keyword of its
    ``acquire`` after ``scan`` (the convention -- not the DAQ's, whose first
    one is ``run_number``, hence the declaration), else ``Npulses``. A detector
    gets its counter's: ``Npulses`` for a bs detector, ``collection_time``
    otherwise."""
    obj = resolve_lazy(obj)
    keyword = getattr(obj, "exposure_keyword", None)
    if keyword:
        return keyword
    if _is_counter(obj):
        names = [n for n in inspect.signature(obj.acquire).parameters if n != "scan"]
        names = [
            n for n in names
            if inspect.signature(obj.acquire).parameters[n].kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        ]
        return names[0] if names else "Npulses"
    if "shots" in exposure_doc(obj):
        return "Npulses"
    return CounterValue.exposure_keyword


def as_counters(objs, options=None):
    """The scan's counters for `objs`, in order, each counter once.

    All plain detectors (case 4 of ``as_counter``) share **one**
    ``CounterValue``, at the place of the first of them: one monitor counter
    reads them all, with one plot.

    With `options` (``counter_options``: one dict or None per entry of
    `objs`, by position) returns ``(counters, options)``, the options moved
    along with their objects -- merged where several objects end up in one
    counter."""
    counters = []
    origin = []  # per input: index into counters
    plain = []
    slot = None
    for obj in objs:
        counter = as_counter(obj)
        if isinstance(counter, CounterValue) and counter is not resolve_lazy(obj):
            # made here for a plain detector: collect, build one at the end
            detector = resolve_lazy(obj)
            if not any(d is detector for d in plain):
                plain.append(detector)
            if slot is None:
                slot = len(counters)
                counters.append(None)
            origin.append(slot)
            continue
        for i, c in enumerate(counters):
            if c is counter:
                origin.append(i)
                break
        else:
            origin.append(len(counters))
            counters.append(counter)
    if plain:
        names = [_counter_name(d, "value_counter") for d in plain]
        counters[slot] = CounterValue(*plain, name="+".join(names))
    if options is None:
        return counters
    options = list(options)
    if len(options) > len(origin):
        raise ValueError(
            f"counter_options has {len(options)} entries for {len(origin)} counters"
        )
    merged = [{} for _ in counters]
    for i, opts in zip(origin, options):
        merged[i].update(opts or {})
    return counters, merged


def find_counter(counters, obj):
    """(counter, channel) of the counter in `counters` that reads `obj` (a
    counter, a bs detector or a monitored detector), or None. `channel` is
    the name the counter knows `obj` by, None for the counter's only or
    first channel."""
    obj = resolve_lazy(obj)
    bs_counter = getattr(obj, "_bs_counter", None)
    for counter in counters:
        if counter is obj or (bs_counter is not None and counter is bs_counter):
            return counter, None
        if isinstance(counter, CounterValue):
            for monitored, name in zip(counter.monitorables, counter.get_monitorable_names()):
                if monitored is obj:
                    return counter, name
    return None
