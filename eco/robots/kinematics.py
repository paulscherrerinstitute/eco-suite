"""Spherical <-> cartesian conversion for the Bernina detector arm.

The detector sits on the robot flange and must always point back at the sample,
so a "spherical" move is not just a coordinate change: the tool orientation
(rx, ry, rz) is a *function* of the direction (gamma, delta). These two
functions carry that convention, including the branch corrections that were
added one commit at a time in the pshell version ("Fixed error in sph2cart for
gamma > 180", "Fixed issue with delta, gamma > 90", "Fixed problems with
gamma < -90 and numerical issues when delta --> 90").

Convention, in the active frame (typically ``f_4mRad``):

* ``r``      -- sample-to-detector distance [mm]
* ``gamma``  -- horizontal scattering angle [deg], in the x/z plane
* ``delta``  -- vertical scattering angle [deg], out of that plane

    x = r cos(delta) sin(gamma)
    y = r sin(delta)
    z = r cos(delta) cos(gamma)

The pshell original computed these inline in ``RobotBernina``; they are pure
functions of their arguments, so they live here where they can be tested
against the controller's own ``jointToPoint`` without a robot attached.
"""

from __future__ import annotations

import math

__all__ = ["deg2rad", "rad2deg", "sph2cart", "cart2sph", "clamp_unit"]

#: Half-width of the "delta is effectively 90 deg" band, in degrees. Inside it
#: the rz formula below is numerically meaningless (see sph2cart).
DELTA_POLE_BAND = 0.05


def clamp_unit(value: float) -> float:
    """Clamp to [-1, 1] for asin/acos.

    Round-off in the products below routinely pushes the argument to
    1.0000000000000002, which is a hard ``ValueError: math domain error`` in
    Python. The pshell version had no guard and simply raised from inside the
    poll loop.
    """
    return max(-1.0, min(1.0, value))


def deg2rad(angles):
    if hasattr(angles, "__len__"):
        return [a / 180.0 * math.pi for a in angles]
    return angles / 180.0 * math.pi


def rad2deg(angles):
    if hasattr(angles, "__len__"):
        return [a * 180.0 / math.pi for a in angles]
    return angles * 180.0 / math.pi


def sph2cart(r=None, gamma=None, delta=None, return_dict=True, **kwargs):
    """(r, gamma, delta) [mm, deg] -> (x, y, z, rx, ry, rz) [mm, deg].

    Orientation is derived so the tool z-axis stays aimed at the origin.
    ``kwargs`` is accepted and ignored so a full cartesian/spherical dict can
    be splatted in directly, as the original did.
    """
    g_deg, d_deg = float(gamma), float(delta)
    g, d = deg2rad([g_deg, d_deg])

    x = r * math.cos(d) * math.sin(g)
    y = r * math.sin(d)
    z = r * math.cos(d) * math.cos(g)

    ry = -math.asin(clamp_unit(-math.cos(d) * math.sin(g)))
    cos_ry = math.cos(ry)
    if cos_ry == 0.0:
        rx = -math.pi / 2
    else:
        rx = -math.acos(clamp_unit(math.cos(d) * math.cos(g) / cos_ry))
    denom = math.cos(rx) ** 2 + (math.sin(ry) * math.sin(rx)) ** 2
    if denom == 0.0:
        rz = 0.0
    else:
        rz = math.asin(
            clamp_unit(-math.sin(ry) * math.sin(rx) * math.sqrt(1.0 / denom))
        )
    rx, ry, rz = rad2deg([rx, ry, rz])

    # Keep the detector orientation continuous across the +-90 deg branches
    # instead of flipping the detector over. Order matters: each correction
    # below assumes the previous ones have been applied.
    if g_deg > 90:
        rz = 180 - rz
    if g_deg < -90:
        rz = -180 - rz
    if d_deg > 90:
        rz = -rz
    # As delta -> 90 the arm points straight up, gamma and rz become the same
    # rotation, and the rz formula above degenerates (0/0). Snap to the limit.
    if 90 - DELTA_POLE_BAND < d_deg < 90 + DELTA_POLE_BAND:
        rz = g_deg
    if abs(rz // 180) > 0:
        rz = rz - (rz + 180) // 360 * 360
    rx = math.copysign(1, d_deg) * rx
    rz = math.copysign(1, d_deg) * rz

    if return_dict:
        return {"x": x, "y": y, "z": z, "rx": rx, "ry": ry, "rz": rz}
    return [x, y, z, rx, ry, rz]


def cart2sph(x=None, y=None, z=None, return_dict=True, ry_fallback=None, **kwargs):
    """(x, y, z) [mm] -> (r, gamma, delta) [mm, deg].

    On the y-axis (x == z == 0) gamma is undefined; the original fell back to
    the current ``ry`` readback, so ``ry_fallback`` (or ``ry`` in ``kwargs``)
    supplies it. Orientation inputs are otherwise ignored -- the inverse of
    :func:`sph2cart` only needs the position.
    """
    x, y, z = float(x), float(y), float(z)
    r = math.sqrt(x * x + y * y + z * z)
    if z * z + x * x > 0:
        gamma = math.copysign(1, x) * math.acos(
            clamp_unit(z / math.sqrt(z * z + x * x))
        )
    else:
        fallback = kwargs.get("ry", ry_fallback)
        if fallback is None:
            raise ValueError(
                "gamma is undefined at x == z == 0; pass ry_fallback=<current ry>"
            )
        gamma = deg2rad(float(fallback))
    delta = 0.0 if r == 0 else math.asin(clamp_unit(y / r))
    gamma, delta = rad2deg([gamma, delta])
    if return_dict:
        return {"r": r, "gamma": gamma, "delta": delta}
    return [r, gamma, delta]
