"""Per-pgroup table of all runs' status, built from the runs' status.json.

Every run's ``aux/status.json`` stays the original record. This module keeps
a derived table - one row per run, one column per status name - as a parquet
file next to the run table, and brings it up to date on every read by parsing
only the status files that are new or changed since. The file is a cache: it
can be deleted at any time and is rebuilt from the status files (once per
pgroup, ~30 s for 400 runs).

Measured on p21582 (413 runs, 5.3 GB of status.json, ~15 000 names):

- full rebuild from the JSON: 28 s; parquet 24 MB (the run table pickle with
  the same content is 184 MB)
- reading a few columns for all runs from the parquet: 0.45 s, everything 1.5 s
- cache + 5 new runs: ~2 s
- a lazily evaluated dask frame over the JSON files: 17-26 s per question,
  since JSON has no columns and every 14 MB file is parsed in full anyway

Values: parquet needs one type per column. Columns whose values are all
numbers (or None) are stored as float64, None as NaN; everything else (strings,
bools, lists, a column that is numeric in one run and text in another) as JSON
text, decoded again on read. The names of the JSON columns travel in the file's
schema metadata.

Several sessions may refresh the same file at once: each writes a private
temporary file and renames it over the cache. A run one of them added that the
other's rename drops is simply read again from its status.json next time.
"""

import glob
import json
import logging
import os
from datetime import datetime
from numbers import Number
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_JSON_COLUMNS_KEY = b"eco.json_columns"
_META_COLUMNS = ("_time", "_mtime", "_source")
SCAN_INFO_COLUMNS = ("scan_info.description", "scan_info.adjustables", "scan_info.steps")


def _is_number(v):
    return v is None or (isinstance(v, Number) and not isinstance(v, bool))


def _encode(v):
    return None if v is None else json.dumps(v, default=str)


def _decode(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return json.loads(v)


def _run_number(path):
    for part in reversed(Path(path).parts):
        if part.startswith("run") and part[3:].isdecimal():
            return int(part[3:])
    return None


def _row_from_status(block):
    """{name: value} for one run's status_run_start block, in any of the
    layouts status.json has had: since the collection rework (2025-11) only
    "status"; before that a separate "settings" dict next to it, which the
    status lacks some of (status wins where both have a name)."""
    row = dict(block.get("settings") or {})
    row.update(block.get("status") or {})
    return row


def _start_from_string(value, mtime):
    """Unix time from a `scans.start_time` string, or None.

    From 2025-07 to 2026-10 it was formatted "%Y-%M-%d %H:%M:%S" - the
    minutes where the month belongs - so the month is taken as the one that
    puts the start closest to the file time, which is right for both
    formats. Measured on p23926/p23620: the result is always 26 s or more
    before the file time, by the scan's length (median 70 s and 747 s).
    """
    try:
        y, _, rest = value.split("-", 2)
        d, hms = rest.split(" ")
    except (AttributeError, ValueError):
        return None
    best = None
    for month in range(1, 13):
        try:
            t = datetime.strptime(f"{y}-{month:02d}-{d} {hms}", "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue
        if best is None or abs(mtime - t) < abs(mtime - best):
            best = t
    # a start after the status was written, or days before it, is not this run's
    if best is None or best > mtime + 60 or mtime - best > 2 * 86400:
        return None
    return best


def _run_time(row, mtime):
    """Unix time of the run start: the scan start the DAQ client pushes with
    the status (server-backed scans), else the scan object's `start_time`,
    else the status file's modification time - which can be hours late for
    a file rewritten afterwards (p23926 run 89: 15 h)."""
    for key in ("bernina.scans.acquiring_scan.start_time", "scans.acquiring_scan.start_time"):
        t = row.get(key)
        if isinstance(t, Number) and not isinstance(t, bool) and t > 1e9:
            return float(t)
    for key in ("bernina.scans.start_time", "scans.start_time"):
        t = _start_from_string(row.get(key), mtime)
        if t is not None:
            return t
    return float(mtime)


def _scan_info_values(path):
    try:
        with open(path, "rb") as fh:
            info = json.loads(fh.read())
    except (OSError, ValueError):
        return {}
    names = (info.get("scan_parameters") or {}).get("name")
    return {
        "scan_info.description": info.get("scan_description"),
        "scan_info.adjustables": names,
        "scan_info.steps": len(info.get("scan_values_all") or info.get("scan_values") or []),
    }


class RunStore:
    """All runs of one pgroup as a table, kept in `cache_path` (parquet).

    status_dirs: directories holding `run<NNNN>/aux/status.json`, in order of
    preference (the DAQ client's res/run_data/daq copy first, it has every
    run; raw/ has only some since 2025).
    """

    def __init__(self, status_dirs, cache_path=None):
        self.status_dirs = [Path(d) for d in status_dirs]
        self.cache_path = Path(cache_path) if cache_path else None
        # the whole table, only loaded when runs have to be merged in (the
        # cache has ~15 000 columns: 5 s to load, against <0.5 s for the few
        # a question needs)
        self._frame = None
        # per run: _time, _mtime, _source - enough to know what is new
        self._meta = None
        self._columns = []
        self._json_columns = set()
        self.unreadable = {}
        self._failed_mtime = {}
        self._warned_write = False

    # ---------------------------------------------------------------- discover
    def find_runs(self):
        """{run: (status.json path, scan_info path or None, mtime)}, the
        first status directory that has a run winning."""
        found = {}
        for d in self.status_dirs:
            for p in glob.glob(str(d / "run*" / "aux" / "status.json")):
                run = _run_number(p)
                if run is None or run in found:
                    continue
                try:
                    mtime = os.stat(p).st_mtime
                except OSError:
                    continue
                info = None
                # stat the usual names; a directory listing per run is what
                # makes this slow on NFS
                for name in ("scan_info_rel.json", "scan_info.json"):
                    try:
                        mtime = max(mtime, os.stat(Path(p).parent / name).st_mtime)
                        info = Path(p).parent / name
                        break
                    except OSError:
                        pass
                found[run] = (Path(p), info, mtime)
        return found

    # ---------------------------------------------------------------- cache io
    def _cache_exists(self):
        return self.cache_path is not None and self.cache_path.exists()

    def _read_cache(self, columns=None):
        """Columns of the cache file (None: all), indexed by run."""
        import pyarrow.parquet as pq

        if columns is not None:
            columns = ["_run"] + [c for c in columns if c != "_run"]
        frame = pq.read_table(self.cache_path, columns=columns).to_pandas()
        frame = frame.set_index("_run")
        frame.index = frame.index.astype(int)
        frame.index.name = None
        # a dotted name may also select the columns below it; keep exactly
        # the ones asked for
        return frame if columns is None else frame.reindex(columns=columns[1:])

    def _load_meta(self):
        self._meta = pd.DataFrame(columns=list(_META_COLUMNS))
        self._columns = []
        self._json_columns = set()
        if not self._cache_exists():
            return
        import pyarrow.parquet as pq

        try:
            schema = pq.read_schema(self.cache_path)
            meta = self._read_cache(list(_META_COLUMNS))
        except Exception as exc:
            logger.warning("run store: ignoring unreadable cache %s (%s)", self.cache_path, exc)
            return
        self._json_columns = set(json.loads((schema.metadata or {}).get(_JSON_COLUMNS_KEY, b"[]")))
        self._columns = [c for c in schema.names if c != "_run" and c not in _META_COLUMNS]
        self._meta = meta

    def _load_full(self):
        self._frame = pd.DataFrame(columns=list(_META_COLUMNS))
        if self._cache_exists():
            try:
                self._frame = self._read_cache()
            except Exception as exc:
                logger.warning("run store: ignoring unreadable cache %s (%s)", self.cache_path, exc)
                self._json_columns = set()

    def _save_cache(self):
        if self.cache_path is None:
            return
        import pyarrow as pa
        import pyarrow.parquet as pq
        from eco.utilities.datafiles import ensure_dir, ensure_group_writable

        tmp = self.cache_path.with_name(f".{self.cache_path.name}.{os.getpid()}.tmp")
        try:
            ensure_dir(self.cache_path.parent)
            frame = self._frame.copy()
            frame.insert(0, "_run", frame.index.astype(int))
            table = pa.Table.from_pandas(frame, preserve_index=False)
            meta = dict(table.schema.metadata or {})
            meta[_JSON_COLUMNS_KEY] = json.dumps(sorted(self._json_columns)).encode()
            pq.write_table(table.replace_schema_metadata(meta), tmp)
            os.replace(tmp, self.cache_path)
            ensure_group_writable(self.cache_path)
        except OSError as exc:
            try:
                tmp.unlink()
            except OSError:
                pass
            if not self._warned_write:
                self._warned_write = True
                print(
                    f"run store: cannot write {self.cache_path} ({exc}); "
                    "keeping the table in this session only."
                )

    # ---------------------------------------------------------------- update
    def _read_run(self, status_path, info_path, mtime):
        try:
            with open(status_path, "rb") as fh:
                block = json.loads(fh.read())["status_run_start"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return None, f"{type(exc).__name__}: {exc}"
        row = _row_from_status(block)
        row["_time"] = _run_time(row, mtime)
        row["_mtime"] = float(mtime)
        row["_source"] = str(status_path)
        if info_path is not None:
            row.update(_scan_info_values(info_path))
        return row, None

    def _to_json_column(self, name):
        """Turn an existing numeric column into JSON text (a new run brought
        a non-numeric value for it)."""
        self._json_columns.add(name)
        if self._frame is not None and name in self._frame.columns:
            col = self._frame[name]
            self._frame[name] = [
                None if (v is None or (isinstance(v, float) and np.isnan(v))) else json.dumps(v)
                for v in col.tolist()
            ]

    def _merge(self, rows):
        names = {}
        for row in rows.values():
            for k in row:
                names[k] = None
        for name in names:
            if name in self._json_columns or name == "_source":
                continue
            if not all(_is_number(r.get(name)) for r in rows.values()):
                self._to_json_column(name)
        runs = sorted(rows)
        data = {}
        for name in names:
            values = [rows[r].get(name) for r in runs]
            if name == "_source":
                data[name] = values
            elif name in self._json_columns:
                data[name] = [_encode(v) for v in values]
            else:
                data[name] = np.array([np.nan if v is None else v for v in values], dtype=float)
        new = pd.DataFrame(data, index=pd.Index(runs, dtype=int))
        old = self._frame.drop(index=[r for r in runs if r in self._frame.index])
        frame = pd.concat([old, new]) if len(old) else new
        # json columns a run lacked come out NaN from the alignment
        for name in self._json_columns & set(frame.columns):
            frame[name] = frame[name].where(frame[name].notna(), None)
        self._frame = frame.sort_index()

    def refresh(self, verbose=True):
        """Bring the table up to date with the status files on disk."""
        if self._meta is None:
            self._load_meta()
        found = self.find_runs()
        cached = self._meta["_mtime"] if "_mtime" in self._meta.columns else pd.Series(dtype=float)
        # a file that failed to parse is tried again only once it changed
        todo = [
            r for r, (_, _, m) in found.items()
            if cached.get(r) != m and self._failed_mtime.get(r) != m
        ]
        if verbose and len(todo) > 20:
            print(f"run store: reading {len(todo)} run status files ...")
        rows = {}
        new_failures = []
        for run in sorted(todo):
            row, error = self._read_run(*found[run])
            if row is None:
                if run not in self.unreadable:
                    new_failures.append(run)
                self.unreadable[run] = error
                self._failed_mtime[run] = found[run][2]
                continue
            self.unreadable.pop(run, None)
            self._failed_mtime.pop(run, None)
            rows[run] = row
        if rows:
            if self._frame is None:
                self._load_full()
            self._merge(rows)
            self._save_cache()
            self._meta = self._frame[list(_META_COLUMNS)]
            self._columns = [c for c in self._frame.columns if c not in _META_COLUMNS]
        if verbose and new_failures:
            print(
                f"run store: {len(new_failures)} run status file(s) could not be "
                f"read: runs {new_failures} (see .unreadable)"
            )
        return self

    # ---------------------------------------------------------------- read
    @property
    def runs(self):
        if self._meta is None:
            self.refresh()
        return list(self._meta.index)

    def columns(self, prefix=None):
        if self._meta is None:
            self.refresh()
        cols = list(self._columns)
        if prefix:
            cols = [c for c in cols if c == prefix or c.startswith(prefix + ".")]
        return cols

    def table(self, columns=None, prefix=None, runs=None, refresh=True):
        """Decoded values, one row per run (index "run"), plus a "time"
        column (datetime of the run).

        columns: full names (as in status.json, e.g. "bernina.kb.ver.x");
        prefix: all names at or below this one; neither: every column.
        runs: run numbers, a slice over the available runs (e.g.
        slice(-50, None) for the last 50) or None for all.
        """
        if refresh or self._meta is None:
            self.refresh()
        cols = list(columns) if columns is not None else self.columns(prefix)
        known = set(self._columns)
        wanted = [c for c in cols if c in known]
        if self._frame is not None:
            source = self._frame.reindex(columns=wanted)
        elif self._cache_exists() and wanted:
            source = self._read_cache(wanted)
        else:
            source = pd.DataFrame(index=self._meta.index)
        index = select_runs(list(self._meta.index), runs)
        run_index = pd.Index(index, name="run")
        present = source.reindex(index=index)
        times = self._meta["_time"].reindex(index) if len(self._meta) else pd.Series(np.nan, index=index)
        # local wall-clock time, with the DST offset of each run's own date
        data = {
            "time": pd.Series(
                pd.to_datetime(
                    [pd.NaT if np.isnan(t) else datetime.fromtimestamp(t) for t in times.tolist()]
                ),
                index=run_index,
            )
        }
        for c in cols:
            if c not in present.columns:
                values = [None] * len(index)
            elif c in self._json_columns:
                values = [_decode(v) for v in present[c].tolist()]
            else:
                data[c] = pd.Series(present[c].to_numpy(), index=run_index)
                continue
            # an object Series: pandas 3 turns strings + None into its string
            # dtype otherwise, which stores None as NaN
            data[c] = pd.Series(values, index=run_index, dtype=object)
        return pd.DataFrame(data, index=run_index)


def plot_table(tab, x="time", ax=None, title=None, **plot_kwargs):
    """Plot every numeric column of a run table (`RunStore.table`) against
    the runs' time (x="time") or run number (x="run"), with the other one as
    a second x axis on top.

    The second axis comes from escape-fel's add_crossing_secondary_axis,
    which places its ticks where the (time, run) pairs actually are, so gaps
    (nights, beam downtime) and runs written out of order stay correct;
    without escape a plain interpolated axis is used.
    """
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    if x not in ("time", "run"):
        raise ValueError(f"x must be 'time' or 'run', not {x!r}")
    tab = tab[tab["time"].notna()] if x == "time" else tab
    if ax is None:
        _, ax = plt.subplots()
    xs = tab["time"] if x == "time" else tab.index
    plot_kwargs.setdefault("marker", ".")
    plotted = []
    for c in tab.columns:
        if c == "time":
            continue
        y = pd.to_numeric(tab[c], errors="coerce")
        if y.notna().sum() == 0:
            print(f"plot: '{c}' has no numeric values, skipped")
            continue
        ax.plot(xs, y, label=str(c), **plot_kwargs)
        plotted.append(c)
    if x == "time":
        locator = mdates.AutoDateLocator()
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    if not plotted:
        raise ValueError("nothing numeric to plot")
    if len(plotted) > 1:
        ax.legend()
    elif plotted[0] != "value":
        ax.set_ylabel(str(plotted[0]))
    if title:
        ax.set_title(title)
    ax.set_xlabel("time" if x == "time" else "run")

    with_time = tab[tab["time"].notna()]
    if len(with_time) < 2:
        return ax
    runs = np.asarray(with_time.index, dtype=float)
    tnum = mdates.date2num(list(with_time["time"]))
    if x == "time":
        x1, x2, label = tnum, runs, "run"
        fmt_ticks = lambda values: [f"{v:.0f}" for v in values]
    else:
        x1, x2, label = runs, tnum, "time"
        tfmt = "%d.%m %H:%M" if np.ptp(tnum) > 1 else "%H:%M"
        fmt_ticks = lambda values: [mdates.num2date(v).strftime(tfmt) for v in values]
    ticks = _secondary_tick_values(x1, x2)

    try:
        from escape.plot_utilities import add_crossing_secondary_axis
    except ImportError:
        add_crossing_secondary_axis = None
    if add_crossing_secondary_axis is not None:
        ax2, crossings = add_crossing_secondary_axis(
            ax, x1, x2, tick_values=ticks, x2_label=label,
            show_markers=False, show_drop_lines=False,
        )
        ax2.set_xticklabels(fmt_ticks([c.value for c in crossings]))
    else:
        order = np.argsort(x2)
        ax2 = ax.twiny()
        ax2.set_xlim(ax.get_xlim())
        ax2.set_xticks(np.interp(ticks, x2[order], x1[order]))
        ax2.set_xticklabels(fmt_ticks(ticks))
        ax2.set_xlabel(label)
    return ax


def _secondary_tick_values(x1, x2, n=6):
    """x2 values of the runs nearest to n evenly spaced positions along the
    primary axis. Runs come in bursts (hundreds of short scans in an hour,
    then nothing overnight), so ticks evenly spaced in x2 would pile up in
    one corner of the x1 axis; these stay spread out and each labels a real
    run."""
    targets = np.linspace(np.nanmin(x1), np.nanmax(x1), n)
    idx = sorted({int(np.nanargmin(np.abs(x1 - t))) for t in targets})
    return [x2[i] for i in idx]


def select_runs(available, runs):
    """`runs` as a list of run numbers out of `available` (sorted): None for
    all, a slice over the available runs, an int (negative counts from the
    newest), or an iterable of those."""
    available = sorted(available)
    if runs is None:
        return available
    if isinstance(runs, slice):
        return available[runs]
    if isinstance(runs, Number):
        runs = [runs]
    out = []
    for r in runs:
        r = int(r)
        if r < 0:
            r = available[r]
        out.append(r)
    return out
