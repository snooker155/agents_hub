"""Gravitational N-body — precise leapfrog (kick-drift-kick) integration.

Scenario 1 (cosmic objects & events). Softened Newtonian gravity; reports total
energy per frame as a conservation check (a fidelity signal). Positions stream
as a ``positions`` channel; the renderer draws them as points.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np

_G = 1.0  # simulation units


class NBody:
    def __init__(self, params: Dict[str, Any]):
        n = int(min(2000, max(2, params.get("count", 200))))
        rng = np.random.default_rng(int(params.get("seed", 42)))
        self.softening = float(params.get("softening", 0.05))
        self.g = float(params.get("g", _G))
        # a rotating disc of bodies around a heavy centre → a mini galaxy
        radius = rng.uniform(0.2, 4.0, n) ** 0.5 * 3
        angle = rng.uniform(0, 2 * np.pi, n)
        self.pos = np.stack([radius * np.cos(angle), radius * np.sin(angle),
                             rng.normal(0, 0.15, n)], axis=1)
        self.mass = rng.uniform(0.5, 1.5, n)
        self.mass[0] = float(params.get("central_mass", 400.0))
        self.pos[0] = [0, 0, 0]
        # circular-ish initial velocities around the centre
        v = np.sqrt(self.g * self.mass[0] / (radius + self.softening))
        self.vel = np.stack([-v * np.sin(angle), v * np.cos(angle), np.zeros(n)], axis=1)
        self.vel[0] = [0, 0, 0]
        self._acc = self._accel()

    def _accel(self) -> np.ndarray:
        d = self.pos[None, :, :] - self.pos[:, None, :]           # (n,n,3)
        r2 = np.sum(d * d, axis=2) + self.softening ** 2
        inv = r2 ** -1.5
        np.fill_diagonal(inv, 0.0)
        return self.g * np.einsum("ij,ijk,j->ik", inv, d, self.mass)

    def step(self, dt: float) -> None:
        self.vel += 0.5 * self._acc * dt
        self.pos += self.vel * dt
        self._acc = self._accel()
        self.vel += 0.5 * self._acc * dt

    def frame(self) -> Dict[str, Any]:
        ke = float(0.5 * np.sum(self.mass * np.sum(self.vel ** 2, axis=1)))
        d = self.pos[None, :, :] - self.pos[:, None, :]
        r = np.sqrt(np.sum(d * d, axis=2) + self.softening ** 2)
        iu = np.triu_indices(len(self.mass), 1)
        pe = float(-self.g * np.sum(self.mass[iu[0]] * self.mass[iu[1]] / r[iu]))
        return {
            "positions": np.round(self.pos, 4).tolist(),
            "aggregates": {"bodies": int(len(self.mass)), "energy": round(ke + pe, 3)},
        }


def create(params: Dict[str, Any]) -> NBody:
    return NBody(params)
