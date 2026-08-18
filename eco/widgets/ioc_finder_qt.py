"""Qt frontend for eco.epics.iocinfo (scripts/desktop use).

Uses qtpy so it works with whichever Qt binding is installed (PyQt5/PyQt6/
PySide2/PySide6) -- same convention as eco.widgets.component_selector_qt.

Usage (embedded):
    from eco.widgets.ioc_finder_qt import IocFinderQt
    finder = IocFinderQt()
    some_layout.addWidget(finder)

Usage (standalone window):
    from eco.widgets.ioc_finder_qt import make_ioc_finder_qt_window
    gui = make_ioc_finder_qt_window()
    ...
    gui.stop()

Search is a fuzzy substring match by default (see
`eco.epics.iocinfo._fuzzify`) and commonly returns matches across several
facilities -- the "Facility:" dropdown filters to one, and "Sort by IOC
name" replaces the API's relevance-ish ordering with a simple alphabetical
one.

Searching and "Check status" only do HTTP lookups and a connect-and-close
TCP probe -- nothing is ever sent. "Show console output" connects and reads
passively (also nothing sent). "Restart IOC" sends Ctrl-X (procServ's
restart-child hotkey) after a QMessageBox confirmation dialog -- see
eco.epics.iocinfo's module docstring for what that hotkey has and hasn't
been verified against.
"""
from typing import Optional

from qtpy import QtCore, QtWidgets

from eco.epics.iocinfo import (
    IocMatch,
    check_console_reachable,
    find_ioc,
    read_console_output,
    restart_ioc,
)

_app_ref = None  # keep a strong reference to any QApplication we create ourselves


def _format_boot(m: IocMatch) -> str:
    if m.boot is None:
        return "no boot info available from iocinfo.psi.ch"
    b = m.boot
    lines = [
        f"Host: {b.hostname or '?'} ({b.ip_address or '?'})",
        f"Platform: {b.platform or '?'}",
        f"EPICS: {b.epics_version or '?'} ({b.epics_host_architecture or '?'})",
        f"Facility: {b.facility or '?'}",
        f"Last boot: {b.boot_date or '?'}",
        f"Responsible: {b.responsible or '?'}",
    ]
    return "\n".join(lines)


class _SearchWorker(QtCore.QThread):
    done = QtCore.Signal(list, str)

    def __init__(self, pattern: str, parent=None):
        super().__init__(parent)
        self.pattern = pattern

    def run(self) -> None:
        try:
            matches = find_ioc(self.pattern)
            self.done.emit(matches, "")
        except Exception as e:
            self.done.emit([], str(e))


class _StatusWorker(QtCore.QThread):
    done = QtCore.Signal(bool)

    def __init__(self, host: str, port: int, parent=None):
        super().__init__(parent)
        self.host = host
        self.port = port

    def run(self) -> None:
        self.done.emit(check_console_reachable(self.host, self.port))


class _ConsoleOutputWorker(QtCore.QThread):
    done = QtCore.Signal(str, str)  # (text, error)

    def __init__(self, host: str, port: int, parent=None):
        super().__init__(parent)
        self.host = host
        self.port = port

    def run(self) -> None:
        try:
            text = read_console_output(self.host, self.port, read_seconds=2.0)
            self.done.emit(text, "")
        except OSError as e:
            self.done.emit("", str(e))


class _RestartWorker(QtCore.QThread):
    done = QtCore.Signal(str)  # error, empty string on success

    def __init__(self, host: str, port: int, parent=None):
        super().__init__(parent)
        self.host = host
        self.port = port

    def run(self) -> None:
        try:
            restart_ioc(self.host, self.port)
            self.done.emit("")
        except OSError as e:
            self.done.emit(str(e))


class IocFinderQt(QtWidgets.QWidget):
    """Embeddable QWidget: search row, result list, detail panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._matches: list[IocMatch] = []
        self._displayed: list[IocMatch] = []
        self._selected: Optional[IocMatch] = None
        self._search_thread: Optional[_SearchWorker] = None
        self._status_thread: Optional[_StatusWorker] = None
        self._output_thread: Optional[_ConsoleOutputWorker] = None
        self._restart_thread: Optional[_RestartWorker] = None

        layout = QtWidgets.QVBoxLayout(self)

        search_row = QtWidgets.QHBoxLayout()
        self.search_box = QtWidgets.QLineEdit()
        self.search_box.setPlaceholderText(
            "IOC name or PV pattern, e.g. SARES20-MF or .*SARES20-MF.*"
        )
        self.search_btn = QtWidgets.QPushButton("Search")
        search_row.addWidget(QtWidgets.QLabel("Search:"))
        search_row.addWidget(self.search_box, 1)
        search_row.addWidget(self.search_btn)
        layout.addLayout(search_row)

        filter_row = QtWidgets.QHBoxLayout()
        self.facility_filter = QtWidgets.QComboBox()
        self.facility_filter.addItems(["All"])
        self.sort_checkbox = QtWidgets.QCheckBox("Sort by IOC name")
        filter_row.addWidget(QtWidgets.QLabel("Facility:"))
        filter_row.addWidget(self.facility_filter)
        filter_row.addWidget(self.sort_checkbox)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        self.status_label = QtWidgets.QLabel("enter a pattern and search")
        layout.addWidget(self.status_label)

        self.result_list = QtWidgets.QListWidget()
        self.result_list.setMaximumHeight(160)
        layout.addWidget(self.result_list)

        self.detail_text = QtWidgets.QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        layout.addWidget(self.detail_text, 1)

        status_row = QtWidgets.QHBoxLayout()
        self.check_status_btn = QtWidgets.QPushButton("Check status")
        self.console_status_label = QtWidgets.QLabel("not checked")
        status_row.addWidget(self.check_status_btn)
        status_row.addWidget(self.console_status_label, 1)
        layout.addLayout(status_row)

        self.console_label = QtWidgets.QLabel("")
        self.console_label.setWordWrap(True)
        self.console_label.setStyleSheet("color: #b36b00;")
        layout.addWidget(self.console_label)

        console_btn_row = QtWidgets.QHBoxLayout()
        self.output_btn = QtWidgets.QPushButton("Show console output")
        self.restart_btn = QtWidgets.QPushButton("Restart IOC")
        self.restart_btn.setStyleSheet("QPushButton { color: white; background-color: #b30000; }")
        console_btn_row.addWidget(self.output_btn)
        console_btn_row.addWidget(self.restart_btn)
        layout.addLayout(console_btn_row)

        self.search_btn.clicked.connect(self.search)
        self.search_box.returnPressed.connect(self.search)
        self.result_list.currentRowChanged.connect(self._on_row_changed)
        self.check_status_btn.clicked.connect(self._check_status)
        self.check_status_btn.setEnabled(False)
        self.output_btn.clicked.connect(self._show_console_output)
        self.output_btn.setEnabled(False)
        self.restart_btn.clicked.connect(self._confirm_restart)
        self.restart_btn.setEnabled(False)
        self.facility_filter.currentTextChanged.connect(self._render_results)
        self.sort_checkbox.stateChanged.connect(self._render_results)

    # -- search ---------------------------------------------------------------
    def search(self) -> None:
        pattern = self.search_box.text().strip()
        if not pattern:
            return
        self.status_label.setText(f"searching for '{pattern}'...")
        self.result_list.clear()
        self.detail_text.setPlainText("")
        self.search_btn.setEnabled(False)

        self._search_thread = _SearchWorker(pattern, parent=self)
        self._search_thread.done.connect(self._on_search_done)
        self._search_thread.start()

    def _on_search_done(self, matches: list, error: str) -> None:
        self.search_btn.setEnabled(True)
        if error:
            self.status_label.setText(f"search failed: {error}")
            return
        self._matches = matches
        self._update_facility_options()
        self._render_results()

    def _update_facility_options(self) -> None:
        facilities = sorted({m.facility for m in self._matches if m.facility})
        current = self.facility_filter.currentText()
        self.facility_filter.blockSignals(True)
        self.facility_filter.clear()
        self.facility_filter.addItems(["All"] + facilities)
        idx = self.facility_filter.findText(current)
        self.facility_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.facility_filter.blockSignals(False)

    def _render_results(self, *_args) -> None:
        self.result_list.clear()
        if not self._matches:
            self.status_label.setText("no IOC found")
            self._displayed = []
            return
        facility = self.facility_filter.currentText()
        shown = [m for m in self._matches if facility == "All" or m.facility == facility]
        if self.sort_checkbox.isChecked():
            shown = sorted(shown, key=lambda m: m.ioc)
        self._displayed = shown

        n_total = len(self._matches)
        n_shown = len(shown)
        suffix = "" if n_shown == n_total else f" ({n_shown} shown)"
        self.status_label.setText(f"{n_total} IOC(s) found{suffix}:")
        for m in shown:
            console = f"{m.console_host}:{m.console_port}" if m.console_host else "console unknown"
            fac = f"  {{{m.facility}}}" if m.facility else ""
            item = QtWidgets.QListWidgetItem(f"{m.ioc}{fac}  [{console}]  ({len(m.devices)} device(s))")
            self.result_list.addItem(item)

    # -- selection / detail -----------------------------------------------------
    def _on_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._displayed):
            self._selected = None
            self.check_status_btn.setEnabled(False)
            self.output_btn.setEnabled(False)
            self.restart_btn.setEnabled(False)
            return
        m = self._displayed[row]
        self._selected = m
        has_console = m.console_host is not None
        self.check_status_btn.setEnabled(has_console)
        self.output_btn.setEnabled(has_console)
        self.restart_btn.setEnabled(has_console)
        self.console_status_label.setText("not checked")

        devices = ", ".join(m.devices[:30])
        if len(m.devices) > 30:
            devices += f", +{len(m.devices) - 30} more"
        text = _format_boot(m) + f"\n\nDevices: {devices}"
        self.detail_text.setPlainText(text)

        if m.console_host:
            self.console_label.setText(
                f"Console (manual): telnet {m.console_host} {m.console_port}\n"
                "'Restart IOC' sends Ctrl-X (procServ's restart-child hotkey, "
                "confirmed for the Bernina MForce consoles) after you confirm "
                "the dialog below. 'Show console output' just reads, nothing "
                "sent."
            )
        else:
            self.console_label.setText(
                "No console address known for this IOC (not in "
                "iocinfo.psi.ch's `shellbox` field or the local override "
                "table). Restart/console access must be looked up manually."
            )

    def _check_status(self) -> None:
        if self._selected is None or not self._selected.console_host:
            return
        m = self._selected
        self.console_status_label.setText("checking...")
        self.check_status_btn.setEnabled(False)
        self._status_thread = _StatusWorker(m.console_host, m.console_port, parent=self)
        self._status_thread.done.connect(lambda ok, m=m: self._on_status_done(ok, m))
        self._status_thread.start()

    def _on_status_done(self, ok: bool, m: IocMatch) -> None:
        self.check_status_btn.setEnabled(True)
        if ok:
            self.console_status_label.setText(f"reachable ({m.console_host}:{m.console_port})")
            self.console_status_label.setStyleSheet("color: green;")
        else:
            self.console_status_label.setText(f"not reachable ({m.console_host}:{m.console_port})")
            self.console_status_label.setStyleSheet("color: red;")

    # -- console output (read-only, on demand) -----------------------------------
    def _show_console_output(self) -> None:
        if self._selected is None or not self._selected.console_host:
            return
        m = self._selected
        self.output_btn.setEnabled(False)
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Console output: {m.ioc}")
        dlg.resize(520, 320)
        dlg_layout = QtWidgets.QVBoxLayout(dlg)
        text = QtWidgets.QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(f"reading {m.console_host}:{m.console_port} for 2s (read-only, nothing sent)...")
        dlg_layout.addWidget(text)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        dlg_layout.addWidget(close_btn)

        self._output_thread = _ConsoleOutputWorker(m.console_host, m.console_port, parent=self)

        def _done(out: str, error: str) -> None:
            self.output_btn.setEnabled(True)
            if error:
                text.setPlainText(f"could not connect: {error}")
            else:
                text.setPlainText(out if out else "(no output received)")

        self._output_thread.done.connect(_done)
        self._output_thread.start()
        dlg.exec_()

    # -- restart (gated by confirmation dialog) -----------------------------------
    def _confirm_restart(self) -> None:
        if self._selected is None or not self._selected.console_host:
            return
        m = self._selected
        answer = QtWidgets.QMessageBox.question(
            self,
            "Restart IOC?",
            f"Really restart {m.ioc}?\n\n"
            f"Sends Ctrl-X to {m.console_host}:{m.console_port} -- this "
            "restarts the IOC process now, no undo.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.restart_btn.setEnabled(False)
        self.console_status_label.setText(f"sending restart to {m.console_host}:{m.console_port}...")
        self._restart_thread = _RestartWorker(m.console_host, m.console_port, parent=self)
        self._restart_thread.done.connect(lambda error, m=m: self._on_restart_done(error, m))
        self._restart_thread.start()

    def _on_restart_done(self, error: str, m: IocMatch) -> None:
        self.restart_btn.setEnabled(True)
        if error:
            self.console_status_label.setText(f"restart failed: {error}")
            self.console_status_label.setStyleSheet("color: red;")
        else:
            self.console_status_label.setText(
                f"restart sent to {m.ioc} -- use 'Show console output' to watch it come back up"
            )
            self.console_status_label.setStyleSheet("color: green;")

    def get_selected(self) -> Optional[IocMatch]:
        return self._selected


class IocFinderQtWindow:
    """Standalone-window wrapper, mirrors ComponentSelectorQtWindow."""

    def __init__(self, auto_start: bool = True):
        self.window: Optional[QtWidgets.QMainWindow] = None
        self.finder: Optional[IocFinderQt] = None
        if auto_start:
            self.start()

    def _build_window(self) -> None:
        global _app_ref
        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication([])
            _app_ref = app
        self.finder = IocFinderQt()
        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle("IOC finder")
        self.window.setCentralWidget(self.finder)
        self.window.resize(700, 560)
        self.window.show()

    def run(self) -> None:
        app = QtWidgets.QApplication.instance()
        created_app = app is None
        if created_app:
            app = QtWidgets.QApplication([])
        if self.window is None:
            self._build_window()
        if created_app:
            app.exec_()

    def start(self) -> None:
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
        if active is None:
            try:
                ip.enable_gui("qt")
            except Exception:
                pass
        elif active not in ("qt", "qt4", "qt5", "qt6"):
            print(
                f"eco IOC finder: a different GUI event loop ('{active}') is "
                "already active in this IPython session, so the window can't "
                "be pumped non-blockingly alongside it. Showing it in "
                "blocking mode instead (closing the window returns control)."
            )
            self.run()
            return

        self._build_window()

    def stop(self) -> None:
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None
            self.finder = None

    def get_selected(self):
        if self.finder is None:
            return None
        return self.finder.get_selected()


def make_ioc_finder_qt() -> IocFinderQt:
    """Build just the embeddable QWidget (no window)."""
    return IocFinderQt()


def make_ioc_finder_qt_window(auto_start: bool = True) -> IocFinderQtWindow:
    return IocFinderQtWindow(auto_start=auto_start)
