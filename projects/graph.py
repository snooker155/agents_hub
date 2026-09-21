"""
Deterministic graph builders for a project's technical and business structure.

Two views are produced, both in a React-Flow–compatible shape
(``{"nodes": [...], "edges": [...]}`` where each node carries an explicit
``position``):

  - **architecture** — the technical structure inferred from the project's
    config (frontend/backend/repo) and a shallow scan of its source tree:
    client → service → data-store / external dependencies.
  - **process** — the business/process structure derived from the project's
    tasks: hierarchy (parent → subtask) and ordering (sequence) as a flow.

These are intentionally deterministic (no LLM): they read what is already
known about the project so the graph is exact, cheap, and reproducible. The
result is meant to seed an editable canvas, not replace one.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


# Horizontal/vertical spacing for the layered layout.
_COL_W = 280
_ROW_H = 120


def _node(node_id: str, label: str, *, kind: str, col: int, row: int,
          subtitle: str = "", group: str = "") -> Dict[str, Any]:
    """A React-Flow node with a computed layered position."""
    return {
        "id": node_id,
        "type": "default",
        "position": {"x": col * _COL_W, "y": row * _ROW_H},
        "data": {"label": label, "kind": kind, "subtitle": subtitle, "group": group},
    }


def _edge(source: str, target: str, label: str = "") -> Dict[str, Any]:
    return {
        "id": f"{source}->{target}",
        "source": source,
        "target": target,
        "label": label,
        "animated": True,
        "markerEnd": {"type": "arrowclosed"},
    }


# ─────────────────────────── ARCHITECTURE ────────────────────────────

# Data stores / infra we recognise from dependency and compose files.
_DATASTORE_HINTS = {
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "psycopg": "PostgreSQL",
    "mysql": "MySQL",
    "mariadb": "MariaDB",
    "mongo": "MongoDB",
    "mongodb": "MongoDB",
    "redis": "Redis",
    "sqlite": "SQLite",
    "chroma": "Chroma",
    "elasticsearch": "Elasticsearch",
    "rabbitmq": "RabbitMQ",
    "kafka": "Kafka",
}


def _read_text(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _detect_frontend_framework(root: Path) -> str:
    pkg = root / "package.json"
    if not pkg.is_file():
        # search one level down for a frontend subfolder
        for child in root.iterdir() if root.is_dir() else []:
            cand = child / "package.json"
            if cand.is_file():
                pkg = cand
                break
    if not pkg.is_file():
        return ""
    try:
        data = json.loads(_read_text(pkg))
    except Exception:
        return ""
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    for name, label in (("next", "Next.js"), ("react", "React"),
                        ("vue", "Vue"), ("@angular/core", "Angular"),
                        ("svelte", "Svelte")):
        if name in deps:
            return label
    return "Node"


def _detect_backend_framework(root: Path) -> str:
    reqs = ""
    for fname in ("requirements.txt", "requirements-agents.txt",
                  "pyproject.toml", "Pipfile"):
        reqs += _read_text(root / fname).lower()
    # also peek at a couple of source files
    for py in list(root.rglob("*.py"))[:20]:
        reqs += _read_text(py).lower()
    for needle, label in (("fastapi", "FastAPI"), ("flask", "Flask"),
                          ("django", "Django"), ("express", "Express"),
                          ("aiohttp", "aiohttp")):
        if needle in reqs:
            return label
    return ""


def _detect_datastores(root: Path) -> List[str]:
    """Recognised data stores, from dependency files and docker-compose."""
    blob = ""
    for fname in ("requirements.txt", "requirements-agents.txt", "pyproject.toml",
                  "docker-compose.yml", "docker-compose.yaml", "package.json",
                  "Pipfile"):
        blob += _read_text(root / fname).lower()
    found: List[str] = []
    for hint, label in _DATASTORE_HINTS.items():
        if hint in blob and label not in found:
            found.append(label)
    return found


# Directories never worth descending into for analysis (vendored deps, build
# output, VCS, caches, test/docs trees). Shared by the module scan and the
# project-map tree so both prune the same noise.
_SKIP_DIRS = {
    "node_modules", "venv", ".venv", "env", "__pycache__", "dist", "build",
    ".git", ".pytest_cache", ".agents_hub", "chroma_db", "coverage",
    ".next", ".turbo", ".cache", "vendor", "target", "tests", "test",
    "docs", "examples", ".idea", ".vscode",
}

# File extensions that carry structural signal (code + config + docs).
_CODE_CONFIG_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".go", ".rs",
    ".java", ".rb", ".php", ".cs", ".json", ".yml", ".yaml", ".toml",
    ".md", ".sql", ".proto",
}

# High-signal files worth showing the agent verbatim (heads only): manifests,
# compose/Docker, and common entry points. Read in this order until the budget
# runs out.
_KEY_FILES = [
    "README.md", "package.json", "requirements.txt", "requirements-agents.txt",
    "pyproject.toml", "Pipfile", "docker-compose.yml", "docker-compose.yaml",
    "Dockerfile", "main.py", "app.py", "server.py", "manage.py", "cli.py",
    "go.mod", "Cargo.toml", "pom.xml", "Makefile",
]


def _top_level_modules(root: Path, limit: int = 8) -> List[str]:
    """Top-level source directories worth showing as modules."""
    mods: List[str] = []
    if not root.is_dir():
        return mods
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name.startswith(".") or name in _SKIP_DIRS:
            continue
        # only keep dirs that actually contain source
        if any(child.rglob("*.py")) or any(child.rglob("*.js")) or any(child.rglob("*.ts")):
            mods.append(name)
        if len(mods) >= limit:
            break
    return mods


def _render_tree(root: Path, max_depth: int = 2, max_entries: int = 160) -> str:
    """A pruned, depth-limited directory listing — the project's shape at a glance.

    Skips vendored/build/test dirs and non-source files, caps depth and total
    entries. This is the "level of depth" control: the agent sees the layout
    without having to crawl it tool-call by tool-call.
    """
    if not root.is_dir():
        return ""
    lines: List[str] = []
    count = [0]

    def walk(d: Path, depth: int, prefix: str) -> None:
        if depth > max_depth or count[0] >= max_entries:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except Exception:
            return
        for p in entries:
            if count[0] >= max_entries:
                lines.append(prefix + "…")
                return
            name = p.name
            if name.startswith(".") or name in _SKIP_DIRS:
                continue
            if p.is_dir():
                lines.append(f"{prefix}{name}/")
                count[0] += 1
                walk(p, depth + 1, prefix + "  ")
            elif p.suffix.lower() in _CODE_CONFIG_EXT:
                lines.append(f"{prefix}{name}")
                count[0] += 1

    walk(root, 0, "")
    return "\n".join(lines)


def _key_file_heads(root: Path, head_lines: int = 30, budget: int = 5000) -> str:
    """Heads of the key manifest/entry-point files, within a total char budget."""
    if not root.is_dir():
        return ""
    candidates = [root / f for f in _KEY_FILES]
    src = root / "src"
    if src.is_dir():
        for f in ("index.js", "index.ts", "main.jsx", "main.tsx",
                  "App.jsx", "App.tsx", "main.py", "app.py"):
            candidates.append(src / f)

    out: List[str] = []
    used = 0
    for path in candidates:
        if used >= budget:
            break
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        head = "\n".join(text.splitlines()[:head_lines]).strip()
        if not head:
            continue
        head = head[: max(0, budget - used)]
        used += len(head)
        out.append(f"### {path.relative_to(root).as_posix()}\n{head}")
    return "\n\n".join(out)


def _build_project_map(root: Optional[Path]) -> str:
    """A compact, bounded snapshot of the project — tree + key file heads.

    Front-loaded into the generation prompt so the Architect Agent can reason
    from it directly and only needs targeted reads for genuine gaps, instead of
    crawling the tree (which trips the tool-repetition guard and is slow)."""
    if root is None:
        return ""
    parts: List[str] = []
    tree = _render_tree(root)
    if tree:
        parts.append("Directory tree (pruned, depth-limited):\n" + tree)
    heads = _key_file_heads(root)
    if heads:
        parts.append("Key file excerpts (heads only):\n" + heads)
    return "\n\n".join(parts)


# Documentation file extensions — the basis for a process view when there is no
# code yet (a fresh project carrying only a spec/brief).
_DOC_EXT = {".md", ".markdown", ".txt", ".rst", ".adoc"}


def _docs_context(root: Optional[Path], budget: int = 8000) -> str:
    """Inline the project's documentation, bounded by a char budget.

    Gathers README first, then anything under ``docs/``, then other top-level
    doc files — enough for the agent to derive a process/business flow from a
    brief or spec without crawling. Returns "" when there are no docs."""
    if root is None or not root.is_dir():
        return ""

    ordered: List[Path] = []
    ordered += sorted(p for p in root.glob("README*") if p.is_file())
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        ordered += sorted(p for p in docs_dir.rglob("*")
                          if p.is_file() and p.suffix.lower() in _DOC_EXT)
    ordered += sorted(p for p in root.iterdir()
                      if p.is_file() and p.suffix.lower() in _DOC_EXT)

    out: List[str] = []
    used = 0
    seen: set = set()
    for p in ordered:
        rp = p.resolve()
        if rp in seen or used >= budget:
            continue
        seen.add(rp)
        try:
            text = p.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            continue
        if not text:
            continue
        text = text[: max(0, budget - used)]
        used += len(text)
        out.append(f"### {p.relative_to(root).as_posix()}\n{text}")
    return "\n\n".join(out)


def build_architecture_graph(project, root: Optional[Path]) -> Dict[str, Any]:
    """Technical/architecture view: client → service → data & dependencies."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    fe_cfg = getattr(project, "frontend", None)
    be_cfg = getattr(project, "backend", None)
    repo_cfg = getattr(project, "repo", None)

    fe_enabled = bool(getattr(fe_cfg, "enabled", False))
    be_enabled = bool(getattr(be_cfg, "enabled", False))

    fe_fw = _detect_frontend_framework(root) if root else ""
    be_fw = _detect_backend_framework(root) if root else ""
    datastores = _detect_datastores(root) if root else []
    modules = _top_level_modules(root) if root else []

    # Column 0: client.  Column 1: service.  Column 2: data / deps.
    frontend_id = None
    if fe_enabled or fe_fw:
        port = getattr(fe_cfg, "port", None)
        sub = " · ".join(x for x in [fe_fw or "frontend", f":{port}" if port else ""] if x)
        frontend_id = "frontend"
        nodes.append(_node(frontend_id, project.name + " UI", kind="frontend",
                          col=0, row=1, subtitle=sub))

    backend_id = None
    if be_enabled or be_fw:
        port = getattr(be_cfg, "port", None)
        sub = " · ".join(x for x in [be_fw or "backend", f":{port}" if port else ""] if x)
        backend_id = "backend"
        nodes.append(_node(backend_id, project.name + " API", kind="backend",
                          col=1, row=1, subtitle=sub))
        if frontend_id:
            edges.append(_edge(frontend_id, backend_id, "HTTP"))

    # Anchor for connecting data/modules when there is no explicit backend.
    service_anchor = backend_id or frontend_id

    # Data stores (column 2).
    for i, ds in enumerate(datastores):
        ds_id = f"ds-{ds.lower()}"
        nodes.append(_node(ds_id, ds, kind="datastore", col=2, row=i,
                          subtitle="data store"))
        if service_anchor:
            edges.append(_edge(service_anchor, ds_id, "reads/writes"))

    # External git remote (column 2, below data stores).
    repo_type = getattr(getattr(repo_cfg, "type", None), "value",
                        getattr(repo_cfg, "type", None))
    repo_type = str(repo_type) if repo_type else "none"
    if repo_type not in ("none", "local"):
        repo_id = "repo"
        nodes.append(_node(repo_id, repo_type.capitalize(), kind="external",
                          col=2, row=len(datastores),
                          subtitle=getattr(repo_cfg, "url", "") or "remote repo"))
        if service_anchor:
            edges.append(_edge(service_anchor, repo_id, "VCS"))

    # Top-level modules hang below the service node (column 1).
    for i, mod in enumerate(modules):
        mod_id = f"mod-{mod}"
        nodes.append(_node(mod_id, mod, kind="module", col=1, row=i + 2,
                          subtitle="module"))
        if service_anchor:
            edges.append(_edge(service_anchor, mod_id))

    # Nothing detected → leave the graph empty (the UI shows its own empty state).
    return {"view": "architecture", "nodes": nodes, "edges": edges}


# ─────────────────────────── PROCESS ────────────────────────────

def _task_label(task) -> str:
    return getattr(task, "title", None) or "Untitled"


def _status_value(task) -> str:
    st = getattr(task, "status", None)
    return str(getattr(st, "value", st) or "todo")


def strip_placeholder_nodes(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]):
    """Drop legacy empty-state placeholder nodes (kind 'empty'/'project') and
    any edges touching them — so a graph saved before they were removed doesn't
    keep showing a stray "No tasks yet" / project node next to real content."""
    dead = {n.get("id") for n in nodes
            if (n.get("data") or {}).get("kind") in ("empty", "project")}
    if not dead:
        return nodes, edges
    nodes = [n for n in nodes if n.get("id") not in dead]
    edges = [e for e in edges if e.get("source") not in dead and e.get("target") not in dead]
    return nodes, edges


def build_process_graph(project, tasks: List[Any]) -> Dict[str, Any]:
    """Business/process view derived from the project's task hierarchy + order."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    if not tasks:
        # No tasks → leave the graph empty (the UI shows its own empty state).
        return {"view": "process", "nodes": nodes, "edges": edges}

    by_id = {str(getattr(t, "id")): t for t in tasks}

    def _parent(t) -> Optional[str]:
        pid = getattr(t, "parent_id", None)
        return str(pid) if pid else None

    # Roots = top-level tasks; children grouped under their parent.
    roots = [t for t in tasks if not _parent(t) or _parent(t) not in by_id]
    children: Dict[str, List[Any]] = {}
    for t in tasks:
        p = _parent(t)
        if p and p in by_id:
            children.setdefault(p, []).append(t)

    def _order_key(t):
        return (getattr(t, "order", None) if getattr(t, "order", None) is not None else 1_000,
                _task_label(t))

    roots.sort(key=_order_key)

    # Lay roots down column 0 as a sequence; subtasks fan out to the right.
    row = 0
    for r in roots:
        rid = str(getattr(r, "id"))
        nodes.append(_node(rid, _task_label(r), kind="task", col=0, row=row,
                          subtitle=_status_value(r)))
        kids = sorted(children.get(rid, []), key=_order_key)
        for j, k in enumerate(kids):
            kid = str(getattr(k, "id"))
            nodes.append(_node(kid, _task_label(k), kind="subtask", col=1,
                              row=row + j, subtitle=_status_value(k)))
            edges.append(_edge(rid, kid, "subtask"))
        row += max(1, len(kids)) + 1

    # Sequence edges between consecutive roots ("then").
    for a, b in zip(roots, roots[1:]):
        edges.append(_edge(str(getattr(a, "id")), str(getattr(b, "id")), "then"))

    return {"view": "process", "nodes": nodes, "edges": edges}


def build_project_graph(project, view: str, root: Optional[Path],
                        tasks: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Dispatch to the requested view builder."""
    if view == "process":
        return build_process_graph(project, tasks or [])
    return build_architecture_graph(project, root)


# ─────────────────────────── AUTO-LAYOUT ────────────────────────────

def _node_group(n: Dict[str, Any]) -> str:
    return str((n.get("data") or {}).get("group") or "")


def layered_layout(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], *,
                   col_w: int = 240, row_h: int = 110) -> List[Dict[str, Any]]:
    """Lay a graph out so connections and clusters are easy to follow.

    A lightweight Sugiyama-style layout:
      - **x = dependency depth** (longest path from a source) → the flow reads
        left → right and edges point forward.
      - **y = ordered slot** within each layer, refined by a few barycenter
        sweeps that pull each node next to its neighbours to cut edge crossings.
      - nodes sharing a ``data.group`` are kept adjacent and separated from other
        groups by a gap row, so clusters read as distinct bands.

    Positions are (re)assigned for every node — used both for one-shot generated
    graphs and for live re-layout as the agent builds the graph.
    """
    if not nodes:
        return nodes
    ids = [n["id"] for n in nodes]
    id_set = set(ids)
    adj: Dict[str, List[str]] = {i: [] for i in ids}
    radj: Dict[str, List[str]] = {i: [] for i in ids}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in id_set and t in id_set and s != t:
            adj[s].append(t)
            radj[t].append(s)

    # Break cycles before layering. Architecture/process graphs routinely contain
    # feedback edges (A↔B, retries, loop-backs); on a cycle the longest-path
    # relaxation below never converges, inflating layers up to len(ids) and
    # shoving nodes hundreds of px to the right — the "part of the graph flies
    # away" bug. A DFS edge-classification drops back edges so we layer over a DAG
    # and depth stays bounded by the longest *acyclic* path. (Full adj/radj are
    # still used for the barycenter step, so back edges still pull endpoints near
    # each other vertically.)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {i: WHITE for i in ids}
    forward: Dict[str, List[str]] = {i: [] for i in ids}
    for root in ids:
        if color[root] != WHITE:
            continue
        color[root] = GRAY
        stack = [(root, iter(adj[root]))]
        while stack:
            node, it = stack[-1]
            descended = False
            for t in it:
                if color[t] == GRAY:
                    continue  # back edge → would close a cycle; skip it
                forward[node].append(t)
                if color[t] == WHITE:
                    color[t] = GRAY
                    stack.append((t, iter(adj[t])))
                    descended = True
                    break
            if not descended:
                color[node] = BLACK
                stack.pop()

    # Layer = longest path from any source over the acyclic `forward` edges.
    layer: Dict[str, int] = {i: 0 for i in ids}
    for _ in range(len(ids)):
        changed = False
        for s in ids:
            for t in forward[s]:
                if layer[t] < layer[s] + 1:
                    layer[t] = layer[s] + 1
                    changed = True
        if not changed:
            break

    # Stable group ordering by first appearance keeps clusters together.
    group_order: Dict[str, int] = {}
    for n in nodes:
        g = _node_group(n)
        if g not in group_order:
            group_order[g] = len(group_order)

    from collections import defaultdict
    layers: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for n in nodes:
        layers[layer[n["id"]]].append(n)

    # Initial order: group, then label — deterministic starting point.
    for L in layers.values():
        L.sort(key=lambda n: (group_order.get(_node_group(n), 0),
                              str((n.get("data") or {}).get("label") or n["id"])))

    slot: Dict[str, int] = {}
    for L in layers.values():
        for i, n in enumerate(L):
            slot[n["id"]] = i

    # Barycenter sweeps: order each node near the average slot of its neighbours,
    # but keep its group as the primary key so clusters never interleave.
    for _ in range(4):
        for Lnum in sorted(layers):
            def bary(n):
                nbrs = radj[n["id"]] + adj[n["id"]]
                ys = [slot[m] for m in nbrs if m in slot]
                base = sum(ys) / len(ys) if ys else slot[n["id"]]
                return (group_order.get(_node_group(n), 0), base)
            layers[Lnum].sort(key=bary)
            for i, n in enumerate(layers[Lnum]):
                slot[n["id"]] = i

    # Assign coordinates; insert a gap row where the group changes within a layer.
    for Lnum in sorted(layers):
        y = 0
        prev_group = None
        for n in layers[Lnum]:
            g = _node_group(n)
            if prev_group is not None and g != prev_group:
                y += 1
            n["position"] = {"x": Lnum * col_w, "y": int(y * row_h)}
            prev_group = g
            y += 1
    return nodes


def auto_layout(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Assign positions to an agent-built graph: the same kind-grouped arrangement
    as the "Arrange" action (same kind in a row, containers/hubs on the left)."""
    return kind_grouped_layout(nodes, edges)


# Top-to-bottom order of kind bands for the kind-grouped layout. Unknown kinds
# fall after these in first-appearance order.
_KIND_BAND_ORDER = [
    "actor", "frontend", "backend", "module", "decision",
    "datastore", "external", "task", "subtask", "artifact", "project",
]


def kind_grouped_layout(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], *,
                        col_w: int = 240, row_h: int = 160) -> List[Dict[str, Any]]:
    """Lay nodes out in horizontal bands by kind.

    Nodes of the **same** kind share a row and spread out horizontally; **different**
    kinds stack vertically as separate bands. Within a band the most-connected
    nodes sit on the left (ordered by link count, descending); ties are broken by a
    barycenter pass that pulls each node toward the average column of its
    neighbours so edges between bands stay readable. Used by the "Arrange" action.
    """
    if not nodes:
        return nodes

    def kind_of(n: Dict[str, Any]) -> str:
        return (n.get("data") or {}).get("kind") or "module"

    ids = [n["id"] for n in nodes]
    id_set = set(ids)
    nbr: Dict[str, List[str]] = {i: [] for i in ids}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in id_set and t in id_set and s != t:
            nbr[s].append(t)
            nbr[t].append(s)
    deg: Dict[str, int] = {i: len(nbr[i]) for i in ids}  # link count per node

    # Group into bands by kind; order bands by _KIND_BAND_ORDER, then by the
    # order each unknown kind first appears (so the layout is deterministic).
    from collections import defaultdict
    appearance: List[str] = []
    bands: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for n in nodes:
        k = kind_of(n)
        if k not in appearance:
            appearance.append(k)
        bands[k].append(n)

    # Rows stay in a fixed semantic order (high-level → low-level), so an
    # abstraction node that links to everything (e.g. an app/frontend container)
    # can't shove its whole band to the top. Within each row the most-linked node
    # is placed leftmost (below), so the container/hub of each type forms the left
    # column with its components fanning out to the right.
    def band_rank(k: str):
        return (0, _KIND_BAND_ORDER.index(k)) if k in _KIND_BAND_ORDER else (1, appearance.index(k))

    ordered_kinds = sorted(bands.keys(), key=band_rank)

    # Initial column = most links first (left), then label (deterministic).
    col: Dict[str, int] = {}
    for k in ordered_kinds:
        bands[k].sort(key=lambda n: (-deg[n["id"]], str((n.get("data") or {}).get("label") or n["id"])))
        for i, n in enumerate(bands[k]):
            col[n["id"]] = i

    # Sweeps: degree stays the primary key (most-linked node stays leftmost);
    # within equal degree, the barycenter pulls each node toward the average
    # column of its neighbours in other bands, cutting cross-band edge crossings.
    for _ in range(4):
        for k in ordered_kinds:
            def order_key(n):
                cs = [col[m] for m in nbr[n["id"]] if m in col]
                bc = sum(cs) / len(cs) if cs else col[n["id"]]
                return (-deg[n["id"]], bc)
            bands[k].sort(key=order_key)
            for i, n in enumerate(bands[k]):
                col[n["id"]] = i

    for row, k in enumerate(ordered_kinds):
        for n in bands[k]:
            n["position"] = {"x": int(col[n["id"]] * col_w), "y": int(row * row_h)}
    return nodes


# ─────────────────────────── LLM GENERATION ────────────────────────────

_VIEW_GUIDANCE = {
    "architecture": (
        "Produce a TECHNICAL ARCHITECTURE graph: components such as frontend, "
        "backend/services, databases, queues, external APIs, and key modules, "
        "with edges describing how they call or depend on each other. "
        "Use node kinds from: frontend, backend, datastore, external, module."
    ),
    "process": (
        "Produce a BUSINESS PROCESS graph: the end-to-end flow of actors, steps, "
        "decisions and handoffs that deliver the project's value. Order steps with "
        "edges labelled like 'then' / 'triggers'. Use node kinds from: actor, task, "
        "subtask, decision, external, and `artifact` for documents/files/reports or "
        "other tangible outputs a step produces or consumes."
    ),
}

_GEN_SCHEMA_HINT = (
    'Return exactly: {"nodes":[{"id":"<unique-slug>","label":"<short name>",'
    '"kind":"<kind>","subtitle":"<optional detail>","group":"<cluster name>"}],'
    '"edges":[{"source":"<node id>","target":"<node id>","label":"<relation>"}]}. '
    "Use 6-18 nodes. Every edge source/target MUST reference a node id you defined. "
    'Set "group" to cluster related nodes (e.g. "frontend", "data", "payments", '
    '"onboarding") — nodes with the same group are placed together; node positions '
    "are computed automatically from the edges, so you never set coordinates."
)

# Read budget appended to the task prompt — keeps tool use targeted and bounded
# so the run doesn't trip the tool-repetition guard or stall reading everything.
_READ_BUDGET = (
    "The project map below (tree + key file heads) is usually enough — reason "
    "from it first. Only if a specific relationship is still unclear, read at "
    "most 5 more files, and prefer `search_text` to locate a symbol over reading "
    "a whole file. Never open files under node_modules, venv, build, dist, or "
    "tests. Do not read the same file twice. Stop investigating and output the "
    "JSON as soon as the graph is clear."
)

# Read budget for the process view: ground the flow in what's actually given.
_PROCESS_READ_BUDGET = (
    "Base the process flow on the project description, the documentation shown "
    "below, and any tasks — not on assumptions. If the project carries more "
    "documentation than is shown, you may read up to 5 doc files (README, docs/) "
    "to fill gaps. Do not invent steps the inputs don't support. Output the JSON "
    "as soon as the flow is clear."
)


def other_view(view: str) -> str:
    """The sibling view: process is architecture's counterpart and vice versa."""
    return "architecture" if view == "process" else "process"


def render_graph_summary(graph: Optional[Dict[str, Any]], label: str,
                         max_nodes: int = 40, max_edges: int = 60) -> str:
    """Compact text rendering of a saved graph for cross-view context.

    Lists node ids/labels/kinds then edges as ``src -> tgt (label)`` so the agent
    can align one view with the other without re-deriving it. Empty graph → ''.
    """
    if not graph:
        return ""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        return ""
    lines = [f"{label} ({len(nodes)} nodes, {len(edges)} edges):"]
    for n in nodes[:max_nodes]:
        data = n.get("data") or {}
        nid = n.get("id") or ""
        lbl = data.get("label") or nid
        kind = data.get("kind") or ""
        sub = data.get("subtitle") or ""
        suffix = f" — {sub}" if sub else ""
        lines.append(f"  - {nid}: {lbl} [{kind}]{suffix}")
    if edges:
        lines.append("  edges:")
        for e in edges[:max_edges]:
            rel = e.get("label") or ""
            rel = f" ({rel})" if rel else ""
            lines.append(f"    {e.get('source')} -> {e.get('target')}{rel}")
    return "\n".join(lines)


def _sibling_view_context(project, view: str) -> str:
    """The other view's saved graph, rendered for cross-reference (or '')."""
    other = other_view(view)
    try:
        from projects.graph_store import ProjectGraphStore
        saved = ProjectGraphStore().get(getattr(project, "id", None), other)
    except Exception:
        return ""
    return render_graph_summary(saved, f"Existing {other.upper()} view")


def _generation_context(project, view: str, root: Optional[Path],
                        tasks: Optional[List[Any]]) -> str:
    """Compact, factual context about the project for the generation prompt."""
    lines: List[str] = []
    lines.append(f"Project name: {project.name}")
    desc = getattr(project, "description", None)
    if desc:
        lines.append(f"Description: {desc}")
    ptype = getattr(getattr(project, "type", None), "value", getattr(project, "type", None))
    if ptype:
        lines.append(f"Type: {ptype}")

    if view == "architecture" and root is not None:
        # Reuse the deterministic architecture scan as a factual prior, then
        # front-load a bounded project map so the agent reasons from it instead
        # of crawling the tree file-by-file.
        arch = build_architecture_graph(project, root)
        detected = [f"{n['data']['label']} ({n['data']['kind']})" for n in arch["nodes"]]
        if detected:
            lines.append("Detected components: " + ", ".join(detected))
        modules = _top_level_modules(root)
        if modules:
            lines.append("Top-level modules: " + ", ".join(modules))
        project_map = _build_project_map(root)
        if project_map:
            lines.append("")
            lines.append(project_map)

    if view == "process":
        # The process flow is derived from tasks when present, and otherwise
        # from the description + documentation — so an empty project (just a
        # brief/spec, no code or tasks) can still be mapped.
        if tasks:
            lines.append("Tasks:")
            for t in tasks[:40]:
                parent = " (subtask)" if getattr(t, "parent_id", None) else ""
                lines.append(f"  - {_task_label(t)} [{_status_value(t)}]{parent}")
        docs = _docs_context(root)
        if docs:
            lines.append("")
            lines.append("Documentation (basis for the process flow):\n" + docs)
        elif not tasks and not desc:
            # Nothing to go on — make that explicit so the agent asks/builds from
            # the user's chat instructions rather than inventing a generic flow.
            lines.append("")
            lines.append("No documentation, README, or tasks found — build the "
                        "flow from the user's instructions in chat.")

    # Cross-view: if the project's other view already exists, hand it over as a
    # prior so this view can be aligned with it (e.g. derive the architecture
    # from an existing process flow, or vice versa).
    sibling = _sibling_view_context(project, view)
    if sibling:
        lines.append("")
        lines.append(
            "The project's OTHER view already exists — use it as a cross-reference "
            "to keep the two views consistent (you may derive this view from it). "
            "It is a different lens on the same project, not nodes to copy verbatim:")
        lines.append(sibling)

    return "\n".join(lines)


def build_chat_turn_prompt(project, view: str, root: Optional[Path],
                           tasks: Optional[List[Any]], graph: Dict[str, Any],
                           history: List[Dict[str, Any]], user_message: str) -> str:
    """Prompt for one interactive build turn.

    Unlike the one-shot generator, this instructs the agent to apply every
    change through the graph tools (so the canvas updates live) rather than
    emitting JSON, and feeds it the current graph state + recent conversation so
    the build is incremental and steerable."""
    guidance = _VIEW_GUIDANCE.get(view, _VIEW_GUIDANCE["architecture"])
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if nodes:
        state = "Nodes: " + ", ".join(
            f"{n['id']}({(n.get('data') or {}).get('kind','')})" for n in nodes[:50])
        if edges:
            state += "\nEdges: " + ", ".join(
                f"{e.get('source')}->{e.get('target')}" for e in edges[:80])
    else:
        state = "(empty — nothing built yet)"
    convo = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in (history or [])[-12:])
    ctx = _generation_context(project, view, root, tasks)

    return (
        f"VIEW: {view}\n\n{guidance}\n\n"
        "You are building this graph INTERACTIVELY with the user. Apply EVERY "
        "change by calling the graph tools — `add_graph_node`, `add_graph_edge`, "
        "`delete_graph_node`, `delete_graph_edge`, `clear_graph` — one call per "
        "node/edge. Each call updates the user's "
        "canvas immediately, so do NOT output JSON and do NOT batch changes into "
        "text; the tools are the only way to change the graph. Add nodes before "
        "the edges that reference them. To MODIFY a node, re-call `add_graph_node` "
        "with its existing id; to relabel an edge, re-call `add_graph_edge` with the "
        "same source/target. To REMOVE things, call `delete_graph_node` (also drops "
        "its edges) or `delete_graph_edge` — do NOT clear the whole graph just to "
        "delete a few items. Set each node's `group` to cluster related "
        "nodes (e.g. a stage, lane, or subsystem) so the layout keeps them together "
        "and the flow stays readable — positions are computed automatically from "
        "the edges, so never worry about coordinates. When the user asks to start "
        "over, call `clear_graph` first. To consult the project's OTHER view (e.g. "
        "build this architecture from the existing process flow, or check the two "
        "are consistent), call `read_graph_view` — it returns the other view's "
        "nodes and edges read-only; adapt them to this view rather than copying "
        "verbatim.\n\n"
        "CRITICAL — act, don't just plan: a tool call is the ONLY thing that "
        "changes the graph. Listing or describing the nodes/edges you intend to "
        "add (in your thinking, a plan, or your reply) does NOTHING — you MUST emit "
        "an actual `add_graph_edge` / `add_graph_node` call for each one. The moment "
        "you decide on an edge, call the tool for it; never write out a batch of "
        "edges as text and stop. Keep calling tools until the graph is fully built "
        "and every edge you planned has a matching call — do NOT end your turn while "
        "there are still nodes or edges left to add. Only after every change is "
        "applied, reply with ONE short sentence describing what you did.\n\n"
        "Allowed node kinds: frontend, backend, datastore, external, module, "
        "task, subtask, decision, actor, artifact. Use `artifact` for a document, "
        "file, report, or other tangible output produced or consumed in a process "
        "flow (most useful in the process view).\n\n"
        f"--- CURRENT GRAPH ---\n{state}\n\n"
        f"--- PROJECT CONTEXT ---\n{ctx}\n\n"
        f"--- CONVERSATION SO FAR ---\n{convo or '(none)'}\n\n"
        f"--- NEW USER MESSAGE ---\n{user_message}"
    )


def _normalize_generated(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce loose LLM output into our node/edge schema, then auto-layout."""
    raw_nodes = raw.get("nodes") or []
    raw_edges = raw.get("edges") or []

    nodes: List[Dict[str, Any]] = []
    seen = set()
    for i, rn in enumerate(raw_nodes):
        if not isinstance(rn, dict):
            continue
        nid = str(rn.get("id") or rn.get("label") or f"n{i}").strip()
        if not nid or nid in seen:
            nid = f"{nid or 'n'}-{i}"
        seen.add(nid)
        nodes.append(_node(
            nid,
            str(rn.get("label") or nid),
            kind=str(rn.get("kind") or "module"),
            col=0, row=0,
            subtitle=str(rn.get("subtitle") or ""),
            group=str(rn.get("group") or ""),
        ))

    valid_ids = {n["id"] for n in nodes}
    edges: List[Dict[str, Any]] = []
    for re_ in raw_edges:
        if not isinstance(re_, dict):
            continue
        s, t = str(re_.get("source") or ""), str(re_.get("target") or "")
        if s in valid_ids and t in valid_ids:
            edges.append(_edge(s, t, str(re_.get("label") or "")))

    auto_layout(nodes, edges)
    return {"nodes": nodes, "edges": edges}


# ─────────────────────────── ARCHITECT AGENT ────────────────────────────

_ARCHITECT_AGENT_ID = "architect_agent"


def agent_workspace_path(project, root: Optional[Path]) -> Optional[str]:
    """Workspace to run the Architect Agent in.

    The project subfolder when it exists; otherwise the project's workspace
    folder — so a fresh project with no folder yet still resolves the
    workspace-level model selection (instead of silently dropping to global
    ``.env``). ``None`` only when neither can be resolved.
    """
    if root is not None:
        return str(root)
    try:
        from workspace import get_workspace_folder
        wf = get_workspace_folder(getattr(project, "workspace", None))
        return str(wf) if wf else None
    except Exception:
        return None


def _ensure_architect_agent() -> bool:
    """Register the Architect Agent in the live registry if missing.

    The runtime registry is only seeded from ``bootstrap/`` on first run, so an
    already-initialized install won't pick up a newly-shipped agent. This
    idempotently upserts the spec (``add_agent`` updates if present) so the
    generation path always has the agent available. Returns False if the
    registry is unavailable.
    """
    try:
        from agents.registry import get_agent, add_agent, AgentSpec
    except Exception:
        return False
    try:
        tools = ["list_files", "read_file", "search_text",
                 "add_graph_node", "add_graph_edge", "delete_graph_node",
                 "delete_graph_edge", "clear_graph", "read_graph_view"]
        existing = get_agent(_ARCHITECT_AGENT_ID)
        tools_ok = existing is not None and all(t in (existing.tools or []) for t in tools)
        # Native model reasoning so the user can follow the agent's thinking as it
        # builds the graph (streamed as think events to the chat).
        reasoning_ok = existing is not None and (existing.reasoning or {}).get("thinking_level") not in (None, "", "off")
        if existing is not None and tools_ok and reasoning_ok:
            return True
        if existing is not None:
            # Update via replace() (AgentSpec is frozen) so user-set fields
            # (model/provider/temperature) survive — a full re-create would reset them.
            import dataclasses
            new_reasoning = (existing.reasoning if reasoning_ok
                             else {**(existing.reasoning or {}), "thinking_level": "medium"})
            add_agent(dataclasses.replace(existing, tools=tools, reasoning=new_reasoning))
            return True
        # Missing entirely → register the full default spec.
        add_agent(AgentSpec(
            id=_ARCHITECT_AGENT_ID,
            name="Architect Agent",
            type="langchain",
            entrypoint="agents.agent_factory:build_agent_executor",
            description=(
                "Analyzes a project and builds its structure graph — a technical "
                "architecture view or a business process view — by calling graph "
                "tools that update the canvas live. Inspects real source and docs "
                "via read-only tools."
            ),
            domain="System",
            tools=tools,
            temperature=0.0,
            reasoning={"thinking_level": "medium"},
        ))
        return True
    except Exception:
        return False


def build_generation_prompt(project, view: str, root: Optional[Path],
                            tasks: Optional[List[Any]]) -> str:
    """The task message handed to the Architect Agent for a given view."""
    guidance = _VIEW_GUIDANCE.get(view, _VIEW_GUIDANCE["architecture"])
    if view == "process":
        budget = f"\n\n{_PROCESS_READ_BUDGET}"
    elif root is not None:
        budget = f"\n\n{_READ_BUDGET}"
    else:
        budget = ""
    return (
        f"VIEW: {view}\n\n{guidance}\n\n{_GEN_SCHEMA_HINT}{budget}\n\n"
        f"--- PROJECT CONTEXT ---\n{_generation_context(project, view, root, tasks)}"
    )


def finalize_generated_output(text: Optional[str], project, view: str,
                              root: Optional[Path],
                              tasks: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Turn raw agent output into a graph: parse → normalize → fallback.

    Shared by the blocking and streaming generation paths. Falls back to the
    deterministic builder (``source: auto``) when ``text`` has no parseable
    graph, so callers always get a usable result.
    """
    from memory.json_extract import extract_json_object

    parsed = extract_json_object(text) if text else None
    if not parsed or not parsed.get("nodes"):
        fallback = build_project_graph(project, view, root, tasks)
        fallback["source"] = "auto"
        return fallback

    result = _normalize_generated(parsed)
    result["view"] = view
    result["source"] = "generated"
    return result


def generate_graph_via_llm(project, view: str, root: Optional[Path],
                           tasks: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Generate a graph for ``view`` using the Architect Agent, normalized to
    our schema.

    Runs the dedicated ``architect_agent`` (read-only filesystem tools, scoped
    to the project workspace). The model is resolved by ``create_agent`` through
    its normal chain — workspace model_override / settings → global ``.env`` —
    so generation uses the user's selected model. Falls back to the
    deterministic builder if the agent is unavailable or returns nothing
    parseable, so the caller always gets a usable graph.
    """
    prompt = build_generation_prompt(project, view, root, tasks)
    text = None
    if _ensure_architect_agent():
        try:
            from agents.agent_factory import create_agent
            from agents.agent_invoke import invoke_agent

            # No model override — let create_agent resolve the provider/model
            # through its canonical chain (agent definition → workspace
            # model_override → workspace settings → global .env), so generation
            # uses the model the user actually selected, not a forced .env value.
            # No repetition ceiling (0 = UNLIMITED_TOOL_REPEATS): reading file
            # after file is the analysis this agent does, and the guard counts by
            # tool name, so any ceiling just truncates a large project.
            agent = create_agent(
                _ARCHITECT_AGENT_ID,
                workspace=agent_workspace_path(project, root),
                max_tool_repeats=0,
            )
            res = invoke_agent(agent, prompt, catch_exceptions=True).result
            if res.ok:
                text = str(res.agent_output)
        except Exception:
            text = None

    return finalize_generated_output(text, project, view, root, tasks)
