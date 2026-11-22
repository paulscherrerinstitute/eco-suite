"""Frame processing behind eco's camera viewer -- plain numpy, no Qt, no eco.

Two uses:

- imported by eco.widgets.camserver_stream_qt, which re-exports what lives
  here (FrameProcessor, normalize_to_uint8, projection_stats, ...);
- run as a script, it is the viewer's *frame process*: it receives the
  camera stream, runs every frame through FrameProcessor (averaging,
  background, ROI, contrast, colormap, block-mean downscaling to the size
  shown on screen), computes the analysis (projections, Gaussian fits,
  pshell's good-region fit, center of mass/FWHM, values under the cursor)
  and sends the finished uint8 image plus those results to the viewer.

Why a process: measured on a 2048x2048 16-bit frame, the viewer's render
tick took 86 ms (gray) to 147 ms (colormap) of *main-thread* time, i.e.
most of the main thread at 10 Hz -- and in the eco desktop app the console
runs on that same thread. A fit or any heavier analysis added on top would
make it worse, and a background *thread* doesn't help much because numpy
and scipy.optimize hold the GIL for much of the work (a full-frame 2-D fit
in a thread stalled the main thread for 100 ms). With the frame process
the viewer only turns a screen-sized uint8 buffer into a pixmap and draws
the overlays.

Nothing in this file may import Qt or the eco package: the frame process is
started as ``python camera_frames.py <endpoint>`` and has to be up in well
under a second (a cold ``import eco`` takes ~50 s on the consoles' NFS).
scipy (fits) and matplotlib (colormap tables) are imported on first use.

Protocol (pickled dicts over one zmq PAIR socket on a private ipc path):

viewer -> frame process
    {"type": "connect", "address": "tcp://host:port", "image_channel": ...}
    {"type": "demo", "color": bool, "rate": float, "shape": (h, w)}
    {"type": "settings", ...}           see FrameWorker.DEFAULT_SETTINGS
    {"type": "probe", "probes": {name: (x, y)}}   values under these raw-frame px
    {"type": "pause", "value": bool}
    {"type": "grab_background"} / {"type": "clear_background"}
    {"type": "publish"}                 open the result stream (see below)
    {"type": "stop"}

frame process -> viewer
    [pickled {"type": "frame", ...}, raw uint8 image bytes]   multipart
    {"type": "hello"}                              connected: the viewer can
                                                   remove the socket file
    {"type": "probes", "probes": {name: value}}    answer to "probe"
    {"type": "status" | "error", "message": str}
    {"type": "published", "address": "tcp://127.0.0.1:port"}

Frames are sent non-blocking with a small high-water mark: if the viewer
falls behind, frames are dropped here rather than queued, so what it shows
is always the latest.

Result stream: on request the frame process also publishes its scalar
results (FrameWorker.result_scalars) as a bsread PUB stream on 127.0.0.1,
each message stamped with the frame's own pulse id -- readable as escape
Streams (see CamServerStreamQt.streams()) and so alignable with any other
beam-synchronous channel.
"""

if __name__ == "__main__":
    # Run as a script, sys.path[0] is eco/widgets/: drop it so nothing in
    # there can shadow a module this process imports.
    import sys as _sys

    del _sys.path[0]

import math
import os
import pickle
import signal
import sys
import time
from collections import deque

import numpy as np

# -- contrast, colormaps -------------------------------------------------------


def _full_range(dtype):
    """Display range for contrast_mode="full": the dtype's own min/max for
    integer types; (None, None) -- meaning "fall back to auto" -- for
    float dtypes, which have no natural fixed range."""
    dtype = np.dtype(dtype)
    if np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        return float(info.min), float(info.max)
    return None, None


def normalize_to_uint8(array, vmin=None, vmax=None, log_scale=False):
    """Pure-numpy, Qt-free: map a 2D image array to 8-bit grayscale for
    display. vmin/vmax fix the display range (in the data's own units --
    same meaning whether or not log_scale is set); if omitted, uses the
    frame's own min/max (auto-contrast). log_scale=True makes the tone
    curve between vmin and vmax logarithmic rather than linear (useful
    for high-dynamic-range data with a bright, narrow peak) without
    changing what vmin/vmax mean, so switching it on/off doesn't require
    re-entering different numbers. Kept separate from Qt so the render
    path's core logic is unit-testable without a display or event loop."""
    arr = np.asarray(array)
    if arr.ndim != 2:
        raise ValueError(f"expected a 2D image array, got shape {arr.shape}")
    arr = arr.astype(np.float32, copy=False)
    lo = float(arr.min()) if vmin is None else float(vmin)
    hi = float(arr.max()) if vmax is None else float(vmax)
    if hi <= lo:
        return np.zeros(arr.shape, dtype=np.uint8)
    if log_scale:
        span = np.log1p(max(hi - lo, 1e-6))
        scaled = np.log1p(np.clip(arr - lo, 0, None)) * (255.0 / span)
    else:
        scaled = (arr - lo) * (255.0 / (hi - lo))
    return np.clip(scaled, 0, 255).astype(np.uint8)


# Colormap names offered in the UI. "gray"/"gray_inverted" are pure numpy
# (no matplotlib needed); "viridis" and the two diverging maps are built
# from matplotlib's exact colormap data the first time they're used (see
# get_colormap_lut) -- matplotlib is already an eco dependency (used e.g.
# by eco.dbase.archiver's strip plots), so this reuses it rather than
# hand-approximating viridis's colors. DIVERGING_CENTERS gives the data
# value each diverging colormap's midpoint (white/neutral) is centered on
# -- FrameProcessor.process widens vmin/vmax symmetrically around that
# value so the reference always lands exactly at the midpoint regardless
# of contrast mode (see its _resolve_contrast_range/process).
COLORMAP_NAMES = ("gray", "gray_inverted", "viridis", "diverging_zero", "diverging_one")
DIVERGING_CENTERS = {"diverging_zero": 0.0, "diverging_one": 1.0}
_MPL_COLORMAP_SOURCE = {"viridis": "viridis", "diverging_zero": "RdBu_r", "diverging_one": "RdBu_r"}

_colormap_lut_cache = {}


def get_colormap_lut(name):
    """256x3 uint8 RGB lookup table for a colormap name. Cached after
    first build (the matplotlib-backed ones involve an import + a bit of
    array work, not worth repeating every frame)."""
    if name in _colormap_lut_cache:
        return _colormap_lut_cache[name]

    if name == "gray":
        lut = np.repeat(np.arange(256, dtype=np.uint8)[:, None], 3, axis=1)
    elif name == "gray_inverted":
        lut = np.repeat(np.arange(255, -1, -1, dtype=np.uint8)[:, None], 3, axis=1)
    elif name in _MPL_COLORMAP_SOURCE:
        import matplotlib

        cmap = matplotlib.colormaps[_MPL_COLORMAP_SOURCE[name]]
        lut = (np.asarray(cmap(np.linspace(0.0, 1.0, 256)))[:, :3] * 255).astype(np.uint8)
    else:
        raise ValueError(f"unknown colormap {name!r}")

    _colormap_lut_cache[name] = lut
    return lut


def apply_colormap(gray_uint8, name):
    """(H, W) uint8 grayscale -> (H, W, 3) uint8 RGB via get_colormap_lut's
    table (vectorized fancy indexing)."""
    lut = get_colormap_lut(name)
    return lut[gray_uint8]


def compute_log_histogram(array, bins=128):
    """Pure numpy: (log1p-compressed counts, bin edges) over array's own
    min/max. The log compression matches what pyqtgraph's ImageView
    histogram does -- without it a strong background/saturation peak
    flattens the rest of the histogram to invisibility."""
    flat = np.asarray(array).ravel()
    data_min, data_max = float(flat.min()), float(flat.max())
    if data_max <= data_min:
        data_max = data_min + 1.0
    counts, edges = np.histogram(flat, bins=bins, range=(data_min, data_max))
    return np.log1p(counts.astype(np.float32)), edges


def block_mean(array, factor):
    """Downscale an (H, W) or (H, W, C) array by an integer factor, each
    output pixel the mean of a factor x factor block (the last partial
    row/column of blocks is dropped). Unlike plain striding this keeps a
    narrow line or a small spot visible at a small zoom."""
    factor = int(factor)
    if factor <= 1:
        return array
    h, w = array.shape[0] // factor, array.shape[1] // factor
    if h == 0 or w == 0:
        return array
    cropped = array[: h * factor, : w * factor]
    return cropped.reshape(h, factor, w, factor, *cropped.shape[2:]).mean(axis=(1, 3))


# -- the per-viewer processing pipeline -----------------------------------------


class FrameProcessor:
    """Pure numpy per-viewer processing pipeline: averaging -> background
    subtract -> ROI crop -> contrast mapping -> a uint8 (grayscale) or
    uint8 RGB(A) array ready for array_to_qimage. No Qt, no network -- the
    unit-tested core of the viewer's render path (see
    tests/test_camserver_stream_qt.py)."""

    def __init__(self, average_n=1, average_mode="running"):
        self.average_n = max(1, int(average_n))
        # "running" | "single" -- mirrors pshell's own CamServerViewer
        # averaging (see ImageIntegrator, decompiled from pshell-workbench):
        # "running" recomputes the mean over the last average_n frames on
        # every new frame (a continuously-updating sliding window -- what
        # this class already did before average_mode existed, and pshell's
        # own behavior for a *negative* integration count); "single"
        # accumulates average_n frames, emits their mean once, then starts
        # a fresh batch -- the display only refreshes every average_n raw
        # frames instead of every frame (pshell's behavior for a
        # *non-negative* integration count).
        self.average_mode = average_mode
        self._ring = deque(maxlen=self.average_n)
        self._single_average_result = None  # frozen output, "single" mode only
        self._last_average = None  # reused when the same frame is processed again
        self.background = None
        self.subtract_background = False
        self.roi = None  # (x, y, w, h) in raw-frame pixel coords, or None
        self.contrast_mode = "auto"  # "auto" | "manual" | "full"
        self.vmin = None
        self.vmax = None
        self.log_scale = False
        self.colormap = "gray"  # one of COLORMAP_NAMES

    def set_average_n(self, n):
        n = max(1, int(n))
        if n != self.average_n:
            self.average_n = n
            self._ring = deque(self._ring, maxlen=n)
            self._single_average_result = None

    def set_average_mode(self, mode):
        if mode not in ("running", "single"):
            raise ValueError(f"average_mode must be 'running' or 'single', got {mode!r}")
        if mode != self.average_mode:
            self.average_mode = mode
            self._ring.clear()
            self._single_average_result = None

    @staticmethod
    def is_color(array):
        return array.ndim == 3 and array.shape[-1] in (3, 4)

    def grab_background(self):
        """Capture the current (possibly averaged) frame as background.
        No-op if no frame has been processed yet."""
        if self._ring:
            self.background = np.mean(np.stack(self._ring, axis=0), axis=0)

    def clear_background(self):
        self.background = None

    def set_roi(self, roi):
        self.roi = roi

    @staticmethod
    def compose_roi(base_roi, drag_rect):
        """A new drag-selected rectangle is in the currently-displayed
        (already-cropped) view's pixel space; offset it by any existing
        ROI's origin so nested ROI selections stay anchored to the raw
        frame instead of compounding drift."""
        x, y, w, h = drag_rect
        if base_roi is None:
            return (x, y, w, h)
        bx, by, _, _ = base_roi
        return (bx + x, by + y, w, h)

    def _averaged(self, array, new_frame=True):
        if not new_frame and self._last_average is not None:
            return self._last_average
        self._last_average = result = self._average_in(array)
        return result

    def _average_in(self, array):
        array = np.asarray(array).astype(np.float32, copy=False)
        if self.average_mode == "single" and self.average_n > 1:
            self._ring.append(array)
            if len(self._ring) >= self.average_n:
                self._single_average_result = np.mean(np.stack(self._ring, axis=0), axis=0)
                self._ring.clear()
            # hold the last completed batch's average until the next batch
            # finishes; before the very first batch completes, there is
            # nothing to hold yet, so show the raw frame in the meantime
            return self._single_average_result if self._single_average_result is not None else array
        self._ring.append(array)
        if len(self._ring) == 1:
            return self._ring[-1]
        return np.mean(np.stack(self._ring, axis=0), axis=0)

    def _crop_roi(self, array):
        if self.roi is None:
            return array
        x, y, w, h = self.roi
        h_max, w_max = array.shape[0], array.shape[1]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w_max, x + w), min(h_max, y + h)
        if x1 <= x0 or y1 <= y0:
            return array
        return array[y0:y1, x0:x1, ...]

    def process(self, raw_array, downsample=1, new_frame=True):
        """raw 2D or (H,W,3/4) array -> (display uint8 array, stats dict).

        downsample: integer factor the display array is block-mean reduced
        by (the frame process sends only what fits on screen); stats and
        stats["values"] always refer to the full-resolution frame, and the
        contrast range is resolved on it, so the picture doesn't change
        with the zoom. new_frame=False processes the same frame again (for
        changed contrast/colormap/ROI settings) without feeding it into the
        averaging a second time."""
        raw_array = np.asarray(raw_array)
        orig_dtype = raw_array.dtype
        color = self.is_color(raw_array)

        arr = self._averaged(raw_array, new_frame=new_frame)
        if self.subtract_background and self.background is not None and self.background.shape == arr.shape:
            arr = np.clip(arr - self.background, 0, None)
        arr = self._crop_roi(arr)
        small = block_mean(arr, downsample)
        data_min, data_max = float(arr.min()), float(arr.max())  # once: a pass each on a big frame

        vmin_used, vmax_used = None, None
        if color:
            display = np.clip(small, 0, 255).astype(np.uint8)
        else:
            if self.contrast_mode == "manual" and self.vmin is not None and self.vmax is not None:
                vmin, vmax = self.vmin, self.vmax
            elif self.contrast_mode == "full":
                vmin, vmax = _full_range(orig_dtype)
            else:
                vmin, vmax = None, None

            center = DIVERGING_CENTERS.get(self.colormap)
            if center is not None:
                # widen to a range symmetric around the reference value so
                # it always lands exactly at the diverging colormap's
                # midpoint, regardless of contrast mode
                lo = data_min if vmin is None else float(vmin)
                hi = data_max if vmax is None else float(vmax)
                half = max(abs(lo - center), abs(hi - center), 1e-6)
                vmin, vmax = center - half, center + half

            vmin_used = data_min if vmin is None else float(vmin)
            vmax_used = data_max if vmax is None else float(vmax)
            gray = normalize_to_uint8(small, vmin_used, vmax_used, log_scale=self.log_scale)
            # "gray" stays plain 2D (cheaper, and the historical/expected
            # shape for the default colormap); every other colormap -> RGB
            display = gray if self.colormap == "gray" else apply_colormap(gray, self.colormap)

        stats = {
            "min": data_min,
            "max": data_max,
            "shape": tuple(arr.shape),
            "color": color,
            # resolved contrast bounds actually applied (None for color
            # images, which bypass the contrast pipeline) and the
            # pre-contrast array itself, both for the histogram/colorscale
            # sidebar (_HistogramColorbar) -- avoids that widget having to
            # re-derive contrast-mode logic that already lives here
            "vmin": vmin_used,
            "vmax": vmax_used,
            "values": arr,
        }
        return display, stats


# -- analysis -------------------------------------------------------------------


def projection_stats(values):
    """Center of mass and FWHM of the two projections of a 2-D array, in its
    own pixel coordinates: {"x_com", "y_com", "x_fwhm", "y_fwhm", "sum"}.
    Each projection's minimum is taken as its background. The FWHM is the
    distance between the outermost half-maximum crossings, linearly
    interpolated; None where it is undefined (flat or empty projection)."""
    values = np.asarray(values)
    if values.ndim != 2 or values.size == 0:
        return {"x_com": None, "y_com": None, "x_fwhm": None, "y_fwhm": None, "sum": None}
    return profile_stats(values.sum(axis=0, dtype=np.float64), values.sum(axis=1, dtype=np.float64))


def profile_stats(x_profile, y_profile):
    """projection_stats from the two projections themselves (the frame
    process computes them once for this and for the fits)."""
    stats = {"sum": float(np.sum(x_profile))}
    for profile, key in ((x_profile, "x"), (y_profile, "y")):
        profile = np.asarray(profile, dtype=np.float64)
        profile = profile - profile.min()
        total = profile.sum()
        if total <= 0:
            stats[f"{key}_com"] = stats[f"{key}_fwhm"] = None
            continue
        stats[f"{key}_com"] = float((profile * np.arange(profile.size)).sum() / total)
        stats[f"{key}_fwhm"] = _fwhm(profile)
    return stats


def _fwhm(profile):
    half = profile.max() / 2.0
    above = np.nonzero(profile >= half)[0]
    if above.size == 0 or profile.max() <= 0:
        return None
    first, last = above[0], above[-1]

    def crossing(i_out, i_in):
        # linear interpolation between an index below and one at/above half max
        y_out, y_in = profile[i_out], profile[i_in]
        return i_in + (i_out - i_in) * (y_in - half) / (y_in - y_out) if y_in != y_out else float(i_in)

    left = crossing(first - 1, first) if first > 0 else float(first)
    right = crossing(last + 1, last) if last < profile.size - 1 else float(last)
    return float(right - left)


def _gauss(x, offset, amplitude, center, sigma):
    return offset + amplitude * np.exp(-((x - center) ** 2) / (2 * sigma**2))


_trapezoid = getattr(np, "trapezoid", None) or np.trapz


def gauss_fit(profile, axis):
    """Gaussian fit of one projection, the way cam_server's processing
    pipeline does it (cam_server.pipeline.data_processing.functions.
    gauss_fit) -- the same start values and the same fallback, so a local
    fit of the image a pipeline sends gives the pipeline's own numbers.

    Returns {"offset", "amplitude", "mean", "sigma", "com", "rms",
    "curve", "converged"}; mean/sigma/com/rms in `axis` units, "curve" the
    fitted function on `axis`. When the fit doesn't converge the start
    values come back with converged=False (cam_server returns them
    silently); a fit whose center lies outside the axis is marked
    converged=False too."""
    profile = np.asarray(profile, dtype=np.float64)
    axis = np.asarray(axis, dtype=np.float64)
    if axis.shape[0] != profile.shape[0]:
        raise ValueError(f"axis length {axis.shape[0]} != profile length {profile.shape[0]}")
    total = profile.sum()
    if profile.size < 4 or total == 0:
        return None
    com = (axis * profile).sum() / total
    rms = math.sqrt(abs((axis * axis * profile).sum() / total - com * com))

    offset = profile.min()
    amplitude = profile.max() - offset
    center = axis[profile.argmax()]
    surface = _trapezoid(profile - offset, x=axis)
    sigma = surface / (amplitude * math.sqrt(2 * math.pi)) if amplitude > 0 else 1.0
    start = [offset, amplitude, center, sigma]
    converged = True
    try:
        import warnings

        from scipy.optimize import curve_fit

        with warnings.catch_warnings():
            # "covariance could not be estimated": common on real frames,
            # and the frame process's stderr is the user's terminal
            warnings.simplefilter("ignore")
            params, _ = curve_fit(_gauss, axis, profile.astype("float32"), p0=start)
    except Exception:
        params, converged = np.asarray(start, dtype=np.float64), False
    offset, amplitude, center, sigma = (float(p) for p in params)
    if not (axis.min() <= center <= axis.max()) or not math.isfinite(sigma):
        converged = False
    return {
        "offset": offset,
        "amplitude": amplitude,
        "mean": center,
        "sigma": abs(sigma),
        "com": float(com),
        "rms": rms,
        "curve": _gauss(axis, offset, amplitude, center, sigma),
        "converged": converged,
    }


def good_region_indices(profile, threshold=0.3, gfscale=1.8):
    """(start, end) indices of the "good region" of one projection, exactly
    as cam_server's get_good_region_profile computes it (pshell's "good
    region" fit reads the pipeline's gr_* results): from the maximum, walk
    out to the first point below min + threshold * (max - min) on either
    side, then widen that span by gfscale around its middle, clipped to the
    profile. Quirk kept on purpose: a side that never drops below the
    threshold starts (or ends) at the maximum itself, as in cam_server."""
    profile = np.asarray(profile)
    lo = profile.min()
    level = (profile.max() - lo) * threshold + lo
    peak = int(profile.argmax())
    start = end = peak
    below_left = np.nonzero(profile[1 : peak + 1] < level)[0]  # cam_server never tests index 0
    if below_left.size:
        start = int(below_left[-1]) + 1
    below_right = np.nonzero(profile[peak:] < level)[0]
    if below_right.size:
        end = peak + int(below_right[0])
    extend = (end - start) * gfscale - (end - start)
    start, end = start - extend / 2, end + extend / 2
    start = start if start > 0 else 0
    end = end if end < profile.size - 1 else profile.size - 1
    return int(start), int(end)


def analyse(values, fit=True, good_region=None, offset=(0, 0)):
    """Projections and fits of a 2-D array of shown values (what the viewer
    displays: averaged, background-subtracted, inside the ROI).

    offset: the ROI origin in the raw frame -- positions in the result
    (fit means, coms, the good region) are raw-frame pixels, like every
    other coordinate the viewer reports. good_region: (threshold, gfscale)
    to also fit the good region, as cam_server does with image_good_region
    (fits the projections of the sub-image inside it), or None.

    Returns {"x_profile", "y_profile", "x_fit", "y_fit", "good_region"},
    the fits as returned by gauss_fit (None when not requested or not
    possible) and "good_region" as {"region": (x0, y0, x1, y1), "x_fit",
    "y_fit", "x_profile", "y_profile"} or None."""
    values = np.asarray(values)
    if values.ndim != 2 or values.size == 0:
        return None
    ox, oy = offset
    x_profile = values.sum(axis=0, dtype=np.float64)
    y_profile = values.sum(axis=1, dtype=np.float64)
    result = {"x_profile": x_profile, "y_profile": y_profile, "x_fit": None, "y_fit": None, "good_region": None}
    if fit:
        result["x_fit"] = gauss_fit(x_profile, np.arange(x_profile.size) + ox)
        result["y_fit"] = gauss_fit(y_profile, np.arange(y_profile.size) + oy)
    if good_region is not None:
        threshold, gfscale = good_region
        x0, x1 = good_region_indices(x_profile, threshold, gfscale)
        y0, y1 = good_region_indices(y_profile, threshold, gfscale)
        sub = values[y0:y1, x0:x1]
        if sub.size:
            gx = sub.sum(axis=0, dtype=np.float64)
            gy = sub.sum(axis=1, dtype=np.float64)
            result["good_region"] = {
                "region": (x0 + ox, y0 + oy, x1 + ox, y1 + oy),
                "x_profile": gx,
                "y_profile": gy,
                "x_fit": gauss_fit(gx, np.arange(x0, x1) + ox),
                "y_fit": gauss_fit(gy, np.arange(y0, y1) + oy),
            }
    return result


# -- stream messages ------------------------------------------------------------


def scalar_fields(data, image_channel="image"):
    """{name: value} for every scalar (0-d) channel of one bsread message's
    data dict besides the image -- for a "_sp"/processing pipeline that is
    its analysis output (x/y_center_of_mass, x/y_fwhm, x/y_fit_*, x/y_rms,
    intensity, ...), sent on the same message as the image."""
    fields = {}
    for name, value in data.items():
        if name == image_channel or value is None or value.value is None:
            continue
        if np.ndim(value.value) == 0:
            fields[name] = value.value
    return fields


# The 1-D outputs of a processing pipeline the overlays use (pshell's
# ImageData reads the same ones, with a "gr_" prefix for its good region).
PIPELINE_ARRAYS = (
    "x_axis", "y_axis", "x_profile", "y_profile", "x_fit_gauss_function", "y_fit_gauss_function",
    "gr_x_axis", "gr_y_axis", "gr_x_fit_gauss_function", "gr_y_fit_gauss_function",
)


def pipeline_arrays(data):
    arrays = {}
    for name in PIPELINE_ARRAYS:
        value = data.get(name)
        if value is not None and value.value is not None and np.ndim(value.value) == 1:
            arrays[name] = np.asarray(value.value)
    return arrays


# -- the frame process ----------------------------------------------------------


def private_runtime_dir():
    """A new directory only this user can enter, on a RAM-backed file system
    when there is one ($XDG_RUNTIME_DIR, else /dev/shm) -- for the viewer's
    ipc socket. Never a fixed path: several accounts share the consoles."""
    import tempfile

    for base in (os.environ.get("XDG_RUNTIME_DIR"), "/dev/shm", None):
        if base is None or (os.path.isdir(base) and os.access(base, os.W_OK)):
            try:
                return tempfile.mkdtemp(prefix="eco-camera-", dir=base)
            except OSError:
                continue
    return tempfile.mkdtemp(prefix="eco-camera-")


def remove_endpoint_dir(endpoint):
    """Remove the viewer's private socket directory (private_runtime_dir)
    behind an "ipc://<dir>/frames" endpoint: once connected (the connection
    outlives the file), or on the way out -- so nothing is left behind
    however the viewer ends. Only a directory of that name holding nothing
    but the socket is touched."""
    if not endpoint.startswith("ipc://"):
        return
    path = endpoint[len("ipc://") :]
    directory = os.path.dirname(path)
    if not os.path.basename(directory).startswith("eco-camera-"):
        return
    try:
        if set(os.listdir(directory)) <= {os.path.basename(path)}:
            for name in os.listdir(directory):
                os.unlink(os.path.join(directory, name))
            os.rmdir(directory)
    except OSError:
        pass


def _free_local_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _die_with_parent():
    """Linux: get SIGTERM when the viewer's process goes away, however it
    goes. Elsewhere the getppid() check in the loop does the same, later."""
    try:
        import ctypes

        ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0)  # PR_SET_PDEATHSIG
    except Exception:
        pass


class _DemoSource:
    """A moving Gaussian blob (or an RGB pattern), like the viewer's own
    --kind demo, with the center of mass a "_sp" pipeline would send."""

    def __init__(self, rate=10.0, shape=(300, 400), color=False):
        self.interval = 1.0 / max(rate, 0.1)
        self.shape = shape
        self.color = color
        self.t = 0
        self.next_time = time.monotonic()
        h, w = shape
        self.yy, self.xx = np.mgrid[0:h, 0:w]
        self.rng = np.random.default_rng()

    def receive(self, timeout):
        wait = self.next_time - time.monotonic()
        if wait > timeout:
            time.sleep(timeout)
            return None
        if wait > 0:
            time.sleep(wait)
        self.next_time = max(self.next_time + self.interval, time.monotonic())
        h, w = self.shape
        cx = w / 2 + (w / 3) * np.sin(self.t / 20.0)
        cy = h / 2 + (h / 3) * np.cos(self.t / 13.0)
        sigma = min(h, w) / 8.0
        blob = 5000 * np.exp(-(((self.xx - cx) ** 2 + (self.yy - cy) ** 2) / (2 * sigma**2)))
        frame = np.clip(blob + self.rng.normal(0, 50, size=(h, w)), 0, 60000).astype(np.uint16)
        if self.color:
            rgb = np.stack([frame, np.roll(frame, 5, axis=0), np.roll(frame, -5, axis=1)], axis=-1)
            frame = np.clip(rgb.astype(np.float32) / 60000 * 255, 0, 255).astype(np.uint8)
        self.t += 1
        return frame, self.t, {"x_center_of_mass": float(cx), "y_center_of_mass": float(cy)}, {}

    def close(self):
        pass


class _BsreadSource:
    def __init__(self, address, image_channel="image"):
        from bsread import SUB, Source

        host, port = address.replace("tcp://", "").rsplit(":", 1)
        self.image_channel = image_channel
        # a short receive timeout keeps the control socket responsive; a
        # small queue drops frames at the socket when this process is slower
        # than the stream instead of falling ever further behind
        self.source = Source(host=host, port=int(port), mode=SUB, queue_size=2, receive_timeout=50)
        self.source.connect()

    def receive(self, timeout):
        message = self.source.receive()
        if message is None:
            return None
        data = message.data.data
        value = data.get(self.image_channel)
        if value is None or value.value is None:
            return None
        return value.value, message.data.pulse_id, scalar_fields(data, self.image_channel), pipeline_arrays(data)

    def close(self):
        try:
            self.source.disconnect()
        except Exception:
            pass


class FrameWorker:
    """The frame process's state and loop (see the module docstring)."""

    DEFAULT_SETTINGS = {
        "average_n": 1,
        "average_mode": "running",
        "subtract_background": False,
        "roi": None,
        "contrast_mode": "auto",
        "vmin": None,
        "vmax": None,
        "log_scale": False,
        "colormap": "gray",
        "rate": 10.0,  # frames processed and sent per second, at most
        "downsample": 1,
        "profile": False,  # send the projections
        "fit": False,  # fit them
        "good_region": None,  # (threshold, gfscale) to also fit the good region
        "pipeline_arrays": False,  # forward the pipeline's own profiles/fit curves
    }

    def __init__(self, endpoint, parent=None):
        import zmq

        self.zmq = zmq
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PAIR)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.setsockopt(zmq.SNDHWM, 4)
        self.socket.connect(endpoint)
        self.endpoint = endpoint
        # the viewer's pid, passed on the command line: os.getppid() here
        # would already be init's if the viewer died during our start-up
        self.parent = os.getppid() if parent is None else parent
        self.processor = FrameProcessor()
        self.settings = dict(self.DEFAULT_SETTINGS)
        self.source = None
        self.latest = None  # (raw, pulse_id, fields, arrays) of the last frame received
        self.fresh = False  # latest not processed yet
        self.dirty = False  # settings changed since the last frame was sent
        self.paused = False
        self.running = True
        self.next_send = 0.0
        self.publisher = None
        self.publisher_address = None
        self.probes = {}  # {name: (x, y) raw-frame px}, e.g. the viewer's cursor
        self.values = None  # shown values of the last frame sent, for probes
        self.offset = (0, 0)

    # -- messages ------------------------------------------------------------

    def send(self, message, image=None):
        parts = [pickle.dumps(message, protocol=pickle.HIGHEST_PROTOCOL)]
        if image is not None:
            parts.append(np.ascontiguousarray(image))
        try:
            self.socket.send_multipart(parts, flags=self.zmq.NOBLOCK, copy=False)
        except self.zmq.Again:
            pass  # the viewer is behind: drop, the next frame will do

    def status(self, text, error=False):
        self.send({"type": "error" if error else "status", "message": text})

    def handle(self, message):
        kind = message.get("type")
        if kind == "settings":
            settings = dict(self.settings)
            settings.update({k: v for k, v in message.items() if k != "type"})
            self.apply_settings(settings)
        elif kind == "connect":
            self.open(_BsreadSource, message["address"], message.get("image_channel", "image"))
            if self.source is not None:
                self.status(f"connected to {message['address']}")
        elif kind == "demo":
            self.open(
                _DemoSource,
                message.get("rate", 10.0),
                shape=tuple(message.get("shape", (300, 400))),
                color=message.get("color", False),
            )
            self.status("connected to demo source")
        elif kind == "probe":
            self.probes = message.get("probes") or {}
            if self.values is not None:
                self.send({"type": "probes", "probes": self.probe_values()})
        elif kind == "pause":
            self.paused = bool(message.get("value"))
        elif kind == "grab_background":
            self.processor.grab_background()
        elif kind == "clear_background":
            self.processor.clear_background()
            self.dirty = True
        elif kind == "publish":
            self.open_publisher()
        elif kind == "stop":
            self.running = False

    def apply_settings(self, settings):
        p = self.processor
        old = self.settings
        self.settings = settings
        if settings["average_n"] != old["average_n"]:
            p.set_average_n(settings["average_n"])
        if settings["average_mode"] != old["average_mode"]:
            p.set_average_mode(settings["average_mode"])
        roi = settings["roi"]
        p.set_roi(tuple(roi) if roi is not None else None)
        for name in ("subtract_background", "contrast_mode", "vmin", "vmax", "log_scale", "colormap"):
            setattr(p, name, settings[name])
        self.dirty = True

    def open(self, factory, *args, **kwargs):
        if self.source is not None:
            self.source.close()
            self.source = None
        try:
            self.source = factory(*args, **kwargs)
        except Exception as exc:
            self.status(f"could not open the stream: {exc}", error=True)

    def open_publisher(self):
        if self.publisher is not None:
            address = self.publisher_address
        else:
            try:
                from bsread.sender import PUB, Sender

                port = _free_local_port()
                self.publisher = Sender(address="tcp://127.0.0.1", port=port, mode=PUB, block=False)
                self.publisher.open()
                self.publisher_address = address = f"tcp://127.0.0.1:{port}"
            except Exception as exc:
                self.publisher = None
                self.status(f"could not open the result stream: {exc}", error=True)
                return
        self.send({"type": "published", "address": address})

    # -- processing ----------------------------------------------------------

    def process(self):
        raw, pulse_id, fields, arrays = self.latest
        s = self.settings
        new_frame, self.fresh, self.dirty = self.fresh, False, False
        display, stats = self.processor.process(raw, downsample=s["downsample"], new_frame=new_frame)
        values = stats.pop("values")
        roi = self.processor.roi if values.shape[:2] != np.shape(raw)[:2] else None
        ox, oy = (max(0, roi[0]), max(0, roi[1])) if roi else (0, 0)  # as _crop_roi clips
        frame = {
            "type": "frame",
            "pulse_id": pulse_id,
            "fields": fields,
            "stats": stats,
            "display_shape": display.shape,
            "offset": (ox, oy),
            "analysis": None,
            "roi_stats": None,
            "probes": {},
        }
        if not stats["color"]:
            # the histogram doesn't need every pixel of a large frame
            step = max(1, math.ceil(math.sqrt(values.size / 2.5e5)))
            frame["histogram"] = compute_log_histogram(values[::step, ::step])
            analysis = analyse(values, fit=s["fit"], good_region=s["good_region"], offset=(ox, oy))
            roi_stats = profile_stats(analysis["x_profile"], analysis["y_profile"])
            for key, origin in (("x_com", ox), ("y_com", oy)):
                if roi_stats[key] is not None:
                    roi_stats[key] += origin
            frame["roi_stats"] = roi_stats
            if s["profile"] or s["fit"] or s["good_region"] is not None:
                frame["analysis"] = analysis
        self.values, self.offset = (None if stats["color"] else values), (ox, oy)
        frame["probes"] = self.probe_values()
        if s["pipeline_arrays"]:
            frame["pipeline_arrays"] = arrays
        self.send(frame, display)
        if self.publisher is not None:
            self.publish(pulse_id, frame)

    def probe_values(self):
        values, (ox, oy) = self.values, self.offset
        found = {}
        if values is None:
            return found
        for name, position in self.probes.items():
            if position is None:
                continue
            x, y = int(position[0]) - ox, int(position[1]) - oy
            if 0 <= y < values.shape[0] and 0 <= x < values.shape[1]:
                found[name] = float(values[y, x])
        return found

    @staticmethod
    def result_scalars(frame):
        """The scalar results of one frame, as published on the result
        stream: x_com/y_com/x_fwhm/y_fwhm/sum of the shown values, and with
        a fit fit_x_mean/fit_x_sigma/fit_x_amplitude/fit_x_offset (and y),
        plus gr_fit_* for the good region -- positions in raw-frame px."""
        scalars = {k: float(v) for k, v in (frame.get("roi_stats") or {}).items() if v is not None}
        analysis = frame.get("analysis") or {}
        for prefix, source in (("fit", analysis), ("gr_fit", analysis.get("good_region") or {})):
            for axis in ("x", "y"):
                fit = source.get(f"{axis}_fit")
                if fit:
                    for key in ("mean", "sigma", "amplitude", "offset"):
                        scalars[f"{prefix}_{axis}_{key}"] = fit[key]
        return scalars

    def publish(self, pulse_id, frame):
        scalars = self.result_scalars(frame)
        if not scalars:
            return
        try:
            self.publisher.send(data=scalars, pulse_id=int(pulse_id), timestamp=time.time())
        except Exception as exc:
            self.status(f"result stream: {exc}", error=True)

    # -- loop ----------------------------------------------------------------

    def remove_endpoint(self):
        """See remove_endpoint_dir. "Connected" means a message has arrived:
        zmq queues what this side sends before the connection exists, so a
        sent message proves nothing, and without the file the connection
        could not be made."""
        if self.endpoint is not None:
            remove_endpoint_dir(self.endpoint)
            self.endpoint = None

    def run(self):
        self.send({"type": "hello"})  # queued until connected
        poller = self.zmq.Poller()
        poller.register(self.socket, self.zmq.POLLIN)
        while self.running:
            if os.getppid() != self.parent:
                break
            # paused: frames would be thrown away, so don't receive (and
            # decode) them at all; wait for control messages instead
            receiving = self.source is not None and not self.paused
            if dict(poller.poll(0 if receiving else 50)).get(self.socket):
                self.remove_endpoint()
                while self.socket.poll(0):
                    self.handle(pickle.loads(self.socket.recv()))
            if self.source is not None and not self.paused:  # control may have changed both
                try:
                    received = self.source.receive(timeout=0.05)
                except Exception as exc:
                    self.status(f"stream error: {exc}", error=True)
                    self.source.close()
                    self.source = None
                    received = None
                if received is not None:
                    self.latest, self.fresh = received, True
            now = time.monotonic()
            due = now >= self.next_send
            # changed settings wait for the rate limit too: a dragged
            # histogram handle must not make this process every frame twice
            if self.latest is not None and (self.fresh or self.dirty) and due:
                self.next_send = now + 1.0 / max(float(self.settings["rate"]), 0.1)
                try:
                    self.process()
                except Exception as exc:
                    self.status(f"frame processing failed: {type(exc).__name__}: {exc}", error=True)
        self.close()

    def close(self):
        if self.source is not None:
            self.source.close()
        if self.publisher is not None:
            try:
                self.publisher.close()
            except Exception:
                pass
        self.socket.close(0)
        self.context.term()
        self.remove_endpoint()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) not in (1, 2):
        print("usage: camera_frames.py <zmq endpoint of the viewer> [<viewer pid>]", file=sys.stderr)
        return 2
    _die_with_parent()
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl-C in the session is not for us
    parent = int(argv[1]) if len(argv) == 2 else None
    if parent is not None and os.getppid() != parent:
        remove_endpoint_dir(argv[0])
        return 0  # the viewer is already gone
    worker = FrameWorker(argv[0], parent=parent)
    # the viewer going away (see _die_with_parent) ends the loop normally,
    # so close() still removes the socket directory
    signal.signal(signal.SIGTERM, lambda signum, frame: setattr(worker, "running", False))
    worker.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
