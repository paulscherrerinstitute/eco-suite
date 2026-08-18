"""
Jupyter widget for an Axis PTZ camera's live video stream, with mouse
control mirroring the Axis web interface: click a point to recenter the
view, drag a rectangle to zoom in, scroll the wheel or right-click to zoom
out (also described by the widget's own "Help" button). codec="mjpeg"
(default) or "h264" -- see
eco.devices_general.cameras_ptz.AxisPTZ.iter_video_frames for the tradeoff.

Requires the `ipyevents` package (pip install ipyevents, or via conda) to
turn browser mouse events on the frame into camera commands -- without it,
make_axisptz_widget raises a RuntimeError with that install hint. Older
classic-Jupyter-Notebook installs may additionally need
`jupyter nbextension enable --py --sys-prefix ipyevents`; JupyterLab >=3 and
Notebook >=7 pick it up automatically once installed.

Usage:
    from eco.widgets.camera_stream_widget import make_axisptz_widget
    w = make_axisptz_widget(cam_west)
    display(w)

Returned widget has methods:
    w.start()  # start streaming (already started by default)
    w.stop()   # stop streaming

The "Settings" button displays cam_west.widget() (the normal
pan/tilt/zoom/iris/focus slider display, inherited from Assembly) inline
below the video.
"""
import io
import threading

_ZOOM_IN_STEP = 150  # area_zoom z: >100 zooms in (see AxisPTZ.area_zoom)
_ZOOM_OUT_STEP = 67  # ~100/150, so wheel-in then wheel-out roughly cancels

_HELP_TEXT = """
"Mouse control" must be checked for any of this to do anything (off by
default, so stray clicks while just viewing don't move the camera):<br>
<b>Mouse controls</b><br>
Left click &ndash; recenter the view on that point<br>
Left drag &ndash; zoom in to fit the dragged rectangle<br>
Scroll wheel &ndash; zoom in (up) / out (down), centered on the cursor<br>
Right click &ndash; zoom out one step, centered on the cursor<br><br>
"Zoom Out" jumps straight to the widest field of view (zoom=1).<br>
"Settings" opens the normal pan/tilt/zoom/iris/focus controls.<br>
"Memories" opens the memory browser directly.<br>
"Home" sends the camera to its home position.
"""


def make_axisptz_widget(
    cam, codec="mjpeg", resolution=None, compression=50, fps=10, auto_start=True
):
    import ipywidgets as widgets
    from IPython.display import display

    try:
        from ipyevents import Event
    except Exception as exc:
        raise RuntimeError(
            "The live-video widget's mouse control requires the 'ipyevents' "
            "package (pip install ipyevents)."
        ) from exc

    image = widgets.Image(format="jpg")
    status = widgets.Label("connecting...")
    home_btn = widgets.Button(description="Home")
    zoom_out_btn = widgets.Button(description="Zoom Out")
    settings_btn = widgets.Button(description="Settings")
    buttons = [home_btn, zoom_out_btn, settings_btn]
    memories_output = widgets.Output()
    if hasattr(cam, "memory"):
        memories_btn = widgets.Button(description="Memories")
        buttons.append(memories_btn)
    help_btn = widgets.Button(description="Help")
    buttons.append(help_btn)
    mouse_control_cb = widgets.Checkbox(
        value=False, description="Mouse control", indent=False
    )
    help_box = widgets.HTML(value=_HELP_TEXT)
    help_box.layout.display = "none"
    settings_output = widgets.Output()
    vbox = widgets.VBox(
        [
            image,
            widgets.HBox(buttons + [mouse_control_cb, status]),
            help_box,
            settings_output,
            memories_output,
        ]
    )

    stop_event = threading.Event()
    stream_thread = [None]
    frame_size = [None]  # [(w, h)] of the most recent frame, mutable cell
    drag_state = {"down": False, "x0": 0, "y0": 0}

    def _dispatch(fn, *args):
        # PTZ commands are blocking HTTP calls -- run them off the UI thread
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _on_home(_btn):
        _dispatch(cam.home)

    home_btn.on_click(_on_home)

    def _on_zoom_out(_btn):
        _dispatch(cam.set_zoom, 1)

    zoom_out_btn.on_click(_on_zoom_out)

    def _on_settings(_btn):
        settings_output.clear_output()
        with settings_output:
            display(cam.widget())

    settings_btn.on_click(_on_settings)

    if hasattr(cam, "memory"):

        def _on_memories(_btn):
            memories_output.clear_output()
            with memories_output:
                from eco.widgets.memory_widget import make_memory_browser_ipywidgets

                display(make_memory_browser_ipywidgets(cam))

        memories_btn.on_click(_on_memories)

    def _on_help(_btn):
        showing = help_box.layout.display != "none"
        help_box.layout.display = "none" if showing else "block"

    help_btn.on_click(_on_help)

    def _on_dom_event(evt):
        if not mouse_control_cb.value:
            return
        if frame_size[0] is None:
            return
        w, h = frame_size[0]
        # dataX/dataY are ipyevents' image-pixel-space coordinates for an
        # Image widget -- already correct regardless of how the browser
        # happens to be displaying/scaling it
        x, y = evt.get("dataX"), evt.get("dataY")
        if x is None or y is None:
            return
        etype = evt.get("type")
        if etype == "mousedown":
            drag_state.update(down=True, x0=x, y0=y)
        elif etype == "mouseup" and drag_state["down"]:
            drag_state["down"] = False
            x0, y0 = drag_state["x0"], drag_state["y0"]
            if abs(x - x0) < 4 and abs(y - y0) < 4:
                _dispatch(cam.click_center, x, y, w, h)
            else:
                _dispatch(cam.zoom_to_rectangle, x0, y0, x, y, w, h)
        elif etype == "wheel":
            dy = evt.get("deltaY", 0)
            if dy:
                z = _ZOOM_IN_STEP if dy < 0 else _ZOOM_OUT_STEP
                _dispatch(cam.area_zoom, x, y, z, w, h)
        elif etype == "contextmenu":
            _dispatch(cam.area_zoom, x, y, _ZOOM_OUT_STEP, w, h)

    event_watcher = Event(
        source=image,
        watched_events=["mousedown", "mouseup", "wheel", "contextmenu"],
        prevent_default_action=True,
    )
    event_watcher.on_dom_event(_on_dom_event)

    def _update_loop():
        # background thread: (re)connect to the video stream (mjpeg or
        # h264, per `codec` -- see AxisPTZ.iter_video_frames), re-encode
        # each decoded frame to JPEG for the Image widget; on a transient
        # read error (e.g. the camera stalls mid-command) back off briefly
        # and retry rather than leaving the widget stuck on a dead stream.
        while not stop_event.is_set():
            try:
                for img in cam.iter_video_frames(
                    codec=codec,
                    resolution=resolution,
                    compression=compression,
                    fps=fps,
                    stop_event=stop_event,
                ):
                    if stop_event.is_set():
                        return
                    w, h = img.size
                    frame_size[0] = (w, h)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85)
                    image.value = buf.getvalue()
                    status.value = f"{w}x{h}"
            except Exception as exc:
                if stop_event.is_set():
                    return
                status.value = f"stream error: {exc}"
                stop_event.wait(2.0)

    def start():
        if stream_thread[0] is not None and stream_thread[0].is_alive():
            return
        stop_event.clear()
        stream_thread[0] = threading.Thread(target=_update_loop, daemon=True)
        stream_thread[0].start()

    def stop():
        stop_event.set()

    vbox.start = start
    vbox.stop = stop
    vbox._stop_event = stop_event

    if auto_start:
        start()

    return vbox
