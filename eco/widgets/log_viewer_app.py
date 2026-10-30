"""Run the kernel-log viewer (eco.widgets.log_timeline_qt) as its own OS
process -- what eco desktop's Tools -> Log Viewer does, so that nothing
the viewer does (a bad log file, a slow parse, a Qt/X11 failure, running
out of memory on a huge history) can freeze or kill the desktop it was
opened from. The viewer only *reads* ~/.eco/kernel_logs/*.jsonl; it needs
no live namespace, which is what makes a separate process free here.

Startup cost: the child does NOT run `import eco` (eco/__init__.py pulls in
the whole control stack, ~10-15 s in a fresh process -- see
eco.widgets.subprocess_embed.EMBED_TIMEOUT_S). BOOTSTRAP registers a bare
`eco` package object pointing at this checkout and then imports only
eco.logs and the viewer modules, which by design need nothing else from eco
(eco.logs, kernel_registry and log_timeline_common keep "zero heavy imports
at module level"). If that ever stops being true, the child fails at
import and says so in its log file; the desktop is unaffected.

    python -m eco.widgets.log_viewer_app --days 14     # also works by hand
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

#: run via `python -c BOOTSTRAP <eco_root> [args...]`
BOOTSTRAP = (
    "import sys, types\n"
    "root = sys.argv[1]\n"
    "pkg = types.ModuleType('eco')\n"
    "pkg.__path__ = [root + '/eco']\n"
    "sys.modules['eco'] = pkg\n"
    "sys.path.insert(0, root)\n"
    "from eco.widgets.log_viewer_app import main\n"
    "sys.exit(main(sys.argv[2:]))\n"
)

CHILD_LOG = Path.home() / ".eco" / "log_viewer_last.log"


def _eco_root():
    return str(Path(__file__).resolve().parents[2])


def build_command(days=None, session=None, title=None, size=None):
    """argv for the child process (see BOOTSTRAP)."""
    cmd = [sys.executable, "-c", BOOTSTRAP, _eco_root()]
    if days is not None:
        cmd += ["--days", str(days)]
    if session is not None:
        cmd += ["--session", str(session)]
    if title:
        cmd += ["--title", title]
    if size:
        cmd += ["--size", f"{int(size[0])}x{int(size[1])}"]
    return cmd


class LogViewerProcess:
    """Handle for a viewer running in a child process; .stop() ends it.
    Same .stop() convention as LogTimelineQtWindow, so eco desktop treats
    both alike."""

    def __init__(self, popen, log_path):
        self.process = popen
        self.log_path = log_path

    def is_running(self):
        return self.process.poll() is None

    def stop(self):
        if self.is_running():
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def __repr__(self):
        state = "running" if self.is_running() else f"exited {self.process.returncode}"
        return f"<LogViewerProcess pid={self.process.pid} {state} log={self.log_path}>"


def launch_subprocess(days=None, session=None, title=None, size=None, log_path=None):
    """Start the viewer in a child process and return at once (a
    LogViewerProcess). The child's stdout/stderr go to `log_path`
    (default ~/.eco/log_viewer_last.log) -- look there if no window shows up."""
    log_path = Path(log_path) if log_path else CHILD_LOG
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop("PYTHONSTARTUP", None)
    with open(log_path, "w") as log_file:
        popen = subprocess.Popen(
            build_command(days=days, session=session, title=title, size=size),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            close_fds=True,
        )
    return LogViewerProcess(popen, log_path)


def main(argv=None):
    p = argparse.ArgumentParser(description="eco kernel-log timeline viewer")
    p.add_argument("--days", type=float, default=None, help="only the last N days (default: everything)")
    p.add_argument("--session", default=None, help="one .jsonl file instead of all sessions")
    p.add_argument("--title", default="Kernel log timeline")
    p.add_argument("--size", default=None, help="initial WxH in pixels")
    args = p.parse_args(argv)

    from qtpy import QtWidgets

    import eco.logs
    from eco.widgets import log_timeline_qt

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    viewer = log_timeline_qt.make_log_timeline_qt_window(
        None, title=args.title, loader=eco.logs.kernel_loader(session=args.session, days=args.days), auto_start=False
    )
    viewer._build_window()
    if args.size:
        w, h = (int(v) for v in args.size.lower().split("x"))
        viewer.window.resize(w, h)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
