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
CameraPCO's own .viewer() uses, passing cam=<the camera Assembly> so the
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
    camera's own .viewer() uses so callers never have to know or guess a
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

    def __init__(self, average_n=1):
        self.average_n = max(1, int(average_n))
        self._ring = deque(maxlen=self.average_n)
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
        self._ring.append(np.asarray(array).astype(np.float32, copy=False))
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


class _ViewLabel(QtWidgets.QLabel):
    """QLabel with two direct-manipulation tools, chosen via
    `interaction_mode`: drag a rectangle to select an ROI ("roi"), or
    click to place the Marker crosshair ("marker"). Mouse positions are
    read in the label's own (on-screen, possibly zoomed) pixel space and
    converted to image-array pixel space via `zoom_scale` (kept in sync
    with whatever scale CamServerStreamQt._render_tick last drew at)
    before being emitted -- so ROI/marker/hover all stay correct at any
    zoom level, including "Fit"."""

    roi_dragged = QtCore.Signal(int, int, int, int)  # x0, y0, x1, y1 -- image pixel space
    marker_placed = QtCore.Signal(int, int)  # image pixel space
    hovered = QtCore.Signal(int, int)  # image pixel space

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.interaction_mode = None  # None | "roi" | "marker"
        self.zoom_scale = 1.0
        self._press_pos = None
        self.setMouseTracking(True)
        self._rubber_band = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Rectangle, self)

    def _to_image_coords(self, pos):
        scale = self.zoom_scale or 1.0
        return int(pos.x() / scale), int(pos.y() / scale)

    def mousePressEvent(self, ev):
        if self.interaction_mode == "roi":
            self._press_pos = ev.pos()
            self._rubber_band.setGeometry(QtCore.QRect(self._press_pos, QtCore.QSize()))
            self._rubber_band.show()
        elif self.interaction_mode == "marker":
            x, y = self._to_image_coords(ev.pos())
            self.marker_placed.emit(x, y)

    def mouseMoveEvent(self, ev):
        x, y = self._to_image_coords(ev.pos())
        self.hovered.emit(x, y)
        if self.interaction_mode == "roi" and self._press_pos is not None:
            # the rubber band itself stays in on-screen widget space so it
            # visually tracks the cursor; only the emitted result is
            # converted to image space (see mouseReleaseEvent)
            self._rubber_band.setGeometry(QtCore.QRect(self._press_pos, ev.pos()).normalized())

    def mouseReleaseEvent(self, ev):
        if self.interaction_mode == "roi" and self._press_pos is not None:
            self._rubber_band.hide()
            x0, y0 = self._to_image_coords(self._press_pos)
            x1, y1 = self._to_image_coords(ev.pos())
            self._press_pos = None
            self.roi_dragged.emit(x0, y0, x1, y1)


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

    def mouseMoveEvent(self, ev):
        if self._dragging is None:
            hovering = self._handle_at(ev.pos().y())
            if hovering != self._hovering:
                self._hovering = hovering
                self.update()
            self.setCursor(QtCore.Qt.SizeVerCursor if hovering else QtCore.Qt.ArrowCursor)
            return

        value = self._value(ev.pos().y())
        if self._dragging == "min":
            self._vmin = min(value, self._vmax - 1e-6)
        else:
            self._vmax = max(value, self._vmin + 1e-6)
        self.update()
        self.levels_changed.emit(self._vmin, self._vmax)
        QtWidgets.QToolTip.showText(ev.globalPos(), f"{value:.4g}", self)

    def mouseReleaseEvent(self, ev):
        self._dragging = None
        self.setCursor(QtCore.Qt.ArrowCursor)

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
    ):
        self.name = name
        self.kind = kind
        self.pipeline_url = pipeline_url
        self.camera_url = camera_url
        self.embed = embed
        self.demo_color = demo_color
        self.theme = theme  # "dark" | "light" | None -- see eco.widgets.qt_theme
        # the eco Assembly (e.g. CameraBasler/CameraPCO) this viewer was
        # opened from, if any -- see cam.viewer()/_default_widget = "viewer".
        # Only used to add a "Camera Settings" button (see _open_settings),
        # mirroring eco.widgets.camera_stream_qt.AxisPTZStreamQt's own
        # "Settings" button/cam parameter.
        self.cam = cam
        self._settings_window = None
        self.window = None
        self._worker = None
        self._processor = FrameProcessor()
        self._latest_payload = None
        self._last_display_array = None
        self._last_values_array = None  # pre-colormap values, for the cursor readout
        self._last_stats = None  # for the "Fix range" button
        self._marker_pos = None
        self._show_reticle = False
        self._hover_pos = None
        self._frame_count = 0
        self._fps_t0 = time.time()
        self._last_fps = 0.0
        self._rate_hz = rate_hz
        self._zoom_mode = "fit"  # "fit" | a float ratio (0.25/0.5/1.0/2.0)
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
        self.window.setWindowTitle(f"cam_server stream - {self.name}")
        self.window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        if self.embed:
            self.window.setWindowFlags(QtCore.Qt.FramelessWindowHint)

        central = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(2, 2, 2, 2)

        self._label = _ViewLabel("connecting...")
        self._label.setScaledContents(False)
        self._label.roi_dragged.connect(self._on_roi_dragged)
        self._label.marker_placed.connect(self._on_marker_placed)
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
        more detailed (numeric ranges, colormap choice, calibration) lives
        in the collapsible "Settings" dock instead (see
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
            # camera Assembly (cam.viewer()), not e.g. the standalone CLI --
            # named "Camera Settings" rather than plain "Settings" to avoid
            # clashing with the dock-visibility "Settings" action in
            # bg_bar below (a different thing: this opens the device's own
            # plain property-grid widget, mirroring
            # eco.widgets.camera_stream_qt.AxisPTZStreamQt's "Settings"
            # button -- see _open_settings)
            settings_btn = QtWidgets.QAction("Camera Settings", self.window)
            settings_btn.triggered.connect(self._open_settings)
            view_bar.addAction(settings_btn)
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
        view_bar.addSeparator()

        self._roi_btn = QtWidgets.QAction("Set ROI", self.window)
        self._roi_btn.setCheckable(True)
        self._roi_btn.toggled.connect(self._on_roi_tool_toggled)
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
        self._marker_btn.toggled.connect(self._on_marker_tool_toggled)
        annotate_bar.addAction(self._marker_btn)
        clear_marker_action = QtWidgets.QAction("Clear marker", self.window)
        clear_marker_action.triggered.connect(self._on_clear_marker)
        annotate_bar.addAction(clear_marker_action)

        self._reticle_btn = QtWidgets.QAction(_reticle_icon(), "Reticle", self.window)
        self._reticle_btn.setCheckable(True)
        self._reticle_btn.toggled.connect(self._on_reticle_toggled)
        annotate_bar.addAction(self._reticle_btn)

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
        settings_action.toggled.connect(lambda checked: self._settings_dock.setVisible(checked))
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
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Average N frames:"))
        self._average_spin = QtWidgets.QSpinBox()
        self._average_spin.setRange(1, 200)
        self._average_spin.valueChanged.connect(self._on_average_changed)
        row.addWidget(self._average_spin)
        row.addStretch(1)

        calibrate_btn = QtWidgets.QPushButton("Calibrate...")
        calibrate_btn.setToolTip("Set physical units per pixel, for the cursor/marker/ROI readouts")
        calibrate_btn.clicked.connect(self._on_calibrate_clicked)
        row.addWidget(calibrate_btn)
        return row

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

    def _on_calibrate_clicked(self):
        dialog = _CalibrationDialog(
            self._cal_scale_x, self._cal_scale_y, self._cal_unit, self.window
        )
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._cal_scale_x = dialog.scale_x
            self._cal_scale_y = dialog.scale_y
            self._cal_unit = dialog.unit

    def _open_settings(self):
        # normal=True: this button specifically wants the plain property
        # grid -- without it, since CameraBasler/CameraPCO set
        # _default_widget = "viewer", plain self.cam.widget() would just
        # reopen this same live viewer instead of the settings widget (see
        # Assembly.widget()'s normal= docstring, and
        # eco.widgets.camera_stream_qt.AxisPTZStreamQt._open_settings,
        # which this mirrors)
        #
        # keep a reference so the window (and its poll thread) isn't
        # garbage-collected as soon as this method returns
        self._settings_window = self.cam.widget(normal=True)

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

    def _on_roi_tool_toggled(self, checked):
        self._label.interaction_mode = "roi" if checked else None
        if checked:
            self._marker_btn.setChecked(False)

    def _on_marker_tool_toggled(self, checked):
        self._label.interaction_mode = "marker" if checked else None
        if checked:
            self._roi_btn.setChecked(False)

    def _on_roi_dragged(self, x0, y0, x1, y1):
        rect = compute_roi_from_drag(x0, y0, x1, y1)
        self._roi_btn.setChecked(False)
        if rect[2] < 2 or rect[3] < 2:
            return  # accidental click, not a real drag
        self._processor.set_roi(self._processor.compose_roi(self._processor.roi, rect))

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
):
    """Convenience factory, mirrors make_axisptz_qt_window's signature.
    theme: "dark" | "light" | None (native) -- see eco.widgets.qt_theme.
    cam: the eco camera Assembly this viewer belongs to, if any -- adds a
    "Camera Settings" button (see CamServerStreamQt._open_settings)."""
    return CamServerStreamQt(
        name,
        kind=kind,
        pipeline_url=pipeline_url,
        camera_url=camera_url,
        rate_hz=rate_hz,
        theme=theme,
        auto_start=auto_start,
        cam=cam,
    )


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
    args = parser.parse_args(argv)

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
    )
    viewer.run()


if __name__ == "__main__":
    _main(sys.argv[1:])
