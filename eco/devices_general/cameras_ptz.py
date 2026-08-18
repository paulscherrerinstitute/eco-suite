import requests
import time
from ..elements.assembly import Assembly
from ..elements.adjustable import AdjustableGetSet, AdjustableTrigger, Tweak
from numpy import polyval
import numpy as np
import urllib.request
import io
from PIL import Image
import os
from timg import Renderer, Ansi24HblockMethod
from shutil import get_terminal_size
from enum import Enum, IntEnum, auto

# function ptz_slider_onChange(group)
# {
#  if ((group == "pan" && "abs" == "rel") ||
#      (group == "tilt" && "abs" == "rel") ||
#      (group == "zoom" && "abs" == "rel") ||
#      (group == "focus" && "no" == "rel") ||
#      (group == "brightness" && "" == "rel") ||
#      (group == "iris" && "abs" == "rel")) {
#    group = "r" + group;
#  }
#  if (theCameraNumber == "") theCameraNumber = "1";
#  var url = "/axis-cgi/com/ptz.cgi?camera="+theCameraNumber+"&"+group+"="+parseFloat(Math.round(theNewSliderValue * 10)/10);
# }
#
# Looks like 'pan' is an absolute pan where 'rpan' is a relative pan.
#
# curl 'http://<<camera address>>/axis-cgi/com/ptz.cgi?camera=1&continuouspantiltmove=0,0&imagerotation=0&timestamp=1491243098477'


AUTOFOCUS = IntEnum("autofocus", {"on": 1, "off": 0})
AUTOIRIS = IntEnum("autoiris", {"on": 1, "off": 0})
# ImageSource.I#.Sensor.WhiteBalance -- values are also the VAPIX wire names,
# so WhiteBalance(str_value) and WhiteBalance[member].name round-trip cleanly
WhiteBalance = Enum(
    "WhiteBalance",
    {
        v: v
        for v in (
            "auto",
            "auto_indoor",
            "auto_outdoor",
            "hold",
            "manual",
            "fixed_outdoor1",
            "fixed_outdoor2",
            "fixed_indoor",
            "fixed_fluor1",
            "fixed_fluor2",
        )
    },
)


def _to_autofocus(value):
    """AUTOFOCUS/AUTOIRIS lookup, robust to how the camera phrased the
    value: normally the text "on"/"off" (via AUTOFOCUS[name]), but
    cameraCmd's response parser tries float() on every field first --
    if the camera ever answers with the numeric "1"/"0" instead of text
    (seen in practice: it made this field intermittently vanish from the
    widget/repr, silently swallowed as an AttributeError/KeyError further
    up) it arrives here as a float, so fall back to value-based lookup."""
    if isinstance(value, str):
        return AUTOFOCUS[value]
    return AUTOFOCUS(int(value))


def _is_notebook():
    try:
        from IPython import get_ipython

        ip = get_ipython()
        return ip is not None and ip.__class__.__name__ == "ZMQInteractiveShell"
    except Exception:
        return False


class AxisPTZ(Assembly):
    def __init__(
        self,
        camera_address,
        name="dummycam",
        timeout=2,
        tweak_steps=[-3, -3],
        image_rotation=None,
        invert_click_x=False,
        invert_click_y=False,
        swap_click_xy=False,
        rotate_display_180=False,
    ):
        # memory_change_serially=True: each axis (zoom/tilt/pan/iris/focus/
        # autofocus) is set over one shared HTTP connection to the camera;
        # firing them all at once (recall()'s normal default) can silently
        # drop some -- known issue with axis webcam settings not being
        # followed correctly. recall() applies them one at a time instead.
        super().__init__(name=name, memory_change_serially=True)
        self.camera_address = camera_address
        self.camera_n = 1
        self.timeout = timeout

        if image_rotation is None:
            # auto-detect the camera's configured sensor rotation, sent as
            # `imagerotation` on every PTZ command (see cameraCmd) -- this
            # is informational for the camera's own video-output handling
            # only. It does NOT correct click-to-center/drag-to-zoom pixel
            # coordinates; see invert_click_x/invert_click_y/swap_click_xy
            # for that. Falls back to 0 if the query fails for any reason.
            try:
                image_rotation = self.get_image_rotation()
            except Exception:
                image_rotation = 0
        self.camera_ir = image_rotation
        # see _to_sensor_xy for what these do and how to find the right
        # combination for a given camera/mount empirically
        self.invert_click_x = invert_click_x
        self.invert_click_y = invert_click_y
        self.swap_click_xy = swap_click_xy
        # purely cosmetic (what the viewer displays), see
        # iter_video_frames -- independent of the click flags above, but a
        # camera whose raw stream needs this to look right-side up will
        # likely also need invert_click_x=invert_click_y=True, since
        # rotating the display shifts where a "correct" click lands the
        # same way a firmware-rotated-vs-raw stream did in practice
        # (cam_mob1's raw/unrotated stream needed neither; cam_north/west/
        # south's firmware-rotated streams needed the click inverts)
        self.rotate_display_180 = rotate_display_180

        try:
            self.get_position()
        except:
            raise Exception(f"Could not connect to camera {self.name}!!")
        self._append(
            AdjustableGetSet,
            lambda: polyval([0.00290058, 0.99709942], self.get_position()["zoom"]),
            lambda val: self.set_par(
                "zoom", int(polyval([344.75862069, -343.75862069], val))
            ),
            precision=10 / 9999 * 30,
            check_interval=0.05,
            name="zoom",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            lambda: self.get_position()["tilt"],
            lambda val: self.set_par("tilt", val),
            name="tilt",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            lambda: self.get_position()["pan"],
            lambda val: self.set_par("pan", val),
            name="pan",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            lambda: (self.get_position()["iris"] - 1) / 9995,
            lambda val: self.set_par("iris", val * 9995 + 1),
            name="iris",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            lambda: (self.get_position()["focus"] - 750) / (9999 - 750),
            lambda val: self.set_par("focus", val * (9999 - 750) + 750),
            name="focus",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            lambda: (self.get_position()["brightness"] - 1) / 9998,
            lambda val: self.set_par("brightness", val * 9998 + 1),
            name="brightness",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            lambda: _to_autofocus(self.get_position()["autofocus"]),
            lambda val: self.set_par("autofocus", _to_autofocus(val).name),
            name="autofocus",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            lambda: _to_autofocus(self.get_position()["autoiris"]),
            lambda val: self.set_par("autoiris", _to_autofocus(val).name),
            name="autoiris",
            is_setting=True,
        )
        # move-to-home-position is a stateless action (see AdjustableTrigger)
        # -- this shadows the plain `home` method defined below with the
        # wired-in trigger, but AdjustableTrigger.__call__ still fires the
        # same underlying method, so cam.home() keeps working unchanged
        self._append(AdjustableTrigger, self.home, name="home", button_label="Home")
        # not wired in as an adjustable (unlike zoom/iris/focus/etc. above):
        # the white-balance VAPIX parameter path is unverified against real
        # hardware (see get_white_balance/list_params) and a bad path would
        # otherwise crash every repr()/display of this whole object, since
        # get_display_str() reads every adjustable's current value. Call
        # get_white_balance()/set_white_balance() directly for now.
        self._tweak_steps = tweak_steps

    def tweak(self):
        t = Tweak([self.pan, self._tweak_steps[0]], [self.tilt, self._tweak_steps[1]])
        t.xy_adjustable_tweak()

    # camera_n = 1
    # camera_url = 'http://<<camera address>>/axis-cgi/com/ptz.cgi'
    # camera_ir = 0

    # presets = {
    # 	'Home':
    # 		{
    # 			'pan'			: -120.7629,
    # 			'tilt'			: -4.8568,
    # 			'zoom'			: 696.0,
    # 			'brightness' 	: 3333.0,
    # 			#'autofocus'		: 'on',
    # 			#'autoiris' 		: 'on',
    # 			#'focus'		 : 6424.0,	# generates error
    # 			#'iris'			 : 2739.0,
    # 		},
    # 	'Mt. Washington':
    # 	#value="tilt=154749:focus=32766.000000:pan=267468:iris=32766.000000:zoom=11111.000000"
    # 		{
    # 			'pan'			: 156.7195,
    # 			'tilt'			: -0.6732,
    # 			'zoom'			: 11111.0,
    # 			#'autofocus'		: 'on',
    # 			#'autoiris' 		: 'on',
    # 			#'focus'		 : 7964.0,
    # 			#'iris'			 : 2583.0,
    # 		}
    # }
    @property
    def camera_url(self):
        return f"http://{self.camera_address}/axis-cgi/com/ptz.cgi"

    def get_image(self, filename=None, as_array=False):
        img_url = f"http://{self.camera_address}/jpg/image.jpg"
        with urllib.request.urlopen(img_url) as url:
            f = io.BytesIO(url.read())
        img = Image.open(f)
        if as_array:
            return np.asarray(img)
        else:
            return img

    def show(self, in_terminal=True):
        r = Renderer()
        r.load_image(self.get_image())
        r.resize(get_terminal_size()[0])
        r.render(Ansi24HblockMethod)

    def _get_param(self, name):
        """Query one VAPIX parameter (dotted path under "root.", e.g.
        "ImageSource.I0.Sensor.WhiteBalance") via param.cgi, returning its
        raw string value."""
        resp = requests.get(
            f"http://{self.camera_address}/axis-cgi/param.cgi",
            params={"action": "list", "group": name},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        line = resp.text.strip().splitlines()[0] if resp.text.strip() else ""
        if "=" not in line:
            raise RuntimeError(
                f"Unexpected param.cgi response for {name!r} (wrong "
                f"parameter path for this camera model?): {resp.text!r}. "
                f"Try list_params() on a broader group to find the real one."
            )
        return line.rsplit("=", 1)[1]

    def list_params(self, group="root"):
        """Raw param.cgi listing for a parameter group (e.g. "ImageSource"
        or "ImageSource.I0") -- dump this to find the exact parameter
        names/paths on this specific camera model when a guessed one (e.g.
        white balance's) doesn't match."""
        resp = requests.get(
            f"http://{self.camera_address}/axis-cgi/param.cgi",
            params={"action": "list", "group": group},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.text

    def _set_param(self, name, value):
        """Set one VAPIX parameter (dotted path under "root.") via
        param.cgi."""
        resp = requests.get(
            f"http://{self.camera_address}/axis-cgi/param.cgi",
            params={"action": "update", name: value},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.text

    def get_image_rotation(self):
        """Query the camera's configured image-source rotation (0/90/180/
        270 degrees) via VAPIX parameter management -- this is what a
        camera mounted upside-down (typically 180) has set so its own
        video output already appears right-side up. Used by __init__ to
        auto-fill `camera_ir`/`image_rotation` when not given explicitly.
        Note this is informational only (also sent as `imagerotation` on
        every PTZ command, see cameraCmd) -- click/drag coordinate
        correction turned out to be unrelated to this, see
        invert_click_x/invert_click_y/swap_click_xy instead."""
        return int(self._get_param(f"ImageSource.I{self.camera_n - 1}.Rotation"))

    def get_white_balance(self):
        """Current white-balance mode, as a WhiteBalance member. The
        parameter path used here is unverified against real hardware -- if
        this raises, run list_params("ImageSource") (or narrower, e.g.
        list_params(f"ImageSource.I{self.camera_n - 1}")) to find the real
        one on this camera model."""
        return WhiteBalance(self._get_param(f"ImageSource.I{self.camera_n - 1}.Sensor.WhiteBalance"))

    def set_white_balance(self, value):
        """Set the white-balance mode (a WhiteBalance member or its string
        name, e.g. "auto_indoor") -- see the WhiteBalance enum for the full
        list of modes (auto/auto_indoor/auto_outdoor/hold/manual/
        fixed_outdoor1/fixed_outdoor2/fixed_indoor/fixed_fluor1/
        fixed_fluor2)."""
        return self._set_param(
            f"ImageSource.I{self.camera_n - 1}.Sensor.WhiteBalance",
            WhiteBalance(value).name,
        )

    def viewer(self, codec="mjpeg", resolution=None, compression=50, fps=10, auto_start=True):
        """Open the live-video viewer, with mouse control mirroring the
        Axis web interface: click to recenter, drag a rectangle to zoom
        in, scroll wheel or right-click to zoom out. Opens as an
        ipywidgets box if called from a Jupyter notebook, a Qt window
        otherwise -- both have a "Settings" button to open widget() (the
        normal pan/tilt/zoom/iris/focus slider display) and a "Help"
        button describing the mouse controls. See show_qt/show_widget for
        the environment-specific versions directly, and show() for the
        plain terminal snapshot."""
        if _is_notebook():
            return self.show_widget(
                codec=codec,
                resolution=resolution,
                compression=compression,
                fps=fps,
                auto_start=auto_start,
            )
        else:
            return self.show_qt(
                codec=codec,
                resolution=resolution,
                compression=compression,
                fps=fps,
                auto_start=auto_start,
            )

    def show_qt(
        self, codec="mjpeg", resolution=None, compression=50, fps=10, auto_start=True
    ):
        """Open a Qt window with the live video stream and mouse control
        mirroring the Axis web interface: click a point to recenter, drag a
        rectangle to zoom into it, scroll wheel or right-click to zoom out.
        codec="mjpeg" (default) needs no extra dependency; codec="h264" is
        far more bandwidth-efficient for continuous viewing but requires
        the `av` package (PyAV) -- see iter_video_frames. The window's
        "Settings" button opens widget() (the normal pan/tilt/zoom/iris/
        focus slider display)."""
        from ..widgets.camera_stream_qt import make_axisptz_qt_window

        return make_axisptz_qt_window(
            self,
            codec=codec,
            resolution=resolution,
            compression=compression,
            fps=fps,
            auto_start=auto_start,
        )

    def show_widget(
        self, codec="mjpeg", resolution=None, compression=50, fps=10, auto_start=True
    ):
        """Jupyter counterpart of show_qt: an ipywidgets box with the live
        video stream and the same mouse control (click/drag/wheel/right-
        click). Requires the `ipyevents` package (mouse control) and, for
        codec="h264", the `av` package too. The "Settings" button displays
        widget() (the normal pan/tilt/zoom/iris/focus slider display)
        inline below the video."""
        from ..widgets.camera_stream_widget import make_axisptz_widget

        return make_axisptz_widget(
            self,
            codec=codec,
            resolution=resolution,
            compression=compression,
            fps=fps,
            auto_start=auto_start,
        )

    def iter_video_frames(
        self,
        codec="mjpeg",
        resolution=None,
        compression=None,
        fps=None,
        stop_event=None,
    ):
        """Yield decoded video frames (PIL.Image, RGB) from the camera,
        picking the stream/protocol to source them from:

        codec="mjpeg" (default): the axis-cgi/mjpg/video.cgi HTTP stream,
            each frame independently JPEG-compressed (see
            iter_mjpeg_frames). No extra dependency beyond
            `requests`+`PIL`, at the cost of more bandwidth per frame since
            there's no inter-frame compression.
        codec="h264": the axis-media/media.amp RTSP stream, decoded with
            PyAV/ffmpeg (see iter_h264_frames). Real inter-frame video
            compression -- far less bandwidth for continuous viewing -- but
            requires the `av` package.

        If `rotate_display_180` is set (settable live, e.g.
        `cam.rotate_display_180 = True`, no need to reconstruct the
        object), every frame is rotated 180 degrees before being yielded --
        for a camera whose raw stream isn't rotation-compensated by its own
        firmware for an upside-down mount (unlike click/drag correction,
        see invert_click_x/invert_click_y/swap_click_xy, this is purely
        cosmetic and only affects what's displayed)."""
        for img in self._iter_raw_video_frames(
            codec, resolution, compression, fps, stop_event
        ):
            if self.rotate_display_180:
                img = img.transpose(Image.ROTATE_180)
            yield img

    def _iter_raw_video_frames(
        self, codec, resolution, compression, fps, stop_event
    ):
        if codec == "mjpeg":
            for frame in self.iter_mjpeg_frames(
                resolution=resolution,
                compression=compression,
                fps=fps,
                stop_event=stop_event,
            ):
                try:
                    yield Image.open(io.BytesIO(frame)).convert("RGB")
                except Exception:
                    continue
        elif codec == "h264":
            yield from self.iter_h264_frames(
                resolution=resolution, fps=fps, stop_event=stop_event
            )
        else:
            raise ValueError(f"Unknown codec {codec!r}, expected 'mjpeg' or 'h264'")

    def iter_mjpeg_frames(
        self, resolution=None, compression=None, fps=None, stop_event=None, chunk_size=4096
    ):
        """Yield successive raw JPEG frames (bytes) from the camera's MJPEG
        stream (axis-cgi/mjpg/video.cgi, a multipart/x-mixed-replace
        sequence of JPEGs). Frames are located by scanning for JPEG
        SOI/EOI markers (0xFFD8/0xFFD9) rather than parsing the multipart
        boundary/Content-Length headers, which is robust to the minor
        header-formatting differences seen across Axis firmware versions.

        Pass a threading.Event as stop_event to stop the stream early --
        the underlying connection is closed as soon as it's set.
        """
        params = {"camera": self.camera_n}
        if resolution is not None:
            params["resolution"] = resolution
        if compression is not None:
            params["compression"] = compression
        if fps is not None:
            params["fps"] = fps
        url = f"http://{self.camera_address}/axis-cgi/mjpg/video.cgi"

        with requests.get(url, params=params, stream=True, timeout=10) as resp:
            resp.raise_for_status()
            buf = b""
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if stop_event is not None and stop_event.is_set():
                    return
                if not chunk:
                    continue
                buf += chunk
                while True:
                    start = buf.find(b"\xff\xd8")
                    if start == -1:
                        buf = buf[-1:]  # keep last byte in case it's a split marker
                        break
                    end = buf.find(b"\xff\xd9", start + 2)
                    if end == -1:
                        if start > 0:
                            buf = buf[start:]  # drop any leading junk before SOI
                        break
                    yield buf[start : end + 2]
                    buf = buf[end + 2 :]

    def iter_h264_frames(self, resolution=None, fps=None, stop_event=None, timeout=10.0):
        """Yield decoded video frames (PIL.Image, RGB) from the camera's
        H.264 RTSP stream (axis-media/media.amp), decoded via PyAV/ffmpeg.
        Real inter-frame video compression -- much less bandwidth than the
        per-frame JPEG stream in iter_mjpeg_frames for continuous viewing.

        Requires the `av` package (PyAV; pip install av). Pass a
        threading.Event as stop_event to stop the stream early.
        """
        try:
            import av
        except ImportError as exc:
            raise RuntimeError(
                "H.264 streaming requires the 'av' package (pip install av)."
            ) from exc

        query = ["videocodec=h264"]
        if resolution is not None:
            query.append(f"resolution={resolution}")
        if fps is not None:
            query.append(f"fps={fps}")
        url = f"rtsp://{self.camera_address}/axis-media/media.amp?" + "&".join(query)

        container = av.open(url, options={"rtsp_transport": "tcp"}, timeout=timeout)
        try:
            stream = container.streams.video[0]
            for packet in container.demux(stream):
                if stop_event is not None and stop_event.is_set():
                    return
                for frame in packet.decode():
                    if stop_event is not None and stop_event.is_set():
                        return
                    yield frame.to_image()
        finally:
            container.close()

    def _to_sensor_xy(self, x, y, image_width, image_height):
        """Map a pixel coordinate in the *displayed* frame (what a viewer
        clicked on) onto whatever coordinate space the camera's `center`/
        `areazoom` commands actually expect.

        Deliberately independent of camera_ir/image rotation: on real
        hardware the click/drag mismatch didn't track the camera's mount
        rotation setting (tying it to `imagerotation`, which is also sent
        on every command -- see cameraCmd -- risked the two corrections
        cancelling each other out). Controlled instead by three plain
        booleans, settable live with no need to reconstruct the object --
        cam.invert_click_x / cam.invert_click_y / cam.swap_click_xy, all
        False by default (today's raw, uncorrected mapping). Cover the 8
        combinations of a square's symmetries; find the right one by
        clicking a point you can see move and comparing to where the
        camera actually pointed:
          - moved to the diagonally-opposite point -> both inverts True
          - moved to the point mirrored left-right only -> invert_click_x
          - moved to the point mirrored top-bottom only -> invert_click_y
          - pan and tilt seem swapped -> swap_click_xy (+ inverts as needed)
        """
        if self.swap_click_xy:
            x, y = y, x
            image_width, image_height = image_height, image_width
        if self.invert_click_x:
            x = image_width - x
        if self.invert_click_y:
            y = image_height - y
        return x, y

    def click_center(self, x, y, image_width, image_height):
        """Re-center the view on pixel (x, y) of a frame sized
        image_width x image_height -- the same interaction as the Axis web
        interface's "click to center"."""
        x, y = self._to_sensor_xy(x, y, image_width, image_height)
        return self.cameraCmd(
            {
                "center": f"{int(round(x))},{int(round(y))}",
                "imagewidth": int(image_width),
                "imageheight": int(image_height),
            }
        )

    def area_zoom(self, x, y, z, image_width, image_height):
        """Center on pixel (x, y) and zoom by a factor of z/100 (z>100
        zooms in, z<100 zooms out), matching the VAPIX `areazoom` command."""
        x, y = self._to_sensor_xy(x, y, image_width, image_height)
        return self.cameraCmd(
            {
                "areazoom": f"{int(round(x))},{int(round(y))},{int(round(z))}",
                "imagewidth": int(image_width),
                "imageheight": int(image_height),
            }
        )

    def zoom_to_rectangle(self, x0, y0, x1, y1, image_width, image_height):
        """Zoom to fit the rectangle (x0, y0)-(x1, y1) (pixel coordinates
        in a frame sized image_width x image_height) -- the same
        interaction as dragging a zoom box in the Axis web interface. A
        near-zero-area rectangle (a plain click) is treated as a plain
        re-center instead."""
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w < 4 or h < 4:
            return self.click_center(cx, cy, image_width, image_height)
        z = 100 * min(image_width / w, image_height / h)
        z = max(1, min(z, 9999))
        return self.area_zoom(cx, cy, z, image_width, image_height)

    def cameraCmd(self, q_cmd):
        resp_data = {}
        base_q_args = {
            "camera": self.camera_n,
            "imagerotation": self.camera_ir,
            "html": "no",
            "timestamp": int(time.time()),
        }

        q_args = merge_dicts(q_cmd, base_q_args)
        resp = requests.get(self.camera_url, params=q_args, timeout=self.timeout)
        if resp.text.startswith("Error"):
            print(resp.text)
        else:
            for line in resp.text.splitlines():
                (name, var) = line.split("=", 2)
                try:
                    resp_data[name.strip()] = float(var)
                except ValueError:
                    resp_data[name.strip()] = var

        return resp_data

    def get_par(self, query):
        # print("cameraGet(" + query + ")")
        return self.cameraCmd({"query": query})

    # 	resp_data = {}
    # 	q_args = { 'query': query,
    # 		'camera': camera_n, 'imagerotation': camera_ir,
    # 		'html': 'no', 'timestamp': int(time.time())
    # 	}
    # 	resp = requests.get(camera_url, params=q_args)
    # 	for line in resp.text.splitlines():
    # 		(name, var) = line.split("=", 2)
    # 		try:
    # 			resp_data[name.strip()] = float(var)
    # 		except ValueError:
    # 			resp_data[name.strip()] = var
    #
    # 	return resp_data

    def set_par(self, group, val):
        print(val)
        # print("cameraSet(" + group + ", " + str(val) + ")")
        return self.cameraCmd({group: val})

    # 	resp_data = {}
    # 	q_args = { group: val,
    # 		'camera': camera_n, 'imagerotation': camera_ir,
    # 		'html': 'no', 'timestamp': int(time.time())
    # 	}
    #
    # 	resp = requests.get(camera_url, params=q_args)
    # 	for line in resp.text.splitlines():
    # 		(name, var) = line.split("=", 2)
    # 		try:
    # 			resp_data[name.strip()] = float(var)
    # 		except ValueError:
    # 			resp_data[name.strip()] = var
    #
    # 	return resp_data

    # def cameraGoToPreset(preset_name):
    #     preset = presets[preset_name]
    #     if preset != None:
    #         for key, value in preset.items():
    #             cameraSet(key, value)

    def home(self):
        return self.cameraCmd({"move": "home"})

    def get_position(self):
        return self.get_par("position")

    def get_limits(self):
        return self.get_par("limits")

    def set_pan(self, value):
        return self.set_par("pan", value)

    def set_tilt(self, value):
        return self.set_par("tilt", value)

    def set_zoom(self, value):
        return self.set_par("zoom", value)

    def set_panrelative(self, value):
        return self.set_par("rpan", value)

    def set_tiltrelative(self, value):
        return self.set_par("rtilt", value)

    def set_zoomrelative(self, value):
        return self.set_par("rzoom", value)


# print("Move to home...")
# print(cameraHome())

# #time.sleep(1)
# print("Get PTZ and Limits...")
# print(cameraGetPTZ())
# print(cameraGetLimits())

# for i in range(5):
# 	time.sleep(1)
# 	print("Move left...")
# 	print(cameraPanRelative(-1))

# for i in range(5):
# 	time.sleep(1)
# 	print("Move right...")
# 	print(cameraPanRelative(1))


# print(cameraTilt(-1))

# time.sleep(5)
# print("Show Mt. Washington...")
# print(cameraGoToPreset('Mt. Washington'))

# time.sleep(2)
# print("Move back home...")
# print(cameraHome())
# print(cameraGoToPreset('Home'))


def merge_dicts(*dict_args):
    """
    Given any number of dicts, shallow copy and merge into a new dict,
    precedence goes to key value pairs in latter dicts.
    """
    result = {}
    for dictionary in dict_args:
        result.update(dictionary)
    return result
