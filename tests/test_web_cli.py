"""`eco web`: the console session with the web gateway started inside it,
or (--no-console) python -m eco.web serving the namespace on its own."""
import sys

import pytest

import eco_cli


@pytest.fixture
def execs(monkeypatch, tmp_path):
    monkeypatch.setenv("ECORC", str(tmp_path / "none"))
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(eco_cli, "_exec", lambda cmd, hint: calls.append(cmd))
    return calls


def test_web_starts_the_console_session_with_the_gateway_in_it(execs):
    eco_cli.main(["web", "-s", "bernina", "--writes", "--port", "9001"])
    (cmd,) = execs
    assert cmd[:4] == ["ipython", "--profile=eco", "--no-banner", "-i"]
    lines = cmd[-1].split("\n")
    assert lines[0] == "%run -m eco.startup_inline -l -s bernina"
    assert "start_web(port=9001, writes=True, host='127.0.0.1')" in lines[-1]


def test_web_is_read_only_by_default(execs):
    eco_cli.main(["web", "-s", "bernina"])
    assert "writes=False" in execs[0][-1]


def test_web_without_console_serves_from_its_own_process(execs):
    eco_cli.main(["web", "-s", "alvra", "--no-console", "--no-lazy"])
    assert execs[0] == [sys.executable, "-m", "eco.web", "-s", "alvra", "--no-lazy",
                        "--port", "8097", "--host", "127.0.0.1"]


def test_web_main_needs_a_source():
    from eco.web.__main__ import main

    with pytest.raises(SystemExit):
        main([])
