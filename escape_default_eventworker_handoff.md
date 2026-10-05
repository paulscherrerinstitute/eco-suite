# Task: create the default `EventWorker` lazily in `escape.stream`

For a session working in the **escape-fel** repo. You have no context from the
conversation this came from, so everything you need is here. Line numbers refer
to the installed copy (escape-fel 0.2.13, `.../site-packages/escape/stream/`);
your checkout may differ slightly, so grep for the patterns quoted below rather
than trusting the numbers. The likely working checkout is
`/sf/bernina/config/personal/lemke_h/escape-fel` (its `escape_stream.py` is the
closest to the installed copy, but not identical, so confirm against the code you
actually have).

## 1. The bug

In a fresh process, with no `EventWorker(make_default=True)` created beforehand:

```python
from escape.stream import Stream
t = Stream.from_dispatcher("JF16T03V02:roi_intensities")
t.accumulate()
# AttributeError: 'NoneType' object has no attribute 'eventCallbacks'
#   Stream.accumulate -> Stream._is_accumulating -> self._source.eventWorker.eventCallbacks
```

`t._source.eventWorker` is `None`. A stream built before a default worker exists
keeps `None` **permanently**: creating a worker afterwards does not repair it.
Reproduced like this:

```python
s  = Stream.from_dispatcher("x")            # s._source.eventWorker -> None
ew = EventWorker(make_default=True)
s2 = Stream.from_dispatcher("x")            # s2._source.eventWorker is ew -> True
s._source.eventWorker                       # still None
```

## 2. Root cause

The default worker is a module global, `escape.stream.escape_stream.eventworker`.
Only one thing sets it: `EventWorker.__init__(..., make_default=True)`
(`globals()["eventworker"] = self`). Nothing in escape creates a worker on its
own. Consumers look the global up **once, at construction time**, and fall back
to `None`:

| Site | What it does today |
|---|---|
| `EventSource.__init__` (~L430) | `if eventWorker is None and "eventworker" in globals(): eventWorker = globals()["eventworker"]`, else stays `None` |
| `Stream.__init__` (~L1356) | for a `str` name: `eventworker = globals().get("eventworker")`, then builds `EventSource(...)` |
| `from_getter` (~L598) | `eventworker = globals().get("eventworker")` |
| `_resolve_eventworker` (~L2898), used by `pulse_id()` / `lab_time()` | same lookup, but **raises** `RuntimeError("No default EventWorker registered; pass one explicitly: ...")` |
| `StreamSession.__init__` (`session.py` ~L105) | `_esn.__dict__.get("eventworker")`: reuse an existing default, else build its own worker with the caller's `handler_kwargs` |
| `_wrapOperatorJoin` (~L3007) | takes `ew` from the *other* operand, then passes it to `from_getter(..., eventworker=ew)`. `ew` can be `None` here |

Downstream (the eco beamline package) only creates the worker as a side effect of
constructing its first `DetectorBsStream`. In a fresh session where the user goes
straight to `Stream.from_dispatcher(...)`, nothing has created one, hence the crash.

## 3. Required behaviour

1. Any `Stream` / `EventSource` / `from_getter` built **without** an explicit
   worker gets a usable default `EventWorker`, creating and registering one if none
   exists yet.
2. All such objects in a process share **one** default worker (it owns the single
   dispatcher connection, so a second default means a second connection).
3. An explicitly passed `eventworker=` always wins and never touches the default.
4. Creation is **lazy and offline**: no thread started and no connection opened
   until something calls `registerSource(s)` / `startEventLoop()`.
   Constructing `EventWorker()` does no network I/O. `DataHubEventHandler.__init__`
   and `EventHandler_SFEL.__init__` only store config. So creating the default at
   `Stream(...)` construction time is safe.
5. No behaviour change for code that already creates its own
   `EventWorker(make_default=True)` first. That one is simply returned.

## 4. Implementation

Put two small helpers in `escape_stream.py` next to `EventWorker`, with a module
lock:

```python
_default_lock = threading.Lock()

def _peek_default_eventworker():
    """The registered default EventWorker, or None. Never creates one."""
    return globals().get("eventworker")

def get_default_eventworker():
    """Return the module-default EventWorker, creating and registering one
    (``EventWorker(make_default=True)``, default handler) on first use.
    Thread-safe: concurrent first calls yield exactly one worker."""
    ew = globals().get("eventworker")
    if ew is None:
        with _default_lock:
            ew = globals().get("eventworker")      # re-check under the lock
            if ew is None:
                ew = EventWorker(make_default=True)  # sets globals()["eventworker"]
    return ew
```

Notes on the helper:
- `EventWorker.__init__` already picks `DataHubEventHandler(backend="bsread")` when
  `_HAS_DATAHUB`, else `EventHandler_SFEL()`, so no handler logic is needed here.
- `EventWorker(make_default=True)` prints "EventWorker registered as module default."
  Keeping that is fine and useful for the user.
- Export `get_default_eventworker` from `escape/stream/__init__.py`. Downstream
  code will import it by that name (see section 6), so **keep this name and
  signature stable**, and tell the user the final public name.

Call-site changes:

| Site | Change |
|---|---|
| `EventSource.__init__` | `if eventWorker is None: eventWorker = get_default_eventworker()` |
| `Stream.__init__` (str-name branch) | `if eventworker is None: eventworker = get_default_eventworker()` |
| `from_getter` | same. Harmless even with no real Stream involved, since the worker's thread isn't started until something registers a channel. It also makes `from_getter(x) + Stream("ch")` always share one worker |
| `_resolve_eventworker` | use `get_default_eventworker()` instead of raising, so `pulse_id()` / `lab_time()` work out of the box. **This removes the old `RuntimeError`.** grep the tests for `"No default EventWorker"` and update any that assert it |
| `StreamSession.__init__` | **do not** use the creating helper. Keep reuse-or-build-own semantics, using `_peek_default_eventworker()` for the "reuse" check. Otherwise a session with custom `handler_kwargs` would silently get a default worker built without them |
| `_wrapOperatorJoin` | no change needed once `from_getter` resolves the default itself |

Also update the docstrings that say "falls back to the module-level default
registered by `EventWorker(make_default=True)`" (`Stream`, `Stream.from_dispatcher`,
`from_getter`, `pulse_id`, `lab_time`, `EventWorker`'s `make_default`): the default
is now also *created on first use*.

## 5. Tests to add

Tests must **not** touch the real dispatcher. Everything below only constructs
objects. Never call `.accumulate()` on a default-handler worker in a test. Reset
module state per test:

```python
import escape.stream.escape_stream as esn
@pytest.fixture(autouse=True)
def _clean_default(monkeypatch):
    monkeypatch.delattr(esn, "eventworker", raising=False)
```

1. `Stream("x")` and `Stream.from_dispatcher("x")` in a clean module have a non-`None`
   `_source.eventWorker`, and no thread started (`ew.loopThread is None`).
2. Two streams built back-to-back share the identical worker object.
3. An explicit `eventworker=` is used as-is, and `esn.eventworker` stays unset.
4. A pre-existing `EventWorker(make_default=True)` is returned, not replaced.
5. 16 threads calling `get_default_eventworker()` behind a `threading.Barrier`
   produce exactly one distinct worker.
6. `pulse_id()` / `lab_time()` work with no prior worker (and replace any old
   `RuntimeError` test).
7. `from_getter(lambda: 1)` has a non-`None` `eventWorker`, and `from_getter(...) +
   Stream("x")` shares one worker.
8. `StreamSession("localhost:9999")` (or a `LocalEventHandler`) still builds its
   own worker with its handler kwargs, and does not create the default as a side
   effect.
9. Regression for the original traceback: `Stream.from_dispatcher("x")._is_accumulating()`
   returns `False` instead of raising.

## 6. Downstream impact: read before finishing

The eco package (`eco/detector/detectors_psi.py::_ensure_bs_event_worker`) currently
creates its own worker with `stream.EventWorker(make_default=True)` on its first
`DetectorBsStream`. **With your change alone there is a new failure mode:**

1. the user does `Stream.from_dispatcher(...)`, so your lazy default is created;
2. later they touch an eco bs-stream device, and eco builds a **second**
   `EventWorker(make_default=True)`, which overwrites `esn.eventworker`.

Result: two dispatcher connections, and the user's earlier streams are orphaned on
the first worker while new ones land on the second. This is the same footgun as any
second `make_default=True` call.

eco will be switched to `get_default_eventworker()` (falling back to the old call
when it isn't available, so old escape installs keep working). That is a separate
change in the eco repo, not yours. But it is why the helper's name must be stable,
and why you should tell the user when it lands.

Optional, only if cheap: have `EventWorker.__init__(make_default=True)` print a
one-line warning when it replaces an existing, different default instance. That would
have made the situation above visible. Don't change the overwrite semantics.

## 7. Done when

- The repro in section 1 no longer raises: `Stream.from_dispatcher("x")._source.eventWorker`
  is a real `EventWorker`, `_is_accumulating()` returns `False`, and no network
  traffic occurred.
- The new tests pass, and the existing test suite still passes (apart from the
  deliberately changed `RuntimeError` expectation).
- Report to the user: the exported helper name, the list of call sites changed,
  and whether you bumped the version, so eco can pin or feature-detect accordingly.
