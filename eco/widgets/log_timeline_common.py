"""Backend-agnostic pieces shared by the Qt and HTML/Jupyter timeline
viewers (eco.widgets.log_timeline_qt, eco.widgets.log_timeline_html), so
eco.logs can feed either one the same shape of data regardless of whether
it came from a local kernel_logs JSONL file or a cheap scilog metadata
query. Zero Qt/IPython/scilog imports here on purpose -- cheap to import,
easy to unit test on its own (same rationale as kernel_registry.py).
"""
import bisect
from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass
class TimelineEntry:
    """One point on a timeline. `text` is what's shown in the list; for a
    source that's cheap to fetch in full (a local kernel_logs line) it's
    the whole entry. For a source where the full content is expensive (a
    scilog snippet body), `text` is a preview and `ref` is the id a caller
    can later pass to a `detail_fetcher(ref)` callback to lazily fetch the
    rest -- only once the entry is actually opened, not while building the
    timeline."""

    t: float  # epoch seconds
    kind: str  # e.g. "input" | "result" | "stream" | "error" | "session"
    #      | scilog's snippetType ("paragraph" | "image" | "file" | "task")
    text: str
    tags: Sequence[str] = field(default_factory=tuple)
    ref: Optional[str] = None
    is_error: bool = False


def bucket_density(entries, t0, t1, n_buckets=140):
    """(counts, has_error) over `n_buckets` equal time slices of [t0, t1].
    Feed `counts[i]` through `log(1 + counts[i])` for bucket height/weight
    to get the same elastic-axis behaviour as the log-timeline HTML
    viewer's minimap: buckets with nothing in them collapse toward zero
    weight, bursts get real width -- this is the one piece of math that
    must agree between the Qt minimap's paintEvent and the HTML/JS one,
    so it lives here instead of being re-derived twice."""
    span = (t1 - t0) or 1.0
    counts = [0] * n_buckets
    errors = [False] * n_buckets
    for e in entries:
        if e.t < t0 or e.t > t1:
            continue
        b = min(n_buckets - 1, max(0, int((e.t - t0) / span * n_buckets)))
        counts[b] += 1
        if e.is_error:
            errors[b] = True
    return counts, errors


def nearest_entry(entries, t):
    """The entry in `entries` (assumed sorted by `.t`) closest to time `t`
    -- what a minimap click/drag resolves to."""
    if not entries:
        return None
    ts = [e.t for e in entries]
    i = bisect.bisect_left(ts, t)
    if i == 0:
        return entries[0]
    if i == len(entries):
        return entries[-1]
    before, after = entries[i - 1], entries[i]
    return before if (t - before.t) <= (after.t - t) else after
