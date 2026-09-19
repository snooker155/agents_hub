"""1D time-dependent Schrödinger equation — split-step Fourier method.

Scenario 4 (quantum), the precise counterpart to a hand-wavy animation. Evolves
a Gaussian wave packet under a configurable potential (free / barrier / well)
and streams the probability density |ψ|² as a ``values`` channel, plus the
potential so the renderer can overlay it. Method is unitary and norm-conserving
(reported as a fidelity check).
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np

_N = 512          # grid points (fixed; cheap)
_MAX_OUT = 256    # streamed density is downsampled to this


class Schrodinger1D:
    def __init__(self, params: Dict[str, Any]):
        self.n = _N
        L = float(params.get("length", 20.0))
        self.x = np.linspace(-L / 2, L / 2, self.n)
        self.dx = self.x[1] - self.x[0]
        self.hbar = 1.0
        self.m = float(params.get("mass", 1.0))
        # initial gaussian packet
        x0 = float(params.get("x0", -5.0)); k0 = float(params.get("k0", 3.0))
        sigma = float(params.get("sigma", 1.0))
        self.psi = np.exp(-((self.x - x0) ** 2) / (2 * sigma ** 2)) * np.exp(1j * k0 * self.x)
        self.psi /= np.sqrt(np.sum(np.abs(self.psi) ** 2) * self.dx)
        # potential
        self.V = self._potential(params)
        # momentum grid for the kinetic half-step
        self.k = 2 * np.pi * np.fft.fftfreq(self.n, d=self.dx)

    def _potential(self, params: Dict[str, Any]) -> np.ndarray:
        kind = str(params.get("potential", "barrier"))
        V = np.zeros(self.n)
        if kind == "barrier":
            h = float(params.get("barrier_height", 4.0)); w = float(params.get("barrier_width", 0.5))
            V[np.abs(self.x) < w] = h
        elif kind == "well":
            V[np.abs(self.x) < 1.0] = -float(params.get("well_depth", 4.0))
        elif kind == "harmonic":
            V = 0.5 * float(params.get("omega", 1.0)) ** 2 * self.x ** 2
        return V

    def step(self, dt: float) -> None:
        # split-step: half potential, full kinetic, half potential
        self.psi *= np.exp(-1j * self.V * dt / (2 * self.hbar))
        psi_k = np.fft.fft(self.psi)
        psi_k *= np.exp(-1j * self.hbar * self.k ** 2 * dt / (2 * self.m))
        self.psi = np.fft.ifft(psi_k)
        self.psi *= np.exp(-1j * self.V * dt / (2 * self.hbar))

    def frame(self) -> Dict[str, Any]:
        density = np.abs(self.psi) ** 2
        step = max(1, self.n // _MAX_OUT)
        d = density[::step]
        v = self.V[::step]
        vmax = float(np.max(np.abs(self.V))) or 1.0
        norm = float(np.sum(density) * self.dx)
        return {
            "values": np.round(d, 5).tolist(),
            "shape": [len(d)],
            "potential": np.round(v / vmax, 4).tolist(),
            "range": [0.0, float(d.max())],
            "aggregates": {"norm": round(norm, 4), "<x>": round(float(np.sum(self.x * density) * self.dx), 3)},
        }


def create(params: Dict[str, Any]) -> Schrodinger1D:
    return Schrodinger1D(params)
