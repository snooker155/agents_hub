## Building a chart view

A `chart` view is a Vega-Lite spec at `spec.vega_lite`.

- Set or replace the spec with `view_apply_ops`: `{"op":"update","path":"spec.vega_lite","value":{"mark":"bar","data":{"values":[...]},"encoding":{...}}}`. Put the data inline under `data.values` for small sets; for a large one write a file into the workspace and pass it as `data` when creating the view.
- Pick the mark from the question: `bar` for categories, `line` for trends over time, `point` for two measures, `area` for stacked totals, `rect` for a heatmap. One chart answers one question; make a second view for a second question.
- Label axes in words with units, sort categories by value unless they have an order, and keep the legend short.
- Controls bind at the paths that drive the chart: a `select` on `spec.vega_lite.mark`, a `range` on a filter param, a `toggle` on a layer. Linked to a simulation with `view_link`, an empty chart plots the simulation's live frame aggregates over t automatically.
