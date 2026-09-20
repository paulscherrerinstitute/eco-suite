import os

import pytest

pytest.importorskip("qtpy")

from eco.widgets.qt_theme import ENV_VAR, TOUCH_ENV_VAR, resolve_theme, resolve_touch


@pytest.fixture(autouse=True)
def _clean_env():
    old = os.environ.pop(ENV_VAR, None)
    old_touch = os.environ.pop(TOUCH_ENV_VAR, None)
    yield
    if old is None:
        os.environ.pop(ENV_VAR, None)
    else:
        os.environ[ENV_VAR] = old
    if old_touch is None:
        os.environ.pop(TOUCH_ENV_VAR, None)
    else:
        os.environ[TOUCH_ENV_VAR] = old_touch


def test_resolve_theme_explicit_dark():
    assert resolve_theme("dark") == "dark"


def test_resolve_theme_explicit_light():
    assert resolve_theme("light") == "light"


def test_resolve_theme_explicit_invalid_falls_back_to_env():
    os.environ[ENV_VAR] = "dark"
    assert resolve_theme("not-a-theme") == "dark"


def test_resolve_theme_none_reads_env_var():
    os.environ[ENV_VAR] = "light"
    assert resolve_theme(None) == "light"


def test_resolve_theme_none_with_unset_env_is_no_theme():
    assert resolve_theme(None) is None


def test_resolve_theme_env_var_invalid_value_is_no_theme():
    os.environ[ENV_VAR] = "solarized"
    assert resolve_theme(None) is None


def test_resolve_touch_explicit_true():
    assert resolve_touch(True) is True


def test_resolve_touch_explicit_false_overrides_env():
    os.environ[TOUCH_ENV_VAR] = "1"
    assert resolve_touch(False) is False


def test_resolve_touch_none_reads_env_var():
    os.environ[TOUCH_ENV_VAR] = "1"
    assert resolve_touch(None) is True


def test_resolve_touch_none_with_unset_env_is_false():
    assert resolve_touch(None) is False


def test_resolve_touch_env_var_anything_but_1_is_false():
    os.environ[TOUCH_ENV_VAR] = "true"
    assert resolve_touch(None) is False


def test_apply_modern_theme_touch_layers_onto_no_theme():
    """touch=True must still do something even with theme=None ("none" is
    the desktop's own CLI default) -- it's an additive switch, not a
    fourth theme choice, so it can't rely on a base theme's stylesheet
    already being set."""
    from qtpy import QtWidgets

    from eco.widgets.qt_theme import _TOUCH_QSS, apply_modern_theme

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    apply_modern_theme(theme=None, touch=True)
    try:
        assert _TOUCH_QSS in app.styleSheet()
    finally:
        apply_modern_theme(theme=None, touch=False)  # leave global app state clean


def test_apply_modern_theme_touch_false_clears_touch_only():
    from qtpy import QtWidgets

    from eco.widgets.qt_theme import _TOUCH_QSS, apply_modern_theme

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    apply_modern_theme(theme=None, touch=True)
    apply_modern_theme(theme=None, touch=False)
    assert _TOUCH_QSS not in app.styleSheet()


def test_apply_modern_theme_records_touch_choice_in_env():
    from qtpy import QtWidgets

    from eco.widgets.qt_theme import apply_modern_theme

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    apply_modern_theme(theme=None, touch=True)
    try:
        assert os.environ[TOUCH_ENV_VAR] == "1"
    finally:
        apply_modern_theme(theme=None, touch=False)
