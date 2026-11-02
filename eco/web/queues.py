"""The session's task queues (eco.utilities.task_queue) for the web client:
what runs, what waits, how far the running scan is, what a task printed --
and the controls of the Qt queue panel (ScanQueuePanel) that act on what is
already queued. Nothing here adds, repeats or edits a task.
"""
import re

from eco.web.viewmodels import _clip

HISTORY = 10  # finished tasks listed per queue
OUTPUT_CHUNK = 20000  # characters of output per reply at most (the newest)

# action -> (needs a task id, what it calls)
ACTIONS = {
    "pause": False,  # don't start the next task
    "resume": False,
    "clear": False,  # drop every waiting task
    "stop": False,  # stop the running task and drop the waiting ones
    "pause_running": False,  # pause the running scan at its next step
    "resume_running": False,
    "stop_running": False,  # its scans stop at their next step, else KeyboardInterrupt
    "remove": True,  # drop one waiting task
    "move_up": True,
    "move_top": True,
}


def _scan(task):
    scan = task.scan
    if scan is None:
        return None
    try:
        done = len(getattr(scan, "_values_done", []))
        total = done + len(getattr(scan, "_values_todo", []))
        paused = bool(scan.is_paused())
    except Exception:
        return None
    return {"done": done, "total": total, "paused": paused,
            "stopping": bool(getattr(scan, "_stop_requested", False))}


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _short(value):
    """An argument as the task line shows it: an eco item by its name (its
    repr is a whole status table), plain values by repr, anything else by
    its type."""
    alias = getattr(value, "alias", None)
    if alias is not None:
        try:
            return alias.get_full_name()
        except Exception:
            pass
    if value is None or isinstance(value, (bool, int, float, complex, str, bytes)):
        return repr(value)
    if isinstance(value, (list, tuple)) and len(value) <= 8:
        inner = ", ".join(_short(v) for v in value)
        return f"[{inner}]" if isinstance(value, list) else f"({inner}{',' if len(value) == 1 else ''})"
    if isinstance(value, dict) and len(value) <= 8:
        return "{" + ", ".join(f"{k!r}: {_short(v)}" for k, v in value.items()) + "}"
    return f"<{type(value).__name__}>"


def task_source(task):
    """Task.source, but with short arguments (see _short)."""
    if isinstance(task.command, str):
        return _ANSI.sub("", task.command.strip())
    name = getattr(task.command, "__qualname__", None) or type(task.command).__name__
    parts = [_short(a) for a in task.args] + [f"{k}={_short(v)}" for k, v in task.kwargs.items()]
    return f"{name}({', '.join(parts)})"


def task_json(task):
    out = {
        "id": task.id,
        "source": _clip(task_source(task)),
        "description": _clip(str(task.description or "")),
        "state": task.state,
        "started": task.started,
        "finished": task.finished,
    }
    if task.exception is not None:
        out["error"] = _clip(f"{type(task.exception).__name__}: {task.exception}")
    scan = _scan(task) if task.state == "running" else None
    if scan is not None:
        out["scan"] = scan
    return out


def queue_json(queue):
    current = queue.current
    return {
        "name": queue.name,
        "paused": queue.paused,
        "running": task_json(current) if current is not None else None,
        "pending": [task_json(t) for t in queue.pending],
        "history": [task_json(t) for t in reversed(queue.history[-HISTORY:])],
    }


def find_task(queue, task_id):
    return queue._find(int(task_id), everywhere=True)


def output_since(task, since=0):
    """(text, offset): what `task` printed after character `since`, at most
    OUTPUT_CHUNK characters (the newest). The oldest output of a very chatty
    task is dropped by the queue itself; an offset beyond the end starts over."""
    text = task.output
    since = int(since or 0)
    if since > len(text):
        since = 0
    chunk = text[since:]
    clipped = len(chunk) > OUTPUT_CHUNK
    if clipped:
        chunk = chunk[-OUTPUT_CHUNK:]
    return chunk, len(text), clipped


def act(queue, action, task_id=None):
    """Run one allowed action on `queue`; returns a short description."""
    if action not in ACTIONS:
        raise ValueError(f"unknown queue action {action!r}")
    if ACTIONS[action]:
        if task_id is None:
            raise ValueError(f"{action} needs a task")
        task = queue._find(int(task_id))
        if task is None:
            raise ValueError(f"task #{task_id} is not waiting in queue {queue.name!r} (any more)")
        if action == "remove":
            queue.remove(task)
        else:
            pending = queue.pending
            index = 0 if action == "move_top" else max(0, pending.index(task) - 1)
            queue.move(task, index)
        return f"task #{task.id}: {action}"
    if action == "pause":
        queue.pause()
    elif action == "resume":
        queue.resume()
    elif action == "clear":
        return f"dropped {len(queue.clear())} waiting tasks"
    elif action == "stop":
        dropped = queue.stop()
        return f"stopping the running task, dropped {len(dropped)} waiting"
    elif action in ("pause_running", "resume_running", "stop_running"):
        method = {"pause_running": queue.pause_running_task, "resume_running": queue.resume_running_task,
                  "stop_running": queue.stop_running_task}[action]
        if method() is None:
            raise ValueError("nothing is running" if action == "stop_running" else "the running task has no scan to pause")
    return action
