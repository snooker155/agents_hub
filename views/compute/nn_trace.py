"""NN inference trace — per-layer activations of a forward pass, frame by frame.

Scenario 2 (NN topologies + live inference). Each ``step`` feeds one input
sample through an MLP and captures every layer's activations; the frame packs
them into a padded ``layers × max_width`` grid so the existing heatmap renderer
draws the network "lighting up" sample by sample. Weights are seeded (or traced
from a real torch module in a later tier — the frame contract stays the same);
aggregates report per-sample saturation (dead ReLU fraction) and output argmax,
the numbers a linked chart plots over the trace.
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

_ACTIVATIONS = {
    "relu": lambda x: np.maximum(x, 0.0),
    "tanh": np.tanh,
    "sigmoid": lambda x: 1.0 / (1.0 + np.exp(-x)),
}


class NNTrace:
    def __init__(self, params: Dict[str, Any]):
        layers = [int(n) for n in (params.get("layers") or [8, 16, 12, 4])]
        if len(layers) < 2:
            layers = [8, 4]
        self.layers = [max(1, min(256, n)) for n in layers]
        self.act_name = str(params.get("activation", "relu")).lower()
        self.act = _ACTIVATIONS.get(self.act_name, _ACTIVATIONS["relu"])
        rng = np.random.default_rng(int(params.get("seed", 7)))
        self.rng = rng
        # He-style init so relu nets don't saturate to zero immediately.
        self.weights: List[np.ndarray] = []
        self.biases: List[np.ndarray] = []
        for i in range(len(self.layers) - 1):
            fan_in = self.layers[i]
            self.weights.append(rng.normal(0, np.sqrt(2.0 / fan_in), (fan_in, self.layers[i + 1])))
            self.biases.append(rng.normal(0, 0.1, self.layers[i + 1]))
        # Optional fixed input batch to trace; otherwise random samples.
        inputs = params.get("inputs")
        self.inputs = np.asarray(inputs, dtype=float) if inputs else None
        self.sample_idx = -1
        self.activations: List[np.ndarray] = [np.zeros(n) for n in self.layers]

    def step(self, dt: float) -> None:
        """Trace one forward pass (dt is unused; a step *is* one sample)."""
        self.sample_idx += 1
        if self.inputs is not None and len(self.inputs):
            x = self.inputs[self.sample_idx % len(self.inputs)][: self.layers[0]]
            x = np.pad(x, (0, max(0, self.layers[0] - len(x))))
        else:
            x = self.rng.normal(0, 1, self.layers[0])
        acts = [x]
        for i, (w, b) in enumerate(zip(self.weights, self.biases)):
            z = acts[-1] @ w + b
            # last layer: softmax head; hidden layers: the chosen activation
            if i == len(self.weights) - 1:
                e = np.exp(z - np.max(z))
                acts.append(e / np.sum(e))
            else:
                acts.append(self.act(z))
        self.activations = acts

    def frame(self) -> Dict[str, Any]:
        width = max(self.layers)
        grid = np.zeros((len(self.layers), width))
        for i, a in enumerate(self.activations):
            hi = float(np.max(np.abs(a))) or 1.0
            grid[i, : len(a)] = np.abs(a) / hi          # per-layer normalized
        hidden = np.concatenate(self.activations[1:-1]) if len(self.activations) > 2 else np.array([1.0])
        out = self.activations[-1]
        return {
            "values": np.round(grid.ravel(), 4).tolist(),
            "shape": [len(self.layers), width],
            "range": [0.0, 1.0],
            "layer_sizes": list(self.layers),
            "aggregates": {
                "sample": int(self.sample_idx),
                "saturation": round(float(np.mean(hidden == 0.0)), 3),
                "output_max": round(float(np.max(out)), 3),
                "predicted": int(np.argmax(out)),
            },
        }


def create(params: Dict[str, Any]) -> NNTrace:
    return NNTrace(params)
