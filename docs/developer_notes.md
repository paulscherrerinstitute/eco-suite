# Developer notes

Pitfalls that have cost real debugging time, and conventions that are easy to
miss when reading the code cold. Each one is short; the code comments at the
named places have the details.

## Testing against the real environment

**NumPy 2.5 turned old deprecations into errors.** `float()` or `int()` of a
1-element, 1-d array now raises
`TypeError: only 0-dimensional arrays can be converted to Python scalars`
(it used to warn), and `np.in1d` is gone (use `np.isin`). Typical source:

```python
float(np.diff([b1, b2]))     # TypeError on numpy >= 2.5
np.diff([b1, b2])[0]         # fine
```

A quick check with an older interpreter that happens to be on the `PATH` hides
this class of bug. Test with the interpreter of the beamline environment.

**Test fakes must not be more permissive than the real object.** The real
`epics.PV` is unhashable. A hashable fake once hid a leak of every
subscription, because the code kept PVs in a set; eco now keys such
bookkeeping by `id(pv)`. Code that handles PV objects directly also needs a
smoke test against a real channel, not only unit tests with fakes.

**Two mistakes `py_compile` does not catch.** After editing the same class
several times, check for duplicate method definitions: Python silently keeps
the last `def`, and a stale copy once overrode a fix. And check that relative
imports name things that exist: one wrong `from .pumps import ...` broke the
whole vacuum package on import. `pylint -E` reports both (E0102, E0611).

## Namespace and Assembly

**Add devices to a Namespace by class name and module.** The house style is

```python
namespace.append_obj(
    "RefLaser_Aramis",
    "SAROP21-OLAS134",
    module_name="eco.xoptics.reflaser",
    name="reflaser_beamline",
    lazy=True,
)
```

rather than passing the class object. The module is then imported only when
the device is first built, so the instrument definition does not pull in
every device's dependencies at startup, and
`Namespace.reinitialize(reload_modules=True)` picks up edited code. Passing a
class still works (`_resolve_reloadable_factory` in `eco/utilities/config.py`),
but a class defined inside the instrument file itself cannot be reloaded.

**An alias has exactly one parent.** Appending a component into a second
Assembly keeps its first parent and only warns. Names computed relative to the
second Assembly (`alias.get_full_name(base=second)`) then come out absolute.
One visible consequence: `memorize()` on the second Assembly stores keys that
`get_memory(filter_existing=True)` cannot match and silently drops, so
browsing a parent's memories works only through the first parent.

**`repr()` of an Assembly walks the whole tree.** The status line of every
Assembly lists failed and never-connected components, which reads every leaf
below it. One bad leaf, typically a virtual detector doing `int(value)` on a
`None` from a disconnected PV, used to break `repr()` of the whole namespace.
The walk is guarded now, but if adding a device seems to "crash the session"
or print status tables repeatedly, look first for an unguarded computation on
a possibly-`None` reading in a `DetectorVirtual`/`AdjustableVirtual`.

## Channel access

**`PV.get()` returns `None` instead of raising** when a read fails. Never feed
a CA read straight into `int()`/`float()` in code that runs during scans; see
{doc}`channel_access`. Pulse ids in particular are read only through
`Daq.get_pulse_id()` / `Daq.wait_for_pulse_id()`, which use a monitor and raise
`TimeoutError` with a diagnosis instead of returning `None`.

**Threads that touch CA must attach to the shared context**
(`epics.ca.use_initial_context()`) before their first CA call, as the parallel
namespace initialization, the status server and `ca_tuning`'s sweeper do.
Without it, CA calls from that thread are undefined behaviour in the C
library, which has caused a segfault in eco before.
