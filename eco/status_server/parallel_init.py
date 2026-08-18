"""Dependency-aware, CA-context-safe parallel namespace initialization -
a prototype, opt-in alternative to Namespace.init_all(max_workers>1),
addressing two things found while testing plain concurrent init_all()
against the real bernina namespace (see DESIGN.md section 13):

1. Concurrent init_all(max_workers=8) segfaulted inside libca's
   CA-TCP-recv thread (confirmed via dmesg). Multiple worker threads each
   implicitly creating their own CA context (pyepics's default behaviour
   the first time a thread touches Channel Access) and connecting
   concurrently is a known instability source. pyepics documents the fix:
   every thread that will touch CA should attach to one shared "initial
   context" via `epics.ca.use_initial_context()` instead. Every worker
   thread here calls that before doing anything else - this is the part
   that actually matters for safety.

2. A plain ThreadPoolExecutor also wastes time: a worker can pull a name
   whose dependency (via NamespaceComponent(...)) is being initialized by
   a *different* concurrent worker, and end up waiting on it deep inside
   the dependency's own lazy-init lock (Namespace.append_obj's init_local
   already handles this correctly - no double-init - just inefficiently).
   Given a `dependencies` mapping, this scheduler initializes ready names
   (no pending dependencies) first and only starts a name once everything
   it's known to depend on is done, which avoids most of that wasted
   cross-thread waiting. This is a scheduling hint for throughput, not a
   correctness mechanism - Namespace's own locking remains the actual
   safety net if the hint is incomplete.

Where `dependencies` comes from: NOT runtime introspection. An earlier
attempt to derive it by inspecting already-registered lazy items at
runtime triggered a real (if contained) initialization as a side effect -
checking `isinstance(x, NamespaceComponent)` on a captured argument that
was itself an already-lazy proxy object forced that proxy to resolve.
Use a purely static source scan instead (ast.parse on the namespace's
root module - see the dep_graph_static extraction used to produce the
numbers in DESIGN.md - never imports or executes anything) and pass the
result in explicitly. Treat it as a lower bound: a dependency resolved
indirectly deep inside some component's __init__ (rather than passed as
a NamespaceComponent(...) constructor argument) won't be in it - that's
fine, Namespace's own lock/wait handles it correctly regardless, just
without the scheduling benefit.

STATUS: implemented and unit-tested with a mocked namespace; NOT yet
validated end-to-end against the real bernina namespace - see DESIGN.md
for what has and hasn't been tried and why.
"""

from __future__ import annotations

import logging
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Lock

logger = logging.getLogger(__name__)


def init_all_parallel(
    namespace,
    dependencies: dict[str, list[str]] | None = None,
    required_only: bool = True,
    max_workers: int = 4,
    verbose: bool = True,
):
    """Dependency-ordered, shared-CA-context parallel alternative to
    Namespace.init_all(). See module docstring for what this does and does
    not guarantee.

    dependencies: {name: [other names it needs initialized first]} - see
    module docstring for how to obtain this safely (static source scan,
    not runtime introspection). Names absent from it are treated as
    immediately ready (no known dependency).

    Returns {"initialized": set(names), "failed": set(names)}.
    """
    import epics.ca as ca

    dependencies = dependencies or {}

    if required_only and namespace.required_names():
        names = (namespace.all_names - namespace.initialized_names) & set(
            namespace.required_names()
        )
    else:
        names = namespace.all_names - namespace.initialized_names

    # Restrict each name's dependencies to names actually being
    # initialized in this run - a dependency that's already initialized,
    # or isn't part of this run at all, can't block anything here.
    remaining_deps = {
        n: set(dependencies.get(n, [])) & names - namespace.initialized_names
        for n in names
    }

    lock = Lock()
    done = set()
    failed = set()
    submitted = set()

    def ready_names():
        with lock:
            return [
                n
                for n in names
                if n not in submitted and remaining_deps.get(n, set()) <= done
            ]

    def worker(name):
        # The essential fix for the segfault observed with plain
        # ThreadPoolExecutor + init_all(max_workers>1): share one CA
        # context across every worker thread instead of each one
        # implicitly creating its own on first use.
        ca.use_initial_context()
        ok = True
        try:
            namespace.init_name(name, verbose=verbose, raise_errors=False, quiet=not verbose)
        except Exception:
            ok = False
            logger.warning("init_all_parallel: %s failed", name, exc_info=True)
        with lock:
            done.add(name)
        return name, ok

    # Establish the shared context on this (main) thread before any
    # worker starts, so use_initial_context() has something to attach to.
    ca.use_initial_context()

    with ThreadPoolExecutor(max_workers=max_workers) as exc:
        futures = {}
        for name in ready_names():
            submitted.add(name)
            futures[exc.submit(worker, name)] = name

        while futures:
            completed, _ = wait(list(futures.keys()), return_when=FIRST_COMPLETED)
            for f in completed:
                name = futures.pop(f)
                _, ok = f.result()
                if not ok:
                    failed.add(name)
            for name in ready_names():
                submitted.add(name)
                futures[exc.submit(worker, name)] = name

    stuck = names - done
    if stuck:
        logger.warning(
            "init_all_parallel: %d name(s) never became ready (unmet/cyclic "
            "dependencies?): %s",
            len(stuck),
            stuck,
        )

    return {"initialized": done - failed, "failed": failed | stuck}
