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


# -- "auto": following the desktop's light/dark preference -----------------

from eco.widgets import qt_theme


def test_resolve_theme_none_string_is_native_even_with_env_set():
    os.environ[ENV_VAR] = "dark"
    assert resolve_theme("none") is None


def test_resolve_mode_explicit_and_auto(monkeypatch):
    assert qt_theme.resolve_mode("dark") == "dark"
    assert qt_theme.resolve_mode("light", system_scheme="dark") == "light"
    assert qt_theme.resolve_mode("none", system_scheme="dark") is None
    assert qt_theme.resolve_mode("auto", system_scheme="dark") == "dark"
    assert qt_theme.resolve_mode("auto", system_scheme="light") == "light"
    # nothing says: the native palette decides
    monkeypatch.setattr(qt_theme, "native_palette_is_dark", lambda: True)
    assert qt_theme.resolve_mode("auto", system_scheme=None) == "dark"
    monkeypatch.setattr(qt_theme, "native_palette_is_dark", lambda: False)
    assert qt_theme.resolve_mode("auto", system_scheme=None) == "light"


def _fake_commands(monkeypatch, answers):
    """answers: {first distinguishing word of the command: stdout or None}"""

    def run_quiet(cmd, timeout):
        key = "portal" if cmd[0] == "gdbus" else cmd[-1]
        return answers.get(key)

    monkeypatch.setattr(qt_theme, "_run_quiet", run_quiet)
    monkeypatch.setattr(qt_theme, "_scheme_from_qt", lambda: None)
    monkeypatch.delenv("GTK_THEME", raising=False)


def test_detect_portal_dark_and_light(monkeypatch):
    _fake_commands(monkeypatch, {"portal": "(<<uint32 1>>,)"})
    assert qt_theme.detect_system_scheme() == "dark"
    _fake_commands(monkeypatch, {"portal": "(<<uint32 2>>,)"})
    assert qt_theme.detect_system_scheme() == "light"


def test_detect_portal_no_preference_falls_through_to_gsettings(monkeypatch):
    _fake_commands(
        monkeypatch,
        {"portal": "(<<uint32 0>>,)", "color-scheme": "'prefer-dark'"},
    )
    assert qt_theme.detect_system_scheme() == "dark"
    _fake_commands(
        monkeypatch,
        {"portal": "(<<uint32 0>>,)", "color-scheme": "'default'", "gtk-theme": "'Yaru-dark'"},
    )
    assert qt_theme.detect_system_scheme() == "dark"


def test_detect_nothing_says(monkeypatch):
    _fake_commands(
        monkeypatch,
        {"portal": "(<<uint32 0>>,)", "color-scheme": "'default'", "gtk-theme": "'Yaru'"},
    )
    assert qt_theme.detect_system_scheme() is None
    monkeypatch.setenv("GTK_THEME", "Adwaita:dark")
    assert qt_theme.detect_system_scheme() == "dark"


def test_detect_real_system_does_not_raise():
    assert qt_theme.detect_system_scheme(timeout=2) in ("dark", "light", None)
