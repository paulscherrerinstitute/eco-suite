"""The "Ask eco" panel: eco.ei (the %ei magic) as a Qt widget, docked by
eco desktop's Ask menu.

The question runs on a worker thread (index update, search, and the
model's streamed answer -- seconds to a minute); its pieces reach the
panel through queued signals, so the GUI never waits. Nothing in an
answer is executed: "To Console" puts its code at the console prompt.
Without an API key the panel only searches (docs, API, history), and says
so; the key lookup itself (gopass may take seconds) runs on a worker too.
"""
import threading

from qtpy import QtCore, QtGui, QtWidgets


class _Bridge(QtCore.QObject):
    delta = QtCore.Signal(str)
    report = QtCore.Signal(str)
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    key = QtCore.Signal(bool)


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
        bottom = QtWidgets.QHBoxLayout()
        bottom.addWidget(self.console_button)
        bottom.addWidget(self.copy_button)
        bottom.addStretch(1)
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
