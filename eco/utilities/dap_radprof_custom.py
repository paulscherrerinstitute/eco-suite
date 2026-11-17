"""Radial profile for sf-daq's dap, as a custom DAP script (numpy only).

A drop-in replacement for dap's built-in radial integration
(``do_radial_integration``) without its two problems - see
``eco.utilities.dap_radial_profile`` for the bug description:

- dap uses Python's builtin ``min``/``max`` on the per-pixel radius array:
  ~140 ms per frame for a 1.5 Mpx JUNGFRAU, ~1.5 s for the 16M;
- with ``apply_threshold`` dap sets thresholded pixels to NaN before
  ``np.bincount``, so every ring holding one becomes NaN - with
  ``threshold_min`` below the photon energy the whole profile is NaN.

Here NaN pixels (thresholded or NaN in the image) leave both the sum and
the pixel count of their ring. The output is dap's: ``radint_I`` (mean per
ring of one pixel, from ``r_min`` up to the largest unmasked radius) and
``radint_q`` = [r_min, r_max] in pixels, plus ``radint_normalised`` with the
silent-region normalization. Parameters come from the dap config, as for
the built-in version: ``beam_center_x/y``, ``apply_threshold``,
``threshold_min/max``, ``radial_integration_silent_min/max``.

Upload and enable (dap prefixes the output channels with the script name)::

    from eco.utilities import dap_radprof_custom
    jf.upload_custom_dap_script(dap_radprof_custom.__file__)
    jf.set_dap_settings({"custom_script": "bernina:dap_radprof_custom",
                         "do_radial_integration": 0})    # skip dap's own, slow version

Only numpy is imported: dap execs custom scripts on its workers, and eco
only uploads scripts with allowed imports (``check_dap_script_imports``).
The ring indices are prepared once per worker for each (shape, beam centre,
mask) and reused for every frame.
"""
import numpy as np

_CACHE = {}


def _prepare(shape, x0, y0, mask):
    """Ring index per pixel (masked pixels in an extra trash ring) and the
    number of pixels per ring; cached per (shape, centre, mask)."""
    # dap's pixel mask is one object per pedestal/mask setting: key on its
    # identity plus a cheap sample of its content (id() alone could be reused)
    mkey = None if mask is None else (id(mask), mask.shape, mask.ravel()[::4099].tobytes())
    key = (tuple(shape), float(x0), float(y0), mkey)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    y, x = np.indices(shape)
    rad = np.sqrt((x - x0) ** 2 + (y - y0) ** 2).astype(np.intp).ravel()
    keep = None if mask is None else np.asarray(mask, bool).ravel()
    nbins = int(rad.max()) + 1
    used = rad if keep is None else rad[keep]
    r_min, r_max = int(used.min()), int(used.max()) + 1
    if keep is not None:
        rad = np.where(keep, rad, nbins)                     # trash ring for masked pixels
    count = np.bincount(rad, minlength=nbins + 1)[:nbins].astype(float)
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[key] = hit = (rad, count, nbins, r_min, r_max)
    return hit


def radial_profile(image, mask, x0, y0, threshold_min=None, threshold_max=None):
    """(profile per ring from r_min to r_max - 1, r_min, r_max); rings of one
    pixel around (x0, y0) [pixels]. Pixels outside [threshold_min,
    threshold_max) and NaN pixels are left out of their ring's mean."""
    rad, count, nbins, r_min, r_max = _prepare(image.shape, x0, y0, mask)
    d = image.ravel()
    bad = np.isnan(d)
    if threshold_min is not None and threshold_max is not None:
        threshold_min, threshold_max = sorted((threshold_min, threshold_max))
    if threshold_min is not None:
        bad = bad | (d < threshold_min)
    if threshold_max is not None:
        bad = bad | (d >= threshold_max)
    if bad.any():
        good = ~bad
        s = np.bincount(rad[good], d[good], nbins + 1)[:nbins]
        n = count - np.bincount(rad[bad], minlength=nbins + 1)[:nbins]
    else:
        s = np.bincount(rad, d, nbins + 1)[:nbins]
        n = count
    with np.errstate(invalid="ignore", divide="ignore"):
        prof = s / n
    return prof[r_min:r_max], r_min, r_max


def proc(results, image, pixel_mask):
    """dap custom-script entry point: dap's radint_I / radint_q, fixed."""
    x0 = results.get("beam_center_x")
    y0 = results.get("beam_center_y")
    if x0 is None or y0 is None:
        return {}
    tmin = tmax = None
    if results.get("apply_threshold", False):
        tmin, tmax = results.get("threshold_min"), results.get("threshold_max")
    prof, r_min, r_max = radial_profile(image, pixel_mask, x0, y0, tmin, tmax)
    out = {"radint_q": [r_min, r_max]}
    smin = results.get("radial_integration_silent_min")
    smax = results.get("radial_integration_silent_max")
    if smin is not None and smax is not None:
        smin, smax = sorted((int(smin), int(smax)))
        if smin > r_min and smax < r_max:
            # dap indexes the full profile (from radius 0) here, before cutting at r_min
            silent = np.nansum(prof[smin - r_min:smax - r_min])
            prof = prof / silent
            out["radint_normalised"] = [smin, smax]
    out["radint_I"] = prof.tolist()
    return out
