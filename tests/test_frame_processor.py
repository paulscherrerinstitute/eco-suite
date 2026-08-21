import numpy as np
import pytest

pytest.importorskip("qtpy")

from eco.widgets.camserver_stream_qt import (
    COLORMAP_NAMES,
    FrameProcessor,
    _full_range,
    apply_colormap,
    array_to_qimage,
    colormap_gradient_stops,
    compute_fit_scale,
    compute_log_histogram,
    compute_roi_from_drag,
    get_colormap_lut,
    normalize_to_uint8,
    value_to_y,
    y_to_value,
)


def test_full_range_integer_dtype():
    assert _full_range(np.uint16) == (0.0, 65535.0)
    assert _full_range(np.uint8) == (0.0, 255.0)


def test_full_range_float_dtype_falls_back_to_none():
    assert _full_range(np.float32) == (None, None)


def test_compute_roi_from_drag_normalizes_any_corner_order():
    assert compute_roi_from_drag(10, 20, 30, 50) == (10, 20, 20, 30)
    assert compute_roi_from_drag(30, 50, 10, 20) == (10, 20, 20, 30)


def test_frame_processor_passthrough_grayscale_auto_contrast():
    proc = FrameProcessor()
    frame = np.array([[0, 100], [200, 255]], dtype=np.uint16)
    display, stats = proc.process(frame)
    assert display.dtype == np.uint8
    assert display.min() == 0
    assert display.max() == 255
    assert stats["color"] is False
    assert stats["shape"] == (2, 2)


def test_frame_processor_manual_contrast_clips():
    proc = FrameProcessor()
    proc.contrast_mode = "manual"
    proc.vmin, proc.vmax = 0, 100
    frame = np.array([[-50, 0], [50, 500]], dtype=np.float32)
    display, _ = proc.process(frame)
    assert display[0, 0] == 0
    assert display[1, 1] == 255


def test_frame_processor_full_range_uses_dtype_bounds():
    proc = FrameProcessor()
    proc.contrast_mode = "full"
    frame = np.full((2, 2), 1000, dtype=np.uint16)
    display, _ = proc.process(frame)
    # 1000 / 65535 * 255 ~= 3.9 -> 3 or 4 depending on rounding, but nowhere
    # near saturating like auto-contrast (which would map a flat frame to 0)
    assert 0 < display[0, 0] < 10


def test_frame_processor_averaging_smooths_across_frames():
    proc = FrameProcessor(average_n=2)
    f1 = np.full((2, 2), 0, dtype=np.uint16)
    f2 = np.full((2, 2), 100, dtype=np.uint16)
    proc.process(f1)
    display, stats = proc.process(f2)
    # average of 0 and 100 is 50, well below the raw incoming frame's max
    assert stats["max"] == pytest.approx(50.0)


def test_frame_processor_set_average_n_resizes_ring():
    proc = FrameProcessor(average_n=5)
    for v in (0, 10, 20, 30, 40):
        proc.process(np.full((2, 2), v, dtype=np.uint16))
    proc.set_average_n(1)
    display, stats = proc.process(np.full((2, 2), 100, dtype=np.uint16))
    assert stats["max"] == pytest.approx(100.0)


def test_frame_processor_background_grab_and_subtract():
    proc = FrameProcessor()
    background_frame = np.full((2, 2), 50, dtype=np.uint16)
    proc.process(background_frame)
    proc.grab_background()
    proc.subtract_background = True

    signal_frame = np.full((2, 2), 80, dtype=np.uint16)
    _, stats = proc.process(signal_frame)
    # 80 - 50 = 30 after subtraction, not 80
    assert stats["max"] == pytest.approx(30.0)


def test_frame_processor_clear_background_disables_subtraction_effect():
    proc = FrameProcessor()
    proc.process(np.full((2, 2), 50, dtype=np.uint16))
    proc.grab_background()
    proc.subtract_background = True
    proc.clear_background()

    _, stats = proc.process(np.full((2, 2), 80, dtype=np.uint16))
    # background is None again, so subtraction is a no-op even though the
    # checkbox-equivalent flag is still True
    assert stats["max"] == pytest.approx(80.0)


def test_frame_processor_roi_crop():
    proc = FrameProcessor()
    frame = np.arange(100, dtype=np.uint16).reshape(10, 10)
    proc.set_roi((2, 3, 4, 5))
    display, stats = proc.process(frame)
    assert stats["shape"] == (5, 4)


def test_frame_processor_compose_roi_offsets_by_existing_origin():
    assert FrameProcessor.compose_roi(None, (5, 5, 10, 10)) == (5, 5, 10, 10)
    assert FrameProcessor.compose_roi((10, 10, 50, 50), (5, 5, 8, 8)) == (15, 15, 8, 8)


def test_frame_processor_color_image_bypasses_contrast_pipeline():
    proc = FrameProcessor()
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    frame[..., 0] = 200  # solid red-ish
    display, stats = proc.process(frame)
    assert stats["color"] is True
    assert display.dtype == np.uint8
    assert display.shape == (4, 4, 3)
    assert display[0, 0, 0] == 200


def test_frame_processor_color_image_background_subtract():
    proc = FrameProcessor()
    bg = np.full((3, 3, 3), 30, dtype=np.uint8)
    proc.process(bg)
    proc.grab_background()
    proc.subtract_background = True

    frame = np.full((3, 3, 3), 80, dtype=np.uint8)
    display, _ = proc.process(frame)
    assert display[0, 0, 0] == 50


def test_array_to_qimage_grayscale():
    arr = np.zeros((4, 6), dtype=np.uint8)
    image = array_to_qimage(arr)
    assert image.width() == 6
    assert image.height() == 4


def test_array_to_qimage_rgb():
    arr = np.zeros((4, 6, 3), dtype=np.uint8)
    image = array_to_qimage(arr)
    assert image.width() == 6
    assert image.height() == 4


def test_array_to_qimage_rejects_bad_shape():
    with pytest.raises(ValueError):
        array_to_qimage(np.zeros((4, 6, 5), dtype=np.uint8))


# -- histogram/colorscale sidebar (_HistogramColorbar) pure-logic core --


def test_value_to_y_maps_high_values_to_top():
    # data_max should land at y=0 (top), data_min at y=height (bottom)
    assert value_to_y(100.0, height=200, data_min=0.0, data_max=100.0) == 0
    assert value_to_y(0.0, height=200, data_min=0.0, data_max=100.0) == 200
    assert value_to_y(50.0, height=200, data_min=0.0, data_max=100.0) == 100


def test_value_to_y_degenerate_span_returns_midpoint():
    assert value_to_y(5.0, height=200, data_min=5.0, data_max=5.0) == 100


def test_y_to_value_is_inverse_of_value_to_y():
    for value in (0.0, 12.5, 50.0, 87.3, 100.0):
        y = value_to_y(value, height=300, data_min=0.0, data_max=100.0)
        roundtripped = y_to_value(y, height=300, data_min=0.0, data_max=100.0)
        assert roundtripped == pytest.approx(value, abs=0.5)


def test_compute_log_histogram_shapes_and_edges():
    arr = np.random.default_rng(0).integers(0, 1000, size=(50, 50)).astype(np.uint16)
    counts, edges = compute_log_histogram(arr, bins=64)
    assert counts.shape == (64,)
    assert edges.shape == (65,)
    assert edges[0] == pytest.approx(float(arr.min()))
    assert edges[-1] == pytest.approx(float(arr.max()))
    assert np.all(counts >= 0)


def test_compute_log_histogram_flat_array_has_nonzero_range():
    arr = np.full((10, 10), 42, dtype=np.uint16)
    counts, edges = compute_log_histogram(arr, bins=8)
    assert edges[-1] > edges[0]  # degenerate range was widened, not zero-width


# -- FrameProcessor.process stats consumed by the histogram sidebar --


def test_frame_processor_stats_include_resolved_vmin_vmax_auto():
    proc = FrameProcessor()
    frame = np.array([[10, 20], [30, 40]], dtype=np.uint16)
    _, stats = proc.process(frame)
    assert stats["vmin"] == pytest.approx(10.0)
    assert stats["vmax"] == pytest.approx(40.0)


def test_frame_processor_stats_include_resolved_vmin_vmax_manual():
    proc = FrameProcessor()
    proc.contrast_mode = "manual"
    proc.vmin, proc.vmax = 5, 500
    frame = np.array([[10, 20], [30, 40]], dtype=np.uint16)
    _, stats = proc.process(frame)
    assert stats["vmin"] == 5
    assert stats["vmax"] == 500


def test_frame_processor_stats_include_resolved_vmin_vmax_full():
    proc = FrameProcessor()
    proc.contrast_mode = "full"
    frame = np.array([[10, 20], [30, 40]], dtype=np.uint16)
    _, stats = proc.process(frame)
    assert stats["vmin"] == 0.0
    assert stats["vmax"] == 65535.0


def test_frame_processor_stats_vmin_vmax_none_for_color():
    proc = FrameProcessor()
    frame = np.zeros((3, 3, 3), dtype=np.uint8)
    _, stats = proc.process(frame)
    assert stats["vmin"] is None
    assert stats["vmax"] is None


def test_frame_processor_stats_values_reflects_averaging_and_roi():
    proc = FrameProcessor(average_n=2)
    proc.set_roi((1, 0, 2, 2))
    proc.process(np.zeros((2, 3), dtype=np.uint16))
    _, stats = proc.process(np.full((2, 3), 10, dtype=np.uint16))
    # averaged (0, 10) -> 5, then cropped to the 2-wide ROI
    assert stats["values"].shape == (2, 2)
    assert np.all(stats["values"] == 5.0)


# -- log-scale contrast --


def test_normalize_to_uint8_log_scale_compresses_bright_peak():
    # a narrow bright peak against a broad low background: log scale
    # should raise the background's apparent brightness relative to
    # linear, since low-vs-high differences get compressed more than
    # high-vs-higher ones
    arr = np.array([[1, 1], [1, 1000]], dtype=np.float32)
    linear = normalize_to_uint8(arr, vmin=0, vmax=1000, log_scale=False)
    log = normalize_to_uint8(arr, vmin=0, vmax=1000, log_scale=True)
    assert log[0, 0] > linear[0, 0]
    assert log[1, 1] == 255  # the max should still saturate either way


def test_normalize_to_uint8_log_scale_clips_negative_to_zero():
    arr = np.array([[-50, 0], [50, 100]], dtype=np.float32)
    out = normalize_to_uint8(arr, vmin=0, vmax=100, log_scale=True)
    assert out[0, 0] == 0


def test_frame_processor_log_scale_flag_reaches_normalize():
    proc = FrameProcessor()
    proc.contrast_mode = "manual"
    proc.vmin, proc.vmax = 0, 1000
    proc.log_scale = True
    arr = np.array([[1, 1], [1, 1000]], dtype=np.float32)
    display, _ = proc.process(arr)
    proc_linear = FrameProcessor()
    proc_linear.contrast_mode = "manual"
    proc_linear.vmin, proc_linear.vmax = 0, 1000
    linear_display, _ = proc_linear.process(arr)
    assert display[0, 0] > linear_display[0, 0]


# -- colormaps --


def test_colormap_names_cover_the_requested_set():
    assert set(COLORMAP_NAMES) == {
        "gray",
        "gray_inverted",
        "viridis",
        "diverging_zero",
        "diverging_one",
    }


@pytest.mark.parametrize("name", COLORMAP_NAMES)
def test_get_colormap_lut_shape_and_dtype(name):
    lut = get_colormap_lut(name)
    assert lut.shape == (256, 3)
    assert lut.dtype == np.uint8


def test_get_colormap_lut_gray_is_identity():
    lut = get_colormap_lut("gray")
    assert np.array_equal(lut[0], [0, 0, 0])
    assert np.array_equal(lut[255], [255, 255, 255])
    assert np.array_equal(lut[128], [128, 128, 128])


def test_get_colormap_lut_gray_inverted_is_reversed():
    lut = get_colormap_lut("gray_inverted")
    assert np.array_equal(lut[0], [255, 255, 255])
    assert np.array_equal(lut[255], [0, 0, 0])


def test_get_colormap_lut_viridis_matches_known_endpoints():
    # matplotlib's viridis: dark purple at 0, yellow at 1 -- confirms
    # we're actually getting real viridis data, not a placeholder
    lut = get_colormap_lut("viridis")
    r0, g0, b0 = lut[0]
    assert r0 < 100 and b0 > 50  # dark purple-ish
    r255, g255, b255 = lut[255]
    assert r255 > 200 and g255 > 200 and b255 < 100  # yellow-ish


def test_apply_colormap_shape():
    gray = np.array([[0, 128], [255, 64]], dtype=np.uint8)
    rgb = apply_colormap(gray, "viridis")
    assert rgb.shape == (2, 2, 3)
    assert rgb.dtype == np.uint8


def test_colormap_gradient_stops_covers_full_range():
    stops = colormap_gradient_stops("viridis", n=8)
    fractions = [f for f, _color in stops]
    assert fractions[0] == 0.0
    assert fractions[-1] == 1.0
    assert len(stops) == 8


def test_frame_processor_viridis_colormap_produces_rgb_display():
    proc = FrameProcessor()
    proc.colormap = "viridis"
    frame = np.array([[0, 100], [200, 255]], dtype=np.uint16)
    display, stats = proc.process(frame)
    assert display.shape == (2, 2, 3)
    assert stats["color"] is False  # input was grayscale; only the display is RGB


def test_frame_processor_diverging_zero_centers_reference_at_midpoint():
    proc = FrameProcessor()
    proc.colormap = "diverging_zero"
    # asymmetric raw range (-10..40) should still map 0 to the LUT's exact
    # midpoint (index 127/128) rather than wherever -10..40 would put it
    frame = np.array([[-10, 0], [20, 40]], dtype=np.float32)
    display, stats = proc.process(frame)
    lut = get_colormap_lut("diverging_zero")
    zero_gray = normalize_to_uint8(frame, stats["vmin"], stats["vmax"])[0, 1]  # the 0 pixel
    assert 120 <= zero_gray <= 135
    assert np.array_equal(display[0, 1], lut[zero_gray])
    # symmetric bounds around 0, sized by the larger side (40)
    assert stats["vmin"] == pytest.approx(-40.0)
    assert stats["vmax"] == pytest.approx(40.0)


def test_frame_processor_diverging_one_centers_reference_at_midpoint():
    proc = FrameProcessor()
    proc.colormap = "diverging_one"
    frame = np.array([[0.5, 1.0], [1.0, 3.0]], dtype=np.float32)
    _, stats = proc.process(frame)
    # symmetric bounds around 1, sized by the larger side (|3-1|=2)
    assert stats["vmin"] == pytest.approx(-1.0)
    assert stats["vmax"] == pytest.approx(3.0)


def test_frame_processor_diverging_respects_manual_bounds():
    proc = FrameProcessor()
    proc.colormap = "diverging_zero"
    proc.contrast_mode = "manual"
    proc.vmin, proc.vmax = -5, 15
    frame = np.zeros((2, 2), dtype=np.float32)
    _, stats = proc.process(frame)
    # widened symmetrically around 0 from the manual bounds (-5, 15) -> the
    # larger magnitude side (15) sets the half-width
    assert stats["vmin"] == pytest.approx(-15.0)
    assert stats["vmax"] == pytest.approx(15.0)


# -- zoom / fit-to-window --


def test_compute_fit_scale_fits_within_viewport_preserving_aspect():
    # 1000x500 image (w x h) into a 400x400 viewport -> limited by width
    scale = compute_fit_scale(viewport_w=400, viewport_h=400, raw_w=1000, raw_h=500)
    assert scale == pytest.approx(0.4)


def test_compute_fit_scale_limited_by_height():
    scale = compute_fit_scale(viewport_w=1000, viewport_h=200, raw_w=500, raw_h=500)
    assert scale == pytest.approx(0.4)


def test_compute_fit_scale_degenerate_inputs_fall_back_to_one():
    assert compute_fit_scale(0, 400, 100, 100) == 1.0
    assert compute_fit_scale(400, 400, 0, 100) == 1.0


def test_compute_fit_scale_never_collapses_to_zero():
    scale = compute_fit_scale(viewport_w=1, viewport_h=1, raw_w=100000, raw_h=100000)
    assert scale >= 0.02
