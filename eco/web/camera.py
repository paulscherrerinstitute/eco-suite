"""Live camera views for the web client: network cameras that stream MJPEG
(eco.devices_general.cameras_ptz.AxisPTZ and anything else with the same
methods), passed on to the browser as one multipart/x-mixed-replace
response, which an <img> shows natively on phones and laptops.

One connection to a camera per quality, however many clients watch it
(`RelayHub`); each client always gets the newest frame and skips the ones
it was too slow for, so a slow SSH tunnel drops frames instead of falling
behind. The relay stops when its last viewer goes.

PTZ control (tap to center, drag a box to zoom, zoom steps, home, wide)
goes through the client session (`ClientSession._op_ptz`), with the
same write permissions as any other write.
"""
import logging
import threading

logger = logging.getLogger(__name__)

# what the stream is asked for, per quality the client picks: (resolution,
# compression, fps). "low" is the default, for a phone on a tunnel.
QUALITIES = {
    "low": ("640x360", 70, 4),
    "medium": ("1280x720", 50, 8),
    "full": (None, 40, 10),
}
ZOOM_IN_STEP = 150  # area_zoom z: > 100 zooms in (as the Qt/ipywidgets viewers)
ZOOM_OUT_STEP = 67


def camera_info(obj):
    """What the client needs to show `obj` as a live camera, or None:
    {"stream": "mjpeg", "ptz": bool, "rotate": bool, "qualities": [...]}.
    Duck-typed on AxisPTZ's methods, looked up on the type (attribute
    access on an Assembly instance can have side effects)."""
    cls = type(obj)
    if not callable(getattr(cls, "iter_mjpeg_frames", None)):
        return None
    ptz = all(callable(getattr(cls, m, None)) for m in ("click_center", "area_zoom", "zoom_to_rectangle"))
    try:
        rotate = bool(object.__getattribute__(obj, "__dict__").get("rotate_display_180", False))
    except Exception:
        rotate = False
    return {"stream": "mjpeg", "ptz": ptz, "rotate": rotate, "qualities": list(QUALITIES)}


class Viewer:
    """One client's place in a relay: woken (on the gateway's loop) when a
    new frame is there."""

    def __init__(self, relay, loop):
        import asyncio

        self.relay = relay
        self.loop = loop
        self.event = asyncio.Event()
        self.seen = 0
        self.closed = False  # the client went

    def wake(self):
        try:
            self.loop.call_soon_threadsafe(self.event.set)
        except RuntimeError:  # loop closed
            pass

    async def next_frame(self):
        """The newest frame not sent yet; None once the stream has ended."""
        while True:
            seq, frame = self.relay.latest
            if self.relay.ended or self.closed:
                return None
            if seq > self.seen:
                self.seen = seq
                return frame
            self.event.clear()
            await self.event.wait()


class CameraRelay:
    def __init__(self, hub, key, obj, quality):
        self.hub = hub
        self.key = key
        self.obj = obj
        self.quality = quality
        self.latest = (0, None)
        self.ended = False
        self.error = None
        self._viewers = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"eco-web-camera-{key[1]}")

    def start(self):
        self._thread.start()
        return self

    def add(self, viewer):
        with self._lock:
            self._viewers.add(viewer)

    def remove(self, viewer):
        with self._lock:
            self._viewers.discard(viewer)
            last = not self._viewers
        if last:
            self.stop()

    def stop(self):
        self._stop.set()
        self.hub.forget(self)

    def _run(self):
        resolution, compression, fps = QUALITIES[self.quality]
        seq = 0
        try:
            for frame in self.obj.iter_mjpeg_frames(resolution=resolution, compression=compression,
                                                    fps=fps, stop_event=self._stop):
                if self._stop.is_set():
                    break
                seq += 1
                self.latest = (seq, frame)
                self._wake_all()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            logger.warning("camera stream %s (%s) ended: %s", self.key[1], self.quality, self.error)
        finally:
            if not self._stop.is_set() and self.error is None:
                self.error = "the camera ended the stream"
            self.ended = True
            self.hub.forget(self)
            self._wake_all()

    def _wake_all(self):
        with self._lock:
            viewers = list(self._viewers)
        for viewer in viewers:
            viewer.wake()


class RelayHub:
    """The running relays, one per (camera, quality)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._relays = {}

    def viewer(self, obj, name, quality, loop):
        """A new Viewer of `obj` at `quality`, on a running or a new relay."""
        if quality not in QUALITIES:
            raise ValueError(f"quality must be one of {', '.join(QUALITIES)}")
        key = (id(obj), name, quality)
        with self._lock:
            relay = self._relays.get(key)
            if relay is None or relay.ended:
                relay = self._relays[key] = CameraRelay(self, key, obj, quality).start()
            viewer = Viewer(relay, loop)
            relay.add(viewer)
        return viewer

    def forget(self, relay):
        with self._lock:
            if self._relays.get(relay.key) is relay:
                del self._relays[relay.key]

    def stop_all(self):
        with self._lock:
            relays = list(self._relays.values())
        for relay in relays:
            relay.stop()
