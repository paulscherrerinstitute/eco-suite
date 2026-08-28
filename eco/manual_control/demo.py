"""Runnable demo: a small *hierarchical* fake beamline built from eco's own
Assembly/Adjustable classes (no EPICS/hardware needed), driven by a mocked
Raspberry-Pi-style box (draggable joystick + rotary encoder + on-screen
navigator) in a Tkinter window.

Run with:
    python -m eco.manual_control.demo                 # 480x320 panel
    python -m eco.manual_control.demo --psi-box       # the 7" 800x480 box

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

import argparse

from eco.elements.adjustable import DummyAdjustable
from eco.elements.assembly import Assembly

from .box import ManualControlBox
from .mock_gui import ManualControlApp
from .remote.pi_app import parse_size


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


def add_view_args(ap):
    """Panel-geometry options shared by the two simulator entry points."""
    ap.add_argument("--psi-box", action="store_true",
                    help="preview the PSI Motor Control Unit box: 800x480 landscape, touch-sized text")
    ap.add_argument("--size", type=parse_size, default=None, metavar="WxH",
                    help="panel size, e.g. 800x480 (default 480x320)")
    ap.add_argument("--font-scale", type=float, default=1.0, help="scale all text")
    ap.add_argument("--layout", choices=("landscape", "stacked"), default=None,
                    help="force a layout (default: landscape for panels >= 640 px wide)")
    return ap


def view_kwargs(args):
    if args.psi_box:
        return dict(screen_size=args.size or (800, 480),
                    font_scale=args.font_scale if args.font_scale != 1.0 else 1.4,
                    layout=args.layout)
    return dict(screen_size=args.size, font_scale=args.font_scale, layout=args.layout)


def main():
    args = add_view_args(argparse.ArgumentParser(
        description="offline manual-control simulator (fake beamline, mock joystick + encoder)"
    )).parse_args()
    beamline = build_fake_beamline()
    box = ManualControlBox(beamline, root_name="beamline", step_sizes=[0.001, 0.01, 0.1, 1, 10])
    ManualControlApp(box, **view_kwargs(args)).mainloop()


if __name__ == "__main__":
    main()
