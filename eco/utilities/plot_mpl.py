import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure


def resolve_plot_axes(plot, *, default="new", **newfig_kwargs):
    """The shared "plot on my axis/figure/last plot" convention for eco's
    various `plot=`/`ax=`-taking methods (archiver time ranges, run tables,
    beamline layouts, ...). Turns whatever was passed as `plot` into the
    Axes to actually draw on, or None to skip plotting -- callers do
    `ax = resolve_plot_axes(plot); if ax is None: return` and then draw
    into `ax` themselves.

    `plot` may be:
      - an existing Matplotlib Axes -- draw into it, e.g. `plot=plt.gca()`
        for "my current axes" / "the last plot" -- or `plot=some_ax` for a
        specific one kept around from before
      - an existing Matplotlib Figure -- use its current axes
      - True -- open a new figure (`plt.subplots(**newfig_kwargs)`)
      - False -- don't plot
      - None -- `default` decides: "new" (the `ax=None` convention: behave
        like True) or "skip" (the `plot=None` convention: behave like
        False)
    """
    if plot is None:
        plot = default == "new"
    if plot is False:
        return None
    if plot is True:
        _, ax = plt.subplots(**newfig_kwargs)
        return ax
    if isinstance(plot, Axes):
        return plot
    if isinstance(plot, Figure):
        return plot.gca()
    raise TypeError(
        "plot= must be a bool, a Matplotlib Axes or a Figure, not "
        f"{type(plot)!r}"
    )


def identifyContrastplotData(x, y, i):
    assert i.ndim == 2, "Intensity data needs to be 2 dimensional!"
    assert (
        x.ndim == y.ndim
    ), "please provide x and y plotting coordinate in same dimension"
