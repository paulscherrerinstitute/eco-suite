"""
Qt live viewer for cam_server camera/pipeline image streams -- a
python/Qt-native prototype of pshell's built-in "Screen Panel"
(ch.psi.pshell.screenpanel.CamServerViewer), which resolves a stream via
the cam_server REST API and displays the resulting bsread image stream.
Same two building blocks here: `cam_server.CamClient`/`PipelineClient` for
REST resolution (already used elsewhere in eco, e.g.
eco.devices_general.pipelines_swissfel) and `bsread.Source` for the zmq
image stream (same wire protocol pshell's ch.psi.bsread.Receiver reads).

Architecture mirrors eco.widgets.camera_stream_qt (AxisPTZStreamQt): a
background thread owns the network connection, decodes each frame, and
hands it to the GUI thread via a Qt signal -- so a stalled/slow stream
never blocks the window. The GUI thread never processes a frame directly
off that signal: it just stores the latest one, and a QTimer (the
"frequency throttle" control) periodically pulls whatever is latest and
runs it through the numpy render pipeline. This decouples render rate
from network receive rate and mirrors pshell's own CamServerViewer, which
(per its decompiled fields) drives redraws from a `javax.swing.Timer`
rather than straight off the stream callback.

Feature set (one instance = one camera/pipeline):
  - frequency throttle (render-rate spinbox, decoupled from stream rate)
  - contrast/color-limit modes: auto (per-frame min/max), manual (fixed
    vmin/vmax), full (dtype range) -- settable via spinboxes or by
    dragging the histogram/colorscale sidebar (_HistogramColorbar), a
    native (no pyqtgraph dependency) resembling of pyqtgraph.ImageView's
    histogram+LUT panel: a log-compressed vertical intensity histogram
    next to a grayscale gradient, with draggable black/white-level
    handles that switch contrast to manual with the dragged bounds
  - client-side ROI: drag-to-select on the image, composes with any
    existing ROI (coordinates always resolved against the raw frame)
  - two crosshair types: a draggable/click-placed "Marker" (with a
    cursor/marker pixel-value readout) and a fixed "Reticle" pinned to
    the image center with tick marks -- mirrors pshell's separate
    marker/reticle overlays
  - background capture (from the current, possibly-averaged frame) and
    subtraction
  - N-frame averaging
  - color images: any frame published as (H, W, 3)/(H, W, 4) is detected
    and displayed as RGB/RGBA directly (bypassing the grayscale contrast
    pipeline); a built-in --kind demo source with --demo-color can
    generate a synthetic color test stream on demand with no cam_server
    dependency, for exercising this path without a real color camera

The render-pipeline core (FrameProcessor, normalize_to_uint8,
array_to_qimage, compute_roi_from_drag) is plain numpy, Qt- and
network-free, and unit tested directly (see
tests/test_camserver_stream_qt.py). GUI mouse-interaction and the actual
image widgets are not (no display in CI); they're kept thin wrappers
around that tested core.

Resolving "the" pipeline for a camera: kind="camera_pipeline" takes a raw
camera name and auto-resolves it to its own default processing pipeline
(creating the pipeline config on the fly if needed) rather than either the
camera's raw stream (kind="camera") or requiring the caller to already know
a pipeline instance name (kind="pipeline") -- see resolve_camera_pipeline,
which mirrors decompiled logic from
ch.psi.pshell.screenpanel.CamServerViewer.setStream's "Cameras" selection
mode. This is what eco.devices_general.cameras_swissfel.CameraBasler/
CameraPCO's own ._widget_viewer() uses, passing cam=<the camera Assembly> so the
viewer can add a "Camera Settings" button (opens cam.widget(normal=True),
the normal property-grid widget) -- mirrors
eco.widgets.camera_stream_qt.AxisPTZStreamQt's own "Settings" button.

Embedding (for eco.widgets.camserver_panel_qt): with embed=True the
window is frameless and, once shown, prints "WINID <id>" (its native
window id) to stdout -- the panel process picks that up and reparents the
window into its own grid via Qt's createWindowContainer. Run directly
(``python -m eco.widgets.camserver_stream_qt NAME --kind pipeline``) for
a normal standalone window.
"""
import argparse
import logging
import sys
import tempfile
import threading
import time
from collections import deque

import numpy as np
from qtpy import QtCore, QtGui, QtWidgets

logger = logging.getLogger(__name__)

_app_ref = None  # keep a strong reference to any QApplication we create ourselves

# Prefixes eco already uses for Bernina-area cam_server names (see e.g.
# eco.xoptics.beamline_bernina) -- used to sort Bernina cameras/pipelines
# to the top of the picker in eco.widgets.camserver_panel_qt.
BERNINA_PREFIXES = ("SARES", "SAROP", "SLAAR", "SARFE")


# The pipeline-name convention pshell's own screen panel uses to go from a
# raw camera name to "the" processing pipeline for it -- decompiled from
# ch.psi.pshell.screenpanel.CamServerViewer's `pipelineNameFormat` field
# (default "%s_sp", i.e. Python's "{}_sp") and its getPipelineName(camera) =
# String.format(pipelineNameFormat, camera). See resolve_camera_pipeline.
DEFAULT_PIPELINE_NAME_FORMAT = "{}_sp"


def default_pipeline_name(camera_name, name_format=DEFAULT_PIPELINE_NAME_FORMAT):
    """The "screenpanel" pipeline name pshell derives from a raw camera name
    by default -- e.g. "SARES20-PROF141-M1" -> "SARES20-PROF141-M1_sp". See
    DEFAULT_PIPELINE_NAME_FORMAT."""
    return name_format.format(camera_name)


def resolve_camera_pipeline(
    camera_name, pipeline_url=None, create=True, name_format=DEFAULT_PIPELINE_NAME_FORMAT
):
    """Resolve "the" processing pipeline for a raw camera name -- what a
    camera's own ._widget_viewer() uses so callers never have to know or guess a
    pipeline instance name themselves.

    This is the piece pshell's screen panel has that eco's own
    resolve_stream(kind="pipeline") doesn't: given just a camera name, it
    doesn't merely reuse-or-create a *running instance* of an
    already-existing same-named pipeline config (what create=True already
    does below) -- it first works out *which* pipeline config that even is
    (default_pipeline_name), and if no such config exists yet either, it
    creates one on the fly. Decompiled from
    ch.psi.pshell.screenpanel.CamServerViewer.setStream's "Cameras"
    selection-mode branch:
        pipelineName = getPipelineName(cameraName)              # "{cam}_sp"
        if pipelineName not in server.getPipelines():
            server.savePipelineConfig(pipelineName, {"camera_name": cameraName})
        server.start(pipelineName, instanceName)                # create/reuse

    Returns (pipeline_name, stream_address).
    """
    from cam_server import PipelineClient

    client = PipelineClient(pipeline_url) if pipeline_url else PipelineClient()
    pipeline_name = default_pipeline_name(camera_name, name_format=name_format)
    try:
        return pipeline_name, client.get_instance_stream(pipeline_name)
    except Exception:
        if not create:
            raise
        if pipeline_name not in client.get_pipelines():
            client.save_pipeline_config(pipeline_name, {"camera_name": camera_name})
        _, stream = client.create_instance_from_name(pipeline_name)
        return pipeline_name, stream


def resolve_stream(name, kind="pipeline", pipeline_url=None, camera_url=None, create=True):
    """Resolve a cam_server camera or pipeline instance name to a bsread
    stream address ("tcp://host:port"), via the same REST calls pshell's
    ch.psi.pshell.camserver.PipelineSource/CameraSource use.

    kind: "pipeline", "camera", or "camera_pipeline" (a raw camera name,
        auto-resolved to its own default processing pipeline -- see
        resolve_camera_pipeline -- rather than the camera's own raw,
        unprocessed stream that kind="camera" gives you).
    create: for a pipeline (or camera_pipeline), start a new instance from
        the pipeline config of the same name if no running instance is
        found (mirrors CamServerViewer's own selectCamera->initialize
        behavior).
    """
    from cam_server import CamClient, PipelineClient

    if kind == "camera":
        client = CamClient(camera_url) if camera_url else CamClient()
        return client.get_instance_stream(name)

    if kind == "camera_pipeline":
        _, stream = resolve_camera_pipeline(name, pipeline_url=pipeline_url, create=create)
        return stream

    client = PipelineClient(pipeline_url) if pipeline_url else PipelineClient()
    try:
        return client.get_instance_stream(name)
    except Exception:
        if not create:
            raise
        _, stream = client.create_instance_from_name(name)
        return stream


def capture_camera_snapshot(
    name,
    kind="camera_pipeline",
    pipeline_url=None,
    camera_url=None,
    n_average=1,
    animate=False,
    n_frames=8,
    fps=4.0,
    contrast_mode="auto",
    vmin=None,
    vmax=None,
    colormap="gray",
    log_scale=False,
    image_channel="image",
    receive_timeout=5.0,
    out_dir=None,
):
    """Grab one or more frames from a camera/pipeline stream, run them
    through the same FrameProcessor pipeline the live viewer uses
    (averaging/contrast/colormap), and save the result to a PNG
    (animate=False, the default) or an animated GIF (animate=True: n_frames
    consecutive output frames, played back at fps). No QApplication/window
    needed -- resolves the stream and reads frames directly, the same way
    _StreamWorker does but connecting just long enough to collect what's
    needed rather than running forever on a background thread.

    n_average>1: each *output* frame (the one PNG, or each GIF frame) is
    itself the running average of n_average raw frames (raw_per_output raw
    frames are pulled from the stream per output frame, not per capture --
    an animate=True GIF with n_average=5 and n_frames=8 reads 40 raw
    frames total, each of its 8 output frames a 5-frame average).

    Backs CameraBasler/CameraPCO.elog() (see eco.devices_general.
    cameras_swissfel) so a snapshot can be posted to the elog/scilog
    without a viewer window open, and CamServerStreamQt's own "Elog"
    toolbar button -- both paths call this the same way, so they produce
    identical images for the same settings.

    Returns (pathlib.Path, stats) -- stats is FrameProcessor.process's own
    stats dict (see its docstring) for the last frame captured, i.e. the
    one actually saved for a PNG, or the final frame of the GIF. The file
    is written under `out_dir` (default: the system temp directory) with a
    name embedding `name` and a timestamp; the caller owns cleanup (elog()
    deletes it once posted, since it's just a transient upload).
    """
    from pathlib import Path

    from bsread import SUB, Source

    address = resolve_stream(name, kind=kind, pipeline_url=pipeline_url, camera_url=camera_url)
    host, port = address.replace("tcp://", "").split(":")

    processor = FrameProcessor(average_n=max(1, int(n_average)), average_mode="running")
    processor.contrast_mode = contrast_mode
    processor.vmin = vmin
    processor.vmax = vmax
    processor.colormap = colormap
    processor.log_scale = log_scale

    n_needed = max(1, int(n_frames)) if animate else 1
    raw_per_output = max(1, int(n_average))
    displays = []
    stats = None
    with Source(
        host=host, port=int(port), mode=SUB, queue_size=10,
        receive_timeout=int(receive_timeout * 1000),
    ) as source:
        while len(displays) < n_needed:
            # pull raw_per_output *real* frames through the processor for
            # each output frame -- with average_mode="running" (a sliding
            # window of size raw_per_output), the display after the last of
            # them is exactly their mean; a malformed/heartbeat message
            # (value is None) doesn't count towards that
            got = 0
            while got < raw_per_output:
                message = source.receive()
                if message is None:
                    raise TimeoutError(
                        f"no frame received from {name!r} ({address}) within {receive_timeout}s"
                    )
                value = message.data.data.get(image_channel)
                if value is None or value.value is None:
                    continue
                display, stats = processor.process(value.value)
                got += 1
            displays.append(display)

    out_dir = Path(out_dir) if out_dir else Path(tempfile.gettempdir())
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    if animate:
        path = out_dir / f"{safe_name}_{timestamp}.gif"
        frames = [_display_array_to_pil(d) for d in displays]
        frames[0].save(
            path, save_all=True, append_images=frames[1:],
            duration=int(1000 / max(fps, 0.1)), loop=0,
        )
    else:
        path = out_dir / f"{safe_name}_{timestamp}.png"
        _display_array_to_pil(displays[-1]).save(path)
    return path, stats


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


def array_to_qimage(array):
    """uint8 numpy array -> QImage. 2D -> grayscale; (H, W, 3) -> RGB888;
    (H, W, 4) -> RGBA8888."""
    array = np.ascontiguousarray(array)
    if array.ndim == 2:
        h, w = array.shape
        return QtGui.QImage(array.tobytes(), w, h, w, QtGui.QImage.Format_Grayscale8)
    if array.ndim == 3 and array.shape[2] == 3:
        h, w, _ = array.shape
        return QtGui.QImage(array.tobytes(), w, h, w * 3, QtGui.QImage.Format_RGB888)
    if array.ndim == 3 and array.shape[2] == 4:
        h, w, _ = array.shape
        return QtGui.QImage(array.tobytes(), w, h, w * 4, QtGui.QImage.Format_RGBA8888)
    raise ValueError(f"unsupported array shape for display: {array.shape}")


def _display_array_to_pil(array):
    """uint8 numpy array -> PIL.Image, same shape convention as
    array_to_qimage (2D grayscale; (H, W, 3) RGB; (H, W, 4) RGBA) --
    PIL/Pillow rather than QImage specifically so capture_camera_snapshot
    can save PNGs/GIFs without a QApplication."""
    from PIL import Image

    array = np.ascontiguousarray(array)
    if array.ndim == 2:
        return Image.fromarray(array, mode="L")
    if array.ndim == 3 and array.shape[2] == 3:
        return Image.fromarray(array, mode="RGB")
    if array.ndim == 3 and array.shape[2] == 4:
        return Image.fromarray(array, mode="RGBA")
    raise ValueError(f"unsupported array shape for display: {array.shape}")


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


def colormap_gradient_stops(name, n=16):
    """n evenly-spaced (fraction, QColor) stops sampling a colormap's LUT
    -- for painting a QLinearGradient that matches what's actually on
    screen (used by _HistogramColorbar's colorbar strip)."""
    lut = get_colormap_lut(name)
    idx = np.linspace(0, 255, n).astype(int)
    return [
        (i / (n - 1), QtGui.QColor(int(lut[k, 0]), int(lut[k, 1]), int(lut[k, 2])))
        for i, k in enumerate(idx)
    ]


def compute_roi_from_drag(x0, y0, x1, y1):
    """Two drag corners (any order) -> (x, y, w, h)."""
    x, y = min(x0, x1), min(y0, y1)
    w, h = abs(x1 - x0), abs(y1 - y0)
    return (int(x), int(y), int(w), int(h))


def compute_fit_scale(viewport_w, viewport_h, raw_w, raw_h):
    """The "Fit" zoom scale: the largest scale that keeps a raw_w x raw_h
    image within a viewport_w x viewport_h viewport while preserving
    aspect ratio. Pure function (no Qt widget needed), used by
    CamServerStreamQt._compute_scale; floored at 0.02 so a degenerate
    (zero-size, not-yet-laid-out) viewport can't collapse the image to
    nothing."""
    if raw_w <= 0 or raw_h <= 0 or viewport_w <= 0 or viewport_h <= 0:
        return 1.0
    return max(min(viewport_w / raw_w, viewport_h / raw_h), 0.02)


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

    def _averaged(self, array):
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

    def process(self, raw_array):
        """raw 2D or (H,W,3/4) array -> (display uint8 array, stats dict)."""
        raw_array = np.asarray(raw_array)
        orig_dtype = raw_array.dtype
        color = self.is_color(raw_array)

        arr = self._averaged(raw_array)
        if self.subtract_background and self.background is not None and self.background.shape == arr.shape:
            arr = np.clip(arr - self.background, 0, None)
        arr = self._crop_roi(arr)

        vmin_used, vmax_used = None, None
        if color:
            display = np.clip(arr, 0, 255).astype(np.uint8)
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
                lo = float(arr.min()) if vmin is None else float(vmin)
                hi = float(arr.max()) if vmax is None else float(vmax)
                half = max(abs(lo - center), abs(hi - center), 1e-6)
                vmin, vmax = center - half, center + half

            gray = normalize_to_uint8(arr, vmin, vmax, log_scale=self.log_scale)
            # "gray" stays plain 2D (cheaper, and the historical/expected
            # shape for the default colormap); every other colormap -> RGB
            display = gray if self.colormap == "gray" else apply_colormap(gray, self.colormap)
            vmin_used = float(arr.min()) if vmin is None else float(vmin)
            vmax_used = float(arr.max()) if vmax is None else float(vmax)

        stats = {
            "min": float(arr.min()),
            "max": float(arr.max()),
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


class LivePipelineFields:
    """Background subscriber caching every *scalar* field a camera's own
    pipeline stream publishes alongside its image channel -- e.g. the
    center-of-mass/intensity/gaussian-fit outputs a "roi"/"processing"-type
    cam_server pipeline can be configured to compute (pshell's own screen
    panel shows these as its "analysis" readouts). Plain threading, no Qt
    -- usable with no window/QApplication at all, which is the point: see
    ScreenpanelAnalysis, which wraps this as camera.screenpanel_ana.<field>
    for reading directly or handing to eco.utilities.strip_plot.strip_plot.

    get_current_value()/wait_for_field() read the in-memory cache (instant
    once a field has been seen) rather than making a network call per
    read -- one connection is shared across every field of one camera,
    kept open by a single background thread started lazily on first use
    (see start()/ScreenpanelAnalysis.__getattr__)."""

    def __init__(
        self, camera_name, kind="camera_pipeline", pipeline_url=None,
        camera_url=None, image_channel="image",
    ):
        self.camera_name = camera_name
        self.kind = kind
        self.pipeline_url = pipeline_url
        self.camera_url = camera_url
        self.image_channel = image_channel
        self._values = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._error = None

    def start(self):
        if self._thread is not None:
            return
        self._error = None
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self):
        from bsread import SUB, Source

        try:
            address = resolve_stream(
                self.camera_name, kind=self.kind,
                pipeline_url=self.pipeline_url, camera_url=self.camera_url,
            )
        except Exception as exc:
            self._error = exc
            return
        host, port = address.replace("tcp://", "").split(":")
        try:
            with Source(
                host=host, port=int(port), mode=SUB, queue_size=10, receive_timeout=2000
            ) as source:
                while not self._stop_event.is_set():
                    message = source.receive()
                    if message is None:
                        continue
                    with self._lock:
                        for field_name, value in message.data.data.items():
                            if field_name == self.image_channel:
                                continue
                            if value is None or value.value is None:
                                continue
                            self._values[field_name] = value.value
        except Exception as exc:
            if not self._stop_event.is_set():
                self._error = exc

    def field_names(self):
        """Field names actually seen so far -- empty until the background
        thread has received at least one message; see the "CAVEAT" in
        ScreenpanelAnalysis's docstring for why it may stay empty forever
        for a plain, non-analysis pipeline."""
        with self._lock:
            return sorted(self._values)

    def get_current_value(self, field_name):
        """Instant, no network call -- raises KeyError if field_name
        hasn't been seen yet (use wait_for_field to block briefly for the
        first read instead)."""
        with self._lock:
            if field_name not in self._values:
                raise KeyError(
                    f"{field_name!r} not seen yet from {self.camera_name!r}'s pipeline "
                    f"stream (known fields so far: {sorted(self._values)})"
                )
            return self._values[field_name]

    def wait_for_field(self, field_name, timeout=5.0):
        deadline = time.time() + timeout
        while True:
            with self._lock:
                if field_name in self._values:
                    return self._values[field_name]
            if self._error is not None:
                raise RuntimeError(
                    f"pipeline stream for {self.camera_name!r} failed: {self._error}"
                ) from self._error
            if time.time() >= deadline:
                raise TimeoutError(
                    f"{field_name!r} not seen from {self.camera_name!r}'s pipeline stream "
                    f"within {timeout}s -- either it hasn't published a message yet, or its "
                    "pipeline isn't configured to compute this analysis field at all (see "
                    "ScreenpanelAnalysis's docstring)"
                )
            time.sleep(0.05)


class _LiveFieldDetector:
    """Detector-protocol (get_current_value()) view of one field of a
    LivePipelineFields cache -- what camera.screenpanel_ana.<field_name>
    returns (see ScreenpanelAnalysis). `.name` is read by e.g.
    eco.utilities.strip_plot.strip_plot's own _monitorable_name to label
    the plot trace when no explicit label is given."""

    def __init__(self, fields, field_name, name=None):
        self._fields = fields
        self.field_name = field_name
        self.name = name or f"{fields.camera_name}.{field_name}"

    def get_current_value(self):
        return self._fields.wait_for_field(self.field_name)

    def __repr__(self):
        return f"<live pipeline field {self.name!r}>"


class ScreenpanelAnalysis:
    """camera.screenpanel_ana.<field_name> -- a live, Detector-protocol
    view of one scalar field a camera's own default processing pipeline
    publishes alongside "image" (center of mass, intensity, gaussian-fit
    parameters, ...) -- for reading directly
    (camera.screenpanel_ana.intensity.get_current_value()) or strip-
    plotting (eco.utilities.strip_plot.strip_plot(camera.screenpanel_ana.
    intensity)), no viewer window needed. Mirrors pshell's own screen
    panel, which shows the same kind of pipeline-computed analysis
    readouts.

    CAVEAT: what fields (if any) exist entirely depends on the *pipeline's
    own server-side configuration*. The plain default pipeline eco's own
    resolve_camera_pipeline auto-creates for a camera (cam_server's own
    default pipeline_type) does no analysis at all and publishes nothing
    beyond "image" -- accessing e.g. .intensity on such a camera will time
    out waiting for a field that will never arrive. The camera's own
    pipeline needs to be configured (via its config_cs.config, or the
    cam_server web UI/API directly) to a pipeline_type that actually
    computes what you want (e.g. center-of-mass/background/good-region
    analysis) before there is anything here to read -- this class only
    reads whatever the pipeline already happens to publish, it does not
    request or configure any analysis itself.

    A background thread is started lazily on first attribute access (one
    per ScreenpanelAnalysis instance, i.e. shared across every field of
    one camera -- see LivePipelineFields); call stop() to end it."""

    def __init__(self, camera_name, kind="camera_pipeline", pipeline_url=None, camera_url=None):
        self._live = LivePipelineFields(
            camera_name, kind=kind, pipeline_url=pipeline_url, camera_url=camera_url
        )

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        self._live.start()
        return _LiveFieldDetector(self._live, name)

    def __dir__(self):
        return sorted(set(super().__dir__()) | set(self._live.field_names()))

    def field_names(self):
        """Analysis field names actually seen so far -- see class
        docstring's CAVEAT for why this can stay empty."""
        return self._live.field_names()

    def stop(self):
        self._live.stop()


class _StreamBridge(QtCore.QObject):
    frame_ready = QtCore.Signal(object)  # (array, pulse_id)
    status = QtCore.Signal(str)
    error = QtCore.Signal(str)


class _StreamWorker(threading.Thread):
    """Background thread: resolves the stream address via REST, subscribes
    with bsread, and emits each decoded frame through a Qt signal."""

    def __init__(
        self,
        name,
        kind,
        bridge,
        pipeline_url=None,
        camera_url=None,
        image_channel="image",
    ):
        super().__init__(daemon=True)
        self.name = name
        self.kind = kind
        self.bridge = bridge
        self.pipeline_url = pipeline_url
        self.camera_url = camera_url
        self.image_channel = image_channel
        self._stop_event = threading.Event()
        self._paused = threading.Event()

    def stop(self):
        self._stop_event.set()

    def pause(self, value=True):
        if value:
            self._paused.set()
        else:
            self._paused.clear()

    def run(self):
        from bsread import SUB, Source

        try:
            address = resolve_stream(
                self.name,
                kind=self.kind,
                pipeline_url=self.pipeline_url,
                camera_url=self.camera_url,
            )
        except Exception as exc:
            self.bridge.error.emit(f"could not resolve stream for {self.name!r}: {exc}")
            return

        host, port = address.replace("tcp://", "").split(":")
        try:
            with Source(
                host=host, port=int(port), mode=SUB, queue_size=10, receive_timeout=2000
            ) as source:
                self.bridge.status.emit(f"connected to {address}")
                while not self._stop_event.is_set():
                    if self._paused.is_set():
                        self._stop_event.wait(0.1)
                        continue
                    message = source.receive()
                    if message is None:
                        continue
                    value = message.data.data.get(self.image_channel)
                    if value is None or value.value is None:
                        continue
                    self.bridge.frame_ready.emit((value.value, message.data.pulse_id))
        except Exception as exc:
            if not self._stop_event.is_set():
                self.bridge.error.emit(str(exc))


class _DemoWorker(threading.Thread):
    """Synthetic frame source: a moving Gaussian blob (grayscale, or an RGB
    test pattern with color=True), no cam_server/bsread/network dependency.
    Used both for automated tests (see tests/test_camserver_panel_qt.py)
    and as an always-available way to demo the viewer/panel -- including
    the color-image path -- without occupying a real cam_server instance."""

    def __init__(self, bridge, rate=10.0, shape=(300, 400), color=False):
        super().__init__(daemon=True)
        self.bridge = bridge
        self.rate = rate
        self.shape = shape
        self.color = color
        self._stop_event = threading.Event()
        self._paused = threading.Event()

    def stop(self):
        self._stop_event.set()

    def pause(self, value=True):
        if value:
            self._paused.set()
        else:
            self._paused.clear()

    def run(self):
        self.bridge.status.emit("connected to demo source")
        h, w = self.shape
        yy, xx = np.mgrid[0:h, 0:w]
        t = 0
        pulse_id = 0
        interval = 1.0 / max(self.rate, 0.1)
        sigma = min(h, w) / 8.0
        while not self._stop_event.is_set():
            if self._paused.is_set():
                self._stop_event.wait(0.1)
                continue
            cx = w / 2 + (w / 3) * np.sin(t / 20.0)
            cy = h / 2 + (h / 3) * np.cos(t / 13.0)
            blob = 5000 * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma**2)))
            noise = np.random.normal(0, 50, size=(h, w))
            frame = np.clip(blob + noise, 0, 60000).astype(np.uint16)
            if self.color:
                rgb = np.stack(
                    [frame, np.roll(frame, 5, axis=0), np.roll(frame, -5, axis=1)], axis=-1
                ).astype(np.float32)
                frame = np.clip(rgb / 60000 * 255, 0, 255).astype(np.uint8)
            pulse_id += 1
            t += 1
            self.bridge.frame_ready.emit((frame, pulse_id))
            self._stop_event.wait(interval)


_DRAG_RECT_MODES = ("roi", "zoom", "calibrate_line", "measure")
_CLICK_MODES = ("marker", "calibrate_center")


class _ViewLabel(QtWidgets.QLabel):
    """QLabel with several direct-manipulation tools, chosen via
    `interaction_mode`: drag a rectangle (live feedback is the same
    rubber-band rectangle for all of these -- for "calibrate_line"/
    "measure" only the drag's two diagonal corners end up mattering, as a
    straight line between them) to select an ROI ("roi"), zoom into it
    ("zoom"), calibrate one axis against a known real-world distance
    ("calibrate_line" -- see CamServerStreamQt._on_line_dragged), or
    measure a distance ("measure" -- same signal, same handler, dispatched
    on interaction_mode); or click a single point to place the Marker
    crosshair ("marker") or set the calibration reference position
    ("calibrate_center"). Mouse positions are read in the label's own
    (on-screen, possibly zoomed) pixel space and converted to image-array
    pixel space via `zoom_scale` (kept in sync with whatever scale
    CamServerStreamQt._render_tick last drew at) before being emitted --
    so every tool stays correct at any zoom level, including "Fit"."""

    roi_dragged = QtCore.Signal(int, int, int, int)  # x0, y0, x1, y1 -- image pixel space
    zoom_dragged = QtCore.Signal(int, int, int, int)  # x0, y0, x1, y1 -- image pixel space
    line_dragged = QtCore.Signal(int, int, int, int)  # x0, y0, x1, y1 -- image pixel space
    marker_placed = QtCore.Signal(int, int)  # image pixel space
    calibrate_center_clicked = QtCore.Signal(int, int)  # image pixel space
    hovered = QtCore.Signal(int, int)  # image pixel space

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.interaction_mode = None  # None | "roi" | "zoom" | "marker" | ... -- see class docstring
        self.zoom_scale = 1.0
        self._press_pos = None
        self.setMouseTracking(True)
        self._rubber_band = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Rectangle, self)

    def _to_image_coords(self, pos):
        scale = self.zoom_scale or 1.0
        return int(pos.x() / scale), int(pos.y() / scale)

    def mousePressEvent(self, ev):
        if self.interaction_mode in _DRAG_RECT_MODES:
            self._press_pos = ev.pos()
            self._rubber_band.setGeometry(QtCore.QRect(self._press_pos, QtCore.QSize()))
            self._rubber_band.show()
        elif self.interaction_mode in _CLICK_MODES:
            x, y = self._to_image_coords(ev.pos())
            if self.interaction_mode == "marker":
                self.marker_placed.emit(x, y)
            else:
                self.calibrate_center_clicked.emit(x, y)

    def mouseMoveEvent(self, ev):
        x, y = self._to_image_coords(ev.pos())
        self.hovered.emit(x, y)
        if self.interaction_mode in _DRAG_RECT_MODES and self._press_pos is not None:
            # the rubber band itself stays in on-screen widget space so it
            # visually tracks the cursor; only the emitted result is
            # converted to image space (see mouseReleaseEvent)
            self._rubber_band.setGeometry(QtCore.QRect(self._press_pos, ev.pos()).normalized())

    def mouseReleaseEvent(self, ev):
        if self.interaction_mode in _DRAG_RECT_MODES and self._press_pos is not None:
            self._rubber_band.hide()
            x0, y0 = self._to_image_coords(self._press_pos)
            x1, y1 = self._to_image_coords(ev.pos())
            mode = self.interaction_mode
            self._press_pos = None
            if mode == "roi":
                self.roi_dragged.emit(x0, y0, x1, y1)
            elif mode == "zoom":
                self.zoom_dragged.emit(x0, y0, x1, y1)
            else:
                self.line_dragged.emit(x0, y0, x1, y1)


def value_to_y(value, height, data_min, data_max):
    """Map a data value to a pixel row in a vertical axis that runs high
    values at the top, low values at the bottom (span data_min..data_max
    over 0..height) -- the coordinate convention _HistogramColorbar draws
    with. Pure function, factored out of the widget for testing."""
    span = data_max - data_min
    if span <= 0:
        return height // 2
    frac = (value - data_min) / span
    return int(height * (1.0 - frac))


def y_to_value(y, height, data_min, data_max):
    """Inverse of value_to_y."""
    span = data_max - data_min
    frac = 1.0 - (y / height if height else 0.0)
    return data_min + frac * span


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


class _HistogramColorbar(QtWidgets.QWidget):
    """A side panel resembling pyqtgraph's ImageView histogram/colorscale:
    a vertical intensity histogram of the current frame next to a
    grayscale gradient colorbar, with two draggable handles for the
    black/white (vmin/vmax) level -- an interactive, always-on-screen
    alternative to typing numbers into the Manual-contrast spinboxes.
    Dragging a handle emits levels_changed(vmin, vmax); the viewer
    responds by switching to manual contrast mode with those values (see
    CamServerStreamQt._on_histogram_levels_changed) -- this widget itself
    has no notion of contrast "modes", it just visualizes/edits a range.

    A drag that does *not* start on either handle instead selects a new
    min/max region in one gesture: press-and-drag anywhere else in the
    widget, release, and both handles jump to the dragged span (whichever
    end is numerically lower becomes vmin) -- much faster than dragging
    each handle from its old position individually when the frame's actual
    range has moved well away from the current levels.

    pyqtgraph itself isn't part of eco's dependency set (not installed in
    the production env this was built against), so this reimplements the
    relevant slice natively in plain Qt rather than adding a dependency;
    swapping in real pyqtgraph.HistogramLUTWidget later, if it's ever
    added, would be a drop-in replacement for just this class."""

    levels_changed = QtCore.Signal(float, float)

    HANDLE_HIT_PX = 8  # generous grab tolerance -- the thin line itself is hard to hit exactly

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(90)
        self.setMouseTracking(True)
        self._data_min = 0.0
        self._data_max = 1.0
        self._vmin = 0.0
        self._vmax = 1.0
        self._hist_counts = None
        self._hist_edges = None
        self._colormap = "gray"
        self._dragging = None  # "min" | "max" | None
        self._hovering = None  # "min" | "max" | None -- for hover highlight/cursor
        self._region_drag_start = None  # widget-space y, or None -- see class docstring
        self._region_drag_current = None

    def set_data(self, array, vmin, vmax, colormap="gray"):
        """array: the pre-contrast (post average/background/ROI) frame;
        vmin/vmax: the currently active display range (handle positions);
        colormap: name from COLORMAP_NAMES, so the colorbar strip matches
        what's actually on screen."""
        self._hist_counts, self._hist_edges = compute_log_histogram(array)
        self._data_min = float(self._hist_edges[0])
        self._data_max = float(self._hist_edges[-1])
        self._vmin = self._data_min if vmin is None else float(vmin)
        self._vmax = self._data_max if vmax is None else float(vmax)
        self._colormap = colormap
        self.update()

    def _y(self, value):
        return value_to_y(value, self.height(), self._data_min, self._data_max)

    def _value(self, y):
        return y_to_value(y, self.height(), self._data_min, self._data_max)

    def _handle_at(self, y):
        if abs(y - self._y(self._vmin)) <= self.HANDLE_HIT_PX:
            return "min"
        if abs(y - self._y(self._vmax)) <= self.HANDLE_HIT_PX:
            return "max"
        return None

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        bar_w = 18
        hist_x0 = bar_w + 4
        hist_w = max(1, w - hist_x0 - 4)

        gradient = QtGui.QLinearGradient(0, h, 0, 0)
        for frac, color in colormap_gradient_stops(self._colormap):
            gradient.setColorAt(frac, color)
        painter.fillRect(0, 0, bar_w, h, gradient)

        if self._hist_counts is not None and self._hist_counts.max() > 0:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(120, 170, 220))
            max_count = float(self._hist_counts.max())
            for i, count in enumerate(self._hist_counts):
                y0 = self._y(self._hist_edges[i])
                y1 = self._y(self._hist_edges[i + 1])
                bar_len = int((count / max_count) * hist_w)
                painter.drawRect(hist_x0, min(y0, y1), bar_len, max(abs(y1 - y0), 1))

        if self._region_drag_start is not None and self._region_drag_current is not None:
            y0, y1 = sorted((self._region_drag_start, self._region_drag_current))
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(120, 170, 220, 90))
            painter.drawRect(0, y0, w, max(y1 - y0, 1))

        for key, value, color in (
            ("min", self._vmin, QtGui.QColor(230, 60, 60)),
            ("max", self._vmax, QtGui.QColor(60, 200, 90)),
        ):
            y = self._y(value)
            active = self._dragging == key or self._hovering == key
            pen = QtGui.QPen(color)
            pen.setWidth(3 if active else 2)
            painter.setPen(pen)
            painter.drawLine(0, y, w, y)
            # a filled triangular grip on the left edge -- a clearer
            # "grab here" affordance than the full-width line alone
            painter.setBrush(color)
            painter.setPen(QtCore.Qt.NoPen)
            tri = QtGui.QPolygon([QtCore.QPoint(0, y - 5), QtCore.QPoint(0, y + 5), QtCore.QPoint(7, y)])
            painter.drawPolygon(tri)

    def mousePressEvent(self, ev):
        self._dragging = self._handle_at(ev.pos().y())
        if self._dragging is None:
            self._region_drag_start = self._region_drag_current = ev.pos().y()

    def mouseMoveEvent(self, ev):
        if self._dragging is not None:
            value = self._value(ev.pos().y())
            if self._dragging == "min":
                self._vmin = min(value, self._vmax - 1e-6)
            else:
                self._vmax = max(value, self._vmin + 1e-6)
            self.update()
            self.levels_changed.emit(self._vmin, self._vmax)
            QtWidgets.QToolTip.showText(ev.globalPos(), f"{value:.4g}", self)
            return

        if self._region_drag_start is not None:
            self._region_drag_current = ev.pos().y()
            self.update()
            QtWidgets.QToolTip.showText(ev.globalPos(), f"{self._value(ev.pos().y()):.4g}", self)
            return

        hovering = self._handle_at(ev.pos().y())
        if hovering != self._hovering:
            self._hovering = hovering
            self.update()
        self.setCursor(QtCore.Qt.SizeVerCursor if hovering else QtCore.Qt.ArrowCursor)

    def mouseReleaseEvent(self, ev):
        if self._dragging is not None:
            self._dragging = None
            self.setCursor(QtCore.Qt.ArrowCursor)
            return

        if self._region_drag_start is not None:
            y0, y1 = self._region_drag_start, self._region_drag_current
            self._region_drag_start = self._region_drag_current = None
            if abs(y1 - y0) >= 3:  # a real drag, not just a stray click
                v0, v1 = self._value(y0), self._value(y1)
                self._vmin, self._vmax = min(v0, v1), max(v0, v1, min(v0, v1) + 1e-6)
                self.levels_changed.emit(self._vmin, self._vmax)
            self.update()

    def leaveEvent(self, event):
        self._hovering = None
        self.setCursor(QtCore.Qt.ArrowCursor)
        self.update()


def _marker_icon(color=QtGui.QColor(230, 60, 60), size=18):
    """Small drawn crosshair/marker icon -- pshell's own icon set isn't
    available/reusable here, so this draws the same visual language used
    for the Marker overlay itself (a plain crosshair) directly with
    QPainter rather than shipping an image asset."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    pen = QtGui.QPen(color)
    pen.setWidth(2)
    painter.setPen(pen)
    c = size / 2
    painter.drawLine(int(c), 2, int(c), size - 2)
    painter.drawLine(2, int(c), size - 2, int(c))
    painter.drawEllipse(QtCore.QPointF(c, c), 3, 3)
    painter.end()
    return QtGui.QIcon(pixmap)


def _reticle_icon(color=QtGui.QColor(60, 200, 90), size=18):
    """Small drawn reticle/target icon (circle + tick marks), matching
    the Reticle overlay's own visual language -- see _marker_icon."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    pen = QtGui.QPen(color)
    pen.setWidth(2)
    painter.setPen(pen)
    c = size / 2
    r = size / 2 - 3
    painter.drawEllipse(QtCore.QPointF(c, c), r, r)
    painter.drawLine(int(c), 1, int(c), 5)
    painter.drawLine(int(c), size - 5, int(c), size - 1)
    painter.drawLine(1, int(c), 5, int(c))
    painter.drawLine(size - 5, int(c), size - 1, int(c))
    painter.end()
    return QtGui.QIcon(pixmap)


def _ruler_icon(color=QtGui.QColor(230, 170, 40), size=18):
    """Small drawn ruler icon (a diagonal bar with tick marks) for the
    Measure tool -- see _marker_icon."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.translate(2, size - 2)
    painter.rotate(-45)
    pen = QtGui.QPen(color)
    pen.setWidth(2)
    painter.setPen(pen)
    length = size  # in the rotated/translated frame, along the local x axis
    painter.drawLine(0, 0, length, 0)
    for x in (0, length // 4, length // 2, 3 * length // 4, length):
        painter.drawLine(x, 0, x, -4)
    painter.end()
    return QtGui.QIcon(pixmap)


class _CalibrationDialog(QtWidgets.QDialog):
    """Direct pixel-size entry: physical units per raw pixel, for X and Y
    (a "linked" checkbox keeps them equal, the common square-pixel case),
    plus a unit label -- applied to the cursor/marker/ROI readouts in the
    status line (see CamServerStreamQt._calibrated/_update_status).
    Answers the same need as pshell's CamServerViewer.calibrate(), as a
    plain numeric-entry form rather than its click-two-points workflow."""

    def __init__(self, scale_x, scale_y, unit, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Calibrate")
        self.scale_x = scale_x
        self.scale_y = scale_y
        self.unit = unit

        layout = QtWidgets.QFormLayout(self)

        self._unit_edit = QtWidgets.QLineEdit(unit)
        layout.addRow("Unit name:", self._unit_edit)

        self._x_spin = QtWidgets.QDoubleSpinBox()
        self._x_spin.setDecimals(6)
        self._x_spin.setRange(1e-6, 1e6)
        self._x_spin.setValue(scale_x)
        self._x_spin.valueChanged.connect(self._on_x_changed)
        layout.addRow("Units per pixel (X):", self._x_spin)

        self._y_spin = QtWidgets.QDoubleSpinBox()
        self._y_spin.setDecimals(6)
        self._y_spin.setRange(1e-6, 1e6)
        self._y_spin.setValue(scale_y)
        layout.addRow("Units per pixel (Y):", self._y_spin)

        self._linked_cb = QtWidgets.QCheckBox("Linked X/Y (square pixels)")
        self._linked_cb.setChecked(scale_x == scale_y)
        self._linked_cb.toggled.connect(self._on_linked_toggled)
        layout.addRow(self._linked_cb)
        self._y_spin.setEnabled(not self._linked_cb.isChecked())

        reset_btn = QtWidgets.QPushButton("Reset to pixels")
        reset_btn.clicked.connect(self._on_reset)
        layout.addRow(reset_btn)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _on_linked_toggled(self, checked):
        self._y_spin.setEnabled(not checked)
        if checked:
            self._y_spin.setValue(self._x_spin.value())

    def _on_x_changed(self, value):
        if self._linked_cb.isChecked():
            self._y_spin.setValue(value)

    def _on_reset(self):
        self._unit_edit.setText("px")
        self._x_spin.setValue(1.0)
        self._y_spin.setValue(1.0)

    def _on_accept(self):
        self.unit = self._unit_edit.text().strip() or "px"
        self.scale_x = self._x_spin.value()
        self.scale_y = self._x_spin.value() if self._linked_cb.isChecked() else self._y_spin.value()
        self.accept()


class CamServerStreamQt:
    """Live-video Qt window for one cam_server camera/pipeline (or a local
    demo source). See the module docstring for the full feature list."""

    def __init__(
        self,
        name,
        kind="pipeline",
        pipeline_url=None,
        camera_url=None,
        embed=False,
        demo_color=False,
        rate_hz=10.0,
        theme=None,
        auto_start=True,
        cam=None,
        eco_name=None,
    ):
        self.name = name
        self.kind = kind
        self.pipeline_url = pipeline_url
        self.camera_url = camera_url
        self.embed = embed
        self.demo_color = demo_color
        self.theme = theme  # "dark" | "light" | None -- see eco.widgets.qt_theme
        # the eco Assembly (e.g. CameraBasler/CameraPCO) this viewer was
        # opened from, if any -- see cam._widget_viewer()/_default_widget = "_widget_viewer".
        # Only used to add a "Camera Settings" button (see _open_settings),
        # mirroring eco.widgets.camera_stream_qt.AxisPTZStreamQt's own
        # "Settings" button/cam parameter.
        self.cam = cam
        # the eco device's own alias name (e.g. "bernina.cam1"), if known --
        # NOT the same as `name` above (the pvname/pipeline name). Used for
        # the window title instead of `name` when given (see _build_window);
        # falls back to today's pvname-based title otherwise, so the plain
        # CLI with no originating eco device is unaffected.
        self.eco_name = eco_name
        self._settings_window = None
        # the window's own width the moment the settings dock was last
        # hidden (i.e. *with* the dock still visible) -- restored when the
        # dock is shown again, see _on_settings_toggled
        self._window_width_before_settings_hidden = None
        self.window = None
        self._worker = None
        self._processor = FrameProcessor()
        self._latest_payload = None
        self._last_display_array = None
        self._last_values_array = None  # pre-colormap values, for the cursor readout
        self._last_stats = None  # for the "Fix range" button
        self._marker_pos = None
        self._show_reticle = False
        # reticle center, in raw-frame pixel coords -- None means "true
        # image center" (the reticle's old, hardcoded-only behavior). Set
        # by the "Set center position only..." calibration tool, or loaded
        # from the camera's own server-side calibration on open (see
        # _load_camera_calibration) -- see _paint_overlays.
        self._reticle_center = None
        self._measurement = None  # (x0, y0, x1, y1) image px, or None -- see _on_line_dragged
        self._calib_line_stage = None  # None | "x" | "y" -- see _on_line_dragged
        self._calib_line_x_um_per_px = None  # staged X result while waiting for the Y line
        self._exclusive_tool_buttons = []  # see _wire_exclusive_tool
        self._tool_modes = {}
        self._hover_pos = None
        self._frame_count = 0
        self._fps_t0 = time.time()
        self._last_fps = 0.0
        self._rate_hz = rate_hz
        self._zoom_mode = "fit"  # "fit" | a float ratio (0.25/0.5/1.0/2.0/drag-to-zoom's own)
        self._pending_zoom_center = None  # (x, y) image coords -- see _on_zoom_dragged
        # calibration: physical units per raw pixel, for status/marker
        # readouts only -- doesn't affect ROI/reticle geometry (see
        # _CalibrationDialog and _update_status)
        self._cal_scale_x = 1.0
        self._cal_scale_y = 1.0
        self._cal_unit = "px"
        if auto_start:
            self.start()

    # -- window construction -------------------------------------------------

    def _build_window(self):
        global _app_ref
        if QtWidgets.QApplication.instance() is None:
            _app_ref = QtWidgets.QApplication([])
        from eco.widgets.qt_theme import apply_modern_theme

        apply_modern_theme(self.theme)

        # QMainWindow (not a bare QWidget) on purpose: it's what gives us
        # a real, overflow-capable QToolBar (a row of stacked QPushButtons
        # instead just kept forcing the window wider and wider as controls
        # were added -- a real toolbar degrades to a ">>" overflow chevron
        # instead) and a real collapsible QDockWidget for the "settings"
        # controls, mirroring pshell's own CamServerViewer structure
        # (decompiled fields include a `toolBar` and a togglable
        # `sidePanel`/`buttonSidePanel`) more closely than the original
        # stack of full-width rows did.
        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle(f"cam_server stream - {self.eco_name or self.name}")
        self.window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        if self.embed:
            self.window.setWindowFlags(QtCore.Qt.FramelessWindowHint)

        central = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(2, 2, 2, 2)

        self._label = _ViewLabel("connecting...")
        self._label.setScaledContents(False)
        self._label.roi_dragged.connect(self._on_roi_dragged)
        self._label.zoom_dragged.connect(self._on_zoom_dragged)
        self._label.line_dragged.connect(self._on_line_dragged)
        self._label.marker_placed.connect(self._on_marker_placed)
        self._label.calibrate_center_clicked.connect(self._on_calibrate_center_clicked)
        self._label.hovered.connect(self._on_hover)

        # scrollable viewport: without this, an oversized image (a raw
        # sensor frame can easily be several thousand pixels a side) just
        # keeps growing the window to match it; wrapping the label lets
        # zoom modes other than 1:1 make sense and, at 1:1/2x on a big
        # frame, lets you pan via scrollbars instead
        self._scroll_area = QtWidgets.QScrollArea()
        self._scroll_area.setWidget(self._label)
        self._scroll_area.setWidgetResizable(False)

        self._histogram = _HistogramColorbar()
        self._histogram.levels_changed.connect(self._on_histogram_levels_changed)

        image_row = QtWidgets.QHBoxLayout()
        image_row.addWidget(self._scroll_area, 1)
        image_row.addWidget(self._histogram)
        outer.addLayout(image_row)

        self._status_label = QtWidgets.QLabel("")
        outer.addWidget(self._status_label)
        self.window.setCentralWidget(central)

        self._add_toolbars()
        settings_dock = self._build_settings_dock()
        self.window.addDockWidget(QtCore.Qt.RightDockWidgetArea, settings_dock)
        self._settings_dock = settings_dock

        # close_calls_stop, not a plain destroyed.connect(setattr(...)):
        # self._worker is a plain background thread, which Qt's own
        # child-deletion never stops on its own -- closing via the
        # window's native close (X) button needs to actually run stop()
        # (which stops the worker), not just clear a reference. See
        # eco.widgets.qt_lifecycle's module docstring for the fuller why.
        from eco.widgets.qt_lifecycle import close_calls_stop

        close_calls_stop(self.window, self.stop)

        self._bridge = _StreamBridge()
        self._bridge.frame_ready.connect(self._on_frame_ready)
        self._bridge.error.connect(self._apply_error)
        self._bridge.status.connect(self._apply_status)

        if self.kind == "demo":
            self._worker = _DemoWorker(self._bridge, color=self.demo_color)
        else:
            self._worker = _StreamWorker(
                self.name,
                self.kind,
                self._bridge,
                pipeline_url=self.pipeline_url,
                camera_url=self.camera_url,
            )
        self._worker.start()
        self._load_camera_calibration()

        self._render_timer = QtCore.QTimer(self.window)
        self._render_timer.timeout.connect(self._render_tick)
        self._set_rate(self._rate_hz)
        self._render_timer.start()

        # a fixed starting size, independent of image size -- once a
        # top-level window has been explicitly resized, Qt stops
        # auto-growing it to fit its content, which is what we want now
        # that oversized images are handled by the scroll area/zoom modes
        # instead
        self.window.resize(780, 620)
        self.window.show()

        if self.embed:
            print(f"WINID {int(self.window.winId())}", flush=True)

    def _add_toolbars(self):
        """The always-visible, one-click/glance controls -- everything
        more detailed (numeric ranges, colormap choice) lives in the
        collapsible "Settings" dock instead (see
        _build_settings_dock); toggled from here.

        Split across two rows via addToolBarBreak() rather than one long
        toolbar: a single overloaded QToolBar relies on an easy-to-miss
        ">>" overflow chevron once it doesn't fit the window width, which
        silently hides actions (Marker/Reticle/background/Settings/Close
        all vanished behind it at the default 700px width). Two shorter,
        logically-grouped rows both fit without overflowing."""
        view_bar = QtWidgets.QToolBar("View controls", self.window)
        view_bar.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        view_bar.setMovable(False)

        if self.cam is not None:
            # only present when this viewer was opened from a real eco
            # camera Assembly (cam._widget_viewer()), not e.g. the standalone CLI --
            # named "Camera Settings" rather than plain "Settings" to avoid
            # clashing with the dock-visibility "Settings" action in
            # bg_bar below (a different thing: this opens the device's own
            # plain property-grid widget, mirroring
            # eco.widgets.camera_stream_qt.AxisPTZStreamQt's "Settings"
            # button -- see _open_settings)
            settings_btn = QtWidgets.QAction("Camera Settings", self.window)
            settings_btn.triggered.connect(self._open_settings)
            view_bar.addAction(settings_btn)

            if hasattr(self.cam, "elog"):
                elog_btn = QtWidgets.QAction("Elog", self.window)
                elog_btn.setToolTip(
                    "Post the current image (with averaging/color-limit settings) to the elog"
                )
                elog_btn.triggered.connect(self._open_elog)
                view_bar.addAction(elog_btn)

            view_bar.addSeparator()

        self._pause_btn = QtWidgets.QAction("Pause", self.window)
        self._pause_btn.setCheckable(True)
        self._pause_btn.toggled.connect(self._on_pause_toggled)
        view_bar.addAction(self._pause_btn)
        view_bar.addSeparator()

        self._zoom_group = QtWidgets.QActionGroup(self.window)
        self._zoom_group.setExclusive(True)
        self._zoom_buttons = {}
        for label, mode in (("Fit", "fit"), ("0.25x", 0.25), ("0.5x", 0.5), ("1:1", 1.0), ("2x", 2.0)):
            action = QtWidgets.QAction(label, self.window)
            action.setCheckable(True)
            action.setChecked(mode == self._zoom_mode)
            self._zoom_group.addAction(action)
            self._zoom_buttons[action] = mode
            view_bar.addAction(action)
        self._zoom_group.triggered.connect(self._on_zoom_changed)

        self._zoom_drag_btn = QtWidgets.QAction("Zoom", self.window)
        self._zoom_drag_btn.setCheckable(True)
        self._zoom_drag_btn.setToolTip("Drag a rectangle on the image to zoom into it")
        self._wire_exclusive_tool(self._zoom_drag_btn, "zoom")
        view_bar.addAction(self._zoom_drag_btn)
        view_bar.addSeparator()

        self._roi_btn = QtWidgets.QAction("Set ROI", self.window)
        self._roi_btn.setCheckable(True)
        self._wire_exclusive_tool(self._roi_btn, "roi")
        view_bar.addAction(self._roi_btn)
        clear_roi_action = QtWidgets.QAction("Clear ROI", self.window)
        clear_roi_action.triggered.connect(self._on_clear_roi)
        view_bar.addAction(clear_roi_action)

        self.window.addToolBar(view_bar)
        self.window.addToolBarBreak()

        # Annotation + first background action -- kept on their own row
        # since "annotate" (301px) + "background" (551px) + "settings/close"
        # (171px) together (~1080px, measured via sizeHint()) don't fit
        # even a fairly generous window, and a QToolBar's own overflow
        # chevron turned out to render invisibly under some platforms
        # (offscreen included) -- silently swallowing actions with no
        # visible affordance. Three modest rows beat one row that might
        # secretly be hiding "Close".
        annotate_bar = QtWidgets.QToolBar("Annotation", self.window)
        annotate_bar.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        annotate_bar.setMovable(False)

        self._marker_btn = QtWidgets.QAction(_marker_icon(), "Marker", self.window)
        self._marker_btn.setCheckable(True)
        self._wire_exclusive_tool(self._marker_btn, "marker")
        annotate_bar.addAction(self._marker_btn)
        clear_marker_action = QtWidgets.QAction("Clear marker", self.window)
        clear_marker_action.triggered.connect(self._on_clear_marker)
        annotate_bar.addAction(clear_marker_action)

        self._reticle_btn = QtWidgets.QAction(_reticle_icon(), "Reticle", self.window)
        self._reticle_btn.setCheckable(True)
        self._reticle_btn.toggled.connect(self._on_reticle_toggled)
        annotate_bar.addAction(self._reticle_btn)

        self._measure_btn = QtWidgets.QAction(_ruler_icon(), "Measure", self.window)
        self._measure_btn.setCheckable(True)
        self._measure_btn.setToolTip("Drag between two points to measure the distance between them")
        self._wire_exclusive_tool(self._measure_btn, "measure")
        annotate_bar.addAction(self._measure_btn)
        clear_measure_action = QtWidgets.QAction("Clear measurement", self.window)
        clear_measure_action.triggered.connect(self._on_clear_measurement)
        annotate_bar.addAction(clear_measure_action)

        # "Calibrate" as one dropdown rather than 3 more toolbar buttons --
        # everything here writes/reads the camera's own server-side
        # camera_calibration config when this viewer has a real camera
        # (self.cam is not None -- the same field pshell's own screen
        # panel uses for its reticle, and what
        # eco.devices_general.cameras_swissfel.CameraBasler.set_cross()
        # used to write by hand); falls back to a viewer-local-only
        # calibration (this session only, nothing persisted) otherwise --
        # see _on_line_dragged/_on_calibrate_center_clicked for exactly
        # which case applies when.
        calibrate_menu_btn = QtWidgets.QToolButton(self.window)
        calibrate_menu_btn.setText("Calibrate")
        calibrate_menu_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        calibrate_menu_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        calibrate_menu = QtWidgets.QMenu(calibrate_menu_btn)

        numeric_action = calibrate_menu.addAction("Numeric entry...")
        numeric_action.triggered.connect(self._on_calibrate_numeric)

        self._calibrate_line_btn = calibrate_menu.addAction("2-line (known distances)...")
        self._calibrate_line_btn.setCheckable(True)
        self._calibrate_line_btn.setToolTip(
            "Drag a line along a known X distance, then a line along a known Y "
            "distance -- sets the scale (position unchanged)"
        )
        self._wire_exclusive_tool(self._calibrate_line_btn, "calibrate_line")
        self._calibrate_line_btn.toggled.connect(self._on_calibrate_line_tool_toggled)

        self._calibrate_center_btn = calibrate_menu.addAction("Set center position only...")
        self._calibrate_center_btn.setCheckable(True)
        self._calibrate_center_btn.setToolTip(
            "Click the new reference position -- keeps the current scale unchanged"
        )
        self._wire_exclusive_tool(self._calibrate_center_btn, "calibrate_center")

        calibrate_menu_btn.setMenu(calibrate_menu)
        annotate_bar.addWidget(calibrate_menu_btn)

        self.window.addToolBar(annotate_bar)
        self.window.addToolBarBreak()

        bg_bar = QtWidgets.QToolBar("Background && settings", self.window)
        bg_bar.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        bg_bar.setMovable(False)

        grab_bg_action = QtWidgets.QAction("Grab background", self.window)
        grab_bg_action.triggered.connect(self._on_grab_background)
        bg_bar.addAction(grab_bg_action)
        self._subtract_cb = QtWidgets.QAction("Subtract background", self.window)
        self._subtract_cb.setCheckable(True)
        self._subtract_cb.toggled.connect(self._on_subtract_toggled)
        bg_bar.addAction(self._subtract_cb)
        clear_bg_action = QtWidgets.QAction("Clear background", self.window)
        clear_bg_action.triggered.connect(self._on_clear_background)
        bg_bar.addAction(clear_bg_action)

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        bg_bar.addWidget(spacer)

        settings_action = QtWidgets.QAction("Settings", self.window)
        settings_action.setCheckable(True)
        settings_action.setChecked(True)
        settings_action.toggled.connect(self._on_settings_toggled)
        bg_bar.addAction(settings_action)
        self._settings_action = settings_action

        close_action = QtWidgets.QAction("Close", self.window)
        close_action.triggered.connect(self.stop)
        bg_bar.addAction(close_action)

        self.window.addToolBar(bg_bar)

    def _build_settings_dock(self):
        """Everything more detailed than a one-click toggle: rate limit,
        contrast range/mode, colormap, averaging, calibration. Collapsible
        (see the toolbar's "Settings" action) so a viewer that's just
        being glanced at can stay compact."""
        dock = QtWidgets.QDockWidget("Settings", self.window)
        dock.setFeatures(
            QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFloatable
        )
        panel = QtWidgets.QWidget()
        panel.setMaximumWidth(400)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.addLayout(self._build_rate_row())
        layout.addLayout(self._build_contrast_row())
        layout.addLayout(self._build_colormap_row())
        layout.addLayout(self._build_averaging_row())
        layout.addStretch(1)
        dock.setWidget(panel)
        return dock

    def _build_rate_row(self):
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Rate limit (Hz):"))
        self._rate_spin = QtWidgets.QDoubleSpinBox()
        self._rate_spin.setRange(0.5, 60.0)
        self._rate_spin.setValue(self._rate_hz)
        self._rate_spin.valueChanged.connect(self._set_rate)
        row.addWidget(self._rate_spin)
        row.addStretch(1)
        return row

    def _build_contrast_row(self):
        # A narrow, multi-line layout (rather than one long QHBoxLayout) so
        # the settings dock stays compact -- cramming the mode radios, both
        # range spinboxes, and the log-scale checkbox onto a single row was
        # what forced the dock (and the whole window) nearly 1000px wide.
        container = QtWidgets.QVBoxLayout()
        container.setSpacing(4)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Contrast:"))
        self._contrast_group = QtWidgets.QButtonGroup(self.window)
        self._auto_radio = QtWidgets.QRadioButton("Auto")
        self._manual_radio = QtWidgets.QRadioButton("Manual")
        self._full_radio = QtWidgets.QRadioButton("Full range")
        self._auto_radio.setChecked(True)
        for i, btn in enumerate((self._auto_radio, self._manual_radio, self._full_radio)):
            self._contrast_group.addButton(btn, i)
            mode_row.addWidget(btn)
        self._contrast_group.buttonClicked.connect(self._on_contrast_mode_changed)
        mode_row.addStretch(1)
        container.addLayout(mode_row)

        range_row = QtWidgets.QHBoxLayout()
        range_row.addWidget(QtWidgets.QLabel("min:"))
        self._vmin_spin = QtWidgets.QDoubleSpinBox()
        self._vmin_spin.setRange(-1e9, 1e9)
        self._vmin_spin.setMaximumWidth(90)
        self._vmin_spin.setEnabled(False)
        self._vmin_spin.valueChanged.connect(self._on_vmin_changed)
        range_row.addWidget(self._vmin_spin)

        range_row.addWidget(QtWidgets.QLabel("max:"))
        self._vmax_spin = QtWidgets.QDoubleSpinBox()
        self._vmax_spin.setRange(-1e9, 1e9)
        self._vmax_spin.setValue(1000)
        self._vmax_spin.setMaximumWidth(90)
        self._vmax_spin.setEnabled(False)
        self._vmax_spin.valueChanged.connect(self._on_vmax_changed)
        range_row.addWidget(self._vmax_spin)
        range_row.addStretch(1)
        container.addLayout(range_row)

        tools_row = QtWidgets.QHBoxLayout()
        # one-click alternative to dragging the histogram handles --
        # freezes whatever range is currently in effect (e.g. auto's
        # per-frame min/max) into a fixed manual range, mirroring
        # pshell's CamServerViewer.btFixColormapRange
        fix_btn = QtWidgets.QPushButton("Fix range")
        fix_btn.setToolTip("Freeze the current min/max into a fixed manual range")
        fix_btn.clicked.connect(self._on_fix_range_clicked)
        tools_row.addWidget(fix_btn)

        self._log_scale_cb = QtWidgets.QCheckBox("Log scale")
        self._log_scale_cb.toggled.connect(self._on_log_scale_toggled)
        tools_row.addWidget(self._log_scale_cb)
        tools_row.addStretch(1)
        container.addLayout(tools_row)

        return container

    _COLORMAP_LABELS = (
        ("Gray", "gray"),
        ("Gray (inverted)", "gray_inverted"),
        ("Viridis", "viridis"),
        ("Diverging (around 0)", "diverging_zero"),
        ("Diverging (around 1)", "diverging_one"),
    )

    def _build_colormap_row(self):
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Colormap:"))
        self._colormap_combo = QtWidgets.QComboBox()
        self._colormap_combo.addItems([label for label, _name in self._COLORMAP_LABELS])
        self._colormap_combo.currentIndexChanged.connect(self._on_colormap_changed)
        row.addWidget(self._colormap_combo)
        row.addStretch(1)
        return row

    def _build_averaging_row(self):
        container = QtWidgets.QVBoxLayout()
        container.setSpacing(4)

        n_row = QtWidgets.QHBoxLayout()
        n_row.addWidget(QtWidgets.QLabel("Average N frames:"))
        self._average_spin = QtWidgets.QSpinBox()
        self._average_spin.setRange(1, 200)
        self._average_spin.valueChanged.connect(self._on_average_changed)
        n_row.addWidget(self._average_spin)
        n_row.addStretch(1)
        container.addLayout(n_row)

        # "Running" (default): a continuously-updated sliding window, mean
        # recomputed on every new frame -- what this viewer already did
        # before this toggle existed. "Single": accumulate N frames, show
        # their mean once, then start a fresh batch -- refreshes only every
        # N raw frames instead of every frame. Mirrors pshell's own
        # CamServerViewer averaging (see FrameProcessor.average_mode).
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Mode:"))
        self._average_mode_group = QtWidgets.QButtonGroup(self.window)
        self._running_radio = QtWidgets.QRadioButton("Running")
        self._single_radio = QtWidgets.QRadioButton("Single")
        self._running_radio.setChecked(True)
        self._running_radio.setToolTip(
            "Continuously-updated rolling average of the last N frames"
        )
        self._single_radio.setToolTip(
            "Accumulate N frames, show their average once, then start a fresh batch "
            "(display refreshes every N frames instead of every frame)"
        )
        for btn in (self._running_radio, self._single_radio):
            self._average_mode_group.addButton(btn)
            mode_row.addWidget(btn)
        self._average_mode_group.buttonClicked.connect(self._on_average_mode_changed)
        mode_row.addStretch(1)
        container.addLayout(mode_row)

        return container

    # -- control handlers ------------------------------------------------

    def _set_rate(self, hz):
        self._rate_hz = hz
        self._render_timer.setInterval(max(1, int(1000 / max(hz, 0.1))))

    def _on_pause_toggled(self, checked):
        if self._worker is not None:
            self._worker.pause(checked)

    def _on_contrast_mode_changed(self, _btn):
        if self._auto_radio.isChecked():
            self._processor.contrast_mode = "auto"
        elif self._manual_radio.isChecked():
            self._processor.contrast_mode = "manual"
        else:
            self._processor.contrast_mode = "full"
        manual = self._manual_radio.isChecked()
        self._vmin_spin.setEnabled(manual)
        self._vmax_spin.setEnabled(manual)

    def _on_vmin_changed(self, v):
        self._processor.vmin = v

    def _on_vmax_changed(self, v):
        self._processor.vmax = v

    def _on_fix_range_clicked(self):
        """Freeze the currently-in-effect range (e.g. auto's own last
        per-frame min/max) as a fixed manual range -- a one-click
        alternative to dragging the histogram handles, mirroring pshell's
        CamServerViewer.btFixColormapRange."""
        if self._last_stats is None or self._last_stats.get("color"):
            return
        vmin, vmax = self._last_stats.get("vmin"), self._last_stats.get("vmax")
        if vmin is None or vmax is None:
            return
        self._on_histogram_levels_changed(vmin, vmax)

    def _on_log_scale_toggled(self, checked):
        self._processor.log_scale = checked

    def _on_colormap_changed(self, index):
        _label, name = self._COLORMAP_LABELS[index]
        self._processor.colormap = name

    def _on_zoom_changed(self, btn):
        self._zoom_mode = self._zoom_buttons[btn]

    def _compute_scale(self, raw_h, raw_w):
        if self._zoom_mode == "fit":
            viewport = self._scroll_area.viewport().size()
            return compute_fit_scale(viewport.width(), viewport.height(), raw_w, raw_h)
        return float(self._zoom_mode)

    def _apply_pending_zoom_center(self, scale):
        # runs right after _render_tick has resized the label to the new
        # scale, so the scroll area's scrollbar ranges already reflect it
        cx, cy = self._pending_zoom_center
        self._pending_zoom_center = None
        viewport = self._scroll_area.viewport().size()
        hbar, vbar = self._scroll_area.horizontalScrollBar(), self._scroll_area.verticalScrollBar()
        hbar.setValue(round(cx * scale - viewport.width() / 2))
        vbar.setValue(round(cy * scale - viewport.height() / 2))

    def _current_reticle_center(self):
        """The reticle's current center in raw-frame pixel coords -- the
        explicitly-set one if there is one, else the true center of the
        last displayed frame (or (0, 0) if no frame has been shown yet)."""
        if self._reticle_center is not None:
            return self._reticle_center
        if self._last_display_array is not None:
            h, w = self._last_display_array.shape[:2]
            return w / 2.0, h / 2.0
        return 0.0, 0.0

    def _persist_calibration_async(self, x, y, x_um_per_px=None, y_um_per_px=None):
        """Move the reticle to (x, y) and, if given, adopt x_um_per_px/
        y_um_per_px as the new local scale -- then, if this viewer has a
        real camera (self.cam is not None), write the same to its server-
        side camera_calibration config (the field pshell's own screen
        panel reads -- see eco.devices_general.cameras_swissfel.
        set_camera_calibration, which does the actual write and keeps
        whichever of x_um_per_px/y_um_per_px is left None unchanged from
        the existing calibration -- "set center position only" passes
        both as None). A viewer with no camera (kind="camera_pipeline"
        with no cam=, or a standalone kind="camera"/"pipeline"/"demo"
        viewer) only ever updates the local reticle/scale -- there is
        nothing to persist to.

        The actual write is real network I/O, so it runs on a background
        thread (mirrors _open_elog's docstring for exactly why -- freezing
        this window for the duration would be exactly the kind of freeze
        eco.utilities.strip_plot's own module docstring calls out)."""
        self._reticle_center = (x, y)
        if x_um_per_px is not None:
            self._cal_scale_x = x_um_per_px
        if y_um_per_px is not None:
            self._cal_scale_y = y_um_per_px
        if self.cam is None:
            return

        def do_write():
            from ..devices_general.cameras_swissfel import set_camera_calibration

            try:
                set_camera_calibration(self.cam, x, y, x_um_per_px, y_um_per_px)
                self._bridge.status.emit("calibration saved")
            except Exception as exc:
                self._bridge.error.emit(f"failed to save calibration: {exc}")

        threading.Thread(target=do_write, daemon=True).start()

    def _load_camera_calibration(self):
        """Load this viewer's reticle position/scale from the camera's own
        server-side camera_calibration config, if there is one -- so
        opening the viewer reflects whatever was last calibrated (e.g. via
        CameraBasler.set_cross() previously, or this viewer's own
        Calibrate menu in an earlier session) instead of always starting
        at "no calibration, dead-center reticle". Runs on a background
        thread (network I/O -- see _persist_calibration_async); silently
        leaves the defaults in place on any error or if there simply is no
        calibration yet."""
        if self.cam is None:
            return

        def do_load():
            from ..devices_general.cameras_swissfel import get_camera_calibration

            try:
                existing = get_camera_calibration(self.cam)
            except Exception:
                return
            if existing is None:
                return
            cx, cy, x_um_per_px, y_um_per_px = existing
            self._reticle_center = (cx, cy)
            if x_um_per_px:
                self._cal_scale_x = x_um_per_px
            if y_um_per_px:
                self._cal_scale_y = y_um_per_px
            if (x_um_per_px or y_um_per_px) and self._cal_unit == "px":
                # camera_calibration itself carries no unit-name string
                # (just numeric width/height) -- "um" matches the
                # x_um_per_px/y_um_per_px naming convention used
                # throughout eco (e.g. CameraBasler.set_cross()) for this
                # same field. Only promotes the still-uncalibrated default
                # -- never overrides a unit name the numeric-entry dialog
                # already set explicitly (including a deliberate "px" reset).
                self._cal_unit = "um"

        threading.Thread(target=do_load, daemon=True).start()

    def _on_calibrate_numeric(self):
        dialog = _CalibrationDialog(
            self._cal_scale_x, self._cal_scale_y, self._cal_unit, self.window
        )
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._cal_unit = dialog.unit
            x, y = self._current_reticle_center()
            self._persist_calibration_async(x, y, dialog.scale_x, dialog.scale_y)

    def _on_calibrate_line_tool_toggled(self, checked):
        if not checked:
            # covers every way out of the tool: both stages completed,
            # explicitly cancelled mid-stage (see _on_calibrate_line_dragged),
            # or switched away to a different tool before finishing (the
            # exclusive-tool wiring unchecks us the same way) -- always
            # start clean next time rather than silently resuming a stale
            # X result from an abandoned earlier attempt
            self._calib_line_stage = None
            self._calib_line_x_um_per_px = None
            return
        if self._cal_unit == "px":
            # camera_calibration carries no unit-name string of its own
            # (just numeric width/height, see get/set_camera_calibration),
            # and asking "known X distance, in px" would be nonsensical --
            # get a real unit name once, up front, rather than per line
            unit, ok = QtWidgets.QInputDialog.getText(
                self.window, "Calibrate",
                "Unit name for the known distances you're about to enter:",
                text="um",
            )
            if not ok or not unit.strip():
                self._calibrate_line_btn.setChecked(False)
                return
            self._cal_unit = unit.strip()
        self._apply_status("calibration: drag a line along a known X distance")

    def _on_line_dragged(self, x0, y0, x1, y1):
        # shared by "measure" and "calibrate_line" (see _ViewLabel's
        # docstring) -- interaction_mode is unchanged since the drag
        # started (a mode switch can't happen mid-gesture), so it still
        # correctly says which tool this drag belongs to
        mode = self._label.interaction_mode
        if mode == "measure":
            self._on_measure_dragged(x0, y0, x1, y1)
        elif mode == "calibrate_line":
            self._on_calibrate_line_dragged(x0, y0, x1, y1)

    def _on_measure_dragged(self, x0, y0, x1, y1):
        if ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 < 2:
            return  # accidental click, not a real drag
        self._measurement = (x0, y0, x1, y1)
        # deliberately left checked/active -- unlike ROI/zoom's one-shot
        # tools, a ruler is normally used for several measurements in a
        # row; toggle it off yourself (or pick a different tool) when done

    def _on_clear_measurement(self):
        self._measurement = None

    def _on_calibrate_line_dragged(self, x0, y0, x1, y1):
        stage = self._calib_line_stage or "x"
        pixel_length = abs(x1 - x0) if stage == "x" else abs(y1 - y0)
        if pixel_length < 2:
            return  # accidental click, not a real drag
        distance, ok = QtWidgets.QInputDialog.getDouble(
            self.window,
            "Calibrate",
            f"Known {stage.upper()}-axis distance for this line "
            f"({pixel_length} px, in your calibration unit -- currently "
            f"{self._cal_unit!r}):",
            1.0, 1e-9, 1e12, 6,
        )
        if not ok:
            self._calibrate_line_btn.setChecked(False)  # also resets staged state, see toggled handler
            return
        um_per_px = distance / pixel_length
        if stage == "x":
            self._calib_line_x_um_per_px = um_per_px
            self._calib_line_stage = "y"
            self._apply_status("calibration: now drag a line along a known Y distance")
            return  # tool stays active for the second line
        x_um_per_px = self._calib_line_x_um_per_px
        y_um_per_px = um_per_px
        self._calibrate_line_btn.setChecked(False)
        cx, cy = self._current_reticle_center()
        self._persist_calibration_async(cx, cy, x_um_per_px, y_um_per_px)

    def _on_calibrate_center_clicked(self, x, y):
        self._calibrate_center_btn.setChecked(False)
        self._persist_calibration_async(x, y)  # x_um_per_px/y_um_per_px default None -- keep existing scale

    def _on_settings_toggled(self, checked):
        """Show/hide the settings dock -- and, unlike a bare setVisible(),
        also shrink/grow the window's own width to match: QMainWindow
        doesn't do this on its own once the window has had an explicit
        resize() called on it (see the "fixed starting size" comment on
        the window.resize() call in _build_window -- the same fixed-size
        behavior that stops it auto-*growing* to fit content also stops it
        auto-*shrinking* when a dock hides, leaving dead space instead)."""
        window, dock = self.window, self._settings_dock
        if checked:
            dock.setVisible(True)
            if self._window_width_before_settings_hidden is not None:
                window.resize(self._window_width_before_settings_hidden, window.height())
                self._window_width_before_settings_hidden = None
        else:
            # capture the *current* (dock-visible) width so re-showing the
            # dock later restores exactly this size, then shrink back down
            # by the dock's own width (read before it's hidden -- an
            # already-hidden dock's width reads back as 0)
            self._window_width_before_settings_hidden = window.width()
            window.resize(max(1, window.width() - dock.width()), window.height())
            dock.setVisible(False)

    def _open_settings(self):
        # normal=True: this button specifically wants the plain property
        # grid -- without it, since CameraBasler/CameraPCO set
        # _default_widget = "_widget_viewer", plain self.cam.widget() would just
        # reopen this same live viewer instead of the settings widget (see
        # Assembly.widget()'s normal= docstring, and
        # eco.widgets.camera_stream_qt.AxisPTZStreamQt._open_settings,
        # which this mirrors)
        #
        # keep a reference so the window (and its poll thread) isn't
        # garbage-collected as soon as this method returns
        self._settings_window = self.cam.widget(normal=True)

    def _open_elog(self):
        """Post the current settings (and a fresh capture at them) to the
        elog, via self.cam.elog() -- see CameraBasler/CameraPCO.elog() and
        capture_camera_snapshot, which both this button and cam.elog()
        called directly (no viewer open) go through, so they produce
        identical images for the same settings.

        Runs the actual capture+post on a background thread (mirrors
        AxisPTZStreamQt._dispatch): it opens its own short-lived stream
        connection and does network I/O to the elog/scilog server, either
        of which can take a real moment -- doing that on the GUI thread
        would freeze this window for the duration, the exact kind of
        freeze eco.utilities.strip_plot's own module docstring calls out
        for the same reason (blocking work on the thread that owns the Qt/
        IPython event loop)."""
        comment, ok = QtWidgets.QInputDialog.getMultiLineText(
            self.window, "Post to elog", "Comment (optional):"
        )
        if not ok:
            return
        proc = self._processor

        def do_post():
            try:
                self.cam.elog(
                    comment=comment,
                    n_average=proc.average_n,
                    contrast_mode=proc.contrast_mode,
                    vmin=proc.vmin,
                    vmax=proc.vmax,
                    colormap=proc.colormap,
                    log_scale=proc.log_scale,
                )
                self._bridge.status.emit("posted to elog")
            except Exception as exc:
                self._bridge.error.emit(f"elog post failed: {exc}")

        self._bridge.status.emit("posting to elog...")
        threading.Thread(target=do_post, daemon=True).start()

    def _on_histogram_levels_changed(self, vmin, vmax):
        # dragging a handle on the histogram/colorscale sidebar implies
        # "I want manual contrast with exactly these bounds" -- switch
        # mode and keep the spinboxes in sync (without re-triggering their
        # own handlers, since we're setting the same processor fields here)
        self._manual_radio.setChecked(True)
        self._processor.contrast_mode = "manual"
        self._vmin_spin.setEnabled(True)
        self._vmax_spin.setEnabled(True)
        for spin, value in ((self._vmin_spin, vmin), (self._vmax_spin, vmax)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self._processor.vmin = vmin
        self._processor.vmax = vmax

    def _wire_exclusive_tool(self, button, mode):
        """Register `button` (a checkable QAction) as one of the mutually-
        exclusive interaction-mode tools (ROI/zoom/marker/calibrate-line/
        calibrate-center/measure -- exactly one, or none, active at a
        time) and connect its toggled signal to _on_tool_toggled. Grew out
        of hand-writing the same "uncheck my siblings" logic per tool,
        which got unwieldy (and once genuinely buggy -- see git history)
        as the tool count grew past two."""
        self._exclusive_tool_buttons.append(button)
        self._tool_modes[button] = mode
        button.toggled.connect(lambda checked, b=button: self._on_tool_toggled(checked, b))

    def _on_tool_toggled(self, checked, button):
        # uncheck the siblings *before* setting our own interaction_mode:
        # unchecking whichever sibling is currently active fires *its own*
        # toggled(False) handler synchronously (a nested call to this same
        # method), which would otherwise stomp interaction_mode back to
        # None right after we set it here -- so that cascade has to run
        # first, and our own mode is the very last write, unconditionally,
        # once the loop is done. (A single previously-active sibling was
        # already unchecked and so is a no-op -- toggling off an
        # already-unchecked QAction fires no signal at all -- but that
        # stops being reliably true once there are more than two tools,
        # since *this* call's own uncheck loop is what unchecks it.)
        if checked:
            for other in self._exclusive_tool_buttons:
                if other is not button:
                    other.setChecked(False)
        self._label.interaction_mode = self._tool_modes[button] if checked else None

    def _on_roi_dragged(self, x0, y0, x1, y1):
        rect = compute_roi_from_drag(x0, y0, x1, y1)
        self._roi_btn.setChecked(False)
        if rect[2] < 2 or rect[3] < 2:
            return  # accidental click, not a real drag
        self._processor.set_roi(self._processor.compose_roi(self._processor.roi, rect))

    def _on_zoom_dragged(self, x0, y0, x1, y1):
        """Drag-to-zoom: fit the dragged rectangle (in the currently
        displayed, possibly-ROI-cropped frame's own pixel space) to the
        viewport and scroll to center it -- a pure *display* zoom on top of
        the already-resolved frame (unlike AxisPTZStreamQt's drag-to-zoom,
        which sends an optical zoom command to a real PTZ camera). None of
        the fixed zoom-factor buttons (Fit/0.25x/...) will match this
        custom scale, so none stays checked afterwards -- click one of them
        to go back to a fixed/fit zoom."""
        self._zoom_drag_btn.setChecked(False)
        x, y, w, h = compute_roi_from_drag(x0, y0, x1, y1)
        if w < 2 or h < 2:
            return  # accidental click, not a real drag
        viewport = self._scroll_area.viewport().size()
        self._zoom_mode = compute_fit_scale(viewport.width(), viewport.height(), w, h)
        self._zoom_group.setExclusive(False)
        for action in self._zoom_buttons:
            action.setChecked(False)
        self._zoom_group.setExclusive(True)
        # applied once the next _render_tick has resized the pixmap to the
        # new scale and the scroll area's ranges are up to date
        self._pending_zoom_center = (x + w / 2.0, y + h / 2.0)

    def _on_clear_roi(self):
        self._processor.set_roi(None)

    def _on_marker_placed(self, x, y):
        self._marker_pos = (x, y)
        self._marker_btn.setChecked(False)

    def _on_clear_marker(self):
        self._marker_pos = None

    def _on_reticle_toggled(self, checked):
        self._show_reticle = checked

    def _on_average_changed(self, n):
        self._processor.set_average_n(n)

    def _on_average_mode_changed(self, _btn):
        mode = "single" if self._single_radio.isChecked() else "running"
        self._processor.set_average_mode(mode)

    def _on_grab_background(self):
        self._processor.grab_background()

    def _on_subtract_toggled(self, checked):
        self._processor.subtract_background = checked

    def _on_clear_background(self):
        self._processor.clear_background()
        self._subtract_cb.setChecked(False)

    def _on_hover(self, x, y):
        self._hover_pos = (x, y)

    # -- rendering ---------------------------------------------------------

    def _on_frame_ready(self, payload):
        self._latest_payload = payload

    def _render_tick(self):
        if self.window is None or self._latest_payload is None:
            return
        array, pulse_id = self._latest_payload
        try:
            display, stats = self._processor.process(array)
            qimage = array_to_qimage(display).copy()
            painter = QtGui.QPainter(qimage)
            self._paint_overlays(painter, display.shape[:2])
            painter.end()
            pixmap = QtGui.QPixmap.fromImage(qimage)

            raw_h, raw_w = display.shape[0], display.shape[1]
            scale = self._compute_scale(raw_h, raw_w)
            self._label.zoom_scale = scale
            scaled_w, scaled_h = max(1, round(raw_w * scale)), max(1, round(raw_h * scale))
            if (scaled_w, scaled_h) != (raw_w, raw_h):
                pixmap = pixmap.scaled(
                    scaled_w, scaled_h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
                )

            self._label.setPixmap(pixmap)
            self._label.setFixedSize(pixmap.size())
            if self._pending_zoom_center is not None:
                self._apply_pending_zoom_center(scale)
            self._last_display_array = display
            self._last_values_array = stats["values"]
            self._last_stats = stats
            self._histogram.setVisible(not stats["color"])
            if not stats["color"]:
                self._histogram.set_data(
                    stats["values"], stats["vmin"], stats["vmax"], colormap=self._processor.colormap
                )
        except RuntimeError:
            return
        except Exception:
            logger.exception("frame processing failed")
            return

        self._frame_count += 1
        now = time.time()
        if now - self._fps_t0 >= 1.0:
            self._last_fps = self._frame_count / (now - self._fps_t0)
            self._frame_count = 0
            self._fps_t0 = now
        self._update_status(pulse_id, stats)

    def _paint_overlays(self, painter, shape):
        h, w = shape
        if self._show_reticle:
            pen = QtGui.QPen(QtGui.QColor(0, 255, 0))
            painter.setPen(pen)
            # calibrated position if set (see "Set center position only..."/
            # "2-line..." in the Calibrate menu, or a calibration loaded
            # from the camera's own server-side config on open -- see
            # _load_camera_calibration), else the true image center, same
            # as this reticle's old, permanently-centered-only behavior
            if self._reticle_center is not None:
                cx, cy = self._reticle_center
                cx, cy = int(round(cx)), int(round(cy))
            else:
                cx, cy = w // 2, h // 2
            painter.drawLine(0, cy, w, cy)
            painter.drawLine(cx, 0, cx, h)
            tick = max(1, min(h, w) // 20)
            for i in range(-4, 5):
                if i == 0:
                    continue
                x = cx + i * tick
                if 0 <= x < w:
                    painter.drawLine(x, cy - 4, x, cy + 4)
                y = cy + i * tick
                if 0 <= y < h:
                    painter.drawLine(cx - 4, y, cx + 4, y)
        if self._marker_pos is not None:
            mx, my = self._marker_pos
            if 0 <= mx < w and 0 <= my < h:
                pen = QtGui.QPen(QtGui.QColor(255, 0, 0))
                painter.setPen(pen)
                painter.drawLine(0, my, w, my)
                painter.drawLine(mx, 0, mx, h)
        if self._measurement is not None:
            x0, y0, x1, y1 = self._measurement
            pen = QtGui.QPen(QtGui.QColor(230, 170, 40))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawLine(x0, y0, x1, y1)
            for x, y in ((x0, y0), (x1, y1)):
                painter.drawLine(x - 4, y, x + 4, y)
                painter.drawLine(x, y - 4, x, y + 4)
            painter.drawText(
                (x0 + x1) // 2 + 6, (y0 + y1) // 2 - 6, self._measurement_label()
            )

    def _measurement_label(self):
        """Distance for the current self._measurement -- physical units
        if calibrated (self._cal_unit != "px"), raw pixels otherwise. Per-
        axis scale (not a single hypot-of-pixels-then-convert) so
        non-square-pixel calibrations still measure correctly."""
        x0, y0, x1, y1 = self._measurement
        if self._cal_unit == "px":
            length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            return f"{length:.3g} px"
        dx = (x1 - x0) * self._cal_scale_x
        dy = (y1 - y0) * self._cal_scale_y
        length = (dx**2 + dy**2) ** 0.5
        return f"{length:.3g} {self._cal_unit}"

    def _calibrated(self, px, py):
        """(pixel_x, pixel_y) -> "(cx, cy) unit" if calibrated, "" if not
        (self._cal_unit == "px" is the uncalibrated default)."""
        if self._cal_unit == "px":
            return ""
        cx, cy = px * self._cal_scale_x, py * self._cal_scale_y
        return f" = ({cx:.3g}, {cy:.3g}) {self._cal_unit}"

    def _update_status(self, pulse_id, stats):
        txt = (
            f"pulse_id={pulse_id}  {self._last_fps:.1f} fps  shape={stats['shape']} "
            f"min={stats['min']:.1f} max={stats['max']:.1f}"
        )
        if self._processor.roi is not None:
            x, y, w, h = self._processor.roi
            txt += f"  roi=({x},{y},{w},{h})"
            if self._cal_unit != "px":
                txt += f" = {w * self._cal_scale_x:.3g}x{h * self._cal_scale_y:.3g} {self._cal_unit}"
        if self._marker_pos is not None:
            mx, my = self._marker_pos
            txt += f"  marker=({mx},{my}){self._calibrated(mx, my)}"
        if self._measurement is not None:
            txt += f"  measured={self._measurement_label()}"
        # the cursor readout reads _last_values_array (the pre-colormap,
        # physically meaningful values) rather than _last_display_array,
        # which is uint8 and, with a colormap active, RGB -- not a single
        # scalar to show
        if self._hover_pos is not None and self._last_values_array is not None:
            hx, hy = self._hover_pos
            arr = self._last_values_array
            if 0 <= hy < arr.shape[0] and 0 <= hx < arr.shape[1]:
                txt += f"  cursor=({hx},{hy}){self._calibrated(hx, hy)} val={arr[hy, hx]:.3g}"
        try:
            self._status_label.setText(txt)
        except RuntimeError:
            pass

    def _apply_error(self, message):
        if self.window is None:
            return
        try:
            self._status_label.setText(f"error: {message}")
        except RuntimeError:
            pass

    def _apply_status(self, message):
        if self.window is None:
            return
        try:
            self._status_label.setText(message)
        except RuntimeError:
            pass

    # -- lifecycle -----------------------------------------------------------

    def run(self):
        """Build and run the window with a blocking Qt event loop."""
        app = QtWidgets.QApplication.instance()
        created_app = app is None
        if created_app:
            app = QtWidgets.QApplication([])
        if self.window is None:
            self._build_window()
        if created_app:
            app.exec_()

    def start(self):
        """Show the window without blocking, reusing IPython's Qt event-loop
        integration when available (mirrors AxisPTZStreamQt.start)."""
        if self.window is not None:
            return
        ip = None
        try:
            from IPython import get_ipython

            ip = get_ipython()
        except Exception:
            ip = None

        if ip is None:
            self.run()
            return

        active = getattr(ip, "active_eventloop", None)
        if active in (None,):
            try:
                ip.enable_gui("qt")
            except Exception:
                pass
        elif active not in ("qt", "qt4", "qt5", "qt6"):
            print(
                f"eco widget: a different GUI event loop ('{active}') is already "
                "active in this IPython session, so the window can't be pumped "
                "non-blockingly alongside it. Showing it in blocking mode instead."
            )
            self.run()
            return

        self._build_window()

    def stop(self):
        """Stop the stream and close the window."""
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None


def make_camserver_stream_qt(
    name,
    kind="pipeline",
    pipeline_url=None,
    camera_url=None,
    rate_hz=10.0,
    theme=None,
    auto_start=True,
    cam=None,
    eco_name=None,
):
    """Convenience factory, mirrors make_axisptz_qt_window's signature.
    theme: "dark" | "light" | None (native) -- see eco.widgets.qt_theme.
    cam: the eco camera Assembly this viewer belongs to, if any -- adds a
    "Camera Settings" button (see CamServerStreamQt._open_settings).
    eco_name: that same Assembly's own alias name, for the window title."""
    return CamServerStreamQt(
        name,
        kind=kind,
        pipeline_url=pipeline_url,
        camera_url=camera_url,
        rate_hz=rate_hz,
        theme=theme,
        auto_start=auto_start,
        cam=cam,
        eco_name=eco_name,
    )


def _build_cam_from_argv(cam_class_path, pvname):
    """Re-instantiate the eco camera Assembly named by `cam_class_path` (a
    dotted import path) directly against `pvname` -- this subprocess's OWN
    independent camera object, talking directly to EPICS/cam_server, NOT
    any live link back to whatever parent session spawned this (see
    eco.devices_general.cameras_swissfel._spawn_separate_process_viewer's
    docstring for the deliberate-simplification rationale and the
    deferred-IPC TODO). Returns None (logged, not raised) if the import or
    construction fails -- a bad class path or unreachable EPICS/cam_server
    degrades to "no Camera Settings button", never crashes the viewer."""
    import importlib

    try:
        module_path, _, class_name = cam_class_path.rpartition(".")
        cam_class = getattr(importlib.import_module(module_path), class_name)
        return cam_class(pvname)
    except Exception:
        logger.exception(
            "failed to construct %r(%r) for cam= in this subprocess", cam_class_path, pvname
        )
        return None


def _main(argv=None):
    parser = argparse.ArgumentParser(description="cam_server / demo live image viewer")
    parser.add_argument("name", help="camera or pipeline instance name (ignored for --kind demo)")
    parser.add_argument(
        "--kind", choices=["pipeline", "camera", "camera_pipeline", "demo"], default="pipeline"
    )
    parser.add_argument("--pipeline-url", default=None)
    parser.add_argument("--camera-url", default=None)
    parser.add_argument(
        "--embed",
        action="store_true",
        help="frameless window; print WINID <id> on stdout once shown, for "
        "eco.widgets.camserver_panel_qt to embed",
    )
    parser.add_argument(
        "--demo-color", action="store_true", help="with --kind demo, generate an RGB test stream"
    )
    parser.add_argument("--rate", type=float, default=10.0, help="initial render rate limit (Hz)")
    parser.add_argument(
        "--theme",
        choices=["dark", "light"],
        default=None,
        help="modern flat skin (default: none/native, or whatever ECO_QT_THEME is set to -- "
        "see eco.widgets.qt_theme)",
    )
    parser.add_argument(
        "--eco-name",
        default=None,
        help="the eco device's own alias name (not the pvname/pipeline name) -- used for the "
        "window title when given, else the existing pvname-based default. Set automatically "
        "by eco.devices_general.cameras_swissfel._spawn_separate_process_viewer; not normally "
        "typed by hand.",
    )
    parser.add_argument(
        "--cam-class",
        default=None,
        help="dotted import path of an eco camera Assembly class to re-instantiate in this "
        "subprocess (e.g. eco.devices_general.cameras_swissfel.CameraBasler), constructed as "
        "CLASS(<name>) using this CLI's own positional `name` as the pvname, for cam= (Camera "
        "Settings button, Elog, screenpanel_ana...). Falls back to cam=None if omitted or if "
        "construction fails -- see _build_cam_from_argv.",
    )
    args = parser.parse_args(argv)

    cam = _build_cam_from_argv(args.cam_class, args.name) if args.cam_class else None

    viewer = CamServerStreamQt(
        args.name,
        kind=args.kind,
        pipeline_url=args.pipeline_url,
        camera_url=args.camera_url,
        embed=args.embed,
        demo_color=args.demo_color,
        rate_hz=args.rate,
        theme=args.theme,
        auto_start=False,
        cam=cam,
        eco_name=args.eco_name,
    )
    viewer.run()


if __name__ == "__main__":
    _main(sys.argv[1:])
