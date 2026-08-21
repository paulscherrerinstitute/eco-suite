"""Process-wide registry of every Jupyter kernel eco's Qt widgets spawn
(the desktop workbench's embedded console, any eco.start_console() window,
...), plus an append-only log of what was typed into each and what came
back.

Why this exists: once there's more than one console/kernel open (the
desktop's own, plus however many independent eco.start_console() windows),
"what did I actually run, and where, and what happened" stops being
something you can just scroll back to find -- especially across separate
processes (see eco.widgets.console_kernel: an independent console's kernel
is a real subprocess, with its own scrollback nobody else can see). Every
KernelSession gets its own timestamped JSONL log file; sessions_summary()
lists what exists so a later session -- or a small "Kernels" browser widget,
not built yet -- can find and read them back.

This module intentionally has zero Qt/qtconsole/ipykernel imports, so it's
cheap to import and easy to unit test on its own; eco.widgets.console_kernel
is what actually wires a KernelSession up to a real console/kernel.
"""
import json
import threading
import time
import uuid
from pathlib import Path

DEFAULT_LOG_DIR = Path.home() / ".eco" / "kernel_logs"

_registry = []
_registry_lock = threading.Lock()


class KernelSession:
    """One kernel's identity plus its append-only JSONL activity log.
    `kind` is a short tag ("desktop", "console", "jupyterlab") identifying
    which eco entry point created it; `label` is a human-readable name
    (defaults to `kind`) callers can set to tell multiple same-kind
    sessions apart (e.g. the scope name)."""

    def __init__(self, kind, label=None, connection_file=None, pid=None, log_dir=None):
        self.id = uuid.uuid4().hex[:8]
        self.kind = kind
        self.label = label or kind
        self.connection_file = str(connection_file) if connection_file else None
        self.pid = pid
        self.started = time.time()
        log_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(self.started))
        self.log_path = log_dir / f"{stamp}_{self.kind}_{self.id}.jsonl"
        self._lock = threading.Lock()
        self._record(
            "session_start",
            {"kind": self.kind, "label": self.label, "pid": self.pid, "connection_file": self.connection_file},
        )

    # -- logging -----------------------------------------------------------

    def log_input(self, code):
        """A command entered into this session's console (typed, or run
        programmatically via `.execute()`), whichever widget it came from."""
        self._record("input", {"code": code})

    def log_output(self, event, **fields):
        """A message received back from this session's kernel -- `event` is
        one of "result" | "stream" | "error" (matching the qtconsole/
        ipykernel message it came from; see eco.widgets.console_kernel)."""
        self._record(event, fields)

    def log_event(self, event, **fields):
        """Anything else worth recording against this session (e.g.
        "session_end")."""
        self._record(event, fields)

    def _record(self, event, payload):
        entry = {"t": time.time(), "event": event}
        entry.update(payload)
        line = json.dumps(entry, default=str)
        with self._lock:
            with open(self.log_path, "a") as f:
                f.write(line + "\n")

    def tail(self, n=20):
        """The last `n` logged entries (parsed dicts, oldest first) --
        for a quick "what just happened here" check without opening the
        log file by hand."""
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text().splitlines()
        out = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out

    def __repr__(self):
        return f"<KernelSession {self.kind}:{self.label} id={self.id} pid={self.pid}>"


def register(session):
    """Add `session` to the process-wide registry. Called once by whatever
    built it (eco.widgets.console_kernel); not usually called directly."""
    with _registry_lock:
        _registry.append(session)
    return session


def unregister(session):
    """Drop `session` from the registry (its log file is left in place --
    only the in-memory listing shrinks) -- called when a console/kernel is
    closed, so sessions_summary() reflects what's actually still running."""
    with _registry_lock:
        if session in _registry:
            _registry.remove(session)


def all_sessions():
    with _registry_lock:
        return list(_registry)


def sessions_summary():
    """[{id, kind, label, pid, started, log_path}, ...] for every
    currently-registered session, oldest first -- the data a "Kernels"
    listing widget or a `print()` from the terminal would want."""
    return [
        {
            "id": s.id,
            "kind": s.kind,
            "label": s.label,
            "pid": s.pid,
            "started": s.started,
            "log_path": str(s.log_path),
        }
        for s in all_sessions()
    ]


def find_all_logs(log_dir=None):
    """Every kernel-session log file on disk (not just ones registered in
    *this* process) -- for reviewing past sessions after the fact, e.g.
    from a following day's terminal."""
    log_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
    if not log_dir.exists():
        return []
    return sorted(log_dir.glob("*.jsonl"))
