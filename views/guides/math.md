## Building a math view

A `math` view plots an expression with live parameters and a KaTeX equation.

- `math_plot(expr, variable, domain, domain2, params, latex, mode)` sets everything at once. Params become sliders and the equation shows their values.
- Modes: `function2d` (y = f(x)), `parametric` (`expr` is the comma pair `"cos(3*t), sin(2*t)"`), `surface3d` (z = f(x, y), sampled over `domain` × `domain2`).
- Write `latex` for the pretty equation and name params the way the equation does (`a`, `omega`), so the sliders and the formula read as one thing.
- Extra controls bind to `spec.params.<name>`; a `play` control sweeps a param min to max for an animated family of curves. `view_annotate` adds labels or a live `equation` whose symbols bind to spec paths.
