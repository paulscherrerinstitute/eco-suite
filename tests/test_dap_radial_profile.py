"""eco.utilities.dap_radial_profile / dap_radprof_custom: the fixed dap
radial profile, its custom-script form, and (with DAP_SRC set to a
sf-daq/dap checkout) the reproduction of dap's two issues."""
import os
from pathlib import Path

import numpy as np
import pytest

from eco.utilities import dap_radial_profile as drp
from eco.utilities import dap_radprof_custom as custom


def reference(image, mask, x0, y0, tmin=None, tmax=None):
    """Mean per ring of one pixel over unmasked, non-NaN pixels in range."""
    y, x = np.indices(image.shape)
    rad = np.sqrt((x - x0) ** 2 + (y - y0) ** 2).astype(int)
    use = mask & np.isfinite(image)
    if tmin is not None:
        use &= image >= tmin
    if tmax is not None:
        use &= image < tmax
    r_min, r_max = rad[mask].min(), rad[mask].max() + 1
    out = np.full(r_max - r_min, np.nan)
    for r in range(r_min, r_max):
        sel = use & (rad == r)
        if sel.any():
            out[r - r_min] = image[sel].astype(float).mean()
    return out, r_min, r_max


@pytest.fixture(scope="module")
def data():
    frames, mask, x0, y0 = drp.synthetic_frames((120, 90), n=2)
    return frames, mask, x0, y0


def test_matches_reference_with_and_without_threshold(data):
    frames, mask, x0, y0 = data
    img = frames[0].copy()
    img[5, 5] = np.nan
    for tmin, tmax in ((None, None), (2.0, None), (None, 20.0), (20.0, 2.0)):
        prof, r_min, r_max = custom.radial_profile(img, mask, x0, y0, tmin, tmax)
        lo, hi = (None, None) if tmin is None and tmax is None else (
            (tmin, tmax) if tmin is None or tmax is None else sorted((tmin, tmax)))
        ref, rmn, rmx = reference(img, mask, x0, y0, lo, hi)
        assert (r_min, r_max) == (rmn, rmx)
        np.testing.assert_allclose(prof, ref, rtol=1e-10, equal_nan=True)


def test_proc_output_and_silent_region(data):
    frames, mask, x0, y0 = data
    cfg = dict(beam_center_x=x0, beam_center_y=y0, apply_threshold=1, threshold_min=2.0,
               radial_integration_silent_min=10, radial_integration_silent_max=20)
    out = custom.proc(cfg, frames[0], mask)
    assert set(out) == {"radint_I", "radint_q", "radint_normalised"}
    prof, r_min, r_max = custom.radial_profile(frames[0], mask, x0, y0, 2.0)
    assert out["radint_q"] == [r_min, r_max]
    np.testing.assert_allclose(out["radint_I"], prof / np.nansum(prof[10 - r_min:20 - r_min]), equal_nan=True)
    assert custom.proc({}, frames[0], mask) == {}                 # no beam centre: nothing


def test_custom_script_passes_ecos_upload_checks():
    jf = pytest.importorskip("eco.detector.jungfrau")
    src = Path(custom.__file__).read_text()
    jf.check_dap_script_imports(src)                              # numpy only
    func = jf._load_dap_script_function_from_file(Path(custom.__file__))
    meta = dict(beam_center_x=300.0, beam_center_y=200.0, apply_threshold=1, threshold_min=0.3)
    # eco's own synthetic run (meta = {}) and one with a beam centre, both within the time limit
    jf._test_dap_script_function(func)
    jf._test_dap_script_function(lambda m, i, k: func(dict(meta), i, k))


def test_upstream_patch_is_a_unified_diff_of_radprof():
    assert drp.UPSTREAM_PATCH.startswith("--- a/dap/algos/radprof.py\n+++ b/dap/algos/radprof.py\n")
    assert "-    r_min = min(rad)" in drp.UPSTREAM_PATCH and "+    r_min = rad.min()" in drp.UPSTREAM_PATCH


def test_reproduce_against_dap_checkout():
    src = os.environ.get("DAP_SRC")
    if not src:
        pytest.skip("set DAP_SRC to a sf-daq/dap checkout")
    out = drp.reproduce(src, shape=(400, 300), n=2, verbose=False)
    assert out["no_threshold_identical"]
    assert out["threshold_nan_rings_dap"] > 0.9 > out["threshold_nan_rings_fixed"]
    assert out["hot_pixel_nan_rings_dap"] > 0
    assert out["ms_dap_patched"] < out["ms_dap"]
