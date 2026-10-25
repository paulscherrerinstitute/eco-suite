"""Named, reusable windows for ``escape.stream.Stream`` live plots.

``Stream.plot_hist()/plot_med()/plot()/plot_corr()`` open an anonymous
``plt.subplots()`` window ("Figure 3") on every call when no ``axes`` is
given, so repeated calls pile up windows that can't be told apart. This
module patches those four methods (same idea as
``eco.detector.bs_counter.install_stream_scans``) so that, with no ``axes``
given, the window

- is opened with ``escape.plot_utilities.nfigure`` and **named after the
  plotted data**, e.g.
  ``hist: mon_mono.intensity.stream [SAROP21-PBPS103:INTENSITY]`` -- that
  string is also the window title;
- is **refreshed in place if a window of that name already exists** (same
  window, same position and size): the old live plot is stopped and its
  figure cleared (``nfigure(close_previous=False)``, never closed), then the
  new plot is built into it. Not closing matters: closing a live plot's
  window stops its stream's accumulation, and the ``plot_*`` methods start
  accumulating *before* they open the figure, so a close-and-reopen would
  leave the fresh plot with a switched-off stream.

An explicit ``axes=`` is left entirely to escape (untouched).

Names: the eco alias path when the stream has one (``stream.alias``), else
``stream.name``, plus the real dispatcher channel(s) it reads in brackets.
``stream.name`` alone is not unique in eco -- ``DetectorBsStream`` is named
after its short component name, so every ``.intensity`` monitor's stream is
called ``intensity_stream``. A figure with that label that this module did
not create (hand-made, or escape's own) is never reused or cleared; the new
window gets a `` (2)``, `` (3)`` ... suffix instead.

Installed by ``eco.detector.detectors_psi._ensure_bs_event_worker`` on the
first ``DetectorBsStream`` of a session, or call
``install_stream_plot_windows()`` yourself (idempotent). Set
``ENABLED = False`` to fall back to escape's behaviour without uninstalling.
"""

import functools
import inspect
import warnings

ENABLED = True

# Stream method -> short kind used in the window name.
_PLOT_METHODS = {
    "plot_hist": "hist",
    "plot_med": "median",
    "plot": "trace",
    "plot_corr": "corr",
}
_ORIGINALS = {}
_installed = False

# Attribute on a matplotlib Figure marking it as one of ours; holds the live
# plot object currently drawing into it.
_TAG = "_eco_stream_plot"


def _channels(stream):
    try:
        from escape.stream.escape_stream import _collect_source_channels

        return list(_collect_source_channels(stream._source))
    except Exception:
        return []


def stream_label(stream):
    """Unique-enough, human-readable label of a Stream: eco alias path (or
    ``stream.name``) plus the real channel(s) it reads, if not already the name."""
    name = None
    full_name = getattr(getattr(stream, "alias", None), "get_full_name", None)
    if full_name is not None:
        try:
            name = full_name()
        except Exception:
            name = None
    name = name or str(stream.name)
    chans = _channels(stream)
    if not chans or chans == [name]:
        return name
    shown = ", ".join(chans[:2]) + (f" +{len(chans) - 2}" if len(chans) > 2 else "")
    return f"{name} [{shown}]"


def window_name(kind, stream, xvar_label=None):
    """The figure name for ``kind`` of ``stream`` (``xvar_label``: already
    formatted label of the x stream, for ``corr``)."""
    label = stream_label(stream)
    if xvar_label is not None:
        label = f"{label} vs {xvar_label}"
    return f"{kind}: {label}"


def _new_figure(label):
    try:
        from escape.plot_utilities import nfigure
    except ImportError:  # escape.stream deliberately works without plot_utilities
        import matplotlib.pyplot as plt

        return plt.figure(num=label)
    return nfigure(num=label, close_previous=False)


def _retire(old_plot, keep):
    """Stop an old live plot's updates and release the streams it alone was
    keeping alive (what its window's close handler would do), but not the
    ones ``keep`` -- the new plot is about to use those."""
    try:
        old_plot.stop()
    except Exception:
        pass
    for stream in getattr(old_plot, "_escdata", []):
        if not any(stream is k for k in keep):
            try:
                stream.accumulate(False)
            except Exception:
                pass


def _open_window(name, keep):
    """Return ``(fig, axes)`` for the window called ``name``: a new one, or
    the existing one of ours cleared for reuse (see module docstring)."""
    import matplotlib.pyplot as plt

    n = 1
    while True:
        label = name if n == 1 else f"{name} ({n})"
        if label not in plt.get_figlabels():
            fig = _new_figure(label)
            break
        fig = plt.figure(label)
        old = getattr(fig, _TAG, None)
        if old is not None:
            _retire(old, keep)
            try:
                from escape.plot_utilities import _close_axes_guis

                _close_axes_guis(fig)  # fit/peak panels bound to the axes about to go
            except Exception:
                pass
            fig.clf()
            try:
                fig.canvas.manager.show()  # raise the existing window
            except Exception:
                pass
            break
        n += 1  # a figure with this label that isn't ours: leave it alone
    return fig, fig.add_subplot()


def _suptitle(kind, stream, kwargs):
    # plot_hist/plot_corr set their own suptitle even with axes given,
    # plot_med/plot only do so when they create the figure themselves.
    if kind == "median":
        return f"{kwargs.get('label') or stream.name}  median"
    if kind == "trace":
        return f"{stream.name}  (live)"
    return None


def _make_wrapper(method, kind, orig):
    sig = inspect.signature(orig)

    @functools.wraps(orig)
    def wrapper(self, *args, **kwargs):
        if not ENABLED:
            return orig(self, *args, **kwargs)
        try:
            bound = sig.bind(self, *args, **kwargs)
        except TypeError:
            return orig(self, *args, **kwargs)  # let escape raise its own error
        if bound.arguments.get("axes") is not None:
            return orig(self, *args, **kwargs)

        import matplotlib.pyplot as plt

        try:
            keep, xlabel = [self], None
            if method == "plot_corr":
                xvar = bound.arguments.get("xVar")
                if xvar is None:
                    default_x = bound.arguments.get("default_x", "lab_time")
                    xvar = getattr(self._source.eventWorker, default_x, None)
                if xvar is not None:
                    keep.append(xvar)
                    xlabel = stream_label(xvar)
            fig, axes = _open_window(window_name(kind, self, xlabel), keep)
        except Exception as exc:
            warnings.warn(
                f"{method}: could not open a named plot window ({exc!r}); "
                "falling back to escape's own window."
            )
            return orig(self, *args, **kwargs)

        bound.arguments["axes"] = axes
        try:
            plot = orig(*bound.args, **bound.kwargs)
        except BaseException:
            plt.close(fig)  # don't leave an empty, untagged window behind
            raise
        setattr(fig, _TAG, plot)
        title = _suptitle(kind, self, bound.arguments)
        if title and getattr(fig, "_suptitle", None) is None:
            fig.suptitle(title)
        return plot

    return wrapper


def install_stream_plot_windows():
    """Patch ``escape.stream.Stream.plot_hist/plot_med/plot/plot_corr`` (see
    module docstring). Idempotent."""
    global _installed
    if _installed:
        return
    from escape.stream import Stream

    for method, kind in _PLOT_METHODS.items():
        orig = getattr(Stream, method)
        _ORIGINALS[method] = orig
        setattr(Stream, method, _make_wrapper(method, kind, orig))
    _installed = True


def uninstall_stream_plot_windows():
    """Restore escape's own methods (mainly for tests)."""
    global _installed
    if not _installed:
        return
    from escape.stream import Stream

    for method, orig in _ORIGINALS.items():
        setattr(Stream, method, orig)
    _ORIGINALS.clear()
    _installed = False
