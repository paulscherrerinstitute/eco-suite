"""Reciprocal-space calculations behind ``RecSpace``: ``escape.bernina.recspace``.

The calculations (Bernina's You geometry in the SwissFEL frame, UB from
reflections and orientations, solving with hklforge, virtual angles) live in
escape, shared with the bernina-scripts prep apps; this module re-exports
them under the old name, so ``from eco.utilities import recspace_calc as rc``
keeps working.

TEMPORARY: while the installed escape has no ``escape.bernina`` (it needs
escape with that subpackage and hklforge >= 0.1.0), eco falls back to its own
copy, ``_recspace_calc_local``. ``SOURCE`` says which one is in use. Remove
the fallback and that file once the beamline environments have both.
"""
try:
    from escape.bernina import recspace as _impl
    from escape.bernina import VIEWER_HTML
except Exception:          # no escape.bernina yet, or one that needs a newer hklforge
    from pathlib import Path

    from eco.utilities import _recspace_calc_local as _impl
    VIEWER_HTML = Path(__file__).with_name("diffractometer_3d.html")

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
SOURCE = _impl.__name__
__all__ = list(_impl.__all__) + ["VIEWER_HTML", "SOURCE"]
