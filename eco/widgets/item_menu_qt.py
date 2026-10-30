"""
The per-item "⋯" menu of the Qt property grid (eco.widgets.display_qt), and
the two views it brings along: a live strip chart and an archiver history
plot. What the menu offers and why: see eco.widgets.item_menu.

Everything opened from the menu goes where the grid is: docked into eco
desktop next to it when the grid is docked there (EcoDesktopApp.host_widget),
its own window otherwise. Every opened view follows eco's ``.window`` /
``.stop()`` convention, so the desktop can dock it and closing it stops its
reads.

Same rules as the grid itself: nothing that may wait on hardware or the
archiver runs on the GUI thread (reads go through live_values.LiveReader,
history and details are fetched in a thread), and every delayed callback
tolerates a widget closed in the meantime.
"""
import logging
import threading

from qtpy import QtCore, QtWidgets

from eco.widgets import item_menu
from eco.widgets.live_values import LiveReader

logger = logging.getLogger(__name__)

_open_handles = set()  # strong references to open views, dropped on stop


class _GuiCall(QtCore.QObject):
    """Runs a function on the GUI thread (emitting a signal across threads
    is thread-safe; the slot runs where this object lives)."""

    call = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.call.connect(self._run)

    def _run(self, fn):
        try:
            fn()
        except RuntimeError:  # a widget closed meanwhile
            pass
        except Exception:
            logger.exception("item menu: GUI callback failed")


_gui = None


def _on_gui(fn):
    """Run `fn` on the GUI thread, from any thread. The bridge is made on
    first use -- always from the GUI thread, since the menus are."""
    _gui.call.emit(fn)


def _ensure_gui_bridge():
    """Made as a child of the QApplication, so it goes with it -- and is
    made again for a new one (tests create several in one process)."""
    global _gui
    try:
        _gui.objectName()
    except (AttributeError, RuntimeError):  # not made yet / deleted with its app
        _gui = _GuiCall(QtCore.QCoreApplication.instance())


def _in_background(work, on_done, on_error):
    """Run `work()` in a thread; `on_done(result)` / `on_error(exc)` on the
    GUI thread."""
    _ensure_gui_bridge()

    def run():
        try:
            result = work()
        except Exception as exc:
            _on_gui(lambda: on_error(exc))
        else:
            _on_gui(lambda: on_done(result))

    threading.Thread(target=run, daemon=True).start()


def _show_error(parent, title, exc):
    try:
        text = f"{type(exc).__name__}: {exc}" if isinstance(exc, BaseException) else str(exc)
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, title, text, parent=parent)
        box.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        box.show()
    except RuntimeError:
        pass


class _Handle:
    """``.window`` + ``.stop()`` around a plain QWidget, so the desktop can
    dock it; `on_stop` runs once when it is closed either way."""

    def __init__(self, widget, on_stop=None):
        from eco.widgets.qt_lifecycle import close_calls_stop

        self.window = widget
        self._on_stop = on_stop
        _open_handles.add(self)
        close_calls_stop(widget, self._stopped)

    def _stopped(self):
        if self._on_stop is not None:
            on_stop, self._on_stop = self._on_stop, None
            try:
                on_stop()
            except Exception:
                logger.exception("stopping %r failed", self.window)
        _open_handles.discard(self)

    def stop(self):
        self._stopped()
        if self.window is not None:
            window, self.window = self.window, None
            try:
                window.close()
            except RuntimeError:
                pass


def host(name, handle, get_container=None):
    """Show `handle` (``.window``/``.stop()``) docked in the grid's desktop
    container if there is one, else as its own window."""
    container = get_container() if get_container is not None else None
    if container is not None and hasattr(container, "host_widget"):
        container.host_widget(name, handle)
        return
    window = handle.window
    window.show()
    window.raise_()
    window.activateWindow()


def _figure_canvas(width=5.5, height=2.8):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

    figure = Figure(figsize=(width, height), layout="constrained")
    canvas = FigureCanvasQTAgg(figure)
    canvas.setMinimumSize(320, 180)
    return figure, canvas


# -- live strip chart -------------------------------------------------------------


class StripChartQt(QtWidgets.QWidget):
    """A rolling time plot of one item's value: monitored where the item
    can push changes, polled every STRIP_POLL_INTERVAL otherwise; redrawn
    twice a second while visible. Window length, pause and clear in the
    top bar."""

    REDRAW_MS = 500

    def __init__(self, item, title=None, parent=None):
        super().__init__(parent)
        self.item = item
        self.title = title or item_menu.full_name(item)
        self.setWindowTitle(f"strip chart - {self.title}")
        self.buffer = item_menu.SeriesBuffer(item)
        windows = dict(item_menu.STRIP_WINDOWS)

        bar = QtWidgets.QHBoxLayout()
        self.window_combo = QtWidgets.QComboBox()
        self.window_combo.addItems([label for label, _ in item_menu.STRIP_WINDOWS])
        self.window_combo.setCurrentText(item_menu.DEFAULT_STRIP_WINDOW)
        self.window_combo.currentTextChanged.connect(lambda text: self._redraw(force=True))
        self.pause_box = QtWidgets.QCheckBox("pause")
        clear_btn = QtWidgets.QPushButton("clear")
        clear_btn.clicked.connect(lambda checked=False: (self.buffer.clear(), self._redraw(force=True)))
        self.value_label = QtWidgets.QLabel("--")
        bar.addWidget(QtWidgets.QLabel("window"))
        bar.addWidget(self.window_combo)
        bar.addWidget(self.pause_box)
        bar.addWidget(clear_btn)
        bar.addStretch(1)
        bar.addWidget(self.value_label)

        self.figure, self.canvas = _figure_canvas()
        self.axes = self.figure.add_subplot()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)
        layout.addWidget(self.canvas, 1)
        self._windows = windows

        self._reader = LiveReader(self._on_result, poll_interval=item_menu.STRIP_POLL_INTERVAL, workers=1)
        self._reader.add(item)
        self._reader.start()
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._redraw)
        self._timer.start(self.REDRAW_MS)

    def _on_result(self, index, ok, payload):  # reader thread
        if ok:
            self.buffer.add(payload)

    def window_seconds(self):
        return self._windows.get(self.window_combo.currentText(), 60)

    def _redraw(self, force=False):
        if self.pause_box.isChecked() and not force:
            return
        try:
            item_menu.draw_strip(self.axes, self.buffer, self.window_seconds())
            self.value_label.setText(self.buffer.last_text)
            self.canvas.draw_idle()
        except RuntimeError:  # closed meanwhile
            pass
        except Exception:
            logger.exception("strip chart of %s: drawing failed", self.title)

    def stop(self):
        self._reader.stop()
        try:
            self._timer.stop()
        except RuntimeError:
            pass


def open_strip_chart(item, name=None, get_container=None):
    chart = StripChartQt(item, title=name)
    handle = _Handle(chart, on_stop=chart.stop)
    host(f"strip chart: {chart.title}", handle, get_container)
    return handle


# -- archiver history ---------------------------------------------------------------


class HistoryPanelQt(QtWidgets.QWidget):
    """The archived history of one item over a chosen time range, fetched
    in the background (eco.widgets.item_menu.fetch_history) and plotted with
    the usual matplotlib toolbar (zoom, pan, save)."""

    def __init__(self, item, title=None, parent=None):
        from matplotlib.backends.backend_qtagg import NavigationToolbar2QT

        super().__init__(parent)
        self.item = item
        self.title = title or item_menu.full_name(item)
        self.setWindowTitle(f"history - {self.title}")
        self._ranges = dict(item_menu.HISTORY_RANGES)
        self._generation = 0  # drops results of superseded requests

        bar = QtWidgets.QHBoxLayout()
        self.range_combo = QtWidgets.QComboBox()
        self.range_combo.addItems([label for label, _ in item_menu.HISTORY_RANGES])
        self.range_combo.setCurrentText(item_menu.DEFAULT_HISTORY_RANGE)
        self.range_combo.currentTextChanged.connect(lambda text: self.load())
        reload_btn = QtWidgets.QPushButton("reload")
        reload_btn.clicked.connect(lambda checked=False: self.load())
        self.status = QtWidgets.QLabel("")
        bar.addWidget(QtWidgets.QLabel("last"))
        bar.addWidget(self.range_combo)
        bar.addWidget(reload_btn)
        bar.addWidget(self.status, 1)

        self.figure, self.canvas = _figure_canvas(6, 3.2)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)
        layout.addWidget(NavigationToolbar2QT(self.canvas, self))
        layout.addWidget(self.canvas, 1)
        self.load()

    def load(self):
        self._generation += 1
        generation = self._generation
        label = self.range_combo.currentText()
        seconds = self._ranges.get(label, 3600)
        self.status.setText(f"loading the last {label} from the archiver…")

        def done(data):
            if generation != self._generation:
                return
            self.figure.clear()
            ax = self.figure.add_subplot()
            if data is None or data.empty:
                self.status.setText(f"no archived data in the last {label}")
            elif item_menu.plot_history(ax, data):
                ax.set_title(self.title, fontsize="small", loc="left")
                self.status.setText(f"last {label}, {len(data)} rows")
            else:
                self.status.setText("archived data is not numeric -- nothing to plot")
            self.canvas.draw_idle()

        def failed(exc):
            if generation == self._generation:
                self.status.setText(f"failed: {type(exc).__name__}: {exc}")

        _in_background(lambda: item_menu.fetch_history(self.item, seconds), done, failed)

    def stop(self):
        self._generation += 1  # ignore a fetch still in flight


def open_history(item, name=None, get_container=None):
    panel = HistoryPanelQt(item, title=name)
    handle = _Handle(panel, on_stop=panel.stop)
    host(f"history: {panel.title}", handle, get_container)
    return handle


# -- the menu -----------------------------------------------------------------------


def _open_view(item, name, method_name, get_container, parent):
    try:
        result = item_menu.call_widget_method(item, method_name, "qt")
    except Exception as exc:
        _show_error(parent, f"{name}: {method_name}()", exc)
        return
    title = f"{name} ({method_name[len('_widget_'):]})"
    if isinstance(getattr(result, "window", None), QtWidgets.QWidget):
        host(title, result, get_container)
    elif isinstance(result, QtWidgets.QWidget):
        host(title, _Handle(result, on_stop=getattr(result, "stop", None)), get_container)
    # anything else showed itself (or is not a Qt view) -- nothing to host


def _open_details(item, name, parent):
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(f"details - {name}")
    dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    text = QtWidgets.QPlainTextEdit("reading…")
    text.setReadOnly(True)
    text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
    font = text.font()
    font.setFamily("monospace")
    text.setFont(font)
    copy_btn = QtWidgets.QPushButton("copy")
    copy_btn.clicked.connect(lambda checked=False: QtWidgets.QApplication.clipboard().setText(text.toPlainText()))
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(text)
    row = QtWidgets.QHBoxLayout()
    row.addStretch(1)
    row.addWidget(copy_btn)
    layout.addLayout(row)
    dialog.resize(560, 320)
    dialog.show()
    _in_background(
        lambda: item_menu.details_text(item_menu.describe_item(item)),
        text.setPlainText,
        lambda exc: text.setPlainText(f"{type(exc).__name__}: {exc}"),
    )
    return dialog


def _open_strip_plot_window(item, name, parent):
    # reads units/channels (hardware) before it spawns its own process
    _in_background(item.strip_plot, lambda handle: None, lambda exc: _show_error(parent, f"{name}: strip plot", exc))


def _open_memories(item, name, get_container):
    from eco.widgets.memory_widget import make_memory_browser_qt

    browser = make_memory_browser_qt(item, parent=None)
    if isinstance(getattr(browser, "window", None), QtWidgets.QWidget):
        host(f"memories: {name}", browser, get_container)


def _find_ioc(channel):
    from eco.widgets.ioc_finder_qt import make_ioc_finder_qt_window

    finder = make_ioc_finder_qt_window(auto_start=True)
    finder.finder.search_box.setText(channel.split(".")[0])
    finder.finder.search()
    return finder


def populate_menu(menu, item, name, get_container=None, parent=None):
    """Fill `menu` (a QMenu, emptied first) with what `item` offers -- see
    eco.widgets.item_menu. `get_container()` returns the desktop container
    to dock new views into, or None for separate windows."""
    _ensure_gui_bridge()
    menu.clear()

    views = item_menu.widget_methods(item)
    if views:
        open_menu = menu.addMenu("Open")
        for label, method_name in views:
            open_menu.addAction(label).triggered.connect(
                lambda checked=False, m=method_name: _open_view(item, name, m, get_container, parent)
            )

    if item_menu.is_readable(item):
        menu.addAction("Live strip chart").triggered.connect(
            lambda checked=False: open_strip_chart(item, name, get_container)
        )
    if item_menu.has_strip_plot(item):
        menu.addAction("Strip plot window (archiver)").triggered.connect(
            lambda checked=False: _open_strip_plot_window(item, name, parent)
        )
    if item_menu.has_history(item):
        menu.addAction("History (archiver)…").triggered.connect(
            lambda checked=False: open_history(item, name, get_container)
        )

    if item_menu.is_readable(item):
        try:
            from eco.widgets.indicator_widgets import INDICATOR_TYPES, _create_and_add
        except Exception:
            INDICATOR_TYPES = ()
        if INDICATOR_TYPES:
            indicator_menu = menu.addMenu("Add indicator")
            for kind, _factory, _needs_range in INDICATOR_TYPES:
                indicator_menu.addAction(kind).triggered.connect(
                    lambda checked=False, k=kind: _create_and_add(item, name, k, None)
                )

    if item_menu.has_memory(item):
        menu.addAction("Memories").triggered.connect(
            lambda checked=False: _open_memories(item, name, get_container)
        )

    menu.addSeparator()
    menu.addAction("Details…").triggered.connect(lambda checked=False: _open_details(item, name, parent))
    full = item_menu.full_name(item)
    menu.addAction(f"Copy name  ({full})").triggered.connect(
        lambda checked=False: QtWidgets.QApplication.clipboard().setText(full)
    )
    channels = item_menu.channels_of(item)
    if channels:
        menu.addAction("Copy PV name" + ("s" if len(channels) > 1 else "")).triggered.connect(
            lambda checked=False: QtWidgets.QApplication.clipboard().setText("\n".join(channels))
        )
        menu.addAction("Find IOC…").triggered.connect(lambda checked=False: _find_ioc(channels[0]))
    return menu


def make_menu_button(item, name, get_container=None):
    """The small "⋯" button at the end of a grid row; its menu is filled
    each time it opens, so it never costs anything for rows nobody clicks."""
    button = QtWidgets.QToolButton()
    button.setText("⋯")
    button.setToolTip("More for this item: views, strip chart, history, indicators, details…")
    button.setAutoRaise(True)
    button.setFixedWidth(26)
    button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
    menu = QtWidgets.QMenu(button)
    menu.aboutToShow.connect(lambda: populate_menu(menu, item, name, get_container, button.window()))
    button.setMenu(menu)
    return button


def attach_context_menu(widget, item, name, get_container=None):
    """The same menu on right-click of `widget` (a row's name label)."""
    widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

    def show(pos):
        menu = QtWidgets.QMenu(widget)
        populate_menu(menu, item, name, get_container, widget.window())
        menu.exec_(widget.mapToGlobal(pos))

    widget.customContextMenuRequested.connect(show)
