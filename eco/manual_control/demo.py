"""Runnable demo: a small *hierarchical* fake beamline built from eco's own
Assembly/Adjustable classes (no EPICS/hardware needed), driven by a mocked
Raspberry-Pi-style box (draggable joystick + rotary encoder + on-screen
navigator) in a Tkinter window.

Run with:
    python -m eco.manual_control.demo

The structure is nested (beamline > chamber > motors) so you can exercise
descending/ascending with both the encoder (rotate to move, OK to
enter/arm) and touch (tap breadcrumb / list rows). This runs the exact
same ManualControlBox/TreeNavigator/Jogger code that would run on the Pi -
only the inputs (mock_gui) and the leaf Adjustables (DummyAdjustable here
instead of e.g. bernina's MotorRecords) differ.

To drive the REAL beamline instead, point the box at the bernina
namespace (an eco Namespace, browsable by name without connecting until
you descend/arm)::

    import eco.bernina as bernina
    from eco.manual_control.box import ManualControlBox
    from eco.manual_control.mock_gui import ManualControlApp
    ManualControlApp(ManualControlBox(bernina.namespace, root_name="bernina")).mainloop()
"""

from eco.elements.adjustable import DummyAdjustable
from eco.elements.assembly import Assembly

from .box import ManualControlBox
from .mock_gui import ManualControlApp


class Group(Assembly):
    """A plain container Assembly holding named Adjustables / sub-groups."""

    def __init__(self, name, children):
        super().__init__(name=name)
        for child in children:
            self._append(child, name=child.name, is_setting=True)


def build_fake_beamline():
    def motor(name, lo=-10, hi=10):
        return DummyAdjustable(name, limits=[lo, hi])

    mono = Group("mono", [motor("theta"), motor("two_theta"), motor("energy", 4000, 12000)])
    slit1 = Group("slit1", [motor("gap_x", 0, 5), motor("gap_y", 0, 5), motor("center_x"), motor("center_y")])
    chamber = Group(
        "sample_chamber",
        [
            motor("sample_x"), motor("sample_y"), motor("sample_z"),
            Group("goniometer", [motor("phi", -180, 180), motor("chi", -90, 90)]),
        ],
    )
    return Group("beamline", [mono, slit1, chamber, motor("attenuator", 0, 20)])


def main():
    beamline = build_fake_beamline()
    box = ManualControlBox(beamline, root_name="beamline", step_sizes=[0.001, 0.01, 0.1, 1, 10])
    ManualControlApp(box).mainloop()


if __name__ == "__main__":
    main()
