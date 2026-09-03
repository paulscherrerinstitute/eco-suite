ELOG = None
ARCHIVER=None

# Table rendering backend used by eco.utilities.tables.format_table for
# assembly/status/memory reprs. "rich" (default) wraps long columns to the
# terminal width instead of letting them overflow; "tabulate" keeps the
# older plain-text output.
TABLE_FORMAT = "rich"

# Name column of assembly display tables (`Assembly.get_display_str()`, i.e.
# what `repr(obj)` shows). False (default): every row of a recursively
# unfolded sub-assembly carries its full dotted name (`ver.x`, `ver.y`, ...).
# True: the shared prefix becomes a header row and its children are indented
# below it as a tree. Per-call override: `obj.get_display_str(tree=True)` /
# `obj._display_text(tree=True)`; per-object: set `obj._display_tree = True`.
DISPLAY_TREE = False
