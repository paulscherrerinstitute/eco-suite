"""
Terminal-callable convenience functions for trying eco's other front-ends
(see STARTUP_MODES.md and eco_cli.py's ``--ui`` flag) *from within* an
already-running "shell" (terminal) session, for quick testing without
leaving it: ``eco.start_desktop()``, ``eco.start_console()``,
``eco.start_jupyterlab()``, ``eco.start_webapp()`` (re-exported from
``eco/__init__.py``, which just
imports these lazily -- this module holds the actual (lengthier, more
GUI/process-adjacent) implementation, kept out of eco/__init__.py on
purpose since that file changes for functional reasons far more than
these do).

Unlike eco_cli.py's ``--ui`` flags (which each ``exec`` -- replace the
current process entirely, since they assume they *are* the whole
session), everything here is non-blocking / spawns a subprocess, so your
existing terminal session keeps working right alongside whatever this
opens.
"""
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_voila_process = None  # tracks a subprocess started by start_webapp, for attach=True


def _package_file(filename):
    """Path of a file shipped inside the eco package, e.g. voila_app.ipynb."""
    import eco

    return os.path.join(os.path.dirname(os.path.abspath(eco.__file__)), filename)


def _calling_namespace():
    """The `namespace` variable from the calling IPython session's own
    user namespace, if there is one (the normal case once eco has
    started via startup_inline.py) -- else None."""
    try:
        from IPython import get_ipython

        ip = get_ipython()
    except Exception:
        return None
    if ip is None:
        return None
    return getattr(ip, "user_ns", {}).get("namespace")


def start_desktop(theme=None, link_terminal=True, scope=None, lazy=True, with_console=True):
    """Open the Qt desktop workbench (eco.widgets.desktop_app) from a
    running terminal session, for easy testing without leaving it.

    Non-blocking when called from an interactive IPython session (the
    normal case) -- the window opens and your terminal keeps working; see
    EcoDesktopApp.start for the one case (a non-Qt GUI event loop already
    active) where it falls back to blocking.

    The namespace used is, in order: the calling session's own
    `namespace` variable if there is one (normal case, once eco has
    started), else a freshly built one for `scope` (default: "bernina").

    link_terminal=True (default): the desktop's embedded console shares
    the calling session's own namespace dict, so a variable set in either
    console is immediately visible in the other. False: the desktop app
    gets its own independent one instead.

    with_console=True (default): the desktop window has an embedded
    console/kernel (in-process when safe, else an independent subprocess
    kernel -- see eco.widgets.console_kernel). False: no console at all --
    the calling terminal stays the one and only "master" session; the
    Namespace panel still works fully (opening a widget never needed the
    console -- see EcoDesktopApp._open_widget), you just can't type Python
    directly into the desktop window itself.
    """
    from eco.widgets.desktop_app import EcoDesktopApp, build_namespace

    scope = scope or "bernina"
    namespace = _calling_namespace()
    if namespace is None:
        namespace = build_namespace(scope=scope, lazy=lazy)

    return EcoDesktopApp(
        namespace,
        theme=theme,
        link_terminal=link_terminal,
        auto_start=True,
        scope=scope,
        lazy=lazy,
        with_console=with_console,
    )


def start_console(kind="qt", scope=None, lazy=True, label=None):
    """Open a brand-new, independent console -- its own Jupyter kernel, not
    sharing any state with the calling session or any other eco window --
    for when you want a second (or third...) working session alongside the
    one you're already in, rather than eco.start_desktop()'s "share this
    session's own namespace when possible" behaviour.

    kind="qt" (default): a lightweight standalone Qt console window (see
    eco.widgets.console_window_qt) -- just a console, no namespace launcher
    dock.
    kind="jupyterlab": a fresh notebook (its own kernel/session, distinct
    from any other open tab) in JupyterLab -- attaches to an already-running
    server if there is one, else starts one.

    Every console opened this way rebuilds `scope`'s namespace itself (as
    its first executed cell, for "qt"; via ECO_SCOPE for "jupyterlab") --
    see eco.widgets.console_kernel for why an independent console can't
    just inherit the caller's live namespace object across a fresh kernel.

    Returns the ConsoleWindowQt handle for kind="qt" (`.stop()` to close
    it), or the started subprocess.Popen / None (attached instead of
    starting one) for kind="jupyterlab".
    """
    scope = scope or "bernina"
    if kind == "qt":
        from eco.widgets.console_window_qt import make_console_window_qt

        return make_console_window_qt(scope=scope, lazy=lazy, label=label)
    if kind == "jupyterlab":
        return _start_jupyterlab_console(scope=scope, lazy=lazy, label=label)
    raise ValueError(f"kind must be 'qt' or 'jupyterlab', got {kind!r}")


def _fresh_console_notebook_path(label):
    """A never-before-used notebook path under ~/.eco/consoles/ -- copying
    eco/voila_app.ipynb here (rather than reopening the packaged path
    directly, as start_jupyterlab does) is what makes JupyterLab start a
    genuinely new kernel/session for it instead of reconnecting to
    whatever kernel an already-open tab on the same path already has."""
    import time

    consoles_dir = Path.home() / ".eco" / "consoles"
    consoles_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(c if c.isalnum() else "_" for c in label)
    return consoles_dir / f"console_{safe_label}_{stamp}.ipynb"


def _start_jupyterlab_console(scope, lazy, label):
    import shutil

    label = label or scope
    notebook_path = _fresh_console_notebook_path(label)
    shutil.copyfile(_package_file("voila_app.ipynb"), notebook_path)

    existing = _find_running_jupyterlab()
    if existing:
        import webbrowser

        base, _, query = existing.partition("?")
        url = base.rstrip("/") + "/lab/tree/" + os.path.basename(str(notebook_path))
        if query:
            url += "?" + query
        webbrowser.open(url)
        print(
            f"eco: opened a fresh notebook ({notebook_path}) with its own kernel "
            f"in the existing JupyterLab server at {existing}. Its ECO_SCOPE/ECO_LAZY "
            "come from that server's own environment, not from this call's scope= "
            "argument -- the server was already running before this call, so its "
            "environment can't be changed retroactively for one tab."
        )
        return None

    env = dict(os.environ)
    env["ECO_SCOPE"] = scope
    env["ECO_LAZY"] = "1" if lazy else "0"
    try:
        proc = subprocess.Popen(["jupyter", "lab", str(notebook_path)], env=env)
    except FileNotFoundError:
        raise RuntimeError(
            "'jupyter' not found -- install JupyterLab, e.g. `pip install eco[lab]` "
            "or `conda install jupyterlab`."
        )
    print(f"eco: started a new JupyterLab server for {notebook_path} (pid {proc.pid})")
    return proc


def _find_running_jupyterlab():
    """URL of a running JupyterLab server (per `jupyter lab list`), or
    None if there isn't one / the command isn't available."""
    try:
        out = subprocess.check_output(
            ["jupyter", "lab", "list"], stderr=subprocess.DEVNULL, text=True, timeout=10
        )
    except Exception:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("http"):
            return line.split("::")[0].strip()
    return None


def start_jupyterlab(attach=True, notebook=None):
    """Open eco's packaged notebook (eco/voila_app.ipynb) in JupyterLab.

    attach=True (default): if a JupyterLab server is already running
    (per `jupyter lab list`), open the notebook there (a browser tab)
    instead of starting a second server. False, or none found: start a
    fresh `jupyter lab <notebook>` server as a background subprocess --
    unlike eco_cli.py's `--ui lab` (which execs and replaces the whole
    process), this returns immediately and your terminal keeps working.

    Returns the started subprocess.Popen, or None if it attached to an
    existing server instead of starting one.
    """
    notebook_path = notebook or _package_file("voila_app.ipynb")

    if attach:
        existing = _find_running_jupyterlab()
        if existing:
            import webbrowser

            base, _, query = existing.partition("?")
            url = base.rstrip("/") + "/lab/tree/" + os.path.basename(notebook_path)
            if query:
                url += "?" + query
            webbrowser.open(url)
            print(f"eco: opened {notebook_path} in the existing JupyterLab server at {existing}")
            return None

    try:
        proc = subprocess.Popen(["jupyter", "lab", notebook_path])
    except FileNotFoundError:
        raise RuntimeError(
            "'jupyter' not found -- install JupyterLab, e.g. `pip install eco[lab]` "
            "or `conda install jupyterlab`."
        )
    print(f"eco: started a new JupyterLab server for {notebook_path} (pid {proc.pid})")
    return proc


def start_webapp(attach=True, notebook=None, port=None):
    """Serve eco's packaged notebook (eco/voila_app.ipynb) as a Voila
    dashboard.

    attach=True (default): if this session already started a still-running
    Voila process, just note that instead of starting a duplicate (Voila,
    unlike JupyterLab, has no `list running servers` command to discover
    one this session didn't itself start -- so "attach" here only covers
    "don't double-start one I already have running"). False: always start
    a fresh instance.

    Returns the subprocess.Popen (freshly started, or the pre-existing
    one being reused).
    """
    global _voila_process
    notebook_path = notebook or _package_file("voila_app.ipynb")

    if attach and _voila_process is not None and _voila_process.poll() is None:
        print(
            f"eco: a voila server is already running (pid {_voila_process.pid}) -- "
            "if you closed its browser tab, pass attach=False for a fresh instance "
            "(Voila has no way to look up a lost tab's URL after the fact)."
        )
        return _voila_process

    cmd = ["voila", notebook_path]
    if port:
        cmd += ["--port", str(port)]
    try:
        proc = subprocess.Popen(cmd)
    except FileNotFoundError:
        raise RuntimeError(
            "'voila' not found -- install it, e.g. `pip install eco[voila]` or `conda install voila`."
        )
    _voila_process = proc
    print(f"eco: started voila for {notebook_path} (pid {proc.pid})")
    return proc
