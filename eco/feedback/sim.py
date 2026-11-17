"""Simulated eco-style objects for development without hardware."""

from __future__ import annotations

import random

import numpy as np


class SimAdjustable:
    """Mimics an eco Adjustable."""

    def __init__(self, value=0.0, name="sim_adj"):
        self.name = name
        self._value = value

    def get_current_value(self):
        return self._value

    def set_target_value(self, value):
        self._value = value


class SimDetector:
    """Reads the adjustable plus drift and noise — something to correct."""

    def __init__(self, adjustable, drift=0.02, noise=0.01, name="sim_det"):
        self.name = name
        self.adjustable = adjustable
        self.drift, self.noise = drift, noise
        self._offset = 0.0

    def get_current_value(self):
        self._offset += self.drift * (random.random() - 0.3)
        return (self.adjustable.get_current_value() + self._offset
                + self.noise * random.gauss(0, 1))


class SimCoupledSystem:
    """N correctors -> response matrix -> M detectors, for exercising
    response-matrix acquisition. ``response_matrix[i, j]`` is
    d(detector_i)/d(corrector_j); each detector reads
    ``response_matrix @ correctors`` plus its own drift and noise, so a
    measured matrix can be checked against the known one.
    """

    def __init__(self, response_matrix, drift=0.005, noise=0.01,
                 corrector_names=None, detector_names=None):
        self.response_matrix = np.asarray(response_matrix, dtype=float)
        n_det, n_adj = self.response_matrix.shape
        self._correctors = np.zeros(n_adj)

        corrector_names = corrector_names or [f"corrector_{j}" for j in range(n_adj)]
        detector_names = detector_names or [f"detector_{i}" for i in range(n_det)]

        self.adjustables = [SimCorrector(self, j, name)
                            for j, name in enumerate(corrector_names)]
        self.detectors = [SimCoupledDetector(self, i, name, drift, noise)
                          for i, name in enumerate(detector_names)]


class SimCorrector:
    """One column of a SimCoupledSystem; mimics an eco Adjustable."""

    def __init__(self, system, index, name):
        self.system, self.index, self.name = system, index, name

    def get_current_value(self):
        return self.system._correctors[self.index]

    def set_target_value(self, value):
        self.system._correctors[self.index] = value


class SimCoupledDetector:
    """One row of a SimCoupledSystem; mimics an eco Detector."""

    def __init__(self, system, index, name, drift=0.005, noise=0.01):
        self.system, self.index, self.name = system, index, name
        self.drift, self.noise = drift, noise
        self._offset = 0.0

    def get_current_value(self):
        self._offset += self.drift * (random.random() - 0.5)
        row = self.system.response_matrix[self.index]
        return (float(row @ self.system._correctors) + self._offset
                + self.noise * random.gauss(0, 1))
