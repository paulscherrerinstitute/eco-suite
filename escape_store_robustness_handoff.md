# Task: make escape's result-file storage robust (list data, missing suffix, half-written files)

> **Status 2026-10-06: open, not started.** Everything below was measured on
> 2026-10-06 against a stock escape-fel 0.2.14; nothing is assumed. Appendix A is an
> acceptance script: 24 of its 27 checks fail on stock 0.2.14, and all MUST and SHOULD
> checks pass against the throwaway reference patch in Appendix B.

For a session working in a checkout of the **escape-fel** repo. You have no context
from the conversation this came from, and you do **not** have access to the machine it
came from, so this file is everything you get: the acceptance script and a reference
patch are embedded as appendices. Line numbers are deliberately not quoted: grep for
the names given below. The main consumer is the **eco** control package (a separate
repo you cannot see and must not touch); it is already fixed on its side (section 7).

## 0. Decisions already taken by the user (do not re-ask)

1. **No escape suffix at all** (`run`, `scan_0.5V`): complete it to `.esc.h5`,
   **silently**. Reading of the user's words ("no suffix and given type (h5) can be
   completed silently"): the default type is `h5`; any other type has to be given as
   a suffix (`.zarr`), which is an *incomplete* suffix and therefore warns (next point).
2. **Incomplete suffix** (`run.h5`, `run.esc`, `run.zarr`): complete it **and raise a
   warning** (`warnings.warn(..., UserWarning)`).
3. **Existing file that is not to be overwritten: `FileExistsError`**, in the
   non-interactive case *and* when the user declines. Never return `None`.
4. **Everything lands on `main`.** No feature branch, and do not use or merge the
   `store-robustness` branch if you ever see it.
5. **The release is a MINOR bump**: `v0.3.0` (latest is `v0.2.14`; confirm with
   `git tag`). See section 9 for the exact steps and the one gate before pushing.

## 1. What happened, and why this task exists

A beamline user ran an eco scan with a plain filename:

```
mon_opt.intensity.scans.ascan(dummy_adjustable, 0, 1, 10, .3, filename='myfile')
...
Ended all steps without interruption.
Could not create dataset file!
```

The scan finished, then **stored nothing** and said nothing useful: eco's counter
wrapped the whole store in a bare `except:`. Reproducing it with real PV data under
the beamline environment (escape 0.2.14) showed **three separate problems in a row**.
Fixing the first one only exposes the second.

| # | What the caller did | What escape did | Result |
|---|---|---|---|
| 1 | `DataSet.create_with_new_result_file("myfile")` | `filespec_to_file` raises `Exception("Expecting esc suffix in filename")`. `"x.esc"` alone is worse: `UnboundLocalError: result_file` | nothing written, caller sees only a generic failure |
| 2 | `ArrayTimestamps(data=<python list>, ...)` then `.store()` | `ArrayH5Dataset.append` writes `timestamps_0000`, then has **no branch** for a list (only `np.ndarray` / `da.Array`) and silently skips the data. `store()` goes on to `get_data_da()` and dies with `KeyError: data_0000` | **a corrupt file**: group has `scan` + `timestamps_0000`, no data. Reading it back: `Corrupt escape ArrayH5Dataset, not equal numbered data and id sub-datasets!` (no file name, no group name). The caller's `except` never closed the h5 handle either |
| 3 | after a good store: `d = DataSet.load_from_result_file(f)`; `arr = d.datasets[name]`; `d.results_file.close()`; later `arr.data` | `datasets[name]` is a lazy `Proxy(partial(ArrayTimestamps.load_from_h5, ...))` resolved on first touch, so it hits the closed file | `ValueError: Unable to synchronously create group (invalid identifier type to function)` from deep inside h5py. `.data`, `.timestamps`, `.scan.plot` all fail |

**Root cause of #2 (a regression):** escape up to 0.2.12 did
`self.data = np.asarray(data)` in `ArrayTimestamps.__init__`. The commit "Make
ArrayTimestamps.data lazy ..." (first released in 0.2.13) changed it to
`self._data = data` so that big dask-backed arrays are not pulled into memory. That is
a deliberate, good change; it simply forgot that the same constructor is also fed plain
lists (eco's monitors accumulate Python lists). `ArrayH5Dataset.append` never knew what
to do with them, and it **writes the timestamps before it has checked the data**, so any
later failure leaves an orphan. Confirmed by diffing the released wheels: `append` is
byte-identical in 0.2.1 and 0.2.14; only the constructor changed.

**Why a fix must be careful.** The beamline runs several environments with different
escape versions, so eco has to keep working against all of them and cannot depend on new
API:

| beamline env | escape-fel |
|---|---|
| `bpy312`, `bpy312-dev`, `bpy312-async` | 0.2.14 (affected) |
| `bpy312-eco`, and a user-site install on python 3.9 | 0.2.1 (not affected) |

What the user wants from escape: (a) convert data into a usable form again, (b) create
the suffix automatically when none is given (both the `.esc` and the file type, rules in
section 4), and (c) "what else would be good to add to escape for more robustness".
Section 5 is the answer to (c).

## 2. Setup

- **Your checkout:** make sure you are on current `main` at or after tag `v0.2.14`:
  ```
  git fetch origin --tags
  git switch main && git merge --ff-only origin/main
  git tag --list 'v0.2.1[34]'                     # v0.2.13 and v0.2.14 must be there
  grep -c "Kept as whatever was passed in" escape/storage/storage_timestamps.py   # >= 1
  grep -n "_normalize_result_filepath" escape/swissfel/parse.py                   # existing helper
  ```
  If any of those does not hold, stop and tell the user: your base is not the one this
  manual was measured on.
- **Interpreter:** any environment that has escape's dependencies (numpy, dask, h5py,
  zarr, matplotlib for check C3) plus pytest. Make your checkout importable
  (`pip install -e .` or `PYTHONPATH=<checkout>`); the first line the acceptance script
  prints is the path of the escape it imported, check it is yours.
- **Tests:** escape has no maintained test suite (its `CLAUDE.md` says so). Add
  `tests/storage/test_result_files.py` (pytest) covering section 6, and keep Appendix A
  runnable unchanged as the acceptance gate.
- **Read `CLAUDE.md` and `docs/development/releasing.md` in the repo first**:
  pushing `main` publishes to PyPI.

## 3. Task A: data conversion

### 3.1 Principle

*Coerce at the door, validate before writing, never silently skip.* Do **not** restore an
unconditional `np.asarray` in the constructor: it would defeat the 0.2.13 laziness
(checks A5 and A6 guard exactly that).

### 3.2 Coercion rules (one helper, used everywhere below)

| input `data` | result |
|---|---|
| `np.ndarray`, `dask.array.Array` | unchanged |
| callable (lazy loader) | **not called** by the constructor; the value it returns is run through this same table on first `.data` access |
| `list`, `tuple`, pandas Series, other array-likes | `np.asarray(data)` |
| the result has `dtype=object` but every element is a number or `None` | `np.asarray(data, dtype=float)` (so `None` becomes `NaN`). Typical cause: eco seeds a monitor with `pv.get()`, which is `None` when the PV is not connected yet |
| still `dtype=object` (strings, arbitrary objects) | leave it; the **storage layer** rejects it (3.4) |
| lists of equal-length lists (waveforms) | `(N, M)` array; ragged input is an error, not a silent object array |

Apply it in: `ArrayTimestamps.__init__`, the `data` getter (after calling a callable),
the `data` **setter** (eco assigns `array.data = np.asarray(...)`; it must stay
idempotent), `ArrayH5Dataset.append` and `ArrayH5File.append`
(`escape/storage/storage_timestamps.py`), and check `escape/storage/storage.py` (`Array`
and its own `ArrayH5Dataset`) for the same silent skip. `ArrayTimestamps.update()` also
breaks on lists (`other.data[new_mask]`: `TypeError`), which falls out of the
constructor coercion; keep a test for it (A11).

### 3.3 Length validation

In the constructor, when neither `data` nor `timestamps` is a callable and both lengths
are known, `len(data) != len(timestamps)` raises `ValueError` naming the array and both
lengths (A9). Today it is accepted and fails (or worse, stores garbage) much later. If an
internal escape code path legitimately builds mismatched arrays, validate in `store()`
instead, but not nowhere. Empty placeholders (`np.array([])` with zero timestamps, used
by eco's archiver) must keep working.

### 3.4 Never leave a half-written pair

`ArrayH5Dataset.append` writes `timestamps_NNNN` first and `data_NNNN` second. Any
failure in between (verified: an object-dtype array raises `TypeError: Object dtype
dtype('O') has no native HDF5 equivalent` **after** the timestamps are on disk) leaves
exactly the unreadable state from section 1. Required: coerce and validate (dtype,
supported type) **before** touching the file; write the pair inside
`try/except BaseException`, delete whatever *this call* created on failure, and append to
`_n_t`/`_n_d` only after both succeeded (already the case). Check A7: no `timestamps_` /
`data_` key may remain after a failed store.

### 3.5 Errors must name things

Unsupported type, object dtype, and corrupt groups raise with the **array name, the group
path and the results-file name** in the message, and the right exception type (`TypeError`
for data, `ValueError` for shape/overlap problems). Compare the current
`Exception("Corrupt escape ArrayH5Dataset, not equal numbered data and id
sub-datasets!")`: it says nothing about which file or channel, in a results file that can
hold thousands. Messages should also say what to do.

### 3.6 Partly overlapping timestamps

`ArrayH5Dataset.append` has `if no overlap ... elif all overlap ...` and **no else**: a
partial overlap hits `UnboundLocalError: new_timestamps`. Add the `else` raising
`ValueError`, with a message like: *"Cannot append to '/chan': the new timestamps
partially, but not fully, overlap the 120 already stored (neither a clean append nor a
clean extend). This usually means data from two different sources or runs is being
written to the same channel."* Check A10.

### 3.7 Drive-by bugs found while reading

- `ArrayTimestamps.store_file` does `self._timestamps = self.h5.index`, but
  `ArrayH5Dataset` has `timestamps`, no `index` (`AttributeError`, verified). Fix it or
  delete the method if unused.
- `ArrayH5File.append` prints `"this should happen"` (debug leftover).

## 4. Task B: automatic suffix

### 4.1 Rules (decided, section 0)

An escape results file needs **both** `.esc` and a type suffix (`.h5` or `.zarr`) in its
suffixes.

| given | created / opened | warning? |
|---|---|---|
| `run` | `run.esc.h5` | **no, silent** |
| `scan_0.5V` | `scan_0.5V.esc.h5` (a dot that is not `.esc/.h5/.zarr` is part of the name: do not eat it) | **no, silent** |
| `run.h5` | `run.esc.h5` | **yes**, one `UserWarning` |
| `run.esc` | `run.esc.h5` (today: `UnboundLocalError`) | **yes** |
| `run.zarr` | `run.esc.zarr` (type taken from what was given) | **yes** |
| `run.esc.h5`, `run.esc.zarr` | unchanged | no |

Default type when nothing says otherwise: **`.h5`**. The warning text names the
filename it used instead (checked by B2/B3).

### 4.2 One implementation, built on what exists

`escape/swissfel/parse.py` already has a private `_normalize_result_filepath(path,
result_type)` (it is in the released 0.2.14). Its policy is **exactly the decided one**:
no recognised suffix at all is added silently; an incomplete or mismatched one is
replaced with a warning. Do not write a second copy. Add a public
`normalize_result_filepath(path, result_type=None, stacklevel=2) -> Path` in
`escape/storage/dataset.py` and make the `parse.py` helper delegate to it:

- `result_type=None` (the `DataSet` case) is **lenient**: a name that `filespec_to_file`
  accepts today (`.esc` and `.h5`/`.zarr` somewhere in its suffixes) is returned
  unchanged, so no currently valid name changes meaning;
- an explicit `result_type` keeps the strict check `parse.py` has today
  (`suffixes[-2:] == [".esc", type]`) and its existing message wording for that case;
- strip only a trailing `.esc` / `.h5` / `.zarr` (the existing loop on `Path(stem).suffix`
  does this and keeps `scan_0.5V` intact);
- `stacklevel` so the warning points at the **caller's** line, not at escape internals.
  The reference patch shows how (3 from `create_with_new_result_file`,
  `load_from_result_file` and `DataSet.__init__`); verified that the warning then reports
  the user's own line for all three entry points.

### 4.3 Where to apply it

- `DataSet.__init__`: only when `results_file` is a `str`/`Path` (leave `h5py.File` and
  `zarr.Group` inputs alone). Expose the path actually used as
  **`DataSet.results_filepath`** so callers can print or log it (B8).
- `DataSet.create_with_new_result_file`: normalise **before** the `Path(...).exists()`
  check. Today the check looks at the raw name, so it can say "does not exist" for a file
  that is about to be overwritten under its real name.
- `DataSet.load_from_result_file`: normalise too, so `create("run")` followed by
  `load("run")` works silently (B7), and `load("run.h5")` finds `run.esc.h5` with the
  warning (B7b).
- `filespec_to_file`: an unknown suffix after `.esc` raises `ValueError` listing the
  accepted ones, not `UnboundLocalError`.
- Use `warnings.warn(..., UserWarning)` (the decision says "raise warnings"), not
  `print`, not `logging`. Silent completion needs no output at all; `results_filepath` is
  the programmatic channel.

### 4.4 The overwrite prompt (decided: `FileExistsError`)

`create_with_new_result_file(force_overwrite=False)` calls `input()` if the file exists,
and **returns `None`** when the answer is not `y`. In a non-interactive caller (a scan's
end-of-run callback, a service) `input()` blocks or raises `EOFError`, and a `None` return
crashes the caller later (`AttributeError: 'NoneType' object has no attribute 'append'`,
seen in eco). Required:

- `sys.stdin` is not a tty: raise `FileExistsError` **without prompting** (B9);
- tty and the answer is not `y`: raise `FileExistsError` (B10);
- either way the existing file is left untouched.

This changes a return value, so it is a breaking change for anyone who tests
`... is None`. grep the repo first (`escape/swissfel/parse.py`, `live_reduce.py`,
`docs/user_guide/dataset.md`, examples) and update every caller. eco handles both the old
`None` and the new exception.

## 5. What else would make escape more robust (answer to "what else?")

Ordered by value. Each item names its evidence; the acceptance check, if any, is in
brackets.

1. **Closed-file access must say so, and arrays must be detachable.** Today (section 1,
   #3) the error is an h5py internal. Check `parent.id.valid` in
   `ArrayH5Dataset.__init__` and raise `RuntimeError` naming the array and the file and
   how to fix it [C2]. Add `ArrayTimestamps.materialize()` (and the same on `Array`):
   pulls `data`, `timestamps` and scan metadata into memory and returns `self`, so the
   array outlives its file [C3]. eco currently does `array.data = np.asarray(array.data);
   array.timestamps` by hand to get this. Document the `with
   DataSet.load_from_result_file(...) as ds:` pattern (`__enter__/__exit__` already exist
   and already close the file even when the body raises [C1]).
2. **Corrupt-file diagnostics and a repair tool.** Reading must name file and group [C4].
   Add `escape.storage.dataset.check_result_file(path, repair=False) -> dict` returning
   one status per group (`"ok"`, `"orphan_timestamps"`, `"missing_scan"`, ...); with
   `repair=True` it removes orphan `timestamps_NNNN` and groups left empty, so a damaged
   file becomes loadable again [C5, NICE]. Files already damaged by problem #2 contain
   timestamps only; their data was never written and cannot be recovered, but they should
   stop poisoning `DataSet.load_from_result_file` for the other channels.
3. **Stop swallowing errors.** Verified bare `except:` / `print` sites to review:
   `DataSet._init_datasets` (its `except: pass` fallback silently drops a channel that
   fails to load, which then looks like a missing dataset); `ArrayTimestamps.load_from_h5`
   (scan-metadata `except:` with the message commented out); `ArrayH5Dataset.__init__`
   (`print("Could not put esc_type metadata.")`); `filespec_to_file` (`print("changing
   perms")` on **every** file open, which fills every scan log, plus `Warning:failed
   setting permissions`); the `*_datasets_max_element_size` methods of `DataSet`
   (`except:` blocks). Use the module `logger` with `exc_info=True` for real failures and
   drop the chatter. Keep "never break the caller" behaviour where it exists on purpose,
   but make it visible.
4. **Crash safety.** `ArrayTimestamps.store()` should `flush()` the h5 file when it
   finishes, so a killed process leaves a readable file up to the last array. Optional and
   cheap: set `results_file.attrs["esc_complete"] = False` on create and `True` on
   `close()`/`__exit__`; readers warn only when it is explicitly `False` (absent means an
   old file: say nothing). Atomic "write to `.partial`, rename at close" is possible but
   touches dask locking; not required.
5. **Contract tests that mirror real callers** (section 6). The regression in 0.2.13
   shipped because nothing stores what eco actually passes.
6. **Document the rules** in `docs/user_guide/dataset.md`: accepted `data` types and what
   is coerced, the suffix rules and when they warn, when a `DataSet` must stay open, and
   the new exceptions.

## 6. Tests to write

A parametrised pytest module, using `tmp_path`, headless, no EPICS. At least:

- every row of the 4.1 table, for `create_with_new_result_file`, `DataSet(...,
  mode="w")` and `load_from_result_file`, **asserting silent vs. exactly one
  `UserWarning`** (use `pytest.warns` / `warnings.catch_warnings(record=True)` with
  `simplefilter("always")`, otherwise Python shows a warning once per location and hides
  the second), plus `.zarr` (skip if zarr is missing);
- the overwrite behaviour of 4.4 in both flavours (non-tty, and a faked tty answering
  `n`);
- data matrix `{ndarray, dask, list, tuple, list of lists, list with None, callable
  returning a list, empty}` x `{store, close, reload, read back equal}`;
- failure atomicity: object dtype, partial overlap, and a store that raises half-way must
  leave **no** orphan keys and a closed handle;
- the four caller patterns that exist in eco today, each end to end:
  1. counter: `ArrayTimestamps` from lists -> `DataSet.create_with_new_result_file` ->
     `append` -> `store()` -> close -> `load_from_result_file` -> access arrays;
  2. status server: `DataSet(results_file=h5py.File(..., "w"), mode="w")` +
     `ArrayTimestamps(...)` + `append` + `store()`;
  3. archiver: `ArrayTimestamps.set_h5_storage_file(...)` + `array.h5.append(...)` (this
     one deliberately bypasses `store()`);
  4. event-based `escape.Array` through `DataSet.append` (eco's bs-stream counter, timing
     diagnostics);
- reading files written by the old code (write one with stock 0.2.14, or generate it by
  hand with h5py) to prove nothing about the layout changed.

## 7. State on the eco side (so you know the contract; nothing for you to do)

eco converts and normalises **on its own, on purpose**, and will keep doing so: it must
work against the 0.2.1 environments too, and escape should not rely on callers. In eco:

- a helper turns a monitor's list into a numpy array (`None` -> `NaN`) and the counter
  trims values and timestamps to the same length (the CA callback appends the timestamp
  before the value);
- the counter's store step completes the filename itself (silent when there is no
  suffix, a printed note when it is incomplete, the same rule as 4.1), closes the file on
  failure, prints stage + exception (+ traceback for non-`OSError`), materialises the
  reloaded arrays before closing, and only attaches the dataset to the elog if it was
  written.

After your change eco keeps working unchanged. Whether eco later drops its own copies is
the user's call, and only once every environment has the new escape.

## 8. Don't

- Don't push, tag-and-push, or publish before the user says so (section 9): a PyPI
  version can never be reused.
- Don't change the on-disk layout (`data_NNNN`, `timestamps_NNNN`, `index_NNNN`,
  `esc_type` attrs, the `scan` group). Files written by old and new escape must open in
  both directions (verified for the reference patch: stock 0.2.14 <-> patched).
- Don't make the constructor eagerly load dask or callable data.
- Don't add `print` (the suffix warning is a `warnings.warn`; other diagnostics go
  through `logging`).
- Don't use or merge the `store-robustness` branch; this goes straight to `main`.
- Don't try to edit eco; you cannot see it.

## 9. Done when, and the release

**Done when:**

- `acceptance_store.py` (Appendix A), run with your checkout importable, reports
  **`failed: MUST=0 SHOULD=0`** (NICE optional) and exits 0.
- Your pytest module passes.
- The work is committed on **`main`** in reviewable pieces (suffix, coercion, atomicity,
  diagnostics, docs), **not pushed**.
- `docs/user_guide/dataset.md` documents the rules.

**Release (decided: MINOR, `v0.3.0`).** Per `docs/development/releasing.md` the version
comes only from git tags, and the `pre-push` hook (`.githooks/pre-push`, enabled with
`git config core.hooksPath .githooks`) auto-tags the next **PATCH** on every push of
`main`, which then publishes to PyPI. A deliberate MINOR therefore needs the tag **before**
the push, so the hook sees the commit is already tagged and leaves it alone:

```
git switch main && git pull                   # up to date, clean tree
git tag v0.3.0                                # on the final commit
# --- STOP here and report to the user. Push only after an explicit go-ahead. ---
git push origin v0.3.0                        # explicit single tag, triggers the publish workflow
git push origin main
```

Then watch the "Publish to PyPI" run in the repo's Actions tab. Optionally
`gh release create v0.3.0 --generate-notes`; the release doc asks that behaviour changes
are called out in the notes, so use this as the starting text:

> **Behaviour changes** (hence a MINOR release)
> - `DataSet.create_with_new_result_file` raises `FileExistsError` instead of returning
>   `None` when the file exists and is not overwritten; it never prompts when stdin is not a
>   terminal.
> - Result-file names without an escape suffix are completed to `.esc.h5`; names with an
>   *incomplete* suffix (`.h5`, `.esc`, `.zarr`) are completed with a `UserWarning`. They
>   used to raise.
> - `ArrayTimestamps` accepts lists/tuples again (restores 0.2.12 behaviour lost in 0.2.13)
>   and raises `ValueError` if `data` and `timestamps` differ in length.
> - A failed `store()` no longer leaves a half-written group in the file.

If a step does not match this section (for instance `v0.3.0` already exists, or the hook is
not enabled in your clone), stop and ask rather than improvising: pushing is the one step
here that cannot be undone.

---

## Appendix A: acceptance script (verbatim)

Save as `acceptance_store.py` and run it with your checkout importable, for example:

```
cd <your escape-fel checkout> && PYTHONPATH=$PWD python /path/to/acceptance_store.py
```

Baseline against a stock escape 0.2.14: `failed: MUST=15 SHOULD=8 NICE=1 (of 27 checks)`;
the 3 that pass are guards (A5 lazy dask, B5 complete suffix stays silent, C1 context
manager). The check ids are the ones referred to in the text above. It uses a temporary
directory, needs no display, no EPICS and no beamline.

```python
"""Headless acceptance check for escape's result-file storage robustness.

Run it with the interpreter and escape you want to test, e.g.
    PYTHONPATH=<escape-fel checkout> \
    /sf/bernina/applications/python/.pixi/envs/bpy312/bin/python acceptance_store.py

Prints one line per check: PASS/FAIL [tier] id - description (detail).
Tiers: MUST = required for the task, SHOULD = expected, NICE = optional.
Exit status 1 if any MUST check fails. Needs no display, no EPICS, no beamline.
"""
import io
import os
import sys
import tempfile
import traceback
import warnings
from pathlib import Path

import h5py
import numpy as np

import escape
from escape import ArrayTimestamps, DataSet
from escape.storage.storage_timestamps import ArrayH5Dataset

TMP = Path(tempfile.mkdtemp(prefix="esc_accept_"))
TS = np.arange(10.0)
INTERVALS = [(0.0, 5.0), (5.0, 10.0)]
PARAM = {"x": {"values": [0, 1]}}
RESULTS = []


def mk(data, ts=TS, name="m"):
    return ArrayTimestamps(
        data=data,
        timestamps=ts,
        timestamp_intervals=INTERVALS,
        parameter=PARAM,
        name=name,
    )


def check(cid, tier, desc):
    def deco(fn):
        try:
            detail = fn()
            RESULTS.append(("PASS", tier, cid, desc, detail or ""))
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {str(exc).splitlines()[0][:100] if str(exc) else ''}"
            RESULTS.append(("FAIL", tier, cid, desc, msg))
        return fn

    return deco


def roundtrip(data, ts=TS, fname="rt.esc.h5", name="m"):
    """store `data` through DataSet exactly like eco does, reload, read back."""
    fn = TMP / fname
    with DataSet.create_with_new_result_file(fn, force_overwrite=True) as ds:
        a = mk(data, ts, name)
        ds.append(a, name=name)
        a.store()
    r = DataSet.load_from_result_file(fn)
    out = r.datasets[name]
    vals, tss = np.asarray(out.data), np.asarray(out.timestamps)
    r.results_file.close()
    return vals, tss


def raises(fn, *types):
    try:
        fn()
    except BaseException as exc:  # noqa: BLE001
        if types and not isinstance(exc, types):
            raise AssertionError(f"raised {type(exc).__name__}, expected {types}: {exc}")
        return exc
    raise AssertionError("did not raise")


# ---------------------------------------------------------------- A: data
@check("A1", "MUST", "list of floats is stored and read back")
def _():
    v, t = roundtrip(list(np.arange(10.0) * 2))
    assert (v == np.arange(10.0) * 2).all() and (t == TS).all(), (v, t)


@check("A2", "MUST", "tuple data is stored and read back")
def _():
    v, _t = roundtrip(tuple(np.arange(10.0)), fname="a2.esc.h5")
    assert (v == np.arange(10.0)).all()


@check("A3", "MUST", "list of equal-length lists (waveforms) -> (N, M) array")
def _():
    v, _t = roundtrip([[i, i + 1, i + 2] for i in range(10)], fname="a3.esc.h5")
    assert v.shape == (10, 3), v.shape


@check("A4", "MUST", "list timestamps + list data (what eco's monitors deliver)")
def _():
    v, t = roundtrip(list(np.arange(10.0)), ts=list(TS), fname="a4.esc.h5")
    assert (t == TS).all() and (v == np.arange(10.0)).all()


@check("A5", "MUST", "dask data stays lazy (not pulled into memory by the constructor)")
def _():
    import dask.array as da

    a = mk(da.arange(10.0, chunks=5))
    assert isinstance(a._data, da.Array), type(a._data)
    assert isinstance(a.data, da.Array), type(a.data)


@check("A6", "MUST", "callable data is not called by the constructor; its result is coerced")
def _():
    calls = []
    a = mk(lambda: calls.append(1) or list(range(10)))
    assert calls == [], "constructor called the data callable"
    assert isinstance(a.data, np.ndarray), type(a.data)
    assert calls == [1]


@check("A7", "MUST", "unstorable (object) data raises TypeError naming the array, and leaves NO orphan timestamps_NNNN")
def _():
    fn = TMP / "a7.esc.h5"
    exc = None
    try:
        with DataSet.create_with_new_result_file(fn, force_overwrite=True) as ds:
            a = mk(np.array([object()] * 10), name="m")
            ds.append(a, name="m")
            a.store()
    except TypeError as e:
        exc = e
    assert exc is not None, "store() did not raise TypeError"
    assert "m" in str(exc), f"message does not name the array: {exc}"
    with h5py.File(fn) as f:
        leftovers = sorted(k for k in f["m"].keys() if k.startswith(("timestamps_", "data_")))
    assert not leftovers, f"orphans left in the file: {leftovers}"


@check("A8", "SHOULD", "None among numbers is stored as NaN (a PV that was not connected yet)")
def _():
    v, _t = roundtrip([None] + list(range(1, 10)), fname="a8.esc.h5")
    assert np.isnan(v[0]) and (v[1:] == np.arange(1.0, 10.0)).all(), v


@check("A9", "SHOULD", "len(data) != len(timestamps) is a ValueError naming both lengths")
def _():
    exc = raises(lambda: mk(np.arange(7.0)), ValueError)
    assert "7" in str(exc) and "10" in str(exc), exc


@check("A10", "MUST", "append with partly overlapping timestamps raises a clear error, not UnboundLocalError")
def _():
    fn = TMP / "a10.esc.h5"
    with DataSet.create_with_new_result_file(fn, force_overwrite=True) as ds:
        a = mk(np.arange(10.0))
        ds.append(a, name="m")
        a.store()
        b = mk(np.arange(10.0), ts=TS + 5)
        exc = raises(lambda: ArrayH5Dataset(ds.results_file, "m").append(b.data, b.timestamps, b.scan), Exception)
    assert not isinstance(exc, (UnboundLocalError, NameError)), repr(exc)
    assert "overlap" in str(exc).lower(), exc


@check("A11", "SHOULD", "ArrayTimestamps.update() works with list-built arrays")
def _():
    c = mk(list(range(10))).update(mk(list(range(10, 20)), ts=TS + 20))
    assert len(c.data) == 20


# ---------------------------------------------------------------- B: suffix
def created_path(name):
    """create_with_new_result_file(TMP/name): which files appeared, which warnings."""
    before = set(os.listdir(TMP))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ds = DataSet.create_with_new_result_file(TMP / name, force_overwrite=True)
    ds.close()
    new = sorted(set(os.listdir(TMP)) - before)
    return new, [w for w in caught if not issubclass(w.category, DeprecationWarning)], ds


# (id, given name, file that must appear, must it warn?)
#   no escape suffix at all  -> completed SILENTLY (default type h5)
#   incomplete suffix        -> completed with exactly one UserWarning
#   complete suffix          -> untouched, silent
for cid, given, expect, warns in [
    ("B1", "run1", "run1.esc.h5", False),
    ("B2", "run2.h5", "run2.esc.h5", True),
    ("B3", "run3.esc", "run3.esc.h5", True),
    ("B4", "scan_0.5V", "scan_0.5V.esc.h5", False),
    ("B5", "run5.esc.h5", "run5.esc.h5", False),
]:

    def _make(given=given, expect=expect, warns=warns):
        new, caught, _ = created_path(given)
        assert new == [expect], f"{given!r} created {new}, expected [{expect!r}]"
        if warns:
            assert len(caught) == 1 and issubclass(caught[0].category, UserWarning), [str(w.message) for w in caught]
            assert expect in str(caught[0].message), f"warning does not name the name used: {caught[0].message}"
        else:
            assert not caught, f"expected no warning, got {[str(w.message) for w in caught]}"

    check(cid, "MUST", f"create({given!r}) -> {expect!r}, " + ("one UserWarning" if warns else "silent"))(_make)


@check("B6", "SHOULD", "'run6.zarr' -> 'run6.esc.zarr' (type from the given suffix), one UserWarning")
def _():
    import zarr  # noqa: F401

    new, caught, _ = created_path("run6.zarr")
    assert new == ["run6.esc.zarr"], new
    assert len(caught) == 1 and issubclass(caught[0].category, UserWarning), [str(w.message) for w in caught]


@check("B7", "MUST", "load_from_result_file('run1') finds run1.esc.h5, silently")
def _():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        r = DataSet.load_from_result_file(TMP / "run1")
    r.close()
    assert not [w for w in caught if not issubclass(w.category, DeprecationWarning)]


@check("B7b", "MUST", "load_from_result_file('run2.h5') finds run2.esc.h5, one UserWarning")
def _():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        r = DataSet.load_from_result_file(TMP / "run2.h5")
    r.close()
    caught = [w for w in caught if not issubclass(w.category, DeprecationWarning)]
    assert len(caught) == 1 and issubclass(caught[0].category, UserWarning), [str(w.message) for w in caught]


@check("B8", "SHOULD", "DataSet exposes the path it really used as .results_filepath")
def _():
    ds = DataSet.create_with_new_result_file(TMP / "run8", force_overwrite=True)
    ds.close()
    assert Path(ds.results_filepath) == TMP / "run8.esc.h5", ds.results_filepath


@check("B9", "MUST", "existing file + non-interactive stdin: FileExistsError (not input() blocking, not None)")
def _():
    (TMP / "exists.esc.h5").write_bytes(b"")
    old = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        exc = raises(lambda: DataSet.create_with_new_result_file(TMP / "exists", force_overwrite=False))
    finally:
        sys.stdin = old
    assert isinstance(exc, FileExistsError), repr(exc)
    assert (TMP / "exists.esc.h5").read_bytes() == b"", "existing file was modified"


@check("B10", "MUST", "existing file + interactive answer 'n': FileExistsError (not a silent None), file untouched")
def _():
    class FakeTty(io.StringIO):
        def isatty(self):
            return True

    (TMP / "exists2.esc.h5").write_bytes(b"keep")
    old = sys.stdin
    sys.stdin = FakeTty("n\n")
    try:
        exc = raises(lambda: DataSet.create_with_new_result_file(TMP / "exists2", force_overwrite=False))
    finally:
        sys.stdin = old
    assert isinstance(exc, FileExistsError), repr(exc)
    assert (TMP / "exists2.esc.h5").read_bytes() == b"keep", "existing file was modified"


# ---------------------------------------------------------------- C: files / handles
@check("C1", "MUST", "DataSet used as a context manager closes the h5 file even when the body raises")
def _():
    fn = TMP / "c1.esc.h5"
    ds = DataSet.create_with_new_result_file(fn, force_overwrite=True)
    try:
        with ds:
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert not bool(ds.results_file), "h5py file still open"


@check("C2", "SHOULD", "touching an array of a CLOSED file says so (not 'invalid identifier type to function')")
def _():
    fn = TMP / "c2.esc.h5"
    with DataSet.create_with_new_result_file(fn, force_overwrite=True) as ds:
        a = mk(np.arange(10.0))
        ds.append(a, name="m")
        a.store()
    r = DataSet.load_from_result_file(fn)
    arr = r.datasets["m"]
    r.results_file.close()
    exc = raises(lambda: np.asarray(arr.data))
    text = str(exc).lower()
    assert "closed" in text and "invalid identifier" not in text, f"{type(exc).__name__}: {exc}"


@check("C3", "SHOULD", "ArrayTimestamps.materialize() makes data/timestamps/scan usable after the file is closed")
def _():
    fn = TMP / "c3.esc.h5"
    with DataSet.create_with_new_result_file(fn, force_overwrite=True) as ds:
        a = mk(np.arange(10.0))
        ds.append(a, name="m")
        a.store()
    r = DataSet.load_from_result_file(fn)
    arr = r.datasets["m"]
    arr.materialize()
    r.results_file.close()
    assert np.asarray(arr.data).shape == (10,) and arr.timestamps.shape == (10,)
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    arr.scan.plot(axis=plt.figure().gca(), fmt="o-")


@check("C4", "SHOULD", "reading a timestamps-only (corrupt) group names the file and the group")
def _():
    fn = TMP / "c4.esc.h5"
    with h5py.File(fn, "w") as f:
        g = f.require_group("m")
        g.attrs["esc_type"] = "array_timestamps_dataset"
        g["timestamps_0000"] = TS
    r = DataSet.load_from_result_file(fn)
    exc = raises(lambda: r.datasets["m"].data)
    r.results_file.close()
    import re

    assert "c4.esc.h5" in str(exc) and re.search(r"['\"]/?m['\"]", str(exc)), f"{type(exc).__name__}: {exc}"


@check("C5", "NICE", "escape.storage.dataset.check_result_file(path) reports the corrupt group; repair=True fixes it")
def _():
    from escape.storage.dataset import check_result_file

    fn = TMP / "c4.esc.h5"
    report = check_result_file(fn)
    assert "m" in report and report["m"] != "ok", report
    check_result_file(fn, repair=True)
    assert check_result_file(fn).get("m", "ok") == "ok"


# ---------------------------------------------------------------- summary
print(f"escape {getattr(escape, '__version__', '?')} from {Path(escape.__file__).parent}")
width = max(len(r[2]) for r in RESULTS)
for status, tier, cid, desc, detail in RESULTS:
    print(f"{status} [{tier:6s}] {cid:<{width}} {desc}" + (f"\n        -> {detail}" if status == "FAIL" else ""))
n_fail = {t: sum(1 for r in RESULTS if r[0] == "FAIL" and r[1] == t) for t in ("MUST", "SHOULD", "NICE")}
print(f"\nfailed: MUST={n_fail['MUST']} SHOULD={n_fail['SHOULD']} NICE={n_fail['NICE']}  (of {len(RESULTS)} checks)")
sys.exit(1 if n_fail["MUST"] else 0)
```

## Appendix B: throwaway reference patch (diff against stock 0.2.14)

A scratch implementation of the MUST and SHOULD items (everything except
`check_result_file`), written only to prove that Appendix A is satisfiable and that files
stay compatible in both directions. With it, Appendix A reports `failed: MUST=0 SHOULD=0
NICE=1`. It applies cleanly (`patch -p1`) to a stock 0.2.14 tree. **Do not paste it in.**
It skips the `parse.py` delegation, the logging, the docs, `ArrayH5File`, the event-based
`Array`, the `store_file` fix and the tests, and its style is not escape's. Use it to see
the shape of the change.

```diff
--- a/escape/storage/storage_timestamps.py
+++ b/escape/storage/storage_timestamps.py
@@ -25,6 +25,20 @@
         return True
 
 
+def _coerce_data(data):
+    """list/tuple/array-likes -> ndarray; dask arrays and lazy callables untouched.
+    Object arrays made of None/numbers become float (None -> NaN)."""
+    if isinstance(data, (np.ndarray, da.Array)) or callable(data):
+        return data
+    arr = np.asarray(data)
+    if arr.dtype == object:
+        try:
+            arr = np.asarray(data, dtype=float)
+        except (TypeError, ValueError):
+            pass
+    return arr
+
+
 class ArrayTimestamps:
     """Time-stamped array wrapper with scan grouping and optional grid support.
 
@@ -50,9 +64,15 @@
         grid_specs=None,
         name="none",
     ):
-        self._data = data
+        self._data = _coerce_data(data)
         self._timestamps = timestamps
         self.name = name
+        if not (callable(self._data) or callable(timestamps)):
+            if len(self._data) != len(timestamps):
+                raise ValueError(
+                    f"ArrayTimestamps {name!r}: len(data)={len(self._data)} "
+                    f"!= len(timestamps)={len(timestamps)}"
+                )
         self.scan = ScanTimestamps(
             parameter=parameter,
             timestamp_intervals=timestamp_intervals,
@@ -69,12 +89,12 @@
         mirrors Array.data. A callable is invoked once and the result cached.
         """
         if callable(self._data):
-            self._data = self._data()
+            self._data = _coerce_data(self._data())
         return self._data
 
     @data.setter
     def data(self, value):
-        self._data = value
+        self._data = _coerce_data(value)
 
     @property
     def timestamps(self):
@@ -219,6 +239,13 @@
         self._timestamps = self.h5.timestamps
         self.scan._save_to_h5(self.h5.grp)
 
+    def materialize(self):
+        """Pull data, timestamps and scan metadata into memory so the array
+        stays usable after its results file is closed."""
+        self._data = np.asarray(self.data)
+        self.timestamps
+        return self
+
     def set_h5_storage(self, parent_h5py, name=None):
         if not hasattr(self, "h5"):
             if not name:
@@ -677,6 +704,12 @@
 class ArrayH5Dataset:
     def __init__(self, parent, name):
         self.parent = parent
+        if hasattr(parent, "id") and not parent.id.valid:
+            raise RuntimeError(
+                f"Cannot read {name!r}: its results file is closed. Keep the DataSet "
+                f"open (with DataSet.load_from_result_file(...) as ds), or call "
+                f".materialize() on the array before closing."
+            )
         try:
             self.grp = parent[name]
         except:
@@ -709,7 +742,10 @@
         # print(self._n_t,self._n_d)
         if not self._n_d == self._n_t:
             raise Exception(
-                "Corrupt escape ArrayH5Dataset, not equal numbered data and id sub-datasets!"
+                f"Corrupt escape ArrayH5Dataset {self.grp.name!r} in "
+                f"{self.grp.file.filename!r}: data_NNNN {self._n_d} vs "
+                f"timestamps_NNNN {self._n_t} do not match (a store was interrupted "
+                f"or failed after the timestamps were written)."
             )
 
     def clear_stored_data(self):
@@ -739,6 +775,12 @@
         if lock == "auto":
             lock = get_lock()
         n_new = len(self._n_t)
+        data = _coerce_data(data)
+        if isinstance(data, np.ndarray) and data.dtype == object:
+            raise TypeError(
+                f"Cannot store {self.grp.name!r}: data has object dtype (mixed or "
+                f"non-numeric values, e.g. None/strings); convert it to a numeric array first."
+            )
         timestamps_stored = self.timestamps
         in_previous_timestamps = np.isin(timestamps, timestamps_stored)
         if ~in_previous_timestamps.any():
@@ -758,8 +800,39 @@
 
             new_timestamps = timestamps[len(timestamps_stored) :]
             new_data = data[len(timestamps_stored) :, ...]
+        else:
+            raise ValueError(
+                f"Cannot append to {self.grp.name!r}: the new timestamps partially "
+                f"overlap the {len(timestamps_stored)} stored ones (neither a clean "
+                f"append nor a clean extend)."
+            )
+
+        if not isinstance(data, (np.ndarray, da.Array)):
+            raise TypeError(f"Cannot store {self.grp.name!r}: unsupported data type {type(data).__name__}")
+        # data first, timestamps last, and undo on failure: a half-written
+        # pair is what leaves a file that can no longer be read
+        created = []
+        try:
+            ret = self._write_pair(
+                n_new, new_timestamps, new_data, data, prep_run, lock, created, **kwargs
+            )
+            if ret is not None:  # prep_run: caller does the writing
+                return ret
+        except BaseException:
+            for key in created:
+                try:
+                    del self.grp[key]
+                except Exception:
+                    pass
+            raise
+        if scan:
+            scan._save_to_h5(self.grp)
+        self._n_t.append(n_new)
+        self._n_d.append(n_new)
 
+    def _write_pair(self, n_new, new_timestamps, new_data, data, prep_run, lock, created, **kwargs):
         self.grp[f"timestamps_{n_new:04d}"] = new_timestamps
+        created.append(f"timestamps_{n_new:04d}")
 
         if isinstance(data, np.ndarray):
             if prep_run:
@@ -809,10 +882,6 @@
             if prep_run:
                 return new_data, dset, n_new
             da.store(new_data, dset, lock=lock, **kwargs)
-        if scan:
-            scan._save_to_h5(self.grp)
-        self._n_t.append(n_new)
-        self._n_d.append(n_new)
 
     def get_data_da(self, memlimit_MB=50):
         allarrays = []
--- a/escape/storage/dataset.py
+++ b/escape/storage/dataset.py
@@ -84,6 +84,50 @@
     return new_array
 
 
+_RESULT_FILE_TYPE_SUFFIXES = {"h5": ".h5", "zarr": ".zarr"}
+
+
+def normalize_result_filepath(path, result_type=None, stacklevel=2):
+    """`path` as an escape results filename: `.esc` plus a type suffix.
+
+    * no escape suffix at all (`run`, `scan_0.5V`): `.esc.<type>` is added
+      silently. The type is `result_type` if given, else `h5`.
+    * an incomplete or mismatched suffix (`run.h5`, `run.esc`, `run.zarr`):
+      completed, with a `UserWarning` saying what was used instead.
+    * already complete: returned unchanged, no warning.
+    A dot that is not `.esc/.h5/.zarr` is part of the name (`scan_0.5V`).
+    """
+    path = Path(path)
+    suffixes = path.suffixes
+    if result_type is None:
+        # lenient, exactly what filespec_to_file accepts: never change a name
+        # that is valid today
+        if ".esc" in suffixes and (".h5" in suffixes or ".zarr" in suffixes):
+            return path
+    else:
+        if suffixes[-2:] == [".esc", _RESULT_FILE_TYPE_SUFFIXES[result_type]]:
+            return path
+    stem, given_type = path.name, None
+    while Path(stem).suffix in (".esc", ".h5", ".zarr"):
+        suf = Path(stem).suffix
+        if suf in (".h5", ".zarr") and given_type is None:
+            given_type = suf
+        stem = Path(stem).stem
+    if result_type is not None:
+        type_suffix = _RESULT_FILE_TYPE_SUFFIXES[result_type]
+    else:
+        type_suffix = given_type or ".h5"
+    new_path = path.with_name(stem + ".esc" + type_suffix)
+    if stem != path.name:  # something was there, but not the full pair
+        warnings.warn(
+            f"result file {path.name!r} has an incomplete escape suffix, "
+            f"expected '.esc{type_suffix}': using {new_path.name!r} instead.",
+            UserWarning,
+            stacklevel=stacklevel,
+        )
+    return new_path
+
+
 class DataSet:
     def __init__(
         self,
@@ -99,7 +143,11 @@
         self.datasets = {}
         self._esc_types = {}
 
+        self.results_filepath = None
         if results_file is not None:
+            if isinstance(results_file, (str, Path)):
+                results_file = normalize_result_filepath(results_file, stacklevel=3)
+                self.results_filepath = results_file
             # self.results_file = results_file
             self.results_file = filespec_to_file(results_file, mode=mode, perm=perm)
             self._init_datasets(lazy_loading=lazy_loading)
@@ -442,6 +490,8 @@
     def load_from_result_file(
         cls, results_filepath, lazy_loading=False, name=None, perm=None
     ):
+        if isinstance(results_filepath, (str, Path)):
+            results_filepath = normalize_result_filepath(results_filepath, stacklevel=3)
         ds = cls(
             results_file=results_filepath,
             name=name,
@@ -455,7 +505,10 @@
     def create_with_new_result_file(
         cls, results_filepath, mode="w", force_overwrite=False, name=None
     ):
+        results_filepath = normalize_result_filepath(results_filepath, stacklevel=3)
         if Path(results_filepath).exists() and not force_overwrite:
+            if not sys.stdin.isatty():
+                raise FileExistsError(f"{results_filepath} exists (not overwriting)")
             if (
                 input(
                     f"Filename {results_filepath} exists, would you like to overwrite its contents? (y/n)"
@@ -463,9 +516,8 @@
                 == "y"
             ):
                 mode="w"
-                pass
             else:
-                return
+                raise FileExistsError(f"{results_filepath} exists, overwriting declined")
 
         ds = cls(results_file=results_filepath, mode=mode, name=name)
         return ds
@@ -487,6 +539,10 @@
 
         elif ".zarr" in results_filepath.suffixes:
             result_file = zarr.open(results_filepath, mode=mode)
+        else:
+            raise ValueError(
+                f"{results_filepath}: expected a '.h5' or '.zarr' suffix after '.esc'"
+            )
         if perm is not None:
             print("changing perms")
             try:
```
