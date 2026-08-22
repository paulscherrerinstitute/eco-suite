"""eco.logs -- browse kernel console history, and (cheaply) scilog
logbook posts, through one shared timeline viewer: eco.widgets.
log_timeline_qt.LogTimelineQt if there's a usable Qt session, eco.
widgets.log_timeline_html's inline renderer if running in Jupyter/
JupyterLab, or a written-to-disk HTML file otherwise.

This module *is* the `eco.logs` namespace -- deliberately a plain module
with module-level functions, not a class instantiated into a singleton:
naming a singleton the same as the module that defines it (`eco.logs`
the object vs. `eco.logs` the module) is a real Python footgun --
`eco/__init__.py`'s `from eco.logs import logs` would silently shadow the
module itself on the `eco` package's namespace, so `import eco.logs`
would no longer reach the actual module (only `sys.modules['eco.logs']`
would). A plain module sidesteps that entirely: `eco/__init__.py` does a
bare `import eco.logs`, and `eco.logs.widget()` etc. just work.

eco.widgets.log_timeline_qt, eco.widgets.log_timeline_html, and eco.
utilities.elog_scilog are all imported lazily inside functions, not at
module level -- `import eco` imports this module, and should stay as
cheap as it is today (see eco.widgets.kernel_registry, which has the
same "zero heavy imports at module level" rule and why).
"""
import datetime
import json
from pathlib import Path

from eco.widgets import kernel_registry
from eco.widgets.log_timeline_common import TimelineEntry

_scilog_client_cache = None


# -- kernel logs --------------------------------------------------------------


def sessions():
    """Every kernel-log JSONL file on disk (not just this process's own
    sessions) -- see kernel_registry.find_all_logs()."""
    return kernel_registry.find_all_logs()


def _session_label_for_file(path, lines):
    """Human-readable, still-per-file-unique stream label for a kernel log
    -- "kind:label · HHMMSS" (e.g. "desktop:bernina · 084826") derived
    from the file's own session_start record, falling back to the raw
    filename stem if that's missing/unparseable. Keeping the timestamp
    suffix (not just "kind:label") is what keeps two same-day desktop
    sessions separable rather than colliding into one filter checkbox --
    but the timestamp alone is only 1-second resolution, so two sessions
    of the same kind+label started within the same second (rare for a
    human typing, real for scripted/test session creation) would still
    collide; the trailing 4 hex chars of the filename's id fragment are
    appended in that case to keep them distinct too."""
    for line in lines[:1]:
        try:
            rec = json.loads(line)
        except Exception:
            break
        if rec.get("event") in ("session_start", "session_start_subprocess"):
            kind, label = rec.get("kind"), rec.get("label")
            if kind:
                # KernelSession.log_path is f"{stamp}_{kind}_{id}.jsonl",
                # stamp = strftime("%Y%m%d_%H%M%S") -- always 15 chars.
                stem = path.stem
                stamp = stem[:15]
                hhmmss = stamp[9:] if len(stamp) == 15 else stamp
                tail = stem[16:]  # "{kind}_{id}", or "" if stamp didn't parse
                id_suffix = tail.rsplit("_", 1)[-1][-4:] if "_" in tail else ""
                tag = hhmmss + (f"-{id_suffix}" if id_suffix else "")
                return f"{kind}:{label}" + (f" · {tag}" if tag else "")
    return path.stem


def _kernel_entries(session=None, log_dir=None):
    paths = [Path(session)] if session is not None else kernel_registry.find_all_logs(log_dir)
    entries = []
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        lines = path.read_text().splitlines()
        session_label = _session_label_for_file(path, lines)
        for line in lines:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            entry = _kernel_record_to_entry(rec, session_label)
            if entry is not None:
                entries.append(entry)
    entries.sort(key=lambda e: e.t)
    return entries


def tail(n=20, session=None):
    """The last `n` kernel-log entries, merged across sessions unless
    `session` narrows it to one .jsonl path."""
    return _kernel_entries(session=session)[-n:]


def widget(session=None, prefer="auto", title=None):
    """Open the kernel-log timeline. `session` is a specific .jsonl path
    (see logs.sessions()); by default every session on disk is merged
    into one timeline. `prefer`: "auto" (Qt if usable, else inline in
    Jupyter, else a written-to-disk HTML file), or force one of "qt" /
    "html" (alias "jupyter") / "webapp"."""
    entries = _kernel_entries(session=session)
    subtitle = f"{len(entries)} entries"
    subtitle += f" · {Path(session).name}" if session else " · all sessions"
    return _open_timeline(
        entries, title=title or "Kernel log timeline", subtitle=subtitle, prefer=prefer
    )


# -- scilog ---------------------------------------------------------------------


def scilog(
    pgroup=None,
    url="https://scilog.psi.ch/api/v1",
    prefer="auto",
    snippet_types=("paragraph", "image"),
    limit=2000,
    **kwargs,
):
    """Open a timeline built from a *cheap* scilog query: get_snippets is
    called with `fields=` restricted to id/createdAt/tags/snippetType/
    createdBy, so post bodies are never fetched just to draw the timeline
    -- this stays fast regardless of how long the logbook's paragraphs
    are (verified live: 961 snippets in 0.18s on a real, active pgroup).
    Clicking a point identifies the post (timestamp, tags, id); logs.
    open_scilog(id) (or double-click in the Qt viewer) does the one
    expensive full-body fetch, for that one post, on demand.

    `snippet_types` defaults to only "paragraph"/"image" -- the installed
    `scilog` client's snippet_factory() only knows how to deserialize
    "location"/"logbook"/"paragraph"/"image"/"basesnippet"; a real,
    common snippetType it *can't* parse ("task", used by to-do-list
    snippets) raises ValueError and aborts the whole batch. Confirmed
    live against 15 real pgroups: 5 of them have task snippets and would
    crash an unfiltered query. "file" was never observed in that sample
    (so its safety is unconfirmed either way) -- add it to snippet_types
    yourself once you've verified it deserializes cleanly against real
    data, rather than widening the default blind."""
    global _scilog_client_cache
    log = _scilog_client(pgroup=pgroup, url=url, **kwargs)
    _scilog_client_cache = log
    where = {"snippetType": {"inq": list(snippet_types)}}
    fields = {"id": True, "createdAt": True, "tags": True, "snippetType": True, "createdBy": True}
    snippets = log.get_snippets(where=where, fields=fields, order=["createdAt ASC"], limit=limit)
    entries = [e for e in (_snippet_to_entry(s) for s in snippets) if e is not None]
    entries.sort(key=lambda e: e.t)
    return _open_timeline(
        entries,
        title="scilog timeline",
        subtitle=f"{len(entries)} posts (metadata only)",
        prefer=prefer,
        on_open=open_scilog,
    )


def _scilog_client(pgroup=None, url="https://scilog.psi.ch/api/v1", **kwargs):
    from eco.utilities.elog_scilog import getDefaultElogInstance

    log, _user = getDefaultElogInstance(url, pgroup=pgroup, **kwargs)
    return log


def open_scilog(snippet_id):
    """Fetch and return the full HTML body of one scilog post by id --
    the deliberately expensive call logs.scilog() avoids making for
    every post up front. Returns an HTML string, suitable for IPython.
    display.HTML(...) or as a LogTimelineQt on_open result (which is
    exactly how logs.scilog()'s Qt viewer uses it)."""
    global _scilog_client_cache
    log = _scilog_client_cache or _scilog_client()
    _scilog_client_cache = log
    result = log.get_snippets(where={"id": snippet_id}, limit=1)
    if not result:
        return f"<i>snippet {snippet_id} not found</i>"
    snip = result[0]
    body = getattr(snip, "textcontent", None) or "<i>(no text content)</i>"
    header = (
        "<div style='color:#888;font-family:monospace;font-size:11px;'>"
        f"{getattr(snip, 'createdAt', '')} · {getattr(snip, 'createdBy', '')}</div>"
    )
    return header + body


def _kernel_record_to_entry(rec, session_label):
    event = rec.get("event")
    t = rec.get("t")
    if t is None or event is None:
        return None
    if event in ("session_start", "session_end", "session_start_subprocess"):
        kind = "session"
        text = f"{event} — {rec.get('kind', '')}:{rec.get('label', session_label)} (pid {rec.get('pid')})"
    elif event == "input":
        kind, text = "input", rec.get("code", "")
    elif event == "widget_control":
        # Same code-is-the-text contract as "input" (see
        # KernelSession.log_widget_control's docstring) -- both kinds are
        # directly usable as script lines in log_timeline_qt's "copy
        # selected as script".
        kind, text = "widget_control", rec.get("code", "")
    elif event == "error":
        kind, text = "error", f"{rec.get('ename', '')}: {rec.get('evalue', '')}"
    elif event == "result":
        kind, text = "result", rec.get("text") or ""
    elif event == "stream":
        kind, text = "stream", f"[{rec.get('name', 'stdout')}] {rec.get('text', '')}"
    else:
        kind = event
        text = json.dumps({k: v for k, v in rec.items() if k not in ("t", "event")})
    return TimelineEntry(
        t=float(t), kind=kind, text=text.rstrip("\n"), is_error=(kind == "error"), session=session_label
    )


def _snippet_to_entry(snip):
    created = getattr(snip, "createdAt", None)
    if not created:
        return None
    try:
        t = datetime.datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None
    tags = tuple(getattr(snip, "tags", None) or ())
    kind = getattr(snip, "snippetType", "snippet")
    text = f"{kind} by {getattr(snip, 'createdBy', '?')}"
    return TimelineEntry(t=t, kind=kind, text=text, tags=tags, ref=getattr(snip, "id", None))


def _in_jupyter():
    try:
        from IPython import get_ipython

        ip = get_ipython()
    except Exception:
        return False
    return ip is not None and "ZMQ" in type(ip).__name__


def _open_timeline(entries, title, subtitle, prefer="auto", on_open=None):
    if prefer not in ("auto", "qt", "html", "jupyter", "webapp", "sidecar"):
        raise ValueError(
            f"prefer must be one of auto/qt/html/jupyter/webapp/sidecar, got {prefer!r}"
        )

    if prefer == "sidecar":
        from eco.widgets.jupyter_sidecar import open_html_in_sidecar
        from eco.widgets.log_timeline_html import render_html

        return open_html_in_sidecar(render_html(entries, title=title, subtitle=subtitle), title=title)

    if prefer in ("html", "jupyter") or (prefer == "auto" and _in_jupyter()):
        from eco.widgets.log_timeline_html import show

        show(entries, title=title, subtitle=subtitle)
        return None

    if prefer in ("auto", "qt"):
        try:
            from eco.widgets.log_timeline_qt import make_log_timeline_qt_window

            return make_log_timeline_qt_window(entries, title=title, on_open=on_open)
        except Exception as exc:
            if prefer == "qt":
                raise
            print(f"eco.logs: Qt window unavailable ({exc}); writing an HTML file instead")

    from eco.widgets.log_timeline_html import write_html_file

    path = write_html_file(entries, title=title, subtitle=subtitle)
    print(f"eco.logs: wrote {path}")
    return path
