"""
Server-side compute runtimes — the *precise* tier of the compute layer.

Where the client runtimes (``dashboard/frontend/src/views/runtimes``) give
real-time *approximations* in the browser, these run numerically on the server
(numpy) and stream **frames** to the view channel, optionally recording a
replayable **clip**. Each runtime exposes the same tiny contract as the client
side — ``create(params) -> obj`` with ``obj.step(dt)`` and ``obj.frame() ->
{channels}`` — so the frame-streaming driver (:mod:`views.compute.runner`) is
runtime-agnostic.

Runtimes: ``nbody`` (gravitational N-body, scenario 1), ``wave2d`` (2D wave
field, scenarios 3/4), ``schrodinger1d`` (1D time-dependent Schrödinger, |ψ|²,
scenario 4), ``nn_trace`` (per-layer activations of a forward pass, scenario
2). Adding one = a module with ``create(params)`` + an entry here.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from views.compute import nbody, wave2d, schrodinger1d, nn_trace

_RUNTIMES = {
    "nbody": nbody.create,
    "wave2d": wave2d.create,
    "schrodinger1d": schrodinger1d.create,
    "nn_trace": nn_trace.create,
}

SERVER_RUNTIMES = tuple(_RUNTIMES.keys())


def create_runtime(name: str, params: Optional[Dict[str, Any]] = None):
    factory = _RUNTIMES.get(name)
    if factory is None:
        return None
    return factory(params or {})


def has_runtime(name: str) -> bool:
    return name in _RUNTIMES


__all__ = ["create_runtime", "has_runtime", "SERVER_RUNTIMES"]
