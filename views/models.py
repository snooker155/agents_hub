"""
View envelope + per-kind spec validation.

The **envelope** (:class:`ViewEnvelope`) is the single contract shared by the
backend, every frontend surface, and the agent prompt. The kind-specific
**spec** is validated by a small Pydantic model registered per ``kind`` in
:data:`KIND_REGISTRY` — mirroring the ``agent_response`` kind registry so a new
view kind is "just" a new spec model + a frontend renderer.

Phase 1 kinds: ``markdown``, ``table``, ``chart`` (Vega-Lite), ``diagram``
(Mermaid), ``image``. Later kinds (``graph``, ``scene3d``, ``html``, ``latex``,
``slides``, ``document``, ``math``, ``process``, ``simulation``) register the
same way; unknown kinds fail validation with a message that lists the known set.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Type, Union

from pydantic import BaseModel, ValidationError, model_validator


class ViewValidationError(ValueError):
    """Raised when a view envelope or its kind-specific spec is invalid.

    Carries a human-readable message that is safe to hand back to the agent as
    tool output so it can self-correct (the same loop ``apply_unified_diff``
    uses)."""


# ── kind-specific spec models ────────────────────────────────────────────────

class MarkdownSpec(BaseModel):
    """A markdown document. Also the body format reused by document/slides."""
    markdown: str


class TableColumn(BaseModel):
    name: str
    type: Optional[str] = None        # "string" | "number" | "date" | ...
    format: Optional[str] = None      # display hint, e.g. "$0,0.00"


class TableSpec(BaseModel):
    """A sortable/filterable table. ``rows`` may be inline here or supplied via
    the envelope's ``data`` reference (a large table lives in a data file)."""
    columns: List[Union[str, TableColumn]]
    rows: List[Any] = []              # list-of-lists or list-of-dicts


class ChartSpec(BaseModel):
    """A chart expressed as a Vega-Lite spec — one grammar covering
    bar/line/scatter/area/maps plus interaction. The dict is passed through to
    the renderer verbatim; we only require it to be a non-empty object."""
    vega_lite: Dict[str, Any]


class DiagramSpec(BaseModel):
    """A diagram as Mermaid source (flowchart / sequence / gantt / state / …)."""
    mermaid: str


class ImageSpec(BaseModel):
    """A single image, referenced by a view-asset path or an absolute URL."""
    src: str
    caption: str = ""


class GraphSpec(BaseModel):
    """A node/edge graph (knowledge graphs, dependencies, org charts) rendered
    with Cytoscape. Built incrementally in the Studio, so ``nodes``/``edges`` are
    keyed maps ``{id: {...}}`` (a list is also accepted and normalized on the
    client). ``layout`` names a Cytoscape layout (``cose``, ``breadthfirst`` …)."""
    nodes: Union[Dict[str, Any], List[Any]] = {}
    edges: Union[Dict[str, Any], List[Any]] = {}
    layout: Optional[str] = None
    directed: bool = True


class Scene3DSpec(BaseModel):
    """A 3D scene rendered with three.js.

    The scene **shows** geometry; it does not describe how to build any. Meshes
    are authored by the Blender geometry engine (``connectors/blender``) and
    bound in as glTF assets, so an object is a produced model plus a placement::

        {"id": "hull", "src": "asset://mesh/hull_r8.glb",
         "position": [0,0,0], "rotation": [0,0,0], "scale": 1,
         "revision": 8}

    A model stays addressable by part name: ``nodes`` ({node: {visible,
    position, rotation, scale, material}}), ``materials`` ({material: {color,
    metalness, roughness, opacity, emissive, wireframe}}, ``"*"`` for all),
    ``morphs`` ({target: weight}) and ``animation`` ({clip, playing, speed}).
    Overrides apply over a snapshot of the file's own values, so removing one
    restores the original.

    ``objects``/``lights`` are keyed maps ``{id: {...}}`` built incrementally by
    ops. ``camera`` is {position, target, fov}; ``environment`` holds
    presentation: background, grid, shadows, ``fit`` (frame the camera on the
    contents), autoRotate.
    """
    objects: Union[Dict[str, Any], List[Any]] = {}
    lights: Union[Dict[str, Any], List[Any]] = {}
    camera: Dict[str, Any] = {}
    environment: Dict[str, Any] = {}


class HtmlSpec(BaseModel):
    """A live, interactive HTML/JS/CSS view. Either inline ``html`` (rendered via
    a sandboxed iframe srcdoc) or an ``entry`` asset file in the view dir served
    through the CSP asset route. Runs origin-isolated (no same-origin), talking to
    the host only through the injected ``viewhost`` postMessage bridge."""
    html: str = ""
    entry: str = ""

    @model_validator(mode="after")
    def _require_body(self):
        # Only enforced on the inline/tool creation path (validate_spec); live
        # Studio html views start empty and are built up by ops.
        if not (self.html.strip() or self.entry.strip()):
            raise ValueError("an html view needs inline 'html' or an 'entry' asset file")
        return self


class LatexSpec(BaseModel):
    """A LaTeX / math expression rendered with KaTeX (display mode)."""
    latex: str


class MathSpec(BaseModel):
    """A plotted mathematical expression. ``expr`` is evaluated over ``domain`` in
    ``variable`` with named ``params`` (exposed as controls / live-bound in the
    equation). ``mode``: function2d (y=f(x)), parametric (``expr`` is the comma
    pair "x(t), y(t)"), or surface3d (z=f(x,y), sampled over ``domain`` ×
    ``domain2``). ``latex`` optionally shows the pretty equation."""
    expr: str = ""
    variable: str = "x"
    domain: List[float] = [-10.0, 10.0]
    domain2: Optional[List[float]] = None
    params: Dict[str, Any] = {}
    mode: str = "function2d"
    latex: str = ""


class SimulationSpec(BaseModel):
    """A time-stepped simulation driven by a client runtime (particles, boids,
    agents, …). ``runtime`` names the compute module; ``params`` are its inputs
    (also exposed as controls, live-tunable mid-run); ``base`` is '2d' or
    'scene3d'; ``bounds`` sizes the world; ``entities`` seeds the population."""
    runtime: str = ""
    base: str = "2d"
    params: Dict[str, Any] = {}
    entities: Dict[str, Any] = {}
    bounds: List[float] = [100.0, 100.0]


class ProcessSpec(BaseModel):
    """A process model rendered in a chosen ``notation`` (flowchart/bpmn/sequence/
    state/petri) and executable via token flow along its ``edges``. ``nodes``/
    ``edges`` are keyed maps built incrementally; a node ``type`` is start/task/
    gateway/end."""
    notation: str = "flowchart"
    nodes: Union[Dict[str, Any], List[Any]] = {}
    edges: Union[Dict[str, Any], List[Any]] = {}
    lanes: Union[Dict[str, Any], List[Any]] = {}


class SlidesSpec(BaseModel):
    """A slide deck. ``slides`` is a keyed map ``{id: {title, body(markdown),
    order}}`` (a list is also accepted) so it can be built incrementally; the
    renderer presents them in ``order`` with prev/next and a print/PDF path."""
    slides: Union[Dict[str, Any], List[Any]] = {}
    theme: str = "light"


class DocumentSpec(BaseModel):
    """A paginated document/report — markdown body plus optional page ``css``.
    Exported to PDF via the browser's print path with print styles."""
    markdown: str = ""
    title: str = ""
    css: str = ""


# kind -> spec model. Adding a kind = one entry here + a frontend renderer.
KIND_REGISTRY: Dict[str, Type[BaseModel]] = {
    "markdown": MarkdownSpec,
    "table": TableSpec,
    "chart": ChartSpec,
    "diagram": DiagramSpec,
    "image": ImageSpec,
    "graph": GraphSpec,
    "scene3d": Scene3DSpec,
    "html": HtmlSpec,
    "latex": LatexSpec,
    "math": MathSpec,
    "simulation": SimulationSpec,
    "process": ProcessSpec,
    "slides": SlidesSpec,
    "document": DocumentSpec,
}

# Starting spec for a live (Studio-built) view of each kind — the empty document
# the op log mutates from. Keyed-map collections start empty so the first `add`
# op has something to write into.
_BASE_SPECS: Dict[str, Dict[str, Any]] = {
    "graph": {"nodes": {}, "edges": {}, "layout": "cose", "directed": True},
    "table": {"columns": [], "rows": []},
    "chart": {"vega_lite": {}},
    "markdown": {"markdown": ""},
    "diagram": {"mermaid": ""},
    "image": {"src": ""},
    "scene3d": {"objects": {}, "lights": {}, "camera": {}, "environment": {}},
    "html": {"html": ""},
    "latex": {"latex": ""},
    "math": {"expr": "", "variable": "x", "domain": [-10.0, 10.0], "params": {}, "mode": "function2d"},
    "simulation": {"runtime": "", "base": "2d", "params": {}, "entities": {}, "bounds": [100.0, 100.0]},
    "process": {"notation": "flowchart", "nodes": {}, "edges": {}, "lanes": {}},
    "slides": {"slides": {}, "theme": "light"},
    "document": {"markdown": "", "title": "", "css": ""},
}


def base_spec_for(kind: str) -> Dict[str, Any]:
    """The empty starting spec a live view of ``kind`` is built up from."""
    import copy as _copy
    return _copy.deepcopy(_BASE_SPECS.get(kind, {}))

SUPPORTED_KINDS = tuple(KIND_REGISTRY.keys())

COMPLEXITY_TIERS = ("inline", "expanded", "fullscreen")


# ── envelope ─────────────────────────────────────────────────────────────────

class ViewFallback(BaseModel):
    """What a non-visual surface (Telegram/email/CLI) shows for this view."""
    text: str = ""
    image: str = ""       # optional preview asset path (server snapshot, later)


class ViewEnvelope(BaseModel):
    """The one contract every view travels in. ``view_id`` is assigned by the
    store on creation; ``spec`` is kind-specific and validated separately."""
    view_id: str = ""
    kind: str
    title: str = ""
    summary: str = ""
    spec: Dict[str, Any] = {}
    # data separated from presentation: {"inline": ...} | {"file": "data.json"}
    # | {"url": "/api/..."}. None means the spec is self-contained.
    data: Optional[Dict[str, Any]] = None
    assets: List[str] = []
    # controls/actions are rendered from Phase 2 on; stored (not dropped) now so
    # a view authored with them round-trips.
    controls: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []
    # Compute layer (Phase 5), both cross-kind and ops-addressable:
    #   timeline: {mode: live|recorded|stepped, t, speed, loop, range:[t0,t1]}
    #     — its presence makes the host render transport controls (play/scrub/…).
    #   annotations: {id: {type: label|callout|region|equation, ...}} — the
    #     interpretation layer overlaid on the view (equation annotations bind
    #     KaTeX params to spec paths for live values).
    timeline: Dict[str, Any] = {}
    annotations: Dict[str, Any] = {}
    # "approx" (real-time client runtime) | "precise" (server compute) | "" — set
    # by view_compute so the chrome can label a computed view honestly.
    fidelity: str = ""
    complexity: str = "inline"
    fallback: ViewFallback = ViewFallback()

    def summary_row(self) -> Dict[str, Any]:
        """The lightweight projection stored in the DB index / list views."""
        return {
            "view_id": self.view_id,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
        }


def validate_spec(kind: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Validate ``spec`` against ``kind``'s model; return the normalized spec.

    Raises :class:`ViewValidationError` with an agent-readable message on an
    unknown kind or an invalid spec.
    """
    model = KIND_REGISTRY.get(kind)
    if model is None:
        raise ViewValidationError(
            f"unknown view kind {kind!r}; supported kinds: {', '.join(SUPPORTED_KINDS)}"
        )
    if not isinstance(spec, dict):
        raise ViewValidationError(f"spec must be a JSON object, got {type(spec).__name__}")
    try:
        parsed = model.model_validate(spec)
    except ValidationError as exc:
        raise ViewValidationError(f"invalid {kind} spec: {_short_errors(exc)}") from exc
    return parsed.model_dump(exclude_none=True)


def normalize_envelope(data: Dict[str, Any], *, validate_spec_body: bool = True) -> ViewEnvelope:
    """Build a validated :class:`ViewEnvelope` from a raw dict.

    Validates the envelope shape and (unless ``validate_spec_body`` is False) the
    kind-specific spec, and requires a non-empty ``fallback.text`` or ``summary``
    so non-visual surfaces never regress to a blank message. Live Studio views
    pass ``validate_spec_body=False`` because their spec starts empty and only
    becomes complete as ops arrive.
    """
    try:
        env = ViewEnvelope.model_validate(data)
    except ValidationError as exc:
        raise ViewValidationError(f"invalid view envelope: {_short_errors(exc)}") from exc

    env.kind = (env.kind or "").strip()
    if validate_spec_body:
        env.spec = validate_spec(env.kind, env.spec)
    elif env.kind not in KIND_REGISTRY:
        raise ViewValidationError(
            f"unknown view kind {env.kind!r}; supported kinds: {', '.join(SUPPORTED_KINDS)}"
        )
    env.complexity = env.complexity if env.complexity in COMPLEXITY_TIERS else "inline"

    if not (env.summary.strip() or env.fallback.text.strip()):
        # Fall back to the title so a view is never wholly silent on a
        # text-only surface; still nudge callers to provide a real summary.
        if env.title.strip():
            env.summary = env.title.strip()
        else:
            raise ViewValidationError(
                "a view needs a non-empty 'summary' or 'fallback.text' so it can "
                "be shown on non-visual surfaces (Telegram/email/CLI)"
            )
    return env


def _short_errors(exc: ValidationError, limit: int = 4) -> str:
    """Compact a Pydantic error into a one-line, agent-readable string."""
    parts: List[str] = []
    for err in exc.errors()[:limit]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    more = len(exc.errors()) - limit
    if more > 0:
        parts.append(f"(+{more} more)")
    return "; ".join(parts)


__all__ = [
    "ViewEnvelope",
    "ViewFallback",
    "KIND_REGISTRY",
    "SUPPORTED_KINDS",
    "COMPLEXITY_TIERS",
    "ViewValidationError",
    "validate_spec",
    "normalize_envelope",
    "base_spec_for",
    "MarkdownSpec",
    "TableSpec",
    "TableColumn",
    "ChartSpec",
    "DiagramSpec",
    "ImageSpec",
    "GraphSpec",
    "Scene3DSpec",
    "HtmlSpec",
    "LatexSpec",
    "MathSpec",
    "SimulationSpec",
    "ProcessSpec",
    "SlidesSpec",
    "DocumentSpec",
]
