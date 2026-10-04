## Building a latex view

A `latex` view renders one expression with KaTeX in display mode: `spec.latex`.

- Set it with `view_apply_ops`: `{"op":"update","path":"spec.latex","value":"\\int_0^1 x^2\\,dx = \\tfrac{1}{3}"}`. KaTeX syntax only; no packages, no document preamble.
- One formula per view. A plotted function belongs in a `math` view, which shows the equation and its curve together.
