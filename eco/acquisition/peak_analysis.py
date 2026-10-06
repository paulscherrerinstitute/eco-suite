"""Peak / step analysis of the running trace of a 1-D scan, for the scan
counters (`CounterValue`, `BsStreamCounter`).

Wraps escape's `find_peak` (`escape._peak_analysis`, escape-fel >= 0.2.14: the
same analysis behind the Peak / Peak-params buttons and the `plot_med`
overlay) and attaches the result to the scan as `scan.peak_analysis`, a
`DetectorObject` with one sub-detector per field, so it shows up in `repr(scan)`
and in the elog's scan display, and can be read as
`scan.peak_analysis.center()` while the scan is still running. A copy goes into
`scan.scan_info["peak_analysis"]` (JSON-clean), which `writeScanInfo` and Daq's
`scan_info_rel.json` carry along.

Scope, deliberately small for now: one analysing counter with one channel in a
1-D scan. A scan with several adjustables (mesh/grid scans), a counter with
several channels, or a second analysing counter in the same scan is simply not
analysed (see `attach`) -- no per-channel nesting, which would need the dotted
channel names (`mon_opt.intensity`, `SAROP21-...:INTENSITY`) turned into valid
attribute names.

Fields of `scan.peak_analysis` (always all of them, `None` -- `False` for
`valid` and `is_peak` -- when not available, because `DetectorObject` builds its
sub-detectors once from the keys present at construction). `None` rather than
NaN because `Assembly` registers every component in the scan's status, and so
in whatever serialises it; nothing there should carry a value strict JSON
cannot. (`_append(is_status=False)` is accepted but ignored, and a component
cannot be in the display without being in the status.)

    valid      a result exists (needs 2 * n_bg + 3 points and some variation)
    is_peak    True for a peak (or dip), False for a step
    center     peak: midpoint of the two half-maximum crossings;
               step: where the curve crosses halfway between its two levels
    fwhm       peak: full width at half maximum;
               step: width between the ~12 % and ~88 % crossings
    peak_x/y   position and value of the extremum (step: `center` and the
               curve's value there)
    height     peak: peak_y minus the baseline at peak_x (negative for a dip);
               step: level after minus level before
    offset     peak: baseline at `center`; step: level before the step
    gradient   peak: slope of the baseline (0 for bg_model="offset");
               step: None
    snr        |height| over a robust noise estimate of the trace (median
               absolute deviation of its point-to-point differences, so a
               smooth peak or step does not count as noise); None for
               noise-free data (no noise to compare to). Advisory:
               `mode="auto"` reports a "peak" or "step" for almost any trace
               with variation, this tells a real one from scatter
    n_points   steps with a finite value that went into the analysis
    parameter  name of the scan variable (`x`, in its units)
    n_bg, bg_model, mode   the `find_peak` settings used ("fixed" as bg_model
               if a fixed offset was given)

`peak_analysis` on the counters takes `True` (defaults), `False`, or a dict
with any of `n_bg`, `bg_model`, `fixed_offset`, `mode` (see `find_peak`).
"""

import weakref

import numpy as np

DEFAULT_SETTINGS = {
    "n_bg": 3,
    "bg_model": "linear",
    "fixed_offset": None,
    "mode": "auto",
}

_find_peak = None
_notes_printed = set()


def note_once(key, message):
    if key not in _notes_printed:
        _notes_printed.add(key)
        print(message)


def _import_find_peak():
    """`escape._peak_analysis.find_peak`, or None (once, with a note) for an
    escape that predates it -- e.g. the 0.2.1 environments, where the counters
    then just do what they always did."""
    global _find_peak
    if _find_peak is None:
        try:
            from escape._peak_analysis import find_peak

            _find_peak = find_peak
        except Exception:
            note_once(
                "no_find_peak",
                "peak_analysis: this escape-fel has no escape._peak_analysis."
                "find_peak (needs >= 0.2.14), scans are not analysed.",
            )
    return _find_peak


def parse_settings(peak_analysis):
    """The counters' `peak_analysis` keyword as a full settings dict, or None
    if it is switched off. Raises early (at counter construction) for
    something that `find_peak` would reject in the middle of a scan."""
    if peak_analysis is None or peak_analysis is False:
        return None
    if peak_analysis is True:
        return dict(DEFAULT_SETTINGS)
    if not isinstance(peak_analysis, dict):
        raise TypeError(
            "peak_analysis must be True, False or a dict of find_peak settings "
            f"{sorted(DEFAULT_SETTINGS)}, got {type(peak_analysis).__name__}"
        )
    unknown = set(peak_analysis) - set(DEFAULT_SETTINGS)
    if unknown:
        raise TypeError(
            f"peak_analysis: unknown setting(s) {sorted(unknown)}, "
            f"expected any of {sorted(DEFAULT_SETTINGS)}"
        )
    settings = {**DEFAULT_SETTINGS, **peak_analysis}
    if settings["bg_model"] not in ("linear", "offset"):
        raise ValueError("peak_analysis: bg_model must be 'linear' or 'offset'")
    if settings["mode"] not in ("auto", "peak", "step"):
        raise ValueError("peak_analysis: mode must be 'auto', 'peak' or 'step'")
    if int(settings["n_bg"]) < 1:
        raise ValueError("peak_analysis: n_bg must be at least 1")
    return settings


def empty_result(settings=None, parameter="", n_points=0):
    """All fields, nothing found."""
    s = settings or DEFAULT_SETTINGS
    nan = float("nan")
    return {
        "valid": False,
        "is_peak": False,
        "center": nan,
        "fwhm": nan,
        "peak_x": nan,
        "peak_y": nan,
        "height": nan,
        "offset": nan,
        "gradient": nan,
        "snr": nan,
        "n_points": int(n_points),
        "parameter": str(parameter),
        "n_bg": int(s["n_bg"]),
        "bg_model": "fixed" if s["fixed_offset"] is not None else s["bg_model"],
        "mode": s["mode"],
    }


def analyze_trace(x, y, settings=None, parameter=""):
    """`find_peak` on one trace, as the flat result dict described in the
    module docstring. Never raises: anything that cannot be analysed comes
    back as `empty_result` (with `n_points` filled in)."""
    settings = settings or DEFAULT_SETTINGS
    try:
        x = np.asarray(x, dtype=float).ravel()
        y = np.asarray(y, dtype=float).ravel()
    except (TypeError, ValueError):
        return empty_result(settings, parameter)
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    finite = np.isfinite(x) & np.isfinite(y)
    result = empty_result(settings, parameter, finite.sum())

    find_peak = _import_find_peak()
    if find_peak is None:
        return result
    try:
        found = find_peak(
            x,
            y,
            n_bg=int(settings["n_bg"]),
            bg_model=settings["bg_model"],
            fixed_offset=settings["fixed_offset"],
            mode=settings["mode"],
        )
    except Exception as exc:
        note_once(
            f"find_peak_failed_{type(exc).__name__}",
            f"peak_analysis: find_peak failed ({type(exc).__name__}: {exc}).",
        )
        return result
    if found is None:
        return result

    nan = float("nan")
    if found["is_peak"]:
        bx, by = found["background"]
        span = bx[-1] - bx[0]
        offset = float(np.interp(found["center"], bx, by))
        gradient = float((by[-1] - by[0]) / span) if span > 0 else nan
        height = float(found["peak_y"] - np.interp(found["peak_x"], bx, by))
    else:
        level_before, level_after = found["levels"]
        offset, gradient, height = level_before, nan, level_after - level_before

    xs, ys = x[finite], y[finite]
    ys = ys[np.argsort(xs, kind="stable")]
    d = np.diff(ys)
    noise = 1.4826 * np.median(np.abs(d - np.median(d))) / np.sqrt(2)
    snr = abs(height) / noise if noise > 0 else float("inf")

    result.update(
        valid=True,
        is_peak=bool(found["is_peak"]),
        center=found["center"],
        fwhm=found["fwhm"],
        peak_x=found["peak_x"],
        peak_y=found["peak_y"],
        height=height,
        offset=offset,
        gradient=gradient,
        snr=float(snr),
    )
    return result


def step_medians(timestamps, values, intervals):
    """Median of a monitor's samples for each step interval `(start, stop)`.

    Takes the same samples escape's `ArrayTimestamps.scan[n]` does -- the last
    sample at or before the start (the value a monitor is holding when the
    step begins) up to the last one at or before the stop -- so the numbers
    are those `scan.plot` draws. Done directly on the arrays rather than via
    `scan[n]`, which raises if there is no sample before the first step.
    NaN for a step without a finite sample, and for non-scalar data.
    """
    n_steps = len(intervals)
    ts = np.asarray(timestamps, dtype=float)
    v = np.asarray(values, dtype=float)
    if v.ndim != 1 or len(v) != len(ts):
        return np.full(n_steps, np.nan)
    order = np.argsort(ts, kind="stable")
    ts, v = ts[order], v[order]
    out = np.full(n_steps, np.nan)
    for i, (t0, t1) in enumerate(intervals):
        i0 = max(int(np.searchsorted(ts, t0, side="right")) - 1, 0)
        i1 = int(np.searchsorted(ts, t1, side="right"))
        seg = v[i0:i1]
        seg = seg[np.isfinite(seg)]
        if seg.size:
            out[i] = np.median(seg)
    return out


def scan_positions(scan):
    """Value of the first (only) adjustable at every step taken so far --
    the scan's own units, as `parameter_from_scan` stores them."""
    out = []
    for step_values in scan.scan_info["scan_values"]:
        try:
            out.append(float(step_values[0]))
        except (TypeError, ValueError, IndexError):
            out.append(float("nan"))
    return np.asarray(out, dtype=float)


def json_clean(result):
    """`result` with plain Python numbers and None for NaN/inf, which strict
    JSON readers (anything but Python's own) refuse."""
    out = {}
    for key, value in result.items():
        if isinstance(value, (float, np.floating)):
            value = float(value) if np.isfinite(value) else None
        elif isinstance(value, np.integer):
            value = int(value)
        elif isinstance(value, np.bool_):
            value = bool(value)
        out[key] = value
    return out


def format_summary(result, unit=""):
    """One line for the console / elog, "" if there is no result."""
    if not result.get("valid"):
        return ""
    where = f"{result['parameter']} = {result['center']:.4g}" + (
        f" {unit}" if unit else ""
    )
    if result["is_peak"]:
        feature = f"peak at {where}, FWHM {result['fwhm']:.4g}"
    else:
        feature = (
            f"step at {where}, width {result['fwhm']:.4g}, "
            f"{result['offset']:.4g} -> {result['offset'] + result['height']:.4g}"
        )
    return f"Peak analysis: {feature}, height {result['height']:.4g}, S/N {result['snr']:.3g}"


class ScanPeakAnalysis:
    """The peak analysis of one scan: owns the result dict that
    `scan.peak_analysis`'s sub-detectors read, and the copy in `scan_info`."""

    def __init__(self, scan, settings, parameter, unit=""):
        self._scan = weakref.ref(scan)
        self.settings = settings
        self.parameter = parameter
        self.unit = unit
        # always the same keys, updated in place (see module docstring)
        self.values = empty_result(settings, parameter)

    def get_values(self):
        """What `scan.peak_analysis`'s sub-detectors read: `values` with
        None for NaN/inf (see the module docstring)."""
        return json_clean(self.values)

    def update(self, x, y):
        """Re-analyse the trace `y(x)` of the steps taken so far."""
        result = analyze_trace(x, y, self.settings, self.parameter)
        self.values.update(result)
        scan = self._scan()
        if scan is not None and hasattr(scan, "scan_info"):
            scan.scan_info["peak_analysis"] = json_clean(result)
        return result

    def summary(self):
        return format_summary(self.values, self.unit)


def adjustable_unit(scan):
    try:
        unit = scan.adjustables[0].unit
        if hasattr(unit, "get_current_value"):
            unit = unit.get_current_value()
        return str(unit) if unit else ""
    except Exception:
        return ""


def _expose_on_scan(scan, analysis):
    """`scan.peak_analysis`: a DetectorObject over the result dict, shown in
    the scan's display (and so in the elog's scan table). The getter it reads
    from is deliberately not itself appended to the scan: it would only be a
    second, flat copy of the same values in the scan's status."""
    if not hasattr(scan, "_append"):
        return
    # imported here: eco.elements.detector pulls in eco.acquisition.decorators,
    # which imports eco.acquisition.counters, which imports this module
    from eco.elements.adj_obj import DetectorObject
    from eco.elements.detector import DetectorGet

    try:
        scan._append(
            DetectorObject,
            DetectorGet(analysis.get_values, name="peak_analysis_values"),
            name="peak_analysis",
            is_setting=False,
            is_display="recursive",
        )
    except Exception as exc:
        print(
            "peak_analysis: could not expose the result as scan.peak_analysis "
            f"({type(exc).__name__}: {exc}); it is still in scan.scan_info."
        )


def attach(scan, owner, settings, n_channels=1):
    """Prepare `scan` for the analysis of counter `owner`'s trace, once per
    scan (a repeated call returns the same object). Returns the
    `ScanPeakAnalysis` to `update()`, or None if this scan is not analysed:
    switched off (`settings` is None), several adjustables, `n_channels` other
    than 1, a scan object without `scan_info` / `counter_scratch`, or another
    counter already analysing this scan."""
    if settings is None or n_channels != 1:
        return None
    counter_scratch = getattr(scan, "counter_scratch", None)
    scan_parameters = (getattr(scan, "scan_info", None) or {}).get("scan_parameters")
    if counter_scratch is None or not scan_parameters:
        return None
    names = scan_parameters.get("name") or []
    if len(names) != 1 or scan_parameters.get("grid_specs"):
        return None

    scratch = counter_scratch(owner)
    if scratch.get("peak_analysis") is not None:
        return scratch["peak_analysis"]
    if "peak_analysis" in vars(scan):
        print(
            f"peak_analysis: scan.peak_analysis already belongs to another "
            f"counter, '{owner}' is not analysed (one analysing counter per scan)."
        )
        return None
    analysis = ScanPeakAnalysis(
        scan, settings, parameter=str(names[0]), unit=adjustable_unit(scan)
    )
    scratch["peak_analysis"] = analysis
    _expose_on_scan(scan, analysis)
    return analysis


def get_attached(scan, owner):
    """The `ScanPeakAnalysis` `attach` made for `owner` in `scan`, or None."""
    counter_scratch = getattr(scan, "counter_scratch", None)
    if counter_scratch is None:
        return None
    return counter_scratch(owner).get("peak_analysis")
