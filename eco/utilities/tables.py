"""Shared table rendering for assembly/status/memory reprs.

Wraps `tabulate` (the historical default) and an optional `rich`-based
renderer behind one function, so the output backend can be switched
globally via `eco.defaults.TABLE_FORMAT` without touching call sites.

    import eco
    eco.defaults.TABLE_FORMAT = "rich"   # wrap long columns to terminal width
    eco.defaults.TABLE_FORMAT = "tabulate"  # back to the old plain output
"""

import shutil

from tabulate import tabulate

import eco.defaults as defaults

_JUSTIFY = {
    "left": "left",
    "right": "right",
    "center": "center",
    "decimal": "right",
}


def format_table(rows, headers=None, tablefmt="simple", maxcolwidths=None, colalign=None):
    """Render `rows` (list of row lists) as a table string.

    Mirrors the subset of `tabulate`'s signature used across the codebase.
    Backend is chosen via `eco.defaults.TABLE_FORMAT` ("tabulate" or
    "rich"). `tablefmt="html"` (used for elog posts) always uses tabulate,
    since rich has no equivalent plain-<table> HTML export.
    """
    rows = list(rows)
    if defaults.TABLE_FORMAT != "rich" or tablefmt == "html":
        kwargs = {}
        if maxcolwidths is not None:
            kwargs["maxcolwidths"] = maxcolwidths
        if colalign is not None:
            kwargs["colalign"] = colalign
        return tabulate(rows, headers=headers or (), tablefmt=tablefmt, **kwargs)
    return _format_table_rich(
        rows, headers=headers, maxcolwidths=maxcolwidths, colalign=colalign
    )


def _format_table_rich(rows, headers=None, maxcolwidths=None, colalign=None):
    import io

    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    ncols = max((len(r) for r in rows), default=len(headers or []))

    table = Table(show_header=bool(headers), header_style="bold")
    for i in range(ncols):
        header = headers[i] if headers and i < len(headers) else ""
        max_width = None
        if maxcolwidths and i < len(maxcolwidths):
            max_width = maxcolwidths[i]
        justify = "left"
        if colalign and i < len(colalign):
            justify = _JUSTIFY.get(colalign[i], "left")
        table.add_column(
            str(header), max_width=max_width, overflow="fold", justify=justify
        )

    for row in rows:
        cells = [_to_cell(c) for c in row]
        cells += [""] * (ncols - len(cells))
        table.add_row(*cells)

    width = shutil.get_terminal_size(fallback=(120, 50)).columns
    console = Console(file=io.StringIO(), width=width)
    console.print(table)
    return console.file.getvalue().rstrip("\n")


def _to_cell(value):
    from rich.text import Text

    if isinstance(value, str) and "\x1b[" in value:
        return Text.from_ansi(value)
    return "" if value is None else str(value)
