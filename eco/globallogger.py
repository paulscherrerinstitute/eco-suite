"""Opt-in command logging for using eco as a plain library
(``from eco import bernina``, no startup script involved at all) -- the
same per-session JSONL activity log every interactive entry point
(``eco``/``eco-dev``, the desktop app's console, ``eco.start_console()``)
already gets via ``eco.widgets.kernel_registry.install_shell_logger``,
made available as one explicit call instead of only happening as a side
effect of going through a startup script::

    from eco import bernina
    from eco import globallogger
    globallogger.start()

Deliberately opt-in, not automatic on ``import eco``: a plain library
import is often a short-lived script, or a notebook cell, that shouldn't
get a ``~/.eco/kernel_logs/*.jsonl`` file appearing on disk just from
``import eco.bernina`` -- same reasoning ``eco.elements.recent`` already
uses for why ``RecentComponents`` tracking has its own ``enabled`` kill
switch rather than being unconditional.

No-op (returns ``None``) outside a real IPython shell (``get_ipython()``
is ``None``) -- a plain non-interactive script has nothing to hook.
Already-viewable through the same ``eco.logs``/desktop-app "Log" viewer
every other kernel log is, since it browses every JSONL file under
``~/.eco/kernel_logs/`` regardless of which entry point wrote it.
"""
from eco.widgets import kernel_registry


def start(label=None, log_dir=None):
    """Install the shell logger on the *current* IPython shell.

    `label` defaults to this session's identity (see
    `eco.elements.access.current_identity`) rather than a generic
    "library" tag -- the point of "global user based" logging: two people
    on a shared account still end up with distinguishable log files, the
    same way `eco.elements.recent`'s per-user Recent list already
    distinguishes them. Falls back to plain "library" if identity can't be
    resolved (e.g. `eco.elements.access` itself unavailable).

    Idempotent: calling this again on the same shell (e.g. re-running a
    notebook cell) returns the already-installed session rather than
    double-logging every cell -- see `install_shell_logger`'s own
    docstring.
    """
    if label is None:
        try:
            from eco.elements.access import current_identity

            label = current_identity().name
        except Exception:
            label = None
    return kernel_registry.install_shell_logger(kind="library", label=label, log_dir=log_dir)
