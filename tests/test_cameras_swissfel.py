"""Tests for CameraBasler/CameraPCO's .viewer() method and its
_default_widget = "viewer" wiring (eco.elements.assembly.Assembly.widget()'s
override mechanism -- see tests/test_assembly_default_widget.py for that
mechanism's own generic tests; these instead check that these two camera
classes plug into it correctly, resolving to the right cam_server pipeline
name for a real camera PV without the caller having to know/guess one).

Importing eco.devices_general.cameras_swissfel itself requires `cam_server`
(a hard, module-level import there) -- pytest.importorskip below mirrors
tests/test_camserver_stream_qt.py's own convention for that.

CameraBasler/CameraPCO.__init__ makes real cam_server/EPICS calls (camserver
alias registration, PV connections), so these tests call .viewer() as an
unbound method against a bare stand-in object carrying just the attributes
it actually reads (pvname) rather than constructing a real instance -- same
spirit as test_assembly_default_widget.py's FakeCameraLike.
"""
import pytest

pytest.importorskip("cam_server")

from eco.devices_general.cameras_swissfel import CameraBasler, CameraPCO

# the two real PVs this feature was built/verified against
BASLER_PVNAME = "SARES20-PROF141-M1"  # eco.xdiagnostics.profile_monitors.ProfKbBernina's camera
PCO_PVNAME = "SARES20-PROF146-M1"  # eco.xdiagnostics.profile_monitors.Pprm_dsd's camera


class _FakeCameraSelf:
    def __init__(self, pvname):
        self.pvname = pvname


def test_camera_basler_default_widget_is_viewer():
    assert CameraBasler._default_widget == "viewer"


def test_camera_pco_default_widget_is_viewer():
    assert CameraPCO._default_widget == "viewer"


def test_camera_basler_viewer_resolves_its_own_camera_pipeline(monkeypatch):
    calls = {}

    def fake_make_camserver_stream_qt(name, **kwargs):
        calls["name"] = name
        calls["kwargs"] = kwargs
        return "the basler viewer"

    monkeypatch.setattr(
        "eco.widgets.camserver_stream_qt.make_camserver_stream_qt",
        fake_make_camserver_stream_qt,
    )

    fake_self = _FakeCameraSelf(BASLER_PVNAME)
    result = CameraBasler.viewer(fake_self)

    assert result == "the basler viewer"
    # the raw PV/camera name is passed through as-is, with kind=
    # "camera_pipeline" doing the "{camera}_sp" resolution (and
    # auto-creating that pipeline config if it doesn't exist yet) --
    # see eco.widgets.camserver_stream_qt.resolve_camera_pipeline
    assert calls["name"] == "SARES20-PROF141-M1"
    assert calls["kwargs"]["kind"] == "camera_pipeline"
    # cam=self: lets the viewer add its "Camera Settings" button (opens
    # widget(normal=True) on the real camera Assembly, not a copy of it)
    assert calls["kwargs"]["cam"] is fake_self


def test_camera_pco_viewer_resolves_its_own_camera_pipeline(monkeypatch):
    calls = {}

    def fake_make_camserver_stream_qt(name, **kwargs):
        calls["name"] = name
        calls["kwargs"] = kwargs
        return "the pco viewer"

    monkeypatch.setattr(
        "eco.widgets.camserver_stream_qt.make_camserver_stream_qt",
        fake_make_camserver_stream_qt,
    )

    fake_self = _FakeCameraSelf(PCO_PVNAME)
    result = CameraPCO.viewer(fake_self)

    assert result == "the pco viewer"
    assert calls["name"] == "SARES20-PROF146-M1"
    assert calls["kwargs"]["kind"] == "camera_pipeline"
    assert calls["kwargs"]["cam"] is fake_self


def test_camera_basler_viewer_passes_through_rate_and_theme(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        "eco.widgets.camserver_stream_qt.make_camserver_stream_qt",
        lambda name, **kwargs: calls.update(kwargs) or "viewer",
    )

    fake_self = _FakeCameraSelf(BASLER_PVNAME)
    CameraBasler.viewer(fake_self, rate_hz=25.0, theme="dark", auto_start=False)

    assert calls["rate_hz"] == 25.0
    assert calls["theme"] == "dark"
    assert calls["auto_start"] is False
