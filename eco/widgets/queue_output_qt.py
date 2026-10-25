"""A window following a task queue's output live --
``queues["default"].show_output()`` / ``%queue show_output`` (see
eco.utilities.task_queue).

It first shows what the queue's tasks printed so far (``Task.output``), then
everything new as it arrives, with a header line per task. The queue's
worker thread only appends to a thread-safe buffer (a queue listener); a
QTimer on the main thread moves that into the text view, so the window keeps
updating while the session itself is busy (eco.utilities.mainthread). Works
whether or not the queue is muted (``%queue mute``), which is the point of
muting: a chatty scan goes here instead of through the prompt.

Follows eco's Qt widget convention (``.window`` + ``.stop()``), so the
desktop can dock it (``show_output(dock=True)``).
"""
import collections

from qtpy import QtCore, QtGui, QtWidgets

_app_ref = None  # keep a strong reference to any QApplication we create ourselves

MAX_BLOCKS = 20000  # lines kept in the view; older ones scroll away


class QueueOutputQt:
    POLL_MS = 200

    def __init__(self, queue, auto_start=True):
        self.queue = queue
        self.window = None
        self._incoming = collections.deque()  # appended to from any thread
        if auto_start:
            self.start()

    # -- lifecycle -------------------------------------------------------------

    def start(self):
        try:
            from IPython import get_ipython

            shell = get_ipython()
            if shell is not None and getattr(shell, "active_eventloop", None) is None:
                shell.enable_gui("qt")
        except Exception:
            pass
        self._build_window()

    def stop(self):
        if self._listener in self.queue._listeners:
            self.queue._listeners.remove(self._listener)
        if self.window is not None:
            self._timer.stop()
            try:
                self.window.close()
            except RuntimeError:
                pass
            self.window = None

    # -- building --------------------------------------------------------------

    def _build_window(self):
        global _app_ref
        if QtWidgets.QApplication.instance() is None:
            _app_ref = QtWidgets.QApplication([])
        from eco.utilities import mainthread
        from eco.widgets.qt_lifecycle import close_calls_stop

        mainthread.install_qt()

        self.window = QtWidgets.QWidget()
        self.window.setWindowTitle(f"queue {self.queue.name!r} output")
        self.window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        layout = QtWidgets.QVBoxLayout(self.window)

        self._status = QtWidgets.QLabel()
        layout.addWidget(self._status)

        self._text = QtWidgets.QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(MAX_BLOCKS)
        self._text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self._text.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        layout.addWidget(self._text, 1)

        row = QtWidgets.QHBoxLayout()
        self._mute_box = QtWidgets.QCheckBox("only here (mute at the prompt)")
        self._mute_box.setChecked(self.queue.quiet)
        self._mute_box.toggled.connect(lambda on: self.queue.mute() if on else self.queue.unmute())
        row.addWidget(self._mute_box)
        row.addStretch(1)
        clear = QtWidgets.QPushButton("Clear view")
        clear.clicked.connect(self._text.clear)
        row.addWidget(clear)
        layout.addLayout(row)

        # what the queue printed before this window existed
        for task in self.queue.history + ([self.queue.current] if self.queue.current else []):
            if task.output:
                self._append(f"=== {task.label}: {task.source}\n{task.output}")
        self.queue._listeners.append(self._listener)

        self._timer = QtCore.QTimer(self.window)
        self._timer.timeout.connect(self._drain)
        self._timer.start(self.POLL_MS)
        close_calls_stop(self.window, self.stop)
        self.window.resize(760, 420)
        self._update_status()
        self.window.show()

    # -- updating --------------------------------------------------------------

    def _listener(self, text):
        self._incoming.append(text)  # any thread: deque.append is atomic

    def _drain(self):
        chunks = []
        while self._incoming:
            chunks.append(self._incoming.popleft())
        if chunks:
            self._append("".join(chunks))
        self._update_status()

    def _append(self, text):
        bar = self._text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 2
        cursor = self._text.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertText(text)
        if at_bottom:  # follow the output unless the user scrolled back
            bar.setValue(bar.maximum())

    def _update_status(self):
        queue = self.queue
        current = queue.current
        state = f"running #{current.id}: {current.source[:60]}" if current else "idle"
        flags = [f"{len(queue.pending)} pending"]
        if queue.paused:
            flags.append("PAUSED")
        if queue.quiet:
            flags.append("muted at the prompt")
        self._status.setText(f"queue {queue.name!r}: {state}  ({', '.join(flags)})")
