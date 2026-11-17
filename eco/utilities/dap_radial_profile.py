"""Two problems in sf-daq's dap radial integration, with a fix and a check.

dap (https://gitea.psi.ch/sf-daq/dap) is the online pipeline that runs on
the JUNGFRAU streams at SwissFEL. Its radial integration
(``do_radial_integration``, ``dap/algos/radprof.py``) has two problems,
found while benchmarking escape.bernina.rebin against it (dap at f7856ed,
2026-10; both still there as of 2026-11-17):

1. Builtin ``min``/``max`` over every pixel (performance)
---------------------------------------------------------
``calc_radial_integration`` does::

    rad, norm = prepare_radial_profile(data.shape, center_x, center_y, pixel_mask)
    r_min = min(rad)
    r_max = max(rad) + 1

``rad`` is a numpy array with one entry per unmasked pixel. Python's builtin
``min``/``max`` iterate over it element by element in the interpreter,
creating a numpy scalar for every pixel. This runs on every frame, although
``prepare_radial_profile`` itself is memoized. Measured on one core, the
cost dap's workers pay per frame:

=====================  ==========  ==================  ====================
detector               dap         dap with            escape (numba, one
                                   rad.min()/max()     thread), same result
=====================  ==========  ==================  ====================
JF01T03 (1.6 Mpx)      144 ms      8 ms                1.7 ms
JF07T32 (18.7 Mpx)     1.9 s       0.37 s              25 ms
=====================  ==========  ==================  ====================

At 100 Hz, every worker doing radial integration loses ~14 frames' worth of
time per frame on the 1.5M detector. Fix: ``rad.min()`` / ``rad.max()``. The
values and their types (numpy integers) stay exactly the same.

2. Threshold turns rings into NaN (wrong result)
------------------------------------------------
With ``apply_threshold`` the integration does::

    data = calc_apply_threshold(results, data, value=np.nan, copy=True)
    ...
    tbin = np.bincount(rad, data)      # NaN in -> NaN sum
    rp = tbin / norm                   # norm still counts the NaN pixels

Every pixel outside [threshold_min, threshold_max) becomes NaN, and one NaN
makes its ring's sum NaN. ``norm`` (pixel count per ring) is not reduced
either. Consequences:

- ``threshold_min`` below the photon energy, the usual way to suppress
  noise, removes some pixel in nearly every ring: ``radint_I`` is NaN
  almost everywhere (100% of rings in the synthetic test below).
- Cutting only a few hot pixels with ``threshold_max`` blanks their rings
  (20 hot pixels: ~56 NaN rings on a 1.5M detector).
- ``radial_integration_silent_min/max`` normalizes with ``np.sum`` over the
  silent region, so one NaN ring there makes the whole profile NaN.

Fix: leave NaN pixels out of both the sum and the count of their ring
(``UPSTREAM_PATCH`` below). That is the mean over the pixels that passed the
threshold. A ring with no pixel left stays NaN.

What is here
------------
- ``UPSTREAM_PATCH``: the fix for dap (two changes in ``radprof.py``,
  numpy only, output unchanged without NaN). Not yet reported upstream.
- ``eco.utilities.dap_radprof_custom``: the fixed radial profile as a dap
  custom script, a workaround usable today (see its docstring for upload).
- ``reproduce(dap_src)``: runs dap's own ``radprof.py`` from a checkout
  (unchanged), the fixed version, and the patched dap side by side on
  synthetic JUNGFRAU-like frames, and prints the evidence for both issues::

      from eco.utilities.dap_radial_profile import reproduce
      reproduce("~/mypy/dap")                      # git clone https://gitea.psi.ch/sf-daq/dap

Needs only numpy (and a dap checkout for ``reproduce``).
"""
import importlib
import sys
import time
import types
from pathlib import Path

import numpy as np

from . import dap_radprof_custom

UPSTREAM_PATCH = '''\
--- a/dap/algos/radprof.py
+++ b/dap/algos/radprof.py
@@ -14,8 +14,9 @@
 
     rad, norm = prepare_radial_profile(data.shape, center_x, center_y, pixel_mask)
 
-    r_min = min(rad)
-    r_max = max(rad) + 1
+    # numpy's min/max: the builtins loop over every pixel in python (~140 ms for 1.5 Mpx)
+    r_min = rad.min()
+    r_max = rad.max() + 1
 
     data = calc_apply_threshold(results, data, value=np.nan, copy=True)
 
@@ -53,8 +54,17 @@
     if keep_pixels is not None:
         data = data[keep_pixels]
     data = data.ravel()
-    tbin = np.bincount(rad, data)
-    rp = tbin / norm
+    nans = np.isnan(data)
+    if nans.any():
+        # NaN pixels (e.g. set by the threshold) leave their ring's sum and count,
+        # instead of turning the whole ring NaN
+        valid = ~nans
+        tbin = np.bincount(rad[valid], data[valid], minlength=len(norm))
+        norm = norm - np.bincount(rad[nans], minlength=len(norm))
+    else:
+        tbin = np.bincount(rad, data, minlength=len(norm))
+    with np.errstate(invalid="ignore", divide="ignore"):
+        rp = tbin / norm
     return rp
 
 
'''


def load_dap_radprof(dap_src):
    """dap's ``dap/algos/radprof.py`` from a checkout, as a module, without
    running ``dap/algos/__init__.py`` (which needs jungfrau_utils, zmq and
    the compiled peakfinder)."""
    algos = Path(dap_src).expanduser() / "dap" / "algos"
    if not (algos / "radprof.py").is_file():
        raise FileNotFoundError(f"no dap/algos/radprof.py below {dap_src}")
    name = "_dap_algos_" + str(abs(hash(str(algos))))
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(algos)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".radprof")


def patched_calc_radial_integration(radprof):
    """dap's calc_radial_integration with ``UPSTREAM_PATCH`` applied, built
    from the loaded dap module's own helpers (for comparison)."""
    np_ = radprof.np

    def radial_profile(data, rad, norm, keep_pixels):
        if keep_pixels is not None:
            data = data[keep_pixels]
        data = data.ravel()
        nans = np_.isnan(data)
        if nans.any():
            valid = ~nans
            tbin = np_.bincount(rad[valid], data[valid], minlength=len(norm))
            norm = norm - np_.bincount(rad[nans], minlength=len(norm))
        else:
            tbin = np_.bincount(rad, data, minlength=len(norm))
        with np_.errstate(invalid="ignore", divide="ignore"):
            return tbin / norm

    def calc(results, data, pixel_mask):
        if not results.get("do_radial_integration", False):
            return
        x0, y0 = results["beam_center_x"], results["beam_center_y"]
        rad, norm = radprof.prepare_radial_profile(data.shape, x0, y0, pixel_mask)
        r_min, r_max = rad.min(), rad.max() + 1
        data = radprof.calc_apply_threshold(results, data, value=np_.nan, copy=True)
        rp = radial_profile(data, rad, norm, pixel_mask)
        smin = results.get("radial_integration_silent_min", None)
        smax = results.get("radial_integration_silent_max", None)
        if smin is not None and smax is not None:
            smin, smax = sorted((smin, smax))
            if smin > r_min and smax < r_max:
                rp = rp / np_.sum(rp[smin:smax])
                results["radint_normalised"] = [smin, smax]
        results["radint_I"] = rp[r_min:]
        results["radint_q"] = [r_min, r_max]

    return calc


def synthetic_frames(shape, n=3, x0=None, y0=None, seed=0):
    """JUNGFRAU-like frames in keV: noise around 0, 9 keV photons with a
    powder ring; a mask with 0.5% bad pixels. Returns (frames, mask, x0, y0)."""
    rng = np.random.default_rng(seed)
    x0 = shape[1] * 0.45 if x0 is None else x0
    y0 = shape[0] * 0.55 if y0 is None else y0
    y, x = np.indices(shape)
    r = np.sqrt((x - x0) ** 2 + (y - y0) ** 2)
    ring = 2.0 * np.exp(-((r - 0.3 * max(shape)) / 20) ** 2)
    frames = (rng.normal(0, 0.3, (n,) + shape) + rng.poisson(ring + 0.02, (n,) + shape) * 9.0).astype(np.float32)
    mask = rng.random(shape) > 0.005
    return frames, mask, x0, y0


def _time(fn, frames):
    fn(frames[0])
    t = time.perf_counter()
    for f in frames:
        fn(f)
    return (time.perf_counter() - t) / len(frames) * 1e3


def reproduce(dap_src, shape=(1614, 1030), n=3, verbose=True):
    """Run dap's radial integration (from the checkout ``dap_src``,
    unchanged), the patched version and the custom script on synthetic
    frames of ``shape`` (default: the 1.5M JF01T03 image), and report both
    issues. Returns a dict of the numbers."""
    radprof = load_dap_radprof(dap_src)
    patched = patched_calc_radial_integration(radprof)
    frames, mask, x0, y0 = synthetic_frames(shape, n)
    base = dict(do_radial_integration=1, beam_center_x=x0, beam_center_y=y0)
    thr = dict(base, apply_threshold=1, threshold_min=2.0)

    def run(calc, img, cfg):
        res = dict(cfg)
        calc(res, img, mask)
        return np.asarray(res["radint_I"], float)

    out = {}
    a, b = run(radprof.calc_radial_integration, frames[0], base), run(patched, frames[0], base)
    c = np.asarray(dap_radprof_custom.proc(base, frames[0], mask)["radint_I"])
    out["no_threshold_identical"] = bool(np.array_equal(a, b, equal_nan=True) and np.allclose(a, c, equal_nan=True,
                                                                                             rtol=1e-12))
    out["threshold_nan_rings_dap"] = float(np.isnan(run(radprof.calc_radial_integration, frames[0], thr)).mean())
    out["threshold_nan_rings_fixed"] = float(np.isnan(run(patched, frames[0], thr)).mean())
    hot = frames[0].copy()
    hot.ravel()[np.random.default_rng(1).choice(hot.size, 20, replace=False)] = 1e4
    out["hot_pixel_nan_rings_dap"] = int(np.isnan(run(radprof.calc_radial_integration, hot,
                                                      dict(base, apply_threshold=1, threshold_max=50.0))).sum())
    rad = np.asarray(radprof.prepare_radial_profile(shape, x0, y0, mask)[0])
    out["ms_builtin_min_max"] = _time(lambda f: (min(rad), max(rad)), frames)
    out["ms_dap"] = _time(lambda f: run(radprof.calc_radial_integration, f, base), frames)
    out["ms_dap_patched"] = _time(lambda f: run(patched, f, base), frames)
    out["ms_custom_script"] = _time(lambda f: dap_radprof_custom.proc(base, f, mask), frames)
    out["ms_dap_threshold"] = _time(lambda f: run(radprof.calc_radial_integration, f, thr), frames)
    out["ms_dap_patched_threshold"] = _time(lambda f: run(patched, f, thr), frames)
    if verbose:
        print(f"dap radial integration, {shape[0]} x {shape[1]} = {np.prod(shape) / 1e6:.2f} Mpx, from {dap_src}")
        print(f"  without threshold, patched and custom script == dap: {out['no_threshold_identical']}")
        print(f"  1. per frame: dap {out['ms_dap']:.1f} ms, of which builtin min/max {out['ms_builtin_min_max']:.1f} ms;"
              f" patched {out['ms_dap_patched']:.1f} ms; custom script {out['ms_custom_script']:.1f} ms")
        print(f"  2. threshold_min=2: NaN rings dap {out['threshold_nan_rings_dap']:.0%},"
              f" patched {out['threshold_nan_rings_fixed']:.1%} (rings with no pixel left);"
              f" 20 hot pixels cut by threshold_max: {out['hot_pixel_nan_rings_dap']} NaN rings in dap")
        print(f"     with threshold: dap {out['ms_dap_threshold']:.1f} ms, patched {out['ms_dap_patched_threshold']:.1f} ms")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dap_src", nargs="?", help="path of a sf-daq/dap checkout")
    ap.add_argument("--shape", type=int, nargs=2, default=(1614, 1030))
    ap.add_argument("--patch", action="store_true", help="print the upstream patch and exit")
    a = ap.parse_args()
    if a.patch:
        print(UPSTREAM_PATCH)
    elif a.dap_src is None:
        ap.error("give a dap checkout, or --patch")
    else:
        reproduce(a.dap_src, tuple(a.shape))
