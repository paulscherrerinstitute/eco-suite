ELOG = None
ARCHIVER=None

# Table rendering backend used by eco.utilities.tables.format_table for
# assembly/status/memory reprs. "tabulate" (default) keeps the existing
# plain-text output; "rich" wraps long columns to the terminal width
# instead of letting them overflow.
TABLE_FORMAT = "tabulate"