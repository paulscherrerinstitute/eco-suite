"""ipywidgets frontend for eco.epics.iocinfo (notebook use).

Search for an IOC by name or PV pattern, inspect its boot info, and check
whether its console is reachable -- the notebook equivalent of PSI's `cmdt`
CLI tool, built on the same `iocinfo.psi.ch` backend (see
`eco.epics.iocinfo` for what that can/can't resolve). Search is a fuzzy
substring match by default (see `eco.epics.iocinfo._fuzzify`) and commonly
returns matches across several facilities -- the "Facility:" dropdown
filters to one, and "Sort by IOC name" replaces the API's relevance-ish
ordering with a simple alphabetical one.

Usage:
    from eco.widgets.ioc_finder_widget import make_ioc_finder_widget
    w = make_ioc_finder_widget()
    display(w)

Searching and "Check status" only do HTTP lookups and a connect-and-close
TCP probe -- nothing is ever sent. "Show console output" connects and reads
passively (also nothing sent). "Restart IOC" is the one action that sends
something (Ctrl-X, procServ's restart-child hotkey) -- it is gated behind an
inline "are you sure?" confirmation step; see eco.epics.iocinfo's module
docstring for what that hotkey has and hasn't been verified against.
"""
from typing import Optional

import ipywidgets as widgets

from eco.epics.iocinfo import (
    IocMatch,
    check_console_reachable,
    find_ioc,
    read_console_output,
    restart_ioc,
)


def _format_boot(m: IocMatch) -> str:
    if m.boot is None:
        return "<i>no boot info available from iocinfo.psi.ch</i>"
    b = m.boot
    lines = [
        f"<b>Host:</b> {b.hostname or '?'} ({b.ip_address or '?'})",
        f"<b>Platform:</b> {b.platform or '?'}",
        f"<b>EPICS:</b> {b.epics_version or '?'} ({b.epics_host_architecture or '?'})",
        f"<b>Facility:</b> {b.facility or '?'}",
        f"<b>Last boot:</b> {b.boot_date or '?'}",
        f"<b>Responsible:</b> {b.responsible or '?'}",
    ]
    return "<br>".join(lines)


class IocFinderWidget(widgets.VBox):
    def __init__(self):
        self._matches: list[IocMatch] = []
        self._selected: Optional[IocMatch] = None

        self.search_box = widgets.Text(
            placeholder="IOC name or PV pattern, e.g. SARES20-MF or .*SARES20-MF.*",
            description="Search:",
            layout=widgets.Layout(width="420px"),
        )
        self.search_btn = widgets.Button(
            description="Search", icon="search", layout=widgets.Layout(width="100px")
        )
        search_row = widgets.HBox([self.search_box, self.search_btn])

        self.facility_filter = widgets.Dropdown(
            options=["All"], value="All", description="Facility:",
            layout=widgets.Layout(width="220px"),
        )
        self.sort_checkbox = widgets.Checkbox(
            value=False, description="Sort by IOC name", indent=False,
        )
        filter_row = widgets.HBox([self.facility_filter, self.sort_checkbox])

        self.results_box = widgets.VBox()
        self.status_label = widgets.HTML(value="<i>enter a pattern and search</i>")

        self.detail_box = widgets.VBox()

        super().__init__(
            [search_row, filter_row, self.status_label, self.results_box, self.detail_box]
        )

        self.search_btn.on_click(lambda _: self.search())
        self.search_box.on_submit(lambda _: self.search())
        self.facility_filter.observe(lambda change: self._render_results(), "value")
        self.sort_checkbox.observe(lambda change: self._render_results(), "value")

    # -- search ---------------------------------------------------------------
    def search(self) -> None:
        pattern = self.search_box.value.strip()
        if not pattern:
            return
        self.status_label.value = f"<i>searching for '{pattern}'...</i>"
        self.results_box.children = []
        self.detail_box.children = []
        try:
            self._matches = find_ioc(pattern)
        except Exception as e:
            self.status_label.value = f"<span style='color:red'>Search failed: {e}</span>"
            self._matches = []
            self._update_facility_options()
            return
        self._update_facility_options()
        self._render_results()

    def _update_facility_options(self) -> None:
        facilities = sorted({m.facility for m in self._matches if m.facility})
        current = self.facility_filter.value
        self.facility_filter.options = ["All"] + facilities
        self.facility_filter.value = current if current in self.facility_filter.options else "All"

    def _render_results(self) -> None:
        if not self._matches:
            self.status_label.value = "<i>no IOC found</i>" if self.search_box.value.strip() else "<i>enter a pattern and search</i>"
            self.results_box.children = []
            return
        facility = self.facility_filter.value
        shown = [m for m in self._matches if facility == "All" or m.facility == facility]
        if self.sort_checkbox.value:
            shown = sorted(shown, key=lambda m: m.ioc)
        n_total = len(self._matches)
        n_shown = len(shown)
        suffix = "" if n_shown == n_total else f" ({n_shown} shown)"
        self.status_label.value = f"<b>{n_total}</b> IOC(s) found{suffix}:"
        self.results_box.children = [self._build_result_row(m) for m in shown]

    def _build_result_row(self, m: IocMatch):
        console = f"{m.console_host}:{m.console_port}" if m.console_host else "console unknown"
        n_dev = len(m.devices)
        fac = f"  {{{m.facility}}}" if m.facility else ""
        btn = widgets.Button(
            description=f"{m.ioc}{fac}  [{console}]  ({n_dev} device(s))",
            layout=widgets.Layout(width="auto"),
        )
        btn.on_click(lambda _, m=m: self._select(m))
        return btn

    # -- detail / actions -------------------------------------------------------
    def _select(self, m: IocMatch) -> None:
        self._selected = m

        info = widgets.HTML(value=_format_boot(m))
        devices = widgets.HTML(
            value="<b>Devices:</b> " + ", ".join(m.devices[:30])
            + ("" if len(m.devices) <= 30 else f", +{len(m.devices) - 30} more")
        )

        status_label = widgets.HTML(value="<i>not checked</i>")
        status_btn = widgets.Button(description="Check status", icon="heartbeat")

        def _check(_):
            if not m.console_host:
                status_label.value = "<span style='color:orange'>console address unknown, cannot check</span>"
                return
            status_label.value = "<i>checking...</i>"
            ok = check_console_reachable(m.console_host, m.console_port)
            if ok:
                status_label.value = f"<span style='color:green'><b>reachable</b> ({m.console_host}:{m.console_port})</span>"
            else:
                status_label.value = f"<span style='color:red'><b>not reachable</b> ({m.console_host}:{m.console_port})</span>"

        status_btn.on_click(_check)

        console_panel = self._build_console_panel(m)

        self.detail_box.children = [
            widgets.HTML(value=f"<h4>{m.ioc}</h4>"),
            info,
            devices,
            widgets.HBox([status_btn, status_label]),
            console_panel,
        ]

    def _build_console_panel(self, m: IocMatch):
        """Console panel: manual telnet command, an on-demand (read-only)
        console-output viewer, and a Restart button gated behind an inline
        confirmation step. Restart sends iocinfo.RESTART_KEY (Ctrl-X),
        confirmed for the Bernina MForce consoles -- see iocinfo's module
        docstring for scope/caveats."""
        if not m.console_host:
            return widgets.HTML(
                value="<i>No console address known for this IOC "
                "(not in iocinfo.psi.ch's `shellbox` field or the local "
                "override table). Restart/console access must be looked up "
                "manually.</i>"
            )
        host, port = m.console_host, m.console_port
        cmd = f"telnet {host} {port}"

        cmd_label = widgets.HTML(
            value=f"<b>Console:</b> <code>{cmd}</code> (or via your usual ssh proxy)"
        )

        output_btn = widgets.Button(description="Show console output", icon="eye")
        output_area = widgets.Output(
            layout=widgets.Layout(border="1px solid #ccc", max_height="200px", overflow="auto")
        )

        def _show_output(_):
            output_area.clear_output()
            with output_area:
                print(f"reading {host}:{port} for 2s (read-only, nothing sent)...")
            try:
                text = read_console_output(host, port, read_seconds=2.0)
            except OSError as e:
                output_area.clear_output()
                with output_area:
                    print(f"could not connect: {e}")
                return
            output_area.clear_output()
            with output_area:
                print(text if text else "(no output received)")

        output_btn.on_click(_show_output)

        restart_btn = widgets.Button(
            description="Restart IOC", icon="power-off", button_style="danger"
        )
        restart_result = widgets.HTML(value="")

        confirm_label = widgets.HTML(
            value=(
                f"<span style='color:#b30000'><b>Really restart {m.ioc}?</b> "
                f"Sends Ctrl-X to {host}:{port} -- this restarts the IOC "
                "process now, no undo.</span>"
            )
        )
        confirm_yes = widgets.Button(description="Yes, restart now", button_style="danger")
        confirm_no = widgets.Button(description="Cancel")
        confirm_row = widgets.VBox(
            [confirm_label, widgets.HBox([confirm_yes, confirm_no])]
        )
        confirm_row.layout.display = "none"

        def _ask(_):
            confirm_row.layout.display = ""
            restart_btn.disabled = True

        def _cancel(_):
            confirm_row.layout.display = "none"
            restart_btn.disabled = False

        def _confirm(_):
            confirm_row.layout.display = "none"
            restart_result.value = f"<i>sending restart to {host}:{port}...</i>"
            try:
                restart_ioc(host, port)
            except OSError as e:
                restart_result.value = f"<span style='color:red'>restart failed: {e}</span>"
            else:
                restart_result.value = (
                    f"<span style='color:green'>restart sent to {m.ioc} "
                    "-- use 'Show console output' to watch it come back up.</span>"
                )
            restart_btn.disabled = False

        restart_btn.on_click(_ask)
        confirm_yes.on_click(_confirm)
        confirm_no.on_click(_cancel)

        return widgets.VBox(
            [
                cmd_label,
                widgets.HBox([output_btn, restart_btn]),
                output_area,
                confirm_row,
                restart_result,
            ]
        )

    def get_selected(self) -> Optional[IocMatch]:
        return self._selected


def make_ioc_finder_widget() -> IocFinderWidget:
    return IocFinderWidget()
