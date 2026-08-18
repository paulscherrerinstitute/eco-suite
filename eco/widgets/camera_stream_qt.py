"""
Qt widget for an Axis PTZ camera's live video stream, with mouse control
mirroring the Axis web interface: click a point to recenter the view, drag
a rectangle to zoom in, scroll the wheel or right-click to zoom out (also
described in the window's own "Help" button). codec="mjpeg" (default) or
"h264" -- see eco.devices_general.cameras_ptz.AxisPTZ.iter_video_frames for
the tradeoff.

Uses qtpy so it works with whichever Qt binding is installed (mirrors
eco.widgets.display_qt). Frames arrive off the GUI thread (a daemon thread
reading AxisPTZ.iter_video_frames) and are marshalled to the GUI thread via
a Qt signal, the same pattern display_qt uses for its polling bridge.

Usage (blocking, e.g. plain python script):
    from eco.widgets.camera_stream_qt import make_axisptz_qt_window
    gui = make_axisptz_qt_window(cam_west, auto_start=False)
    gui.run()   # blocks until the window is closed

Usage (non-blocking, e.g. terminal IPython session):
    gui = make_axisptz_qt_window(cam_west)  # auto_start=True by default
    ...
    gui.stop()

The window's "Settings" button opens cam_west.widget() (the normal
pan/tilt/zoom/iris/focus slider display, inherited from Assembly).
"""
import threading

from qtpy import QtCore, QtGui, QtWidgets

_app_ref = None  # keep a strong reference to any QApplication we create ourselves


class _StreamBridge(QtCore.QObject):
    frame_ready = QtCore.Signal(object)  # (QImage, width, height)
    error = QtCore.Signal(str)


_ZOOM_IN_STEP = 150  # area_zoom z: >100 zooms in (see AxisPTZ.area_zoom)
_ZOOM_OUT_STEP = 67  # ~100/150, so wheel-in then wheel-out roughly cancels


class _StreamLabel(QtWidgets.QLabel):
    """QLabel that reports clicks, drag-rectangles and zoom gestures in its
    own (unscaled) pixel coordinates -- since the label is never scaled
    relative to the pixmap it displays, those map 1:1 onto the source video
    frame. Left click/drag zoom in (see clicked/dragged); the wheel and
    right-click are the only way to zoom back out (see zoomed)."""

    clicked = QtCore.Signal(int, int)
    dragged = QtCore.Signal(int, int, int, int)
    zoomed = QtCore.Signal(int, int, int)  # x, y, z (see AxisPTZ.area_zoom)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._press_pos = None
        self.mouse_control_enabled = False  # see "Mouse control" checkbox
        self._rubber_band = QtWidgets.QRubberBand(
            QtWidgets.QRubberBand.Rectangle, self
        )

    def mousePressEvent(self, ev):
        if not self.mouse_control_enabled:
            return
        if ev.button() == QtCore.Qt.RightButton:
            self.zoomed.emit(ev.pos().x(), ev.pos().y(), _ZOOM_OUT_STEP)
            return
        self._press_pos = ev.pos()
        self._rubber_band.setGeometry(QtCore.QRect(self._press_pos, QtCore.QSize()))
        self._rubber_band.show()

    def mouseMoveEvent(self, ev):
        if not self.mouse_control_enabled:
            return
        if self._press_pos is not None:
            self._rubber_band.setGeometry(
                QtCore.QRect(self._press_pos, ev.pos()).normalized()
            )

    def mouseReleaseEvent(self, ev):
        if not self.mouse_control_enabled:
            return
        self._rubber_band.hide()
        if self._press_pos is None:
            return
        x0, y0 = self._press_pos.x(), self._press_pos.y()
        x1, y1 = ev.pos().x(), ev.pos().y()
        self._press_pos = None
        if abs(x1 - x0) < 4 and abs(y1 - y0) < 4:
            self.clicked.emit(x1, y1)
        else:
            self.dragged.emit(x0, y0, x1, y1)

    def wheelEvent(self, ev):
        if not self.mouse_control_enabled:
            ev.ignore()
            return
        pos = ev.pos()
        z = _ZOOM_IN_STEP if ev.angleDelta().y() > 0 else _ZOOM_OUT_STEP
        self.zoomed.emit(pos.x(), pos.y(), z)
        ev.accept()


class AxisPTZStreamQt:
    """Live-video Qt window for one AxisPTZ camera. See module docstring."""

    def __init__(
        self, cam, codec="mjpeg", resolution=None, compression=50, fps=10, auto_start=True
    ):
        self.cam = cam
        self.codec = codec
        self.resolution = resolution
        self.compression = compression
        self.fps = fps
        self.window = None
        self._frame_size = None  # (w, h) of the most recent frame
        self._stop_event = threading.Event()
        self._stream_thread = None
        self._memory_browser = None  # lazily-built "Memories" sub-window
        if auto_start:
            self.start()

    def _build_window(self):
        global _app_ref
        if QtWidgets.QApplication.instance() is None:
            # should normally already exist (created by IPython's qt inputhook,
            # or by run() below), but guard against it missing regardless
            _app_ref = QtWidgets.QApplication([])

        self.window = QtWidgets.QWidget()
        self.window.setWindowTitle(f"Axis PTZ stream - {getattr(self.cam, 'name', '')}")
        # don't let closing our window quit a shared QApplication/event loop
        self.window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)

        layout = QtWidgets.QVBoxLayout(self.window)

        self._label = _StreamLabel()
        self._label.setScaledContents(False)
        self._label.setText("connecting...")
        self._label.clicked.connect(self._on_click)
        self._label.dragged.connect(self._on_drag)
        self._label.zoomed.connect(self._on_zoom)
        layout.addWidget(self._label)

        controls = QtWidgets.QHBoxLayout()
        home_btn = QtWidgets.QPushButton("Home")
        home_btn.clicked.connect(lambda: self._dispatch(self.cam.home))
        controls.addWidget(home_btn)
        zoom_out_btn = QtWidgets.QPushButton("Zoom Out")
        zoom_out_btn.clicked.connect(lambda: self._dispatch(self.cam.set_zoom, 1))
        controls.addWidget(zoom_out_btn)
        settings_btn = QtWidgets.QPushButton("Settings")
        settings_btn.clicked.connect(self._open_settings)
        controls.addWidget(settings_btn)
        if hasattr(self.cam, "memory"):
            memories_btn = QtWidgets.QPushButton("Memories")
            memories_btn.clicked.connect(self._open_memories)
            controls.addWidget(memories_btn)
        help_btn = QtWidgets.QPushButton("Help")
        help_btn.clicked.connect(self._show_help)
        controls.addWidget(help_btn)
        mouse_control_cb = QtWidgets.QCheckBox("Mouse control")
        mouse_control_cb.setChecked(False)
        mouse_control_cb.toggled.connect(
            lambda checked: setattr(self._label, "mouse_control_enabled", checked)
        )
        controls.addWidget(mouse_control_cb)
        controls.addStretch(1)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.stop)
        controls.addWidget(close_btn)
        layout.addLayout(controls)

        self.window.destroyed.connect(lambda *a: setattr(self, "window", None))

        # frames are read off the GUI thread so a stalled camera never
        # freezes the window; results are marshalled back via the bridge
        self._bridge = _StreamBridge()
        self._bridge.frame_ready.connect(self._apply_frame)
        self._bridge.error.connect(self._apply_error)
        self._stop_event.clear()
        self._stream_thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._stream_thread.start()

        self.window.show()

    def _stream_loop(self):
        # background thread: (re)connect to the video stream (mjpeg or
        # h264, per self.codec -- see AxisPTZ.iter_video_frames), convert
        # each decoded frame and hand it to the GUI thread; on a transient
        # read error (e.g. the camera stalls mid-command) back off briefly
        # and retry rather than leaving the window stuck on a dead stream.
        while not self._stop_event.is_set():
            try:
                for img in self.cam.iter_video_frames(
                    codec=self.codec,
                    resolution=self.resolution,
                    compression=self.compression,
                    fps=self.fps,
                    stop_event=self._stop_event,
                ):
                    if self._stop_event.is_set():
                        return
                    w, h = img.size
                    qimage = QtGui.QImage(
                        img.tobytes(), w, h, w * 3, QtGui.QImage.Format_RGB888
                    ).copy()
                    self._bridge.frame_ready.emit((qimage, w, h))
            except Exception as exc:
                if self._stop_event.is_set():
                    return
                self._bridge.error.emit(str(exc))
                self._stop_event.wait(2.0)

    def _apply_frame(self, payload):
        # runs on the GUI thread (queued signal) - safe to touch widgets here
        qimage, w, h = payload
        self._frame_size = (w, h)
        pixmap = QtGui.QPixmap.fromImage(qimage)
        self._label.setPixmap(pixmap)
        self._label.setFixedSize(pixmap.size())

    def _apply_error(self, message):
        self._label.setText(f"stream error: {message}")

    def _dispatch(self, fn, *args):
        # PTZ commands are blocking HTTP calls -- run them off the GUI thread
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _on_click(self, x, y):
        if self._frame_size is None:
            return
        w, h = self._frame_size
        self._dispatch(self.cam.click_center, x, y, w, h)

    def _on_drag(self, x0, y0, x1, y1):
        if self._frame_size is None:
            return
        w, h = self._frame_size
        self._dispatch(self.cam.zoom_to_rectangle, x0, y0, x1, y1, w, h)

    def _on_zoom(self, x, y, z):
        if self._frame_size is None:
            return
        w, h = self._frame_size
        self._dispatch(self.cam.area_zoom, x, y, z, w, h)

    def _open_settings(self):
        # keep a reference so the window (and its poll thread) isn't
        # garbage-collected as soon as this method returns
        self._settings_window = self.cam.widget()

    def _open_memories(self):
        if self._memory_browser is not None and self._memory_browser.window is not None:
            self._memory_browser.window.raise_()
            self._memory_browser.window.activateWindow()
            return
        from eco.widgets.memory_widget import make_memory_browser_qt

        self._memory_browser = make_memory_browser_qt(self.cam, parent=None)

    def _show_help(self):
        QtWidgets.QMessageBox.information(
            self.window,
            "Live-video viewer help",
            "\"Mouse control\" must be checked for any of this to do anything "
            "(off by default, so stray clicks while just viewing don't move "
            "the camera):\n\n"
            "Left click - recenter the view on that point\n"
            "Left drag - zoom in to fit the dragged rectangle\n"
            "Scroll wheel - zoom in (up) / out (down), centered on the cursor\n"
            "Right click - zoom out one step, centered on the cursor\n\n"
            "\"Zoom Out\" jumps straight to the widest field of view (zoom=1).\n"
            "\"Settings\" opens the normal pan/tilt/zoom/iris/focus controls.\n"
            "\"Memories\" opens the memory browser directly.\n"
            "\"Home\" sends the camera to its home position.",
        )

    def run(self):
        """Build and run the window with a blocking Qt event loop. Use this
        when there is no GUI event-loop integration available to pump the
        window for you (e.g. a plain python script, or a terminal with an
        incompatible GUI loop already active)."""
        app = QtWidgets.QApplication.instance()
        created_app = app is None
        if created_app:
            app = QtWidgets.QApplication([])
        if self.window is None:
            self._build_window()
        if created_app:
            app.exec_()

    def start(self):
        """
        Show the window without blocking. Inside an IPython terminal
        session this reuses (or enables) IPython's Qt event-loop
        integration, which pumps the window between prompts on the main
        thread. Outside of IPython, or if a different/incompatible GUI
        loop is already active, this falls back to the blocking run().
        """
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
                "non-blockingly alongside it. Showing it in blocking mode instead "
                "(closing the window returns control) - re-run after '%gui' "
                "(no arguments) to disable the current loop if you want the "
                "non-blocking window."
            )
            self.run()
            return

        self._build_window()

    def stop(self):
        """Stop the stream and close the window."""
        self._stop_event.set()
        self._stream_thread = None
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None


def make_axisptz_qt_window(
    cam, codec="mjpeg", resolution=None, compression=50, fps=10, auto_start=True
):
    """Convenience factory, mirrors make_assembly_qt_window's signature."""
    return AxisPTZStreamQt(
        cam,
        codec=codec,
        resolution=resolution,
        compression=compression,
        fps=fps,
        auto_start=auto_start,
    )
