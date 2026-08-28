import os
import json
import threading
from dash import Dash, html, dcc
from dash.dependencies import Input, Output
from flask import Response
from IPython import get_ipython

# Shared between the browser (Dash) and native-window (WebKitGTK) backends.
# Extracts the Python command attached to an SVG element, in priority order:
# 1. onclick="// eco: <command>" - set via Inkscape's Object Properties
#    (Ctrl+Shift+O) "Interactivity" section, "onclick" field. Deliberately
#    wrapped as a JS comment rather than raw code: onclick is a real DOM
#    event-handler attribute, so if this SVG is ever opened standalone or
#    embedded on a real page, a genuine browser will try to execute its
#    content as JavaScript on click. A JS line comment makes that a
#    guaranteed no-op (verified empirically) instead of a broken/misleading
#    attempt to run Python as JS. "eco" is this project's marker so it reads
#    clearly in the raw XML and won't collide with unrelated onclick use.
# 2. <desc> child element - set via Inkscape's Object Properties (Ctrl+Shift+O)
#    "Description" field. This is a CHILD ELEMENT, not a desc="..." attribute -
#    so what you see in Inkscape's Properties dialog is exactly what runs.
#    Deliberately NOT wiring up "Title" too: Title is commonly used as a
#    plain accessible label, and treating it as code would make plain labels
#    crash as syntax errors.
# 3. Beamline-diagram convention (only when NAMESPACE_PREFIX is set): a <text>
#    label whose first monospace-font tspan names a device, e.g. "Attenuator"
#    / "att". Any trailing "@123°" reference-angle annotation on that line is
#    stripped first.
#
# When NAMESPACE_PREFIX is set, it is prepended to whichever raw string was
# found above (from onclick, <desc>, OR the monospace convention) - e.g.
# prefix "bernina" + raw "slit_att" -> "bernina.slit_att", executed against
# that namespace object in the IPython session rather than as a standalone
# expression. With no prefix, onclick/<desc> content is used verbatim (e.g. a
# full print(...) call).
_EXTRACT_COMMAND_JS = r"""
const ONCLICK_MARKER_RE = /^\s*\/\/\s*eco:\s*(.*)$/;

function isInsideExcludedGroup(el) {
    for (let node = el; node; node = node.parentNode) {
        if (node.getAttribute && EXCLUDE_GROUP_IDS.includes(node.getAttribute("id"))) {
            return true;
        }
    }
    return false;
}

function extractCommand(el) {
    let raw = null;

    let onclickAttr = el.getAttribute("onclick");
    if (onclickAttr) {
        let m = onclickAttr.match(ONCLICK_MARKER_RE);
        if (m && m[1].trim()) {
            raw = m[1].trim();
        }
    }
    if (!raw) {
        let descEl = el.querySelector(":scope > desc");
        if (descEl && descEl.textContent.trim()) {
            raw = descEl.textContent.trim();
        }
    }
    if (!raw && NAMESPACE_PREFIX) {
        let textEl = el.closest("text");
        if (textEl && isInsideExcludedGroup(textEl)) {
            textEl = null;
        }
        if (textEl) {
            let tspans = textEl.querySelectorAll("tspan");
            for (let i = 0; i < tspans.length; i++) {
                let fam = tspans[i].style.fontFamily || "";
                if (fam.indexOf("mono") !== -1) {
                    let cleaned = (tspans[i].textContent || "")
                        .replace(ANGLE_SUFFIX_RE, "").trim();
                    if (cleaned) {
                        raw = cleaned;
                    }
                    break;
                }
            }
        }
    }
    if (!raw) return null;
    return NAMESPACE_PREFIX ? (NAMESPACE_PREFIX + "." + raw) : raw;
}
"""


def _build_extract_command_js(namespace_prefix, exclude_group_ids):
    header = (
        "const NAMESPACE_PREFIX = " + json.dumps(namespace_prefix) + ";\n"
        "const EXCLUDE_GROUP_IDS = " + json.dumps(list(exclude_group_ids or [])) + ";\n"
        r"const ANGLE_SUFFIX_RE = /\s*@\s*[\d.]+\s*°?\s*$/;" + "\n"
    )
    return header + _EXTRACT_COMMAND_JS


def _peek_svg_dimensions(svg_content, default=(800, 600)):
    """Reads an SVG's own width/height so viewers can open at a sane,
    correctly-proportioned size instead of an arbitrary default."""
    import xml.etree.ElementTree as ET

    width, height = default
    try:
        root = ET.fromstring(svg_content)
        width = float(root.get("width", width))
        height = float(root.get("height", height))
    except Exception:
        pass
    return width, height


def _in_notebook(ip):
    """True for a Jupyter notebook/lab kernel, False for terminal IPython."""
    return ip.__class__.__name__ == "ZMQInteractiveShell"


def _run_dash_server(svg_path, ip_instance, namespace_prefix=None, exclude_group_ids=None,
                      refresh=None, refresh_interval_ms=2000, port_holder=None):
    """Launches a local webserver hosting the interactive Inkscape SVG.

    refresh (see `launch_svg_viewer`): optional no-arg callable returning the
    path of a freshly-rebuilt SVG (e.g. an Assembly's `lambda: self._widget_svg_panel(
    live=True)`). When given, `/svg-content` rebuilds from it on *every*
    request instead of serving a fixed snapshot, and a small script injected
    below reloads the embedded SVG object every `refresh_interval_ms` for as
    long as the page stays open. This is the *one* refresh mechanism shared
    by both this browser-based viewer and the in-window Qt viewer (see
    `_build_qt_window`, which for a live panel points its QWebEngineView at
    this same server instead of embedding a static snapshot): the polling
    and DOM-swap run entirely inside the browser engine's own JS runtime, in
    its own process, independent of whatever the eco Python process' main
    thread happens to be doing at the time (e.g. blocked inside a running
    `PrepumpSystem.pump_down()` poll loop) -- see CLAUDE.md if this class of
    "the live panel doesn't update during a running macro" issue comes up
    again for some other panel.

    port_holder: optional dict with a "port" key and a `threading.Event`
    under "ready", filled in with the OS-assigned port (and, for `_build_qt_
    window`'s benefit, the bound `server` object itself, under "server")
    once actually bound -- since this normally runs on a background thread,
    the caller can `.wait()` on "ready" to learn the real URL before
    displaying/loading it. Binding to port 0 (OS-assigned) rather than a
    fixed one avoids two live panels (e.g. prepump and beamline open at the
    same time) colliding on the same port.
    """
    app = Dash(__name__)

    # Fail fast (and unblock a waiting caller) if the file isn't even
    # readable, before spending any time standing up a server for it.
    try:
        with open(svg_path, "r", encoding="utf-8") as f:
            f.read()
    except Exception as e:
        print(f"Error reading SVG file: {e}")
        if port_holder is not None:
            port_holder["ready"].set()
        return

    # Serve the SVG from a same-origin Flask route instead of a data: URL.
    # data: URLs get an opaque origin, so the browser blocks the parent page
    # from accessing the <object>'s contentDocument (silently, no error) -
    # the click listener below would never see the SVG's DOM at all.
    #
    # Re-reads (and, if `refresh` is given, rebuilds) on *every* request
    # rather than serving a snapshot captured once at server start -- both
    # the page's initial load and every subsequent periodic reload (below)
    # hit this same route, so one code path covers both.
    @app.server.route("/svg-content")
    def serve_svg():
        try:
            path = refresh() if refresh is not None else svg_path
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"eco SVG viewer: live refresh failed: {e}")
            with open(svg_path, "r", encoding="utf-8") as f:
                content = f.read()
        return Response(content, mimetype="image/svg+xml")

    # Dash's html.Script component is mounted through React's client-side
    # renderer, and browsers only auto-execute <script> tags that come from
    # the parser - one inserted this way never runs. Putting the script in
    # index_string makes it part of the raw HTML the browser actually parses.
    app.index_string = r"""
    <!DOCTYPE html>
    <html>
        <head>
            {%metas%}
            <title>{%title%}</title>
            {%favicon%}
            {%css%}
        </head>
        <body>
            {%app_entry%}
            <footer>
                {%config%}
                {%scripts%}
                {%renderer%}
            </footer>
            <script>
                __EXTRACT_COMMAND_JS__

                // Script tracking click events inside the SVG's internal document DOM tree
                function attachSvgClickHandler(svgDoc) {
                    svgDoc.addEventListener("click", function(e) {
                        let target = e.target;
                        while (target && target !== svgDoc) {
                            let commandStr = extractCommand(target);

                            if (commandStr) {
                                var relay = document.getElementById("click-relay");
                                if (relay) {
                                    // React tracks <input> values through its own setter, so
                                    // assigning relay.value directly and dispatching "input"
                                    // is invisible to it - go through the native setter instead.
                                    var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
                                    nativeSetter.call(relay, commandStr + "|||" + e.ctrlKey + "|||" + Math.random());
                                    var event = new Event("input", { bubbles: true });
                                    relay.dispatchEvent(event);
                                }
                                e.preventDefault();
                                break;
                            }
                            target = target.parentNode;
                        }
                    });
                }

                function setupSvgListener() {
                    var embedObj = document.getElementById("inkscape-svg-object");
                    if (!embedObj || embedObj.dataset.listenerAttached) {
                        setTimeout(setupSvgListener, 100);
                        return;
                    }

                    // The object's "load" event can fire before this script even
                    // runs (fast, same-origin loads), so a one-shot "load"
                    // listener added afterward could miss it entirely. Poll for
                    // readiness instead of waiting on the event.
                    var svgDoc = embedObj.contentDocument;
                    if (svgDoc && svgDoc.readyState === "complete") {
                        embedObj.dataset.listenerAttached = "1";
                        attachSvgClickHandler(svgDoc);
                    } else {
                        setTimeout(setupSvgListener, 100);
                    }
                }
                setupSvgListener();

                // Live panel only (__LIVE_REFRESH_INTERVAL_MS__ is 0
                // otherwise, so this is a no-op setInterval that never
                // fires): periodically reload the embedded SVG object from
                // /svg-content, which re-runs `refresh()` server-side on
                // every hit -- see _run_dash_server's docstring. Resetting
                // .data forces the browser to actually re-fetch (a cache-
                // busting query param, since GET responses could otherwise
                // be cached) rather than reuse the existing embedded
                // document; clearing listenerAttached first lets
                // setupSvgListener() re-attach the click handler to the new
                // document once it's ready.
                var LIVE_REFRESH_INTERVAL_MS = __LIVE_REFRESH_INTERVAL_MS__;
                if (LIVE_REFRESH_INTERVAL_MS > 0) {
                    setInterval(function() {
                        var embedObj = document.getElementById("inkscape-svg-object");
                        if (!embedObj) return;
                        embedObj.dataset.listenerAttached = "";
                        embedObj.data = "/svg-content?t=" + Date.now();
                        setupSvgListener();
                    }, LIVE_REFRESH_INTERVAL_MS);
                }
            </script>
        </body>
    </html>
    """
    # Plain string.replace, not str.format/f-string: the template above already
    # uses literal {%...%} placeholders that Dash itself substitutes, which
    # would collide with Python's own {}-based formatting syntax.
    app.index_string = app.index_string.replace(
        "__EXTRACT_COMMAND_JS__", _build_extract_command_js(namespace_prefix, exclude_group_ids)
    )
    app.index_string = app.index_string.replace(
        "__LIVE_REFRESH_INTERVAL_MS__",
        str(int(refresh_interval_ms)) if refresh is not None else "0",
    )

    app.layout = html.Div([
        # 2. Changed from html.Embed to html.ObjectEl, served from a same-origin route
        html.ObjectEl(
            id="inkscape-svg-object",
            data="/svg-content",
            type="image/svg+xml",
            style={"width": "100vw", "height": "95vh", "border": "none"}
        ),

        # Standard input field used as a bridge channel between JS events and Python callbacks
        dcc.Input(id="click-relay", type="text", style={"display": "none"})
    ], style={"margin": "0", "padding": "0", "backgroundColor": "#f0f0f0"})

    @app.callback(
        Output("click-relay", "style"),
        Input("click-relay", "value"),
        prevent_initial_call=True
    )
    def handle_web_click(relay_data):
        if not relay_data:
            return {"display": "none"}

        try:
            command_str, ctrl_pressed_str, _ = relay_data.split("|||")
            ctrl_pressed = ctrl_pressed_str == "true"

            if ctrl_pressed:
                print(f"\n[SVG Paste] -> {command_str}", end="", flush=True)
                ip_instance.set_next_input(command_str, replace=False)
            else:
                print(f"\nExecuting from SVG: {command_str}")
                ip_instance.run_cell(command_str)
        except Exception as err:
            print(f"\nError processing event layout channel: {err}")

        return {"display": "none"}

    # Run the server quietly on background loops
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    # make_server + serve_forever (not the higher-level app.run()) so we can
    # bind to an OS-assigned port (port=0) and read back which one it picked
    # -- app.run() has no way to report that back before it starts blocking.
    # Needed now that a live panel can spin up one of these per `.show(live=
    # True)` call (see launch_svg_viewer/_build_qt_window): a fixed port
    # would make a second simultaneously-open live panel (e.g. prepump and
    # beamline both open at once) fail to bind at all. The returned server
    # object also gives callers (see _build_qt_window's closeEvent) a way to
    # `.shutdown()` this thread when its window closes, instead of it
    # polling EPICS forever in the background for the rest of the session.
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, app.server)
    if port_holder is not None:
        port_holder["port"] = server.server_port
        port_holder["server"] = server
        port_holder["ready"].set()
    server.serve_forever()


_qt_app_ref = None  # keep a strong reference to any QApplication we create ourselves
_qt_windows = []  # keep strong refs to open SVG windows (+ their bridge/channel) so they aren't GC'd


class _SvgViewerHandle:
    """`.window`/`.stop()` wrapper around an already-built SvgWindow, for
    the eco desktop dock convention `EcoDesktopApp._dock_widget_object`
    expects (same shape as DisplayQt/AxisPTZStreamQt/CamServerStreamQt --
    see desktop_app.py's module docstring). `.window` is the SvgWindow
    itself (a QWebEngineView, so it dock/undocks/floats like any other
    QWidget); `.stop()` shuts down the background live-refresh server, if
    any, so nothing keeps polling EPICS after the dock is closed."""

    def __init__(self, view):
        self.window = view

    def stop(self):
        _shutdown_dash_server(self.window)


def _shutdown_dash_server(view):
    """Stops the background Dash/Flask server a live SvgWindow started for
    itself (see `_build_qt_window`), if any -- `server.shutdown()` must be
    called from a thread other than the one running `serve_forever()`,
    which is exactly the case here (this runs on the Qt/main thread, the
    server owns its own background thread), so this is safe to call
    directly rather than needing yet another thread hop."""
    server = getattr(view, "_dash_server", None)
    if server is not None:
        server.shutdown()


def _build_qt_window(svg_path, ip_instance, namespace_prefix=None, exclude_group_ids=None,
                      blocking=False, refresh=None, refresh_interval_ms=2000,
                      dock_in=None, dock_name=None):
    """Opens a native, resizable window rendering the SVG via Qt's QWebEngineView.

    dock_in (see `launch_svg_viewer`): an EcoDesktopApp instance (or
    anything with the same `_dock_widget_object(name, widget_obj)` method)
    to embed the viewer into as a tiled dock instead of opening it as a
    separate top-level window -- same convention any other eco Qt widget
    docks with (see desktop_app.py's module docstring). When given, this
    builds the SvgWindow but does NOT call `.show()`/register it in
    `_qt_windows` itself; `dock_in._dock_widget_object` takes over showing
    it (reparented into the dock) instead. Still poppable back out to a
    free-floating window at any time via the dock's own float button.

    refresh (see `launch_svg_viewer`): if given, this is a *live* panel.
    Rather than embedding a frozen snapshot and re-pushing new content into
    it from Python on a `QTimer` (the previous approach here -- removed:
    Qt's own event loop only gets pumped between IPython prompts, see
    CLAUDE.md, so that timer would never fire while e.g. a `pump_down()` is
    running, which is the whole reason this exists), this view is instead
    pointed at the *same* background Dash server the plain browser viewer
    uses (`_run_dash_server`, started here for this window specifically).
    That server's own page polls and reloads itself via a plain JS
    `setInterval` -- see `_run_dash_server`'s docstring -- which runs
    entirely inside this QWebEngineView's own Chromium process, independent
    of whatever the eco Python process' main thread is doing at the time.
    Click handling then also goes through that same page's existing Dash
    callback instead of a separate QWebChannel bridge -- one mechanism
    shared by both viewers instead of two.

    QWebEngineView embeds Chromium (the same class of engine as
    WebKitGTK/Safari) - it is NOT Qt's QtSvg module, which only implements a
    static subset of SVG (no filters, very limited gradients) and was why an
    earlier Qt-based attempt at this feature rendered complex diagrams
    incorrectly. Since QWebEngineView paints SVG the same way a real browser
    tab does, that failure mode doesn't apply here.

    Uses qtpy (same as eco.widgets.display_qt / eco.elements.assembly)
    rather than importing PyQt5 directly: IPython enforces a single Qt
    binding per session, and matplotlib's conda-forge package pulls in
    PySide6 unconditionally, so a hardcoded PyQt5 import here would collide
    with whatever binding IPython/matplotlib already loaded and raise
    "Importing PyQt5 disabled by IPython, which has already imported an
    Incompatible QT Binding". qtpy detects and reuses whichever binding is
    already active instead.

    Unlike the former WebKitGTK backend (whose Gtk.main() tolerated running
    in a background thread), Qt's event loop hard-requires the main thread -
    QApplication.exec() raises "Must be called from the main thread"
    otherwise. So this is called directly on the main thread from
    launch_svg_viewer, exactly like eco.widgets.display_qt.DisplayQt.start():
    when blocking=False, IPython's own Qt event-loop integration
    (ip.enable_gui("qt")) is expected to already be pumping the loop, so this
    only needs to show() the window and return; when blocking=True (a
    different, incompatible GUI loop is already active), it runs its own
    exec() and blocks until the window is closed.

    For a non-live panel (refresh=None), the SVG is instead embedded
    directly inline in the loaded HTML (as with the former WebKitGTK
    backend, no server needed at all), and JS-to-Python communication goes
    through a QWebChannel bridge rather than WebKitGTK's
    script-message-handler API.
    """
    from qtpy.QtCore import Qt, QUrl
    from qtpy.QtWidgets import QApplication
    from qtpy.QtWebEngineWidgets import QWebEngineView

    with open(svg_path, "r", encoding="utf-8") as f:
        svg_content = f.read()

    # The window stays freely resizable afterward - the inline <svg>'s own
    # viewBox scaling (CSS below) keeps it from distorting, the same way it
    # would in a real browser tab.
    width, height = _peek_svg_dimensions(svg_content)

    class SvgWindow(QWebEngineView):
        def closeEvent(self, event):
            if self in _qt_windows:
                _qt_windows.remove(self)
            _shutdown_dash_server(self)
            super().closeEvent(event)

    global _qt_app_ref
    app = QApplication.instance()
    created_app = app is None
    if created_app:
        # Must be set before the QApplication is constructed, not after.
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        app = QApplication([])
        _qt_app_ref = app

    view = SvgWindow()
    # Don't let closing this window quit a shared QApplication/event loop
    # (e.g. the one IPython's "qt" gui integration is pumping).
    view.setAttribute(Qt.WA_QuitOnClose, False)
    view.setWindowTitle("Interactive SVG Viewer")
    view.resize(max(int(width), 200), max(int(height), 150))

    if refresh is not None:
        port_holder = {"port": None, "server": None, "ready": threading.Event()}
        threading.Thread(
            target=_run_dash_server,
            args=(svg_path, ip_instance, namespace_prefix, exclude_group_ids),
            kwargs=dict(refresh=refresh, refresh_interval_ms=refresh_interval_ms,
                        port_holder=port_holder),
            daemon=True,
        ).start()
        # One-time, bounded startup wait (binding a local port is normally
        # near-instant) -- this runs on the Qt/main thread, same place any
        # other one-off setup work here already happens.
        if not port_holder["ready"].wait(timeout=5.0) or port_holder["port"] is None:
            print("eco SVG viewer: live panel's background server did not start in time.")
            return
        view._dash_server = port_holder["server"]  # keep alive + for closeEvent's shutdown()
        view.load(QUrl(f"http://127.0.0.1:{port_holder['port']}"))
    else:
        from qtpy.QtCore import QObject, Slot
        from qtpy.QtWebChannel import QWebChannel

        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
        <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
        <style>
            html, body { margin: 0; padding: 0; width: 100%; height: 100%; background: #f0f0f0; overflow: hidden; }
            svg { display: block; width: 100%; height: 100vh; }
        </style>
        </head>
        <body>
        """ + svg_content + """
        <script>
        __EXTRACT_COMMAND_JS__

        var pybridge = null;
        new QWebChannel(qt.webChannelTransport, function(channel) {
            pybridge = channel.objects.pybridge;
        });

        document.addEventListener("click", function(e) {
            let target = e.target;
            while (target && target !== document) {
                let commandStr = extractCommand(target);
                if (commandStr) {
                    if (pybridge) {
                        pybridge.onCommand(commandStr, e.ctrlKey);
                    }
                    e.preventDefault();
                    break;
                }
                target = target.parentNode;
            }
        });
        </script>
        </body>
        </html>
        """
        html_content = html_content.replace(
            "__EXTRACT_COMMAND_JS__", _build_extract_command_js(namespace_prefix, exclude_group_ids)
        )

        class Bridge(QObject):
            @Slot(str, bool)
            def onCommand(self, command, ctrl_pressed):
                if ctrl_pressed:
                    print(f"\n[SVG Paste] -> {command}", end="", flush=True)
                    ip_instance.set_next_input(command, replace=False)
                else:
                    print(f"\nExecuting from SVG: {command}")
                    ip_instance.run_cell(command)

        bridge = Bridge()
        channel = QWebChannel()
        channel.registerObject("pybridge", bridge)
        view.page().setWebChannel(channel)
        view._bridge = bridge  # keep alive alongside the window
        view._channel = channel
        base_url = QUrl.fromLocalFile(os.path.dirname(os.path.abspath(svg_path)) + "/")
        view.setHtml(html_content, base_url)

    if dock_in is not None:
        # dock_in takes over showing the window (reparented into a tile) --
        # calling view.show() first would flash it as a top-level window
        # for one frame before _dock_widget_object hides/reparents it, so
        # leave that to it (same "hide before it can flash" note in
        # _dock_widget_object's own docstring).
        dock_in._dock_widget_object(dock_name or "svg viewer", _SvgViewerHandle(view))
        return

    view.show()
    _qt_windows.append(view)

    if blocking and created_app:
        # No GUI event-loop integration is available to pump this window
        # non-blockingly (see launch_svg_viewer) - block here instead;
        # closing the window returns control. .exec() (not the legacy
        # .exec_() alias, which PyQt6 dropped) works across all qtpy bindings.
        app.exec()


def launch_svg_viewer(svg_path, in_window=None, namespace_prefix=None, exclude_group_ids=None,
                       refresh=None, refresh_interval_ms=2000, dock_in=None, dock_name=None,
                       sidecar_anchor=None):
    """Spawns an isolated background service for the SVG interface.

    refresh: optional no-arg callable returning a *path* to a freshly-built
    SVG (e.g. an Assembly's `lambda: self._widget_svg_panel(live=True)`); if given, this
    is a *live* panel: both the native window and the Jupyter/browser one
    re-call it and reload the displayed SVG every `refresh_interval_ms` for
    as long as the page/window stays open, instead of only ever showing the
    state `svg_path` had at open time. Both viewers share the *same*
    background Dash server and the same client-side JS refresh loop for
    this (see `_run_dash_server`) -- for the native window, this also means
    it opens by loading that server's URL rather than an inline HTML
    snapshot (see `_build_qt_window`), so the refresh runs entirely inside
    the view's own browser-engine process, independent of whatever the eco
    Python process' main thread happens to be busy doing at the time (e.g.
    blocked inside a running macro's poll loop -- see CLAUDE.md).

    in_window defaults to None, which auto-selects based on the calling
    context: a terminal IPython session opens a native window (in_window=
    True behavior), while a Jupyter notebook/lab kernel displays inline in
    the notebook output instead (in_window=False behavior, via an embedded
    IFrame onto the browser-based Dash server rather than a plain printed
    URL) - a native window would open on the server's display, not the
    user's, so it's not a usable option from a notebook. Pass True/False
    explicitly to override the auto-detection.

    With in_window=True, this opens a native, resizable window rendered via
    Qt's QWebEngineView (requires qtpy plus a Qt binding with WebEngine
    support - e.g. PyQt5+PyQtWebEngine or PySide6+qt6-webengine) - no browser tab
    needed, but still real browser-engine (Chromium) rendering fidelity. With
    in_window=False, this launches a browser-based Dash server instead.

    dock_in: an EcoDesktopApp instance (in_window=True only) -- embeds the
    viewer as a tiled QDockWidget in that window instead of opening it as
    a separate top-level one, via the same `_dock_widget_object` mechanism
    the Namespace launcher's "Open" action uses for every other eco Qt
    widget (see `_build_qt_window`'s docstring). `dock_name` sets the
    dock's title (default: "svg viewer").

    sidecar_anchor: (in_window=False / notebook only) e.g. "split-right" --
    opens the viewer in its own JupyterLab Sidecar panel (a real Lumino
    dock widget, see eco.widgets.jupyter_sidecar) instead of displaying
    inline in the current cell's output. Needs the `sidecar` package and a
    JupyterLab session, same caveat as eco.widgets.jupyter_sidecar (not
    classic Notebook, not Voila -- raises a clear RuntimeError there
    instead of silently doing nothing). Returns the Sidecar instance in
    that case -- keep it referenced (same rule as
    eco.widgets.jupyter_sidecar.open_in_sidecar) or the panel closes.

    Clickable commands can come from (checked in this order): an
    onclick="// eco: <command>" attribute (Inkscape's Object Properties >
    Interactivity > onclick field - wrapped as a JS comment so a real
    browser opening this SVG standalone treats it as a harmless no-op
    instead of trying to run Python as JavaScript), a <desc> child element
    (Object Properties > Description), or - only when namespace_prefix is
    set - a <text> label whose first monospace-font tspan names a device
    (e.g. "Attenuator" / "att").

    namespace_prefix changes how *any* matched command above is executed:
    it's prepended as f"{namespace_prefix}.{raw command}" and run against
    that namespace object in the IPython session, e.g. namespace_prefix=
    "bernina" plus a raw value of "slit_att" (from onclick, <desc>, or the
    monospace convention) runs "bernina.slit_att". With no namespace_prefix,
    onclick/<desc> content runs verbatim (e.g. a full print(...) call).

    exclude_group_ids opts specific <g id="..."> subtrees out of the
    monospace-tspan convention entirely (e.g. a zoomed-in inset whose axis
    labels aren't meant to be individually clickable).
    """
    ip = get_ipython()
    if not ip:
        print("Error: Must run inside an interactive IPython terminal context.")
        return

    if in_window is None:
        in_window = not _in_notebook(ip)

    if in_window:
        try:
            from qtpy.QtWidgets import QApplication  # noqa: F401
            from qtpy.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        except Exception as err:
            print(f"Error: in_window=True requires qtpy plus a Qt binding with "
                  f"WebEngine support (conda-forge: qtpy, pyqt, pyqtwebengine - or "
                  f"qtpy, pyside6, qt6-webengine). Details: {err}")
            return

        # Qt's event loop must run on the main thread (unlike the former
        # WebKitGTK backend's Gtk.main(), which tolerated a background
        # thread), so this can't be threading.Thread'd like _run_dash_server
        # below. Instead, reuse IPython's own Qt event-loop integration -
        # same pattern as eco.widgets.display_qt.DisplayQt.start() - so the
        # window is pumped on the main thread between prompts.
        blocking = False
        if dock_in is not None:
            # dock_in._dock_widget_object reparents into a window whose
            # QApplication.exec() is already running (EcoDesktopApp owns
            # the event loop, unlike a bare terminal session) -- no gui
            # integration to arrange here, and no separate top-level
            # window that could need blocking-mode fallback either.
            pass
        else:
            active = getattr(ip, "active_eventloop", None)
            if active is None:
                try:
                    ip.enable_gui("qt")
                except Exception:
                    pass
            elif active not in ("qt", "qt4", "qt5", "qt6"):
                print(
                    f"eco SVG viewer: a different GUI event loop ('{active}') is already "
                    "active in this IPython session, so the window can't be pumped "
                    "non-blockingly alongside it. Showing it in blocking mode instead "
                    "(closing the window returns control) - re-run after '%gui' "
                    "(no arguments) to disable the current loop if you want the "
                    "non-blocking window."
                )
                blocking = True

        _build_qt_window(svg_path, ip, namespace_prefix, exclude_group_ids, blocking=blocking,
                          refresh=refresh, refresh_interval_ms=refresh_interval_ms,
                          dock_in=dock_in, dock_name=dock_name)
        if not blocking and dock_in is None:
            print("Interactive SVG window launched.")
        return

    port_holder = {"port": None, "server": None, "ready": threading.Event()}
    threading.Thread(
        target=_run_dash_server,
        args=(svg_path, ip, namespace_prefix, exclude_group_ids),
        kwargs=dict(refresh=refresh, refresh_interval_ms=refresh_interval_ms, port_holder=port_holder),
        daemon=True,
    ).start()
    if not port_holder["ready"].wait(timeout=5.0) or port_holder["port"] is None:
        print("eco SVG viewer: background server did not start in time.")
        return
    url = f"http://127.0.0.1:{port_holder['port']}"

    if _in_notebook(ip):
        from IPython.display import IFrame, display

        with open(svg_path, "r", encoding="utf-8") as f:
            _, height = _peek_svg_dimensions(f.read())
        iframe = IFrame(src=url, width="100%", height=int(height) + 20)

        if sidecar_anchor is not None:
            try:
                from sidecar import Sidecar
            except ImportError as exc:
                raise RuntimeError(
                    "sidecar_anchor needs the 'sidecar' package (pip install sidecar) "
                    "and a JupyterLab session -- it does not work in classic Notebook "
                    "or Voila (see eco.widgets.jupyter_sidecar)."
                ) from exc
            sc = Sidecar(title=dock_name or "svg viewer", anchor=sidecar_anchor)
            with sc:
                display(iframe)
            return sc

        display(iframe)
    else:
        print("Interactive SVG application initialized successfully!")
        print(f"--> Open your web browser and navigate to: {url}")

