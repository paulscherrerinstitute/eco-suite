"""Look up a password in the shared Bernina gopass store, if available.

See /sf/bernina/bin/gopass.md for how the store itself is set up. This is
a thin, deliberately silent-on-failure bridge so elog.py/elog_scilog.py
can try gopass first and fall back to their existing mechanisms - not a
place to surface gopass setup problems to a user who never asked to know
about gopass.
"""
import sys

_GOPASS_BIN_DIR = "/sf/bernina/bin"


def get_gopass_password(path, store="bernina", timeout=10):
    """Return the decrypted password at `path` in the gopass `store`, or
    None if gopass/the entry/decrypt access isn't available. Callers
    should fall back to their own mechanism when this returns None."""
    if _GOPASS_BIN_DIR not in sys.path:
        sys.path.insert(0, _GOPASS_BIN_DIR)
    try:
        from gopass import GopassStore, GopassError
    except ImportError:
        print("import error")
        return None
    try:
        return GopassStore(store, timeout=timeout).get_password(path)
    except GopassError:
        print("gopass error")
        return None
