"""Generate a directly-printable enclosure (two parts) for the Pi Zero 2 W
handheld pendant, using numpy-stl. No OpenSCAD needed:

    python3 generate_stl.py <output_dir>

produces  pendant_tray.stl  and  pendant_faceplate.stl.

Layout (landscape, held in the left hand, operated with the right):
the 4" display fills the left, a right-hand column carries the thumbstick
(upper) above the rotary encoder (lower). Cutouts here are RECTANGULAR
(robust, watertight from box primitives); the OpenSCAD master pendant.scad
has the same layout with proper round holes and screw posts.

FIRST ARTICLE: every dimension below is a guess from published part sizes.
Print the faceplate ALONE first, offer it up to your real display /
thumbstick / encoder, then adjust these numbers. Do not print the tray
until the faceplate cutouts check out.
"""

import os
import sys

import numpy as np
from stl import mesh

# ---- dimensions in mm (VERIFY against your actual parts) ----
WALL = 2.5
FLOOR = 2.0
FACE_THK = 2.5
DEPTH_IN = 36.0  # internal depth: Pi + PiSugar 3+ (5000mAh) + display standoffs
#                  (was 24 for the slim 1200mAh PiSugar 3)

FACE_W = 148.0  # outer width
FACE_H = 76.0  # outer height

# display window (visible area of the 4" 480x320 panel)
WIN_W, WIN_H = 85.0, 57.0
WIN_X0 = 5.0
WIN_Y0 = (FACE_H - WIN_H) / 2.0

# right-hand control column
COL_CX = (WIN_X0 + WIN_W + (FACE_W - WALL)) / 2.0  # ~118
JOY_SIZE = 28.0  # thumbstick cap clearance (square pocket)
JOY_CY = FACE_H * 0.66
ENC_SIZE = 9.0  # encoder shaft/nut clearance
ENC_CY = FACE_H * 0.25

# USB cable exit notch in the bottom wall
USB_W = 16.0
USB_CX = WIN_X0 + WIN_W / 2.0

# ventilation (passive convection; pair with a stick-on SoC heatsink)
VENT_SLOT_W = 3.0
VENT_SLOT_L = 16.0


def vent_slots(x_start, x_end, step, y_center, length=VENT_SLOT_L, width=VENT_SLOT_W):
    holes = []
    x = x_start
    while x <= x_end:
        holes.append((x, y_center - length / 2, x + width, y_center + length / 2))
        x += step
    return holes


def add_box(tris, x0, y0, z0, x1, y1, z1):
    v = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    faces = [
        (0, 2, 1), (0, 3, 2),  # bottom -z
        (4, 5, 6), (4, 6, 7),  # top +z
        (0, 1, 5), (0, 5, 4),  # front -y
        (3, 7, 6), (3, 6, 2),  # back +y
        (0, 4, 7), (0, 7, 3),  # left -x
        (1, 2, 6), (1, 6, 5),  # right +x
    ]
    for a, b, c in faces:
        tris.append([v[a], v[b], v[c]])


def panel_with_holes(tris, W, H, z0, z1, holes, ox=0.0, oy=0.0):
    """A flat plate [0..W]x[0..H] extruded z0..z1 with axis-aligned
    rectangular holes, built as a union of boxes via y-band decomposition."""
    ys = sorted(set([0.0, H] + [y for h in holes for y in (h[1], h[3])]))
    for i in range(len(ys) - 1):
        yb0, yb1 = ys[i], ys[i + 1]
        active = sorted(
            (h[0], h[2]) for h in holes if h[1] <= yb0 + 1e-6 and h[3] >= yb1 - 1e-6
        )
        x = 0.0
        segs = []
        for hx0, hx1 in active:
            if hx0 > x:
                segs.append((x, hx0))
            x = max(x, hx1)
        if x < W:
            segs.append((x, W))
        for sx0, sx1 in segs:
            add_box(tris, ox + sx0, oy + yb0, z0, ox + sx1, oy + yb1, z1)


def build_faceplate():
    tris = []
    holes = [
        (WIN_X0, WIN_Y0, WIN_X0 + WIN_W, WIN_Y0 + WIN_H),
        (COL_CX - JOY_SIZE / 2, JOY_CY - JOY_SIZE / 2, COL_CX + JOY_SIZE / 2, JOY_CY + JOY_SIZE / 2),
        (COL_CX - ENC_SIZE / 2, ENC_CY - ENC_SIZE / 2, COL_CX + ENC_SIZE / 2, ENC_CY + ENC_SIZE / 2),
    ]
    # vent slots in the top and bottom margins (clear of window/controls)
    holes += vent_slots(12, 78, 11, WIN_Y0 - 4.0, length=3.5, width=6.0)
    holes += vent_slots(12, 78, 11, WIN_Y0 + WIN_H + 4.0, length=3.5, width=6.0)
    panel_with_holes(tris, FACE_W, FACE_H, 0.0, FACE_THK, holes)
    return tris


def build_tray():
    tris = []
    top = FLOOR + DEPTH_IN
    # floor with a field of vent slots over the Pi (back of the device)
    floor_vents = vent_slots(24, 92, 9, FACE_H / 2.0)
    panel_with_holes(tris, FACE_W, FACE_H, 0, FLOOR, floor_vents)
    # left / right walls
    add_box(tris, 0, 0, FLOOR, WALL, FACE_H, top)
    add_box(tris, FACE_W - WALL, 0, FLOOR, FACE_W, FACE_H, top)
    # back wall (full)
    add_box(tris, 0, FACE_H - WALL, FLOOR, FACE_W, FACE_H, top)
    # front wall split around the USB notch (notch open to wall top)
    sx0, sx1 = USB_CX - USB_W / 2, USB_CX + USB_W / 2
    add_box(tris, 0, 0, FLOOR, sx0, WALL, top)
    add_box(tris, sx1, 0, FLOOR, FACE_W, WALL, top)
    add_box(tris, sx0, 0, FLOOR, sx1, WALL, FLOOR + 6.0)  # sill below the notch
    return tris


def save(tris, path):
    data = np.zeros(len(tris), dtype=mesh.Mesh.dtype)
    for i, t in enumerate(tris):
        data["vectors"][i] = np.array(t, dtype=np.float32)
    m = mesh.Mesh(data)
    m.save(path)
    mn = m.vectors.reshape(-1, 3).min(axis=0)
    mx = m.vectors.reshape(-1, 3).max(axis=0)
    print(f"  {os.path.basename(path)}: {len(tris)} triangles, "
          f"bbox {mx[0]-mn[0]:.1f} x {mx[1]-mn[1]:.1f} x {mx[2]-mn[2]:.1f} mm")


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(outdir, exist_ok=True)
    print("generating enclosure (FIRST ARTICLE - verify against real parts):")
    save(build_faceplate(), os.path.join(outdir, "pendant_faceplate.stl"))
    save(build_tray(), os.path.join(outdir, "pendant_tray.stl"))
    print("done.")


if __name__ == "__main__":
    main()
