"""Shared-account-safe temp file naming.

Stdlib-only (no eco/EPICS/GUI imports) so it's safe to import from any code
path, including ones that otherwise avoid heavy optional dependencies.
"""

import os
import getpass
import tempfile


def user_temp_svg_path(prefix, name):
    """A stable, per-instance temp file path for a live-refreshed SVG panel
    (``<prefix>_<user>_<name>.svg`` in the OS temp dir), namespaced by the
    OS username so two different accounts running on the same host (e.g. a
    personal checkout next to the shared ``gac-bernina`` console account)
    never collide on each other's file in ``/tmp`` -- whichever account
    creates the file first owns it, and the other then gets a
    ``PermissionError`` trying to rewrite it on its own next live refresh.
    Deliberately deterministic (not `tempfile.mkstemp`'s random name) so
    repeated calls for the same object keep overwriting the same path --
    that's what lets an already-open viewer refresh in place.
    """
    return os.path.join(
        tempfile.gettempdir(), f"{prefix}_{getpass.getuser()}_{name}.svg"
    )
