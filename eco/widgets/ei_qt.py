"""The "Ask eco" panel: eco.ei (the %ei magic) as a Qt widget, docked by
eco desktop's Ask menu.

The question runs on a worker thread (index update, search, and the
model's streamed answer -- seconds to a minute); its pieces reach the
panel through queued signals, so the GUI never waits. Nothing in an
answer is executed: "To Console" puts its code at the console prompt.
Without an API key the panel only searches (docs, API, history), and says
so; the key lookup itself (gopass may take seconds) runs on a worker too.

With a model, notes on paper can be added: "Scan..." (images, PDFs) and
"Webcam..." (WebcamDialog: a picture from a camera attached to the machine
eco runs on). Both transcribe on a worker and show the text.
"""
import io
import socket
import threading

from qtpy import QtCore, QtGui, QtWidgets

IMAGE_FILTER = "Notes (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp *.pdf *.txt *.md);;All files (*)"


def qimage_to_pil(qimage):
    """PIL image of a QImage (through PNG bytes: no format juggling)."""
    from PIL import Image

    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.WriteOnly)
    qimage.save(buf, "PNG")
    with Image.open(io.BytesIO(bytes(buf.data()))) as img:
        return img.copy()


def _run_in_worker(fn, bridge, name):
    """fn() on a daemon thread; its result or error through bridge.done /
    bridge.failed (queued to the GUI thread)."""

    def run():
        try:
            result = fn()
        except Exception as exc:
            try:
                bridge.failed.emit(f"{type(exc).__name__}: {exc}")
            except RuntimeError:  # widget closed meanwhile
                pass
            return
        try:
            bridge.done.emit(result)
        except RuntimeError:
            pass

    threading.Thread(target=run, name=name, daemon=True).start()


class _Bridge(QtCore.QObject):
    delta = QtCore.Signal(str)
    report = QtCore.Signal(str)
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    key = QtCore.Signal(bool)


class _ScanBridge(QtCore.QObject):
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)


class WebcamDialog(QtWidgets.QDialog):
    """Live picture of a webcam; "Capture" keeps a still, transcribes it
    with the assistant's vision model and adds it to the index
    (Assistant.scan_image). Qt 5 QtMultimedia. The camera is the one of
    the machine eco runs on -- over SSH that is the console, not your own
    computer."""

    def __init__(self, assistant, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ask eco -- notes from the webcam")
        self.assistant = assistant
        self.camera = self.capture = None
        self._busy = False
        self._bridge = _ScanBridge(self)
        self._bridge.done.connect(self._on_done)
        self._bridge.failed.connect(self._on_failed)

        layout = QtWidgets.QVBoxLayout(self)
        self.camera_box = QtWidgets.QComboBox()
        layout.addWidget(self.camera_box)
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        try:
            from qtpy import QtMultimedia, QtMultimediaWidgets

            self._mm = QtMultimedia
            self.cameras = list(QtMultimedia.QCameraInfo.availableCameras())
            self.viewfinder = QtMultimediaWidgets.QCameraViewfinder()
        except Exception as exc:  # no QtMultimedia, or Qt 6 (other API)
            self._mm, self.cameras, self.viewfinder = None, [], None
            self.status.setText(f"No camera support in this Qt: {type(exc).__name__}: {exc}")
        for info in self.cameras:
            self.camera_box.addItem(info.description() or info.deviceName())
        self.camera_box.currentIndexChanged.connect(self._start_camera)
        if self.viewfinder is not None:
            self.viewfinder.setMinimumSize(480, 360)
            layout.addWidget(self.viewfinder, 1)
        layout.addWidget(self.status)
        self.transcript = QtWidgets.QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText("The transcribed text appears here.")
        self.transcript.setMaximumHeight(160)
        layout.addWidget(self.transcript)
        row = QtWidgets.QHBoxLayout()
        self.capture_button = QtWidgets.QPushButton("Capture && Transcribe")
        self.capture_button.clicked.connect(self.take_picture)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(self.capture_button)
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)

        if self._mm is not None and not self.cameras:
            self.status.setText(
                f"No camera found on {socket.gethostname()}. The webcam has to be attached to the "
                "machine eco runs on -- over SSH, your own computer's camera isn't visible here. "
                "Photos can be added with Scan... instead."
            )
        self.capture_button.setEnabled(False)
        if self.cameras:
            self._start_camera(0)

    def _start_camera(self, index):
        try:
            self._stop_camera()
            QtMultimedia = self._mm
            self.camera = QtMultimedia.QCamera(self.cameras[index])
            self.camera.setViewfinder(self.viewfinder)
            self.camera.setCaptureMode(QtMultimedia.QCamera.CaptureStillImage)
            self.capture = QtMultimedia.QCameraImageCapture(self.camera)
            self.capture.setCaptureDestination(QtMultimedia.QCameraImageCapture.CaptureToBuffer)
            self.capture.imageCaptured.connect(self._on_captured)
            self.capture.error.connect(lambda _id, _err, text: self._on_failed(f"capture: {text}"))
            # a camera takes seconds to start; capturing before fails
            self.capture.readyForCaptureChanged.connect(self._on_ready)
            self.capture_button.setEnabled(False)
            self.status.setText("starting the camera ...")
            self.camera.start()
        except Exception as exc:
            self.status.setText(f"Camera failed: {type(exc).__name__}: {exc}")

    def _on_ready(self, ready):
        try:
            if ready and not self._busy:
                self.status.setText("Hold the note in front of the camera, then Capture.")
            self.capture_button.setEnabled(bool(ready) and not self._busy)
        except RuntimeError:
            pass

    def _stop_camera(self):
        if self.camera is not None:
            try:
                self.camera.stop()
            except RuntimeError:
                pass
            self.camera = self.capture = None

    def take_picture(self):
        if self.capture is None or self._busy:
            return
        self._busy = True
        self.capture_button.setEnabled(False)
        self.status.setText("capturing ...")
        self.capture.capture()

    def _on_captured(self, _id, qimage):
        try:
            image = qimage_to_pil(qimage)
            self.status.setText(f"transcribing a {image.width}x{image.height} picture ...")
            _run_in_worker(lambda: self.assistant.scan_image(image, label="webcam", report=None), self._bridge, "eco-ei-webcam")
        except RuntimeError:
            pass
        except Exception as exc:
            self._on_failed(f"{type(exc).__name__}: {exc}")

    def _on_done(self, result):
        try:
            path, text = result
            self.transcript.setPlainText(text.strip())
            self.status.setText(f"Indexed: {path}. Capture again for the next page.")
            self._busy = False
            self.capture_button.setEnabled(self.capture is not None and self.capture.isReadyForCapture())
        except RuntimeError:
            pass

    def _on_failed(self, message):
        try:
            self.status.setText(f"failed: {message}")
            self._busy = False
            self.capture_button.setEnabled(self.capture is not None and self.capture.isReadyForCapture())
        except RuntimeError:
            pass

    def done(self, result):
        self._stop_camera()
        super().done(result)


def run_webcam_dialog(assistant, parent=None):
    """WebcamDialog, modal (what `%ei --webcam` opens; creates the
    QApplication in a plain terminal session)."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    dialog = WebcamDialog(assistant, parent)
    dialog.resize(680, 720)
    return dialog.exec_()


class EiPanel(QtWidgets.QWidget):
    #: re-render the streamed markdown at most this often (ms)
    RENDER_MS = 150

    def __init__(self, assistant, insert_code=None, parent=None):
        super().__init__(parent)
        self.assistant = assistant
        self.insert_code = insert_code
        self._worker = None
        self._stop = None
        self._text = ""
        self._answer = None

        self._bridge = _Bridge(self)
        self._bridge.delta.connect(self._on_delta)
        self._bridge.report.connect(self._on_report)
        self._bridge.done.connect(self._on_done)
        self._bridge.failed.connect(self._on_failed)
        self._bridge.key.connect(self._on_key)
        self._has_key = None  # unknown until the lookup below returns
        self._render_timer = QtCore.QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render)

        self.model_box = QtWidgets.QComboBox()
        self.model_box.setToolTip("Language model answering (Ask -> Model, or ~/.eco/ei.json)")
        self.model_box.activated.connect(self._on_model_chosen)
        self.search_only = QtWidgets.QCheckBox("Search only")
        self.search_only.setToolTip("Show the matching history, docs and API entries without asking the model")
        self.new_button = QtWidgets.QPushButton("New")
        self.new_button.setToolTip("Start a new conversation (forget earlier questions)")
        self.new_button.clicked.connect(self._on_new)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.model_box, 1)
        top.addWidget(self.search_only)
        top.addWidget(self.new_button)

        self.question = QtWidgets.QPlainTextEdit()
        self.question.setPlaceholderText("How do I ...?   (Ctrl+Enter to ask)")
        self.question.setMaximumHeight(80)
        self.ask_button = QtWidgets.QPushButton("Ask")
        self.ask_button.clicked.connect(self.ask)
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            QtWidgets.QShortcut(QtGui.QKeySequence(keys), self.question, activated=self.ask)
        qrow = QtWidgets.QHBoxLayout()
        qrow.addWidget(self.question, 1)
        qrow.addWidget(self.ask_button)

        self.answer = QtWidgets.QTextBrowser()
        self.answer.setOpenExternalLinks(True)
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: palette(mid);")
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        self.console_button = QtWidgets.QPushButton("To Console")
        self.console_button.setToolTip("Put the answer's code at the console prompt (not run)")
        self.console_button.clicked.connect(self._on_to_console)
        self.console_button.setVisible(insert_code is not None)
        self.copy_button = QtWidgets.QPushButton("Copy Code")
        self.copy_button.clicked.connect(self._on_copy)
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.clicked.connect(self._on_stop)
        self.scan_button = QtWidgets.QPushButton("Scan...")
        self.scan_button.setToolTip("Add notes from pictures or PDFs: transcribed by the model, then searchable")
        self.scan_button.clicked.connect(self.choose_scan_files)
        self.webcam_button = QtWidgets.QPushButton("Webcam...")
        self.webcam_button.setToolTip("Add a note from a webcam picture (camera of the machine eco runs on)")
        self.webcam_button.clicked.connect(self.open_webcam)
        self._scan_bridge = _ScanBridge(self)
        self._scan_bridge.done.connect(self._on_scanned)
        self._scan_bridge.failed.connect(self._on_scan_failed)
        self._scanning = False
        bottom = QtWidgets.QHBoxLayout()
        bottom.addWidget(self.console_button)
        bottom.addWidget(self.copy_button)
        bottom.addStretch(1)
        bottom.addWidget(self.scan_button)
        bottom.addWidget(self.webcam_button)
        bottom.addWidget(self.stop_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(qrow)
        layout.addWidget(self.answer, 1)
        layout.addWidget(self.status)
        layout.addLayout(bottom)

        self.sync_model()
        self._set_busy(False)
        self.check_key()

    def check_key(self):
        """Look for the API key on a worker; _on_key adapts the panel."""
        bridge = self._bridge

        def run():
            try:
                found = self.assistant.backend(recheck=True) is not None
            except Exception:
                found = False
            try:
                bridge.key.emit(found)
            except RuntimeError:  # panel closed meanwhile
                pass

        threading.Thread(target=run, name="eco-ei-key", daemon=True).start()

    # -- state -------------------------------------------------------------

    def sync_model(self):
        models = list(dict.fromkeys([self.assistant.model, *self.assistant.config.get("model_options", {})]))
        self.model_box.blockSignals(True)
        self.model_box.clear()
        self.model_box.addItems(models)
        self.model_box.setCurrentText(self.assistant.model)
        self.model_box.blockSignals(False)

    def focus_question(self):
        self.question.setFocus()
        self.question.selectAll()

    def _set_busy(self, busy):
        self.ask_button.setEnabled(not busy)
        self.new_button.setEnabled(not busy)
        self.model_box.setEnabled(not busy and self._has_key is not False)
        self.stop_button.setEnabled(busy)
        has_code = bool(self._answer is not None and self._answer.code)
        self.console_button.setEnabled(has_code and not busy)
        self.copy_button.setEnabled(has_code and not busy)
        # pictures need the model
        model = self._has_key is True
        self.scan_button.setEnabled(model and not busy and not self._scanning)
        self.webcam_button.setEnabled(model and not busy)

    # -- asking --------------------------------------------------------------

    def ask(self):
        question = self.question.toPlainText().strip()
        if not question or self._worker is not None:
            return
        self._text, self._answer = "", None
        self.answer.setPlainText("")
        self.status.setText("searching ..." if self.search_only.isChecked() else f"asking {self.assistant.model} ...")
        self._set_busy(True)
        self._stop = threading.Event()
        bridge, stop, search_only = self._bridge, self._stop, self.search_only.isChecked()

        def emit(signal, value):
            try:
                signal.emit(value)
            except RuntimeError:  # panel closed meanwhile
                stop.set()

        def run():
            try:
                ans = self.assistant.ask(
                    question,
                    on_delta=lambda piece: emit(bridge.delta, piece),
                    stop=stop,
                    search_only=search_only,
                    report=lambda text: emit(bridge.report, text),
                )
                emit(bridge.done, ans)
            except Exception as exc:
                emit(bridge.failed, f"{type(exc).__name__}: {exc}")

        self._worker = threading.Thread(target=run, name="eco-ei-ask", daemon=True)
        self._worker.start()

    # slots: called through queued signals, possibly after the panel has
    # been closed -- a RuntimeError from a deleted widget must not escape
    # (PyQt aborts the process on an exception in a slot)

    def _on_delta(self, piece):
        try:
            self._text += piece
            if not self._render_timer.isActive():
                self._render_timer.start(self.RENDER_MS)
        except RuntimeError:
            pass

    def _render(self):
        try:
            self.answer.setMarkdown(self._text)
            bar = self.answer.verticalScrollBar()
            bar.setValue(bar.maximum())
        except RuntimeError:
            pass

    def _on_key(self, found):
        try:
            self._has_key = found
            if not found:
                self.search_only.setChecked(True)
                self.search_only.setEnabled(False)
                self.model_box.setEnabled(False)
                self.status.setText(
                    "No API key for the model: searching only (docs, API, command history). "
                    "To get answers, add a key from ai.psi.ch -- see `%ei` in the console."
                )
            else:
                self.search_only.setEnabled(True)
            self._set_busy(self._worker is not None)
        except RuntimeError:
            pass

    def _on_report(self, text):
        try:
            self.status.setText(text)
        except RuntimeError:
            pass

    def _on_done(self, ans):
        try:
            self._worker = None
            self._answer = ans
            self._render_timer.stop()
            if ans.model:
                self.answer.setMarkdown(ans.text)
                self.status.setText(f"{ans.model}, {ans.seconds:.1f} s -- context: {ans.sources()}")
            else:
                self.answer.setPlainText(ans.text)
                self.status.setText(f"{len(ans.hits)} matches (no model asked)")
            self._set_busy(False)
        except RuntimeError:
            pass

    # -- notes on paper -------------------------------------------------------

    def choose_scan_files(self):
        try:
            paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Notes to add (pictures, PDFs)", "", IMAGE_FILTER)
        except RuntimeError:
            return
        if paths:
            self.scan_files(paths)

    def scan_files(self, paths):
        """Transcribe/read `paths` on a worker and add them to the index."""
        if self._scanning or self._has_key is not True:
            return
        self._scanning = True
        self._set_busy(self._worker is not None)
        self.status.setText(f"reading {len(paths)} file(s) -- pictures are transcribed by the model ...")
        _run_in_worker(lambda: self.assistant.scan(paths, report=None), self._scan_bridge, "eco-ei-scan")

    def _on_scanned(self, result):
        try:
            self._scanning = False
            texts = result.get("transcripts", [])
            problems = {p: n for p, n in result.get("notes", {}).items() if n != "OCR"}
            self.answer.setPlainText(
                "\n\n---\n\n".join(t.strip() for t in texts)
                + ("\n\n" if texts and problems else "")
                + "\n".join(f"{p}: {n}" for p, n in problems.items())
            )
            self.status.setText(f"{result['files_indexed']} file(s) indexed, {len(texts)} picture(s) transcribed")
            self._set_busy(self._worker is not None)
        except RuntimeError:
            pass

    def _on_scan_failed(self, message):
        try:
            self._scanning = False
            self.status.setText(f"scan failed: {message}")
            self._set_busy(self._worker is not None)
        except RuntimeError:
            pass

    def open_webcam(self):
        try:
            dialog = WebcamDialog(self.assistant, self)
            dialog.resize(680, 720)
            dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose)
            dialog.show()
        except Exception as exc:
            self.status.setText(f"webcam: {type(exc).__name__}: {exc}")

    def _on_failed(self, message):
        try:
            self._worker = None
            self.status.setText(f"failed: {message}")
            self._set_busy(False)
        except RuntimeError:
            pass

    # -- buttons --------------------------------------------------------------

    def _on_model_chosen(self, _index):
        self.assistant.set_model(self.model_box.currentText())

    def _on_new(self):
        self.assistant.reset()
        self._answer = None
        self.answer.setPlainText("")
        self.status.setText("new conversation")
        self._set_busy(False)
        self.focus_question()

    def _on_stop(self):
        if self._stop is not None:
            self._stop.set()
        self.status.setText("stopping ...")

    def _on_copy(self):
        if self._answer is not None and self._answer.code:
            QtWidgets.QApplication.clipboard().setText(self._answer.code)

    def _on_to_console(self):
        if self._answer is not None and self._answer.code and self.insert_code is not None:
            try:
                self.insert_code(self._answer.code)
            except Exception as exc:
                self.status.setText(f"could not put the code in the console: {exc}")

    def closeEvent(self, event):
        if self._stop is not None:
            self._stop.set()
        super().closeEvent(event)
