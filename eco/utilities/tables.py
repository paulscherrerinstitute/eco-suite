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

#: Row-background palette for `section_row_styles`: one (base, alt) pair of
#: dark, muted `rich` style strings per group, cycled in first-seen order.
#:
#: `_format_table_rich` renders at `color_system="256"`, whose colour cube
#: only steps at 0/95/135/175/215/255 (0x00/0x5f/0x87/0xaf/0xd7/0xff) -- hex
#: values built from finer steps collapse to the same nearest cube corner and
#: become indistinguishable (a first draft of this palette, built from
#: "reasonable-looking" dark hex values a few units apart, rendered all 6
#: "different" section colours as the *same* one). Built from cube-safe
#: levels only and individually verified distinct via
#: `rich.color.Color(...).downgrade(ColorSystem.EIGHT_BIT)`.
DEFAULT_SECTION_PALETTE = [
    ("on #00005f", "on #000087"),  # blue-grey
    ("on #005f00", "on #008700"),  # green
    ("on #5f005f", "on #5f0087"),  # purple
    ("on #5f5f00", "on #875f00"),  # amber
    ("on #5f0000", "on #870000"),  # red/maroon
    ("on #005f5f", "on #008787"),  # teal
]

#: fallback alternation for rows that aren't part of a real multi-row group
#: (see `section_row_styles`) -- identical to the old hardcoded default so a
#: flat, unnested table's look is unchanged.
_PLAIN_ALTERNATION = ("", "on grey30")


def section_row_styles(section_keys, palette=None):
    """Turn a list of "which group does this row belong to" keys (one per
    row, in row order; e.g. a beamline section name, or the top-level
    sub-assembly a nested/"unfolded" status row descends from) into a matching
    list of `rich` row-background styles, suitable for
    `format_table(..., row_styles=...)`.

    Each *distinct* key that covers more than one row is assigned the next
    (base, alt) pair from `palette` (default `DEFAULT_SECTION_PALETTE`,
    cycled in first-seen order), and consecutive rows sharing that key
    alternate between its two shades -- so e.g. a beamline's sections, or a
    sub-assembly's several displayed properties, read as distinct blocks while
    staying individually easy to track by eye.

    A key that covers only a *single* row does not claim a whole palette
    colour for itself -- that would just tint every unrelated one-line entry
    a different colour (all noise, no signal, for the common case of a flat
    assembly with no nested structure). Singletons instead get the plain
    two-shade alternation tables always used before this existed, so a flat
    table's look is unchanged; the palette only kicks in where there is an
    actual group of rows worth setting apart.
    """
    palette = palette or DEFAULT_SECTION_PALETTE
    counts = {}
    for key in section_keys:
        counts[key] = counts.get(key, 0) + 1

    styles = []
    group_shades = {}  # key -> (base, alt), assigned in first-seen order
    group_seen = {}  # key -> rows seen so far, for its base/alt alternation
    n_singleton = 0
    for key in section_keys:
        if counts[key] <= 1:
            styles.append(_PLAIN_ALTERNATION[n_singleton % 2])
            n_singleton += 1
            continue
        shades = group_shades.setdefault(key, palette[len(group_shades) % len(palette)])
        i = group_seen.get(key, 0)
        group_seen[key] = i + 1
        styles.append(shades[i % 2])
    return styles


def _patch_rich_emoji_width():
    """Make rich measure emoji-presentation glyphs at their *bare* width.

    Our terminals render "base + U+FE0F" (✏️ 👁️ ⚠️) at the same width as the
    bare base — 1 cell — rather than the wide 2-cell colour-emoji form. rich
    15, though, promotes such sequences to 2 cells (older rich counted 1), so
    on these terminals rich 15 over-reserves and every emoji cell drifts one
    column right (worst seen on wrapped rows, where the no-emoji continuation
    line stays correct while the emoji line does not). Measured directly off
    the live terminal via cursor-position reporting — see glyph_width_probe.py.

    We fix rich's *measurement*, never the displayed text (which keeps its
    colour emoji): strip U+FE0F before measuring so rich sees the bare-glyph
    width the terminal actually draws. rich 15 funnels every width query
    through `_cell_len`, so wrapping that one function covers column sizing,
    padding and wrapping alike. On rich versions without it we do nothing —
    their default already counts these as 1. Wrapped defensively so a future
    rich refactor can never break import.
    """
    try:
        import rich.cells as rc

        if getattr(rc, "_eco_emoji_patched", False):
            return

        orig = getattr(rc, "_cell_len", None)
        if orig is None:
            return  # older rich already measures these as one cell

        def stripped_cell_len(text, *args, _orig=orig, **kwargs):
            if "️" in text:
                text = text.replace("️", "")
            return _orig(text, *args, **kwargs)

        rc._cell_len = stripped_cell_len
        if hasattr(rc.cached_cell_len, "cache_clear"):
            rc.cached_cell_len.cache_clear()

        rc._eco_emoji_patched = True
    except Exception:
        pass


_patch_rich_emoji_width()


def format_table(rows, headers=None, tablefmt="simple", maxcolwidths=None, colalign=None, row_styles=None):
    """Render `rows` (list of row lists) as a table string.

    Mirrors the subset of `tabulate`'s signature used across the codebase.
    Backend is chosen via `eco.defaults.TABLE_FORMAT` ("tabulate" or
    "rich"). `tablefmt="html"` (used for elog posts) always uses tabulate,
    since rich has no equivalent plain-<table> HTML export.

    `row_styles` (rich backend only): styles to cycle per row, passed straight
    through to `rich.table.Table(row_styles=...)`. Default is a 2-entry
    alternating shade (plain repr/status tables); pass a list with **exactly
    one style per row** (e.g. a background colour per section, precomputed by
    the caller) to give every row its own style instead of a repeating
    2-cycle -- rich cycles `row_styles[i % len(row_styles)]`, which is just
    `row_styles[i]` once the list is as long as `rows`.
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
        rows, headers=headers, maxcolwidths=maxcolwidths, colalign=colalign,
        row_styles=row_styles,
    )


def _format_table_rich(rows, headers=None, maxcolwidths=None, colalign=None, row_styles=None):
    import io

    from rich.console import Console
    from rich.table import Table

    ncols = max((len(r) for r in rows), default=len(headers or []))

    table = Table(
        show_header=bool(headers),
        header_style="bold",
        box=None,
        row_styles=list(row_styles) if row_styles is not None else list(_PLAIN_ALTERNATION),
    )
    for i in range(ncols):
        header = headers[i] if headers and i < len(headers) else ""
        max_width = None
        if maxcolwidths and i < len(maxcolwidths):
            max_width = maxcolwidths[i]
        justify = "left"
        if colalign and i < len(colalign):
            justify = _JUSTIFY.get(colalign[i], "left")
        table.add_column(
            str(header),
            max_width=max_width,
            overflow="fold",
            justify=justify,
        )

    for row in rows:
        cells = [_to_cell(c) for c in row]
        cells += [""] * (ncols - len(cells))
        table.add_row(*cells)

    width = shutil.get_terminal_size(fallback=(120, 50)).columns
    # force_terminal: we render into a StringIO, which isn't a tty, so rich
    # would otherwise strip all styling (row shading, bold header) from the
    # returned string before it ever reaches the real terminal via print().
    console = Console(file=io.StringIO(), width=width, force_terminal=True, color_system="256")
    console.print(table)
    return console.file.getvalue().rstrip("\n")


def _to_cell(value):
    # Emoji width (✏️ 👁️ ⚠️) is handled by _patch_rich_emoji_width above, so
    # cells need no manual padding here.
    from rich.text import Text

    if isinstance(value, str) and "\x1b[" in value:
        return Text.from_ansi(value)
    if value is None:
        return ""
    return str(value)
