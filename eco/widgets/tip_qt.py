"""The small "Did you know?" window -- ``eco.tip()``, see eco.utilities.tips.

Shows one tip at a time from the session's tip history
(``eco.utilities.tips.history``): **Back** returns to tips already seen,
**Next** walks forward through them and, at the end, picks a new random one
(unseen first). The startup tip printed in the terminal is the first entry,
so the window continues from it. Links open in the normal web browser.

One window per session: calling ``eco.tip()`` again raises it instead of
opening another. Follows eco's Qt widget convention (``.window`` +
``.stop()``).
"""
from qtpy import QtCore, QtGui, QtWidgets

_app_ref = None  # keep a strong reference to any QApplication we create ourselves
_open_window = None  # the one TipWindowQt of this session, while it is open

_CSS = """
body { font-size: 14px; }
pre { background: rgba(128, 128, 128, 40); padding: 6px; }
code { font-family: monospace; }
"""


def _render(tip):
    import markdown

    from eco.utilities.tips import markdown_for_links

    # the title may hold `code` too; QTextDocument ignores CSS sizes on <h2>,
    # so size it inline
    title = markdown.markdown(tip.title)
    if title.startswith("<p>") and title.endswith("</p>"):
        title = title[3:-4]
    body = markdown.markdown(markdown_for_links(tip.body), extensions=["fenced_code", "tables"])
    return (
        f"<style>{_CSS}</style>"
        f'<p style="font-size: 15pt; font-weight: 600;">{title}</p>{body}'
    )


class TipWindowQt:
    def __init__(self, auto_start=True):
        self.window = None
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
        global _open_window
        if _open_window is self:
            _open_window = None
        if self.window is not None:
            try:
                self.window.close()
            except RuntimeError:
                pass
            self.window = None

    # -- navigation ------------------------------------------------------------

    def next(self):
        from eco.utilities.tips import history

        history.next()
        self.refresh()

    def back(self):
        from eco.utilities.tips import history

        history.back()
        self.refresh()

    def refresh(self):
        """Show the history's current tip."""
        from eco.utilities.tips import history

        if self.window is None:
            return
        tip = history.current
        try:
            if tip is None:
                self._text.setHtml("<p>No tips found (this eco has no docs/did_you_know folder).</p>")
            else:
                self._text.setHtml(_render(tip))
            pos, n = history.position()
            self._count.setText(f"{pos} of {n} seen" if n else "")
            self._back.setEnabled(history.can_back)
            self._open.setEnabled(tip is not None)
        except RuntimeError:  # window already deleted
            pass

    def raise_(self):
        try:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
        except RuntimeError:
            pass

    # -- building --------------------------------------------------------------

    def _open_in_browser(self):
        from eco.utilities.tips import history

        tip = history.current
        if tip is not None:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(tip.details_url or tip.url))

    def _build_window(self):
        global _app_ref
        if QtWidgets.QApplication.instance() is None:
            _app_ref = QtWidgets.QApplication([])
        from eco.utilities import mainthread
        from eco.widgets.qt_lifecycle import close_calls_stop

        mainthread.install_qt()

        self.window = QtWidgets.QWidget()
        self.window.setWindowTitle("eco: did you know?")
        self.window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        layout = QtWidgets.QVBoxLayout(self.window)

        self._text = QtWidgets.QTextBrowser()
        self._text.setOpenExternalLinks(True)
        self._text.setFrameShape(QtWidgets.QFrame.NoFrame)
        layout.addWidget(self._text, 1)

        row = QtWidgets.QHBoxLayout()
        self._count = QtWidgets.QLabel()
        row.addWidget(self._count)
        row.addStretch(1)
        self._open = QtWidgets.QPushButton("Open in docs")
        self._open.clicked.connect(self._open_in_browser)
        row.addWidget(self._open)
        self._back = QtWidgets.QPushButton("< Back")
        self._back.clicked.connect(self.back)
        row.addWidget(self._back)
        next_button = QtWidgets.QPushButton("Next >")
        next_button.setDefault(True)
        next_button.clicked.connect(self.next)
        row.addWidget(next_button)
        layout.addLayout(row)

        QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Right), self.window, self.next)
        QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Left), self.window, self.back)
        QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self.window, self.stop)

        close_calls_stop(self.window, self.stop)
        self.window.resize(560, 400)
        self.refresh()
        self.window.show()


def show_tip_window():
    """Open the tip window, or raise and refresh the one already open."""
    global _open_window
    if _open_window is not None and _open_window.window is not None:
        _open_window.refresh()
        _open_window.raise_()
        return _open_window
    _open_window = TipWindowQt()
    return _open_window
