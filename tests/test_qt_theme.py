import os

import pytest

pytest.importorskip("qtpy")

from eco.widgets.qt_theme import ENV_VAR, resolve_theme


@pytest.fixture(autouse=True)
def _clean_env():
    old = os.environ.pop(ENV_VAR, None)
    yield
    if old is None:
        os.environ.pop(ENV_VAR, None)
    else:
        os.environ[ENV_VAR] = old


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
