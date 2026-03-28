import numpy as np
import random
import matplotlib.pyplot as plt


def _calc_0(lx):
    r, h, k = 0.4, 0.5, 0.5
    inner = r**2 - (lx - h) ** 2
    if inner < 0:
        return []

    y_top = k + np.sqrt(inner)
    y_bottom = k - np.sqrt(inner)

    points = [y_top]
    # 'e' has a gap in the bottom right arc
    if lx < 0.8:
        points.append(y_bottom)
    # 'e' has a middle bar
    if 0.15 <= lx <= 0.85:
        points.append(0.5)
    return points


def _calc_1(lx):
    r, h, k = 0.4, 0.5, 0.5
    inner = r**2 - (lx - h) ** 2
    if inner < 0:
        return []

    # 'c' is open on the right side
    if lx > 0.75:
        return []
    return [k + np.sqrt(inner), k - np.sqrt(inner)]


def _calc_2(lx):
    r, h, k = 0.4, 0.5, 0.5
    inner = r**2 - (lx - h) ** 2
    if inner < 0:
        return []
    return [k + np.sqrt(inner), k - np.sqrt(inner)]


def get_sig_y(x):

    if x < 0 or x > 1:
        return 0.0

    # Split the [0, 1] range into three segments
    if x <= 1 / 3:
        lx = x * 3
        possible_y = _calc_0(lx)
    elif x <= 2 / 3:
        lx = (x - 1 / 3) * 3
        possible_y = _calc_1(lx)
    else:
        lx = (x - 2 / 3) * 3
        possible_y = _calc_2(lx)

    ret = float(random.choice(possible_y)) if possible_y else 0.0
    return np.random.normal(scale=0.05) + ret
