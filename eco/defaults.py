ELOG = None
ARCHIVER=None

# Table rendering backend used by eco.utilities.tables.format_table for
# assembly/status/memory reprs. "rich" (default) wraps long columns to the
# terminal width instead of letting them overflow; "tabulate" keeps the
# older plain-text output.
TABLE_FORMAT = "rich"