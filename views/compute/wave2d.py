"""2D wave equation — explicit finite-difference stepper.

Scenarios 3/4 (fields, physics). ``∂²u/∂t² = c²∇²u`` on a grid with damping;
streams the amplitude field as a ``values`` channel (flattened) + ``shape`` so
the renderer draws a heatmap. The grid is capped and downsampled to keep frame
size bounded.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np

_MAX_GRID = 96          # compute cap
_MAX_OUT = 64           # streamed field is downsampled to at most this per side


class Wave2D:
    def __init__(self, params: Dict[str, Any]):
        self.n = int(min(_MAX_GRID, max(16, params.get("grid", 80))))
        self.c = float(params.get("speed", 1.0))
        self.damping = float(params.get("damping", 0.999))
        self.h = 1.0 / self.n
        # CFL-safe timestep factor baked into step()
        self.u = np.zeros((self.n, self.n))
        self.u_prev = np.zeros((self.n, self.n))
        # initial disturbance: a gaussian bump (a "droplet")
        cx = float(params.get("source_x", 0.5))
        cy = float(params.get("source_y", 0.5))
        gx, gy = np.meshgrid(np.linspace(0, 1, self.n), np.linspace(0, 1, self.n))
        self.u = np.exp(-((gx - cx) ** 2 + (gy - cy) ** 2) / (2 * 0.02 ** 2))
        self.u_prev = self.u.copy()

    def step(self, dt: float) -> None:
        u = self.u
        lap = (
            -4 * u
            + np.roll(u, 1, 0) + np.roll(u, -1, 0)
            + np.roll(u, 1, 1) + np.roll(u, -1, 1)
        )
        coeff = (self.c * dt / self.h) ** 2
        coeff = min(coeff, 0.5)  # keep the explicit scheme stable
        u_next = (2 * u - self.u_prev + coeff * lap) * self.damping
        # fixed (clamped) boundaries
        u_next[0, :] = u_next[-1, :] = u_next[:, 0] = u_next[:, -1] = 0.0
        self.u_prev = u
        self.u = u_next

    def frame(self) -> Dict[str, Any]:
        field = self.u
        step = max(1, self.n // _MAX_OUT)
        small = field[::step, ::step]
        return {
            "values": np.round(small, 4).ravel().tolist(),
            "shape": list(small.shape),
            "range": [float(small.min()), float(small.max())],
            "aggregates": {"energy": round(float(np.sum(field ** 2)), 3)},
        }


def create(params: Dict[str, Any]) -> Wave2D:
    return Wave2D(params)
