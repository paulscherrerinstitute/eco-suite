"""Optimizing scans: maximise a live signal over several adjustables while an
ordinary ``StepScan`` records every point.

User documentation: ``docs/optimize.md``. Entry point: ``Scans.optimize``.
The scan machinery is the adaptive scan's (``eco.acquisition.adaptive``):
``OptimizeDriver`` is an ``AdaptiveDriver`` whose strategy, ``Optimizer``,
places points anywhere (a mesh without a grid, kind "line"/"path"), not on a
candidate grid. ``Optimizer`` is pure numpy and knows nothing about eco
(``tests/test_optimize.py`` drives it with plain functions).

Coordinates: every axis is counted in its *step* from the start position,
``u = (x - start) / step``. The step is the operator's "a move that changes
the signal clearly near the optimum", so distances, curvatures and the
largest move mean the same for a mm stage, a rotation and a delay.

The algorithm, written as a generator (``Optimizer._algorithm``) that yields
batches of points and receives their values:

1. **Probe** around the centre: the centre `repeats` times (noise), +-1 step
   per axis and, with `pairs`, both diagonals of every axis pair. A local
   quadratic model ``f = a + g.du + du.H.du/2`` is fitted to the points near
   the centre (weighted least squares). The eigenvectors of ``H`` are the
   coupled *directions* ("x and rz together"), sorted by curvature.
2. **Calibrate followers** (``follow={axis: "calibrate"}``): once the model
   has a significant maximum along such an axis, its best value is an affine
   function of the others, ``u_D = c_D - H_DD^-1 (g_D + H_DF (u_F - c_F))``;
   from then on it is not searched but moved along with these coefficients
   (they are printed and stored in the scan). The directions are then those
   of the profile (the Schur complement of ``H_DD``).
3. **Line searches** along the directions (and first along the model's
   Newton step), RCDS-like (X. Huang, NIM A 726 (2013)): bracket the maximum
   with growing steps until the signal is clearly (3 sigma) worse than the
   best on the line, fill the bracket, fit a parabola. Cycles repeat while
   they improve by more than 2 sigma; the model and directions are refitted
   from all data near the new centre between cycles.
4. **Probe again** at the optimum: the final model, eigen-axes and, if not
   done yet, the calibration.
5. **Check** for a better maximum elsewhere: a Gaussian process through all
   points; points within the limits whose upper confidence bound (mean + 2
   sigma) exceeds the best clearly are measured. A better one restarts 1-4
   there.
6. **Best**: the best point is measured `repeats` times; the scan ends
   there.

Fixed followers (``follow={follower: {axis: coefficient}}``) are extra scan
adjustables that are never searched: ``follower = start + sum(coefficient *
(axis - axis_start))``.
"""

import math
from types import SimpleNamespace

import numpy as np

from eco.acquisition.adaptive import AdaptiveDriver

PROBE_RADIUS = 2.5  # steps: weight radius of the local quadratic fit
LINE_MAX_POINTS = 12  # points per line search
GOLD = 1.618
CHECK_CANDIDATES = 4000
SURPRISE = 0.5  # check point above the model's mean by 3 sigma + this x its sd: searched around
SUSPICIOUS_ERROR = 5.0  # a point's error this many times the typical one: measured again
CALIBRATION_OFFSET = 0.3  # steps: calibrate only this close to the calibrated axis' best


# --------------------------------------------------------------------------
# setup: axis specs and follow= into numbers


def _adjustable_limits(adj):
    try:
        low, high = adj.get_limits()
        low, high = float(low), float(high)
    except Exception:
        return -math.inf, math.inf
    if not (math.isfinite(low) or math.isfinite(high)) or low == high:
        return -math.inf, math.inf  # (0, 0) is "no limits" for motor records
    return low, high


def _name(adj):
    try:
        from eco.utilities.utilities import get_eco_name

        return get_eco_name(adj)
    except Exception:
        return getattr(adj, "name", None) or repr(adj)


def parse_setup(axes, follow=None, limits=10):
    """Adjustables, start values, steps, bounds and followers from
    ``Scans.optimize``'s arguments (reads the current values)."""
    from eco.elements.protocols import resolve_lazy

    if not axes:
        raise ValueError("optimize: give at least one axis, [adjustable, step]")
    adjustables, steps, bounds = [], [], []
    n = len(axes)
    limits = np.broadcast_to(np.asarray(limits, float), (n,))
    for spec, lim in zip(axes, limits):
        spec = list(spec)
        if len(spec) not in (2, 3, 4):
            raise ValueError(f"optimize: axis {spec!r}: [adjustable, step] or [adjustable, step, (low, high)]")
        adj = resolve_lazy(spec[0])
        step = float(spec[1])
        if not step > 0:
            raise ValueError(f"optimize: {_name(adj)}: step must be > 0")
        start = float(adj.get_current_value())
        if len(spec) == 2:
            low, high = start - lim * step, start + lim * step
        else:
            low, high = (spec[2] if len(spec) == 3 else spec[2:4])
            low, high = float(low), float(high)
        alow, ahigh = _adjustable_limits(adj)
        low, high = max(low, alow), min(high, ahigh)
        if not low <= start <= high:
            raise ValueError(f"optimize: {_name(adj)} is at {start}, outside its range {(low, high)}")
        adjustables.append(adj)
        steps.append(step)
        bounds.append((low, high))
    start = np.array([float(a.get_current_value()) for a in adjustables])

    def axis_index(obj, what):
        obj = resolve_lazy(obj)
        for i, adj in enumerate(adjustables):
            if adj is obj:
                return i
        raise ValueError(f"optimize: follow: {what} {_name(obj)} is not one of the axes")

    calibrate, followers, follower_adjs = [], [], []
    for key, value in (follow or {}).items():
        if isinstance(value, str):
            if value != "calibrate":
                raise ValueError(f'optimize: follow={{{_name(key)}: {value!r}}}: "calibrate" or a dict')
            calibrate.append(axis_index(key, "calibrated axis"))
            continue
        follower = resolve_lazy(key)
        if any(follower is a for a in adjustables):
            raise ValueError(
                f"optimize: follow: {_name(follower)} is an axis; give it \"calibrate\" "
                "or leave it out of the axes"
            )
        coef = np.zeros(n)
        for axis, c in dict(value).items():
            coef[axis_index(axis, "axis")] = float(c)
        followers.append((float(follower.get_current_value()), coef, _adjustable_limits(follower)))
        follower_adjs.append(follower)
    if calibrate and not len(calibrate) < n:
        raise ValueError("optimize: follow: at least one axis must stay free")
    return SimpleNamespace(
        adjustables=adjustables + follower_adjs,
        names=[_name(a) for a in adjustables],
        follower_names=[_name(a) for a in follower_adjs],
        start=start,
        steps=np.array(steps),
        bounds=np.array(bounds),
        followers=followers,
        calibrate=calibrate,
    )


# --------------------------------------------------------------------------
# models


def fit_quadratic(U, y, yerr, center, dims, radius=PROBE_RADIUS, sigma=0.0, full=True):
    """Weighted local quadratic fit in the coordinates `dims` around `center`:
    returns dict(a, g, H, se_H, dims, n) or None if under-determined. Points
    are weighted by 1/(yerr^2 + sigma^2) and a Gaussian of `radius` steps."""
    dims = list(dims)
    k = len(dims)
    if not len(y) or not k:
        return None
    D = U[:, dims] - center[dims]
    dist2 = (D**2).sum(1)
    keep = dist2 <= (3 * radius) ** 2
    pairs = [(i, j) for i in range(k) for j in range(i + 1, k)] if full else []
    p = 1 + 2 * k + len(pairs)
    if keep.sum() < p + 2:
        if full:
            return fit_quadratic(U, y, yerr, center, dims, radius, sigma, full=False)
        return None
    D, yy, ee, d2 = D[keep], y[keep], yerr[keep], dist2[keep]
    cols = [np.ones(len(yy))] + [D[:, i] for i in range(k)] + [0.5 * D[:, i] ** 2 for i in range(k)]
    cols += [D[:, i] * D[:, j] for i, j in pairs]
    A = np.column_stack(cols)
    w = np.exp(-0.5 * d2 / radius**2) / (ee**2 + sigma**2 + 1e-300)
    sw = np.sqrt(w)
    Aw = A * sw[:, None]
    if np.linalg.matrix_rank(Aw) < p:
        if full:
            return fit_quadratic(U, y, yerr, center, dims, radius, sigma, full=False)
        return None
    coef, *_ = np.linalg.lstsq(Aw, yy * sw, rcond=None)
    resid = (yy - A @ coef) * sw
    dof = max(len(yy) - p, 1)
    scale = max(float(resid @ resid) / dof, 1.0)
    cov = np.linalg.pinv(Aw.T @ Aw) * scale
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    H = np.zeros((k, k))
    se_H = np.zeros((k, k))
    H[np.arange(k), np.arange(k)] = coef[1 + k : 1 + 2 * k]
    se_H[np.arange(k), np.arange(k)] = se[1 + k : 1 + 2 * k]
    for (i, j), c, s in zip(pairs, coef[1 + 2 * k :], se[1 + 2 * k :]):
        H[i, j] = H[j, i] = c
        se_H[i, j] = se_H[j, i] = s
    return dict(a=coef[0], g=coef[1 : 1 + k], H=H, se_H=se_H, dims=dims, n=int(keep.sum()), full=bool(pairs))


class GaussianProcess:
    """Small GP (squared-exponential, one length per dimension, constant
    mean at the low end of the data) for the check phase and the landscape plots.
    Lengths in steps."""

    def __init__(self, X, y, yerr, lengths, amplitude=None, mean=None):
        self.X = np.asarray(X, float)
        self.y = np.asarray(y, float)
        self.noise2 = np.asarray(yerr, float) ** 2 + 1e-12
        self.lengths = np.asarray(lengths, float)
        # away from the data the signal may be anything from the lowest to
        # the highest value measured: the mean at the low end, that range as
        # spread (so unexplored space always allows a better maximum, until
        # it is measured)
        self.mean = float(np.percentile(y, 2)) if mean is None else mean
        if amplitude is None:
            amplitude = max(float(np.max(y) - self.mean), float(np.std(y)))
        self.amp2 = amplitude**2 + 1e-12
        K = self._k(self.X, self.X) + np.diag(self.noise2)
        self.L = np.linalg.cholesky(K + 1e-10 * self.amp2 * np.eye(len(K)))
        self.alpha = np.linalg.solve(self.L.T, np.linalg.solve(self.L, self.y - self.mean))

    def _k(self, A, B):
        d = (A[:, None, :] - B[None, :, :]) / self.lengths
        return self.amp2 * np.exp(-0.5 * (d**2).sum(-1))

    def log_likelihood(self):
        r = self.y - self.mean
        return float(-0.5 * r @ self.alpha - np.log(np.diag(self.L)).sum())

    def predict(self, Xs):
        Xs = np.atleast_2d(np.asarray(Xs, float))
        Ks = self._k(Xs, self.X)
        mu = self.mean + Ks @ self.alpha
        v = np.linalg.solve(self.L, Ks.T)
        var = np.clip(self.amp2 - (v**2).sum(0), 0, None)
        return mu, np.sqrt(var)

    @classmethod
    def fitted(cls, X, y, yerr, lengths):
        """The best of a few overall scalings of `lengths`, by likelihood."""
        best = None
        for f in (0.5, 0.7, 1.0, 1.4, 2.0, 3.0):
            try:
                gp = cls(X, y, yerr, np.asarray(lengths) * f)
            except np.linalg.LinAlgError:
                continue
            ll = gp.log_likelihood()
            if best is None or ll > best[0]:
                best = (ll, gp)
        return None if best is None else best[1]


# --------------------------------------------------------------------------
# the strategy


class _Plan:
    """What ``AdaptiveDriver`` takes from a strategy's ``plan``."""

    def __init__(self, positions, exposures, ndim):
        self.positions = np.asarray(positions, float).reshape(len(exposures), ndim)
        self.exposures = list(exposures)

    def __len__(self):
        return len(self.exposures)


class Optimizer:
    """Maximises a noisy signal over `start`-centred axes; see the module
    docstring. The ``AdaptiveDriver`` strategy interface: ``plan``, ``add``,
    ``ndim``, ``n_initial``, ``n_points``, ``yerr``, ``exposure_model``,
    ``summary``. All arrays per axis; `followers` as built by
    ``parse_setup`` ((start, coefficients per axis, limits) each)."""

    def __init__(
        self, start, steps, bounds, names=None, followers=(), calibrate=(),
        exposure=1, repeats=3, pairs=True, max_step=4.0, cycles=4, check=5,
        restarts=1, seed=0, verbose=True,
    ):
        self.start = np.asarray(start, float)
        self.steps = np.asarray(steps, float)
        self.n = len(self.start)
        self.names = list(names) if names else [f"axis{i}" for i in range(self.n)]
        bounds = np.asarray(bounds, float).reshape(self.n, 2)
        self.ubounds = (bounds - self.start[:, None]) / self.steps[:, None]  # in steps
        self.followers = list(followers)
        self.calibrate = list(calibrate)
        self.ndim = self.n + len(self.followers)
        self.exposure_base = exposure
        self.exposure_model = SimpleNamespace(overhead=0.0)  # the driver sets it
        self.motion = None
        self.repeats = max(int(repeats), 1)
        self.pairs = pairs
        self.max_step = float(max_step)
        self.cycles = int(cycles)
        self.check = int(check)
        self.restarts = int(restarts)
        self.verbose = verbose
        self.rng = np.random.default_rng(seed)
        self.n_initial = 10**6  # the whole first batch (the probe)

        self.cal = None  # calibration of the `calibrate` axes, once done
        self.free = list(range(self.n))
        self.model = None  # last quadratic fit (dict, see fit_quadratic)
        self._L = np.full(self.n, 3.0)  # GP lengths in steps, from the fits
        self.directions = []  # (vector in steps over all axes, curvature)
        self.best_u = np.zeros(self.n)
        self.best_y = None
        self.sigma = 0.0
        self.unexplored = []  # check candidates: (u, mean, sd)
        self.check_result = None
        self.log = []  # what happened, for summary()

        self._U = np.zeros((0, self.n))
        self.y = np.zeros(0)
        self.yerr = np.zeros(0)
        self.phase = []
        self._issued = []  # [u, phase, y or None] per planned point, in order
        self._answered = 0  # issued points up to here have a value (or none)
        self._queue = []
        self._batch = []
        self._gen = self._algorithm()
        self._finished = False
        self._next_batch(first=True)

    # -- driver interface ------------------------------------------------------
    @property
    def n_points(self):
        return len(self.y)

    @property
    def x(self):
        return self.start + self._U * self.steps

    @property
    def finished(self):
        """The algorithm ran to its end (the best point is measured)."""
        return self._finished and not self._queue

    def absolute(self, u, complete=True):
        """Scan values (axes, then followers) of the point `u` (in steps);
        `complete`: set the calibrated axes from the free ones first."""
        u = self.complete(u) if complete else np.asarray(u, float)
        x = self.start + u * self.steps
        out = list(x)
        for f_start, coef, (low, high) in self.followers:
            out.append(float(np.clip(f_start + coef @ (x - self.start), low, high)))
        return np.array(out)

    def plan(self, n=1, position=None, reorder=None):
        if not self._queue and not self._finished:
            self._next_batch()
        take, self._queue = self._queue[:n], self._queue[n:]
        # points are clipped and completed when the algorithm makes them
        self._issued.extend([[u, phase, None] for u, phase in take])
        return _Plan(
            [self.absolute(u, complete=False) for u, _ in take], [self.exposure_base] * len(take), self.ndim
        )

    def add(self, x, y, yerr, exposure=None, refit=True):
        X = np.asarray(x, float).reshape(-1, self.ndim)
        for xi, yi, ei in zip(X, np.ravel(y), np.ravel(yerr)):
            u = (xi[: self.n] - self.start) / self.steps
            record = None
            for k in range(self._answered, len(self._issued)):
                if np.allclose(self._issued[k][0], u, atol=1e-6, rtol=0):
                    record = self._issued[k]
                    self._answered = k + 1  # points before it got no value
                    break
            phase = record[1] if record else "extra"
            if record is not None:
                record[2] = (float(yi), float(ei))
            self._U = np.vstack([self._U, u])
            self.y = np.append(self.y, float(yi))
            self.yerr = np.append(self.yerr, float(ei))
            self.phase.append(phase)

    def needs_final(self):
        """True if the scan ended early (budget): measure the best once."""
        return not self._finished and self.best_y is not None

    def final_plan(self):
        self._finished = True
        self._issued.append([self.best_u.copy(), "best", None])
        return _Plan([self.absolute(self.best_u)], [self.exposure_base], self.ndim)

    # -- generator plumbing ------------------------------------------------------
    def _next_batch(self, first=False):
        """Hand the measured values of the last batch to the algorithm and
        queue its next batch (nothing once it is done)."""
        try:
            if first:
                batch = next(self._gen)
            else:
                recs = self._issued[len(self._issued) - len(self._batch) :]
                ys = np.array([np.nan if r[2] is None else r[2][0] for r in recs])
                errs = np.array([np.nan if r[2] is None else r[2][1] for r in recs])
                batch = self._gen.send((ys, errs))
        except StopIteration:
            self._finished = True
            self._batch = []
            return
        self._batch = batch
        self._queue = list(batch)

    def _measure(self, points, phase):
        """Generator step: measure `points` (in steps); returns their values
        (NaN where no value came)."""
        points = [self.clip(p) for p in points]
        if not points:
            return np.zeros(0)
        ys, errs = yield [(p, phase) for p in points]
        # a value whose own spread is far above the usual one is mixed up
        # with the move (stale detector samples, a beam drop): once more
        typical = float(np.median(self.yerr)) if len(self.yerr) else 0.0
        bad = [k for k, e in enumerate(errs) if np.isfinite(e) and typical > 0
               and e > SUSPICIOUS_ERROR * typical and e > 3 * self.sigma]
        if bad:
            ys2, errs2 = yield [(points[k], phase) for k in bad]
            ys = np.array(ys, float)
            for k, y2, e2 in zip(bad, ys2, errs2):
                if np.isfinite(y2) and not e2 > errs[k]:
                    ys[k] = y2
        return ys

    # -- geometry ----------------------------------------------------------------
    def complete(self, u):
        """`u` with the calibrated axes set from the free ones."""
        u = np.array(u, float)
        if self.cal is not None:
            c, D, F = self.cal["center"], self.cal["D"], self.cal["F"]
            u[D] = c[D] - self.cal["Hinv"] @ (self.cal["gD"] + self.cal["HDF"] @ (u[F] - c[F]))
        return u

    def clip(self, u):
        u = np.clip(np.asarray(u, float), self.ubounds[:, 0], self.ubounds[:, 1])
        u = self.complete(u)
        return np.clip(u, self.ubounds[:, 0], self.ubounds[:, 1])

    def _line_range(self, u, v):
        """(t_min, t_max) of u + t v inside the bounds (free axes)."""
        lo, hi = -math.inf, math.inf
        for i in np.flatnonzero(np.abs(v) > 1e-12):
            a = (self.ubounds[i, 0] - u[i]) / v[i]
            b = (self.ubounds[i, 1] - u[i]) / v[i]
            lo, hi = max(lo, min(a, b)), min(hi, max(a, b))
        return lo, hi

    def _say(self, text):
        self.log.append(text)
        if self.verbose:
            print(f"optimize: {text}")

    # -- noise and models ----------------------------------------------------------
    def _update_sigma(self):
        """Noise of one measurement: pooled spread of repeated points, at
        least the typical error the feedback reports."""
        groups = {}
        for u, y, ph in zip(self._U, self.y, self.phase):
            if ph in ("probe", "best") and np.isfinite(y):
                groups.setdefault(tuple(np.round(u, 6)), []).append(y)
        ss, dof = 0.0, 0
        for v in groups.values():
            if len(v) > 1:
                ss += float(np.sum((np.array(v) - np.mean(v)) ** 2))
                dof += len(v) - 1
        rep = math.sqrt(ss / dof) if dof else 0.0
        err = float(np.median(self.yerr)) if len(self.yerr) else 0.0
        self.sigma = max(rep, err, 1e-12 * (abs(float(np.nanmax(np.abs(self.y)))) if len(self.y) else 1.0))

    def _ok(self):
        return np.isfinite(self.y)

    def _refit(self, center, calibrate=False):
        """Quadratic model around `center`, directions, and with `calibrate`
        (probes only: centred, structured data) the calibration if possible."""
        ok = self._ok()
        dims = self.free if self.cal is not None else list(range(self.n))
        if self.cal is not None:
            # only points on the calibrated relation describe the profile
            D = self.cal["D"]
            on = np.array([np.abs(self.complete(u)[D] - u[D]).max() < 0.5 for u in self._U], bool)
            ok = ok & on
        m = fit_quadratic(self._U[ok], self.y[ok], self.yerr[ok], center, dims, sigma=self.sigma, full=self.pairs)
        if m is None:
            if not self.directions:
                self.directions = [(np.eye(self.n)[i], 0.0) for i in self.free]
            return
        self.model = dict(m, center=center.copy())
        peak = max(abs(self.best_y or 0.0), self.sigma)
        for k, i in enumerate(m["dims"]):
            h = -m["H"][k, k]
            if h > 0:
                self._L[i] = math.sqrt(peak / h)
        if calibrate and self.cal is None and self.calibrate:
            self._try_calibration(center)
        self._set_directions()

    def _try_calibration(self, center):
        m = self.model
        if not m["full"]:
            self._say("calibration needs the pair probes (pairs=True); the axes stay free")
            self.calibrate = []
            return
        dims = m["dims"]
        D = [dims.index(i) for i in self.calibrate]
        F = [k for k in range(len(dims)) if k not in D]
        H, se = m["H"], m["se_H"]
        HDD = H[np.ix_(D, D)]
        eig = np.linalg.eigvalsh(HDD)
        # a significant maximum along every calibrated axis
        if not (eig.max() < 0 and np.all(np.diag(HDD) < -3 * np.diag(se[np.ix_(D, D)]))):
            self._say("no clear maximum along " + ", ".join(self.names[i] for i in self.calibrate)
                      + " yet; calibrating later")
            return
        Hinv = np.linalg.inv(HDD)
        # away from its own maximum the coupling of a calibrated axis is
        # biased (f = field * waveform(delay): cross terms field' * waveform'
        # that are no timing at all): only near it
        offset = -Hinv @ m["g"][D]
        if np.abs(offset).max() > CALIBRATION_OFFSET:
            self._say(
                ", ".join(self.names[i] for i in self.calibrate)
                + f" not near its best yet ({np.abs(offset).max():.1f} steps away); calibrating later"
            )
            return
        self.cal = dict(
            center=center.copy(), D=np.array(self.calibrate), F=np.array([dims[k] for k in F]),
            gD=m["g"][D], HDF=H[np.ix_(D, F)], Hinv=Hinv,
        )
        self.free = [dims[k] for k in F]
        K = -Hinv @ H[np.ix_(D, F)]  # steps per step
        self.follow_coefficients = {}
        for a, d in enumerate(self.calibrate):
            coefs = {self.names[f]: float(K[a, b] * self.steps[d] / self.steps[f]) for b, f in enumerate(self.free)}
            self.follow_coefficients[self.names[d]] = coefs
            terms = " ".join(f"{c:+.4g}*d({n})" for n, c in coefs.items())
            self._say(f"calibrated: d({self.names[d]}) = {terms}; it follows from now on")
        # the best point moves onto the calibrated relation
        self.best_u = self.complete(self.best_u)

    def _set_directions(self):
        m = self.model
        dims = m["dims"]
        H = m["H"]
        if self.cal is not None and set(self.calibrate) <= set(dims):
            # profile over the calibrated axes: Schur complement
            D = [dims.index(i) for i in self.calibrate]
            F = [k for k in range(len(dims)) if k not in D]
            H = H[np.ix_(F, F)] - H[np.ix_(F, D)] @ np.linalg.inv(H[np.ix_(D, D)]) @ H[np.ix_(D, F)]
            dims = [dims[k] for k in F]
        lam, vec = np.linalg.eigh(H)
        order = np.argsort(-np.abs(lam))
        self.directions = []
        for k in order:
            v = np.zeros(self.n)
            v[dims] = vec[:, k]
            self.directions.append((v, float(lam[k])))
        self.newton = None
        g = m["g"] if list(m["dims"]) == list(dims) else None
        if g is not None and lam.max() < 0:
            step = -np.linalg.solve(H, g)
            norm = float(np.linalg.norm(step))
            if 0.3 < norm < 3 * self.max_step:
                v = np.zeros(self.n)
                v[dims] = step / norm
                self.newton = v

    def eigen_axes(self):
        """[(direction over all axes in steps, curvature per step^2,
        {axis name: component in axis units})] of the last model."""
        out = []
        for v, lam in self.directions:
            out.append((v, lam, {self.names[i]: float(v[i] * self.steps[i]) for i in range(self.n) if abs(v[i]) > 1e-9}))
        return out

    # -- the algorithm -------------------------------------------------------------
    def _algorithm(self):
        center = np.zeros(self.n)
        for _ in range(self.restarts + 1):
            yield from self._probe(center)
            center, _ = yield from self._local(center, self.best_y)
            yield from self._probe(center, final=True)
            if self.cal is not None and self._calibrated_late:
                center, _ = yield from self._local(center, self.best_y, cycles=1)
            found = yield from self._check(center)
            if found is None:
                break
            self._say(f"better maximum found ({found[1]:.4g}): optimising from there")
            center = self.clip(found[0])
            self.best_u, self.best_y = center.copy(), found[1]
        yield from self._measure([self.best_u] * self.repeats, "best")
        ys = self.y[[i for i, ph in enumerate(self.phase) if ph == "best"]][-self.repeats :]
        ys = ys[np.isfinite(ys)]
        if len(ys):
            self._say(f"best point measured {len(ys)}x: {np.mean(ys):.4g} +- {np.std(ys):.2g}")

    def _probe(self, center, final=False):
        was_calibrated = self.cal is not None
        center = self.clip(center)
        pts = [center] * self.repeats
        free = self.free
        for i in free:
            for s in (1, -1):
                pts.append(center + s * np.eye(self.n)[i])
        if self.pairs:
            for a in range(len(free)):
                for b in range(a + 1, len(free)):
                    e = np.eye(self.n)
                    pts.append(center + e[free[a]] + e[free[b]])
                    pts.append(center + e[free[a]] - e[free[b]])
        ys = yield from self._measure(pts, "probe")
        self._update_sigma()
        rep = ys[: self.repeats]
        rep = rep[np.isfinite(rep)]
        if len(rep):
            # the centre is the best point so far; its repeats are a better
            # estimate of its value than a line search's fitted one
            self.best_u, self.best_y = self.clip(center), float(np.mean(rep))
        self._refit(self.clip(center), calibrate=True)
        self._calibrated_late = final and not was_calibrated and self.cal is not None
        if self.cal is not None and not was_calibrated:
            self.best_u = self.complete(self.best_u)
        self._store()

    def _local(self, center, f, cycles=None, newton=True, quiet=False):
        """Line-search cycles from `center` (value `f`) along the directions;
        returns (centre, value) reached. The global best follows."""
        center = self.clip(center)
        for cycle in range(self.cycles if cycles is None else cycles):
            before = f
            dirs = [v for v, _ in self.directions]
            if newton and getattr(self, "newton", None) is not None:
                dirs = [self.newton] + dirs
            for v in dirs:
                center, f = yield from self._line(center, f, v)
                if f > self.best_y:
                    self.best_u, self.best_y = center.copy(), f
            gain = f - before
            if not quiet:
                self._say(f"cycle {cycle + 1}: {f:.4g} (gain {gain:+.3g}, noise {self.sigma:.2g})")
            if gain < 2 * self.sigma:
                break
            self._refit(center)
            self._store()
        return center, f

    def _line(self, center, f0, v):
        """RCDS-style line search from `center` (value `f0`) along `v`;
        returns (centre, value): the line's maximum if better, else the
        start."""
        sigma = self.sigma
        tmin, tmax = self._line_range(center, v)
        ts, fs = [0.0], [f0]

        def at(t):
            return self.clip(center + t * v)

        def bracket(sign):
            t, step = 0.0, 1.0
            best_f = max(fs)
            while len(ts) < LINE_MAX_POINTS:
                t_new = t + sign * min(step, self.max_step)
                t_new = min(max(t_new, tmin), tmax)
                if abs(t_new - t) < 1e-9:
                    return
                (f,) = yield from self._measure([at(t_new)], "line")
                if not np.isfinite(f):
                    return
                ts.append(t_new)
                fs.append(f)
                if f < best_f - 3 * sigma:
                    return
                best_f = max(best_f, f)
                t, step = t_new, step * GOLD

        yield from bracket(+1)
        forward_better = max(fs[1:], default=-math.inf) > fs[0]
        if not forward_better:
            yield from bracket(-1)
        # fill the bracket to at least 6 points
        lo, hi = min(ts), max(ts)
        if hi - lo > 1e-9 and len(ts) < 6:
            fill = [t for t in np.linspace(lo, hi, 8)[1:-1] if min(abs(t - s) for s in ts) > 0.3]
            fill = fill[: 6 - len(ts)]
            if fill:
                ys = yield from self._measure([at(t) for t in fill], "line")
                for t, f in zip(fill, ys):
                    if np.isfinite(f):
                        ts.append(t)
                        fs.append(f)
        t_best, f_best = self._line_peak(np.array(ts), np.array(fs), sigma)
        if f_best > f0:
            return at(t_best), f_best
        return center, f0

    @staticmethod
    def _line_peak(ts, fs, sigma):
        """Maximum along a line: vertex of a parabola through the points (one
        round of outlier removal) if it is concave and inside, else the best
        point -- as (t, value); the value is the fitted one."""
        i = int(np.argmax(fs))
        if len(ts) >= 4 and np.ptp(ts) > 0:
            keep = np.ones(len(ts), bool)
            for _ in range(2):
                c = np.polyfit(ts[keep], fs[keep], 2)
                r = fs - np.polyval(c, ts)
                new = np.abs(r) < max(3 * sigma, 1e-300) * 1.5
                if new.sum() < 4 or (new == keep).all():
                    break
                keep = new
            if c[0] < 0:
                t = -c[1] / (2 * c[0])
                if ts.min() <= t <= ts.max():
                    return float(t), float(np.polyval(c, t))
            return float(ts[i]), float(np.polyval(c, ts[i]))
        return float(ts[i]), float(fs[i])

    # -- check for a better maximum ----------------------------------------------
    def lengths(self):
        """GP length per axis in steps, from the model's curvature (a
        Gaussian peak of height f has curvature -f/l^2)."""
        return np.clip(self._L, 1.0, 20.0)

    def gp(self):
        ok = self._ok()
        if ok.sum() < 5:
            return None
        return GaussianProcess.fitted(self._U[ok], self.y[ok], np.maximum(self.yerr[ok], 0.3 * self.sigma), self.lengths())

    def candidates(self, n=CHECK_CANDIDATES):
        """Random points within the bounds (free axes; calibrated ones follow)."""
        lo, hi = self.ubounds[:, 0], self.ubounds[:, 1]
        lo = np.where(np.isfinite(lo), lo, -20.0)
        hi = np.where(np.isfinite(hi), hi, 20.0)
        U = lo + (hi - lo) * self.rng.random((n, self.n))
        return np.array([self.clip(u) for u in U])

    def _check(self, center):
        """Up to `check` points, one at a time: the model (GP through all
        points) is refitted after each, the next point is where its upper
        bound is highest. Returns (u, value) of a clearly better point, or
        None."""
        measured = []
        best_before_check = self.best_y
        for i in range(self.check):
            gp = self.gp()
            if gp is None:
                return None
            C = self.candidates()
            mu, sd = gp.predict(C)
            ucb = mu + 2 * sd
            threshold = self.best_y + 2 * self.sigma
            order = np.argsort(-ucb)
            self.unexplored = [(C[k], float(mu[k]), float(sd[k])) for k in order[:20] if ucb[k] > threshold]
            if not self.unexplored:
                break
            k = order[0]
            if i == 0:
                self._say(f"check: a better maximum is possible (bound {ucb[k]:.3g} > best {self.best_y:.3g})")
            (y,) = yield from self._measure([C[k]], "check")
            if not np.isfinite(y):
                continue
            measured.append(y)
            u = self.clip(C[k])
            mu_k = float(mu[k])
            if y <= self.best_y + 3 * self.sigma and y > mu_k + 3 * self.sigma + SURPRISE * sd[k]:
                # higher than the model expected there: the flank of another
                # maximum? a short local search from it
                self._say(f"check: {y:.3g} where {mu_k:.3g} was expected -- searching around it")
                best_before = self.best_y
                u, y = yield from self._local(u, y, cycles=2, newton=False, quiet=True)
                if self.best_y > best_before:  # the global best moved there
                    y = self.best_y
            if y > self.best_y - 1e-12 and y > best_before_check + 3 * self.sigma:
                self.check_result = f"found a better point ({y:.4g} > {best_before_check:.4g})"
                self._store()
                return u, float(y)
        if not measured:
            self.check_result = "the model allows no clearly better maximum within the limits"
        elif self.unexplored:
            self.check_result = (
                f"{len(measured)} check point(s) measured, none better (highest {max(measured):.4g}); "
                f"{len(self.unexplored)} candidate(s) still allow a better maximum"
            )
        else:
            self.check_result = (
                f"{len(measured)} check point(s) measured, none better (highest {max(measured):.4g}); "
                "the model now allows no clearly better maximum"
            )
        self._say(f"check: {self.check_result}")
        self._store()
        return None

    # -- results -------------------------------------------------------------------
    def best(self):
        """{axis or follower name: value} of the best point."""
        values = self.absolute(self.best_u)
        names = self.names + [f"follower{i}" for i in range(len(self.followers))]
        return dict(zip(names, map(float, values)))

    def parameters(self):
        """What is stored in the scan (scan_parameters["optimize"])."""
        out = {
            "axes": self.names,
            "start": self.start.tolist(),
            "steps": self.steps.tolist(),
            "best": self.absolute(self.best_u).tolist(),
            "best_value": self.best_y,
            "noise": self.sigma,
            "phases": list(self.phase),
            "check": self.check_result,
        }
        if self.directions:
            out["directions"] = [
                {"curvature_per_step2": lam, "components": comps} for _, lam, comps in self.eigen_axes()
            ]
        if self.cal is not None:
            out["follow_coefficients"] = self.follow_coefficients
        return out

    def _store(self):
        cb = getattr(self, "on_update", None)
        if cb is not None:
            cb()

    def summary(self):
        lines = [f"optimize: {self.n_points} points, best {self.best_y:.4g} (noise {self.sigma:.2g})"]
        for name, s, b, st in zip(self.names, self.start, self.absolute(self.best_u), self.steps):
            lines.append(f"  {name:30s} {s:+.6g} -> {b:+.6g}  ({(b - s) / st:+.2f} steps)")
        for (f_start, _, _), b in zip(self.followers, self.absolute(self.best_u)[self.n :]):
            lines.append(f"  {'(follower)':30s} {f_start:+.6g} -> {b:+.6g}")
        if self.cal is not None:
            for d, coefs in self.follow_coefficients.items():
                lines.append(f"  {d} follows: " + ", ".join(f"{c:+.4g} per {n}" for n, c in coefs.items()))
        if self.directions:
            lines.append("  directions (curvature per step^2; components in axis units per step):")
            for k, (_, lam, comps) in enumerate(self.eigen_axes()):
                comp = ", ".join(f"{n} {c:+.3g}" for n, c in comps.items() if abs(c) > 1e-12)
                lines.append(f"    dir{k}: {lam:+.3g}   {comp}")
        if self.check_result:
            lines.append(f"  check: {self.check_result}")
        return "\n".join(lines)

    # -- plots -----------------------------------------------------------------------
    PHASE_COLORS = {"probe": "C0", "line": "C1", "check": "C3", "best": "C2", "extra": "C7"}

    def plot_progress(self, fig):
        """Live plot: signal per point coloured by phase, best so far."""
        ax = fig.add_subplot(111)
        y = self.y
        idx = np.arange(len(y))
        for ph, col in self.PHASE_COLORS.items():
            m = np.array([p == ph for p in self.phase], bool)
            if m.any():
                ax.errorbar(idx[m], y[m], self.yerr[m], fmt=".", color=col, label=ph)
        if len(y):
            ax.plot(idx, np.fmax.accumulate(np.where(np.isfinite(y), y, -np.inf)), "k-", lw=0.8, label="highest so far")
        ax.set_xlabel("point")
        ax.set_ylabel("signal")
        ax.legend(fontsize="small")

    def landscape(self, fig=None, dims=None, resolution=60):
        """The measured field after the run: signal against the projection on
        each eigen-direction (all points, by phase), and the GP's mean and
        uncertainty on the plane of the two strongest directions (or of the
        two axes `dims`, names or indices) through the best point, all
        measured points projected onto it. Slices hide maxima off the plane:
        the uncertainty map says where the data is thin; the points where the
        check phase still allowed a better maximum are in ``unexplored``."""
        import matplotlib.pyplot as plt

        fig = fig or plt.figure(figsize=(12, 7))
        dirs = [v for v, _ in self.directions] or [np.eye(self.n)[i] for i in range(self.n)]
        nd = len(dirs)
        gs = fig.add_gridspec(2, max(nd, 2))
        rel = self._U - self.best_u
        for k, v in enumerate(dirs):
            ax = fig.add_subplot(gs[0, k])
            proj = rel @ v
            for ph, col in self.PHASE_COLORS.items():
                m = np.array([p == ph for p in self.phase], bool)
                if m.any():
                    ax.plot(proj[m], self.y[m], ".", color=col, ms=4, label=ph)
            ax.set_xlabel(f"dir{k} (steps)")
            if k == 0:
                ax.set_ylabel("signal")
                ax.legend(fontsize="x-small")
        if dims is not None:
            e = np.eye(self.n)
            vs = [e[self.names.index(d) if isinstance(d, str) else d] for d in dims]
            labels = [f"{self.names[int(np.argmax(np.abs(v)))]} (steps)" for v in vs]
        else:
            vs = (dirs + [np.eye(self.n)[0]])[:2] if nd >= 2 else [dirs[0], np.eye(self.n)[1 % self.n]]
            labels = ["dir0 (steps)", "dir1 (steps)"]
        gp = self.gp()
        span = np.max(np.abs(rel)) if len(rel) else 5.0
        a = np.linspace(-span, span, resolution)
        A, B = np.meshgrid(a, a)
        P = self.best_u + A.ravel()[:, None] * vs[0] + B.ravel()[:, None] * vs[1]
        P = np.array([self.complete(p) for p in P])
        for col, what in ((0, "mean"), (1, "uncertainty")):
            ax = fig.add_subplot(gs[1, col])
            if gp is not None:
                mu, sd = gp.predict(P)
                img = (mu if what == "mean" else sd).reshape(A.shape)
                pc = ax.pcolormesh(A, B, img, shading="auto")
                fig.colorbar(pc, ax=ax, label=f"model {what}")
            ax.scatter(rel @ vs[0], rel @ vs[1], c=self.y, s=8, edgecolors="k", linewidths=0.3)
            ax.set_xlabel(labels[0])
            ax.set_ylabel(labels[1])
            ax.set_title(f"model {what}, plane through the best point", fontsize="small")
        fig.tight_layout()
        return fig


# --------------------------------------------------------------------------
# the driver


class OptimizeDriver(AdaptiveDriver):
    """``AdaptiveDriver`` for an ``Optimizer``: points anywhere, nothing
    learned about motion, the best point measured last (also when a budget
    ends the scan early), the strategy's state stored in the scan as
    ``scan_parameters["optimize"]``. After the scan: ``scan.optimizer``."""

    def __init__(self, strategy, adjustables, feedback, n_pulses, budget=None, max_points=None, plot=True):
        steps = list(strategy.steps) + [0.0] * len(strategy.followers)
        super().__init__(
            strategy, adjustables, [[a] for a in strategy.absolute(np.zeros(strategy.n))],
            feedback, n_pulses, budget=budget, max_points=max_points, batch=10**6,
            motion=None, plot=plot, resolution=[0.1 * s for s in steps], name="optimize",
        )
        self.description = "optimize"
        strategy.on_update = self._store
        self._final_added = False

    def _on_start(self, scan, **kwargs):
        super()._on_start(scan, **kwargs)
        scan.optimizer = self
        self._store()

    def _store(self):
        scan = self._scan() if self._scan is not None else None
        if scan is not None:
            try:
                scan.set_scan_parameter("optimize", self.strategy.parameters())
            except Exception:
                pass

    def _refill(self, scan):
        super()._refill(scan)
        if self._done and not self._final_added and self.strategy.needs_final():
            self._final_added = True
            print("optimize: budget used up -- measuring the best point once more and ending there")
            self._append_steps(scan, self._steps_from_plan(self.strategy.final_plan()))

    def _update_expected(self, scan):
        # the strategy's own queue, not the (huge) batch size
        super()._update_expected(scan)
        queued = len(self.strategy._queue)
        remaining = self._remaining_points(scan)
        scan.extra_expected_steps = 0 if self._done else (
            queued if remaining is None else min(queued, remaining)
        )

    def _on_end(self, scan, **kwargs):
        self._store()
        super()._on_end(scan, **kwargs)

    def _draw(self):
        try:
            import matplotlib.pyplot as plt

            if self._fig is None or not plt.fignum_exists(self._fig.number):
                self._fig = plt.figure(num="optimize")
            self._fig.clf()
            self.strategy.plot_progress(self._fig)
            self._fig.canvas.draw_idle()
            plt.show(block=False)
        except Exception as exc:
            if not getattr(self, "_plot_failed", False):
                self._plot_failed = True
                print(f"optimize: plot failed ({type(exc).__name__}: {exc})")

    # -- results ---------------------------------------------------------------
    def best(self):
        """{adjustable name: value} of the best point (axes and followers)."""
        names = [_name(a) for a in self.adjustables]
        return dict(zip(names, map(float, self.strategy.absolute(self.strategy.best_u))))

    def move_to_best(self):
        """Move all adjustables to the best point."""
        values = self.strategy.absolute(self.strategy.best_u)
        changers = [a.set_target_value(v) for a, v in zip(self.adjustables, values)]
        for c in changers:
            c.wait()

    def landscape(self, **kwargs):
        return self.strategy.landscape(**kwargs)

    def summary(self):
        return self.strategy.summary()

    def virtual_axes(self):
        """The eigen-directions as ``AdjustableVirtual``s over all scan
        adjustables (followers move along), in steps from the best point:
        ``opt.virtual_axes()[0].mv(1.5)``, ``.tweak()``."""
        from eco.elements.adjustable import AdjustableVirtual

        s = self.strategy
        origin = s.best_u.copy()
        out = []
        for k, (v, _, _) in enumerate(s.eigen_axes()):
            def setter(t, v=v):
                return tuple(s.absolute(origin + t * v))

            def getter(*values, v=v):
                u = (np.asarray(values[: s.n], float) - s.start) / s.steps
                return float((u - origin) @ v)

            out.append(AdjustableVirtual(list(self.adjustables), getter, setter, name=f"optimize_dir{k}", unit="steps"))
        return out
