"""Guess a deployment from what is in the project folder.

The proposal is a starting point the operator (or the agent) edits, never
the last word: a folder with a ``docker-compose.yml`` becomes a compose
deployment listing the services that publish a port; a ``Dockerfile``
becomes one docker service; a ``package.json`` becomes a node service with
its ``dev`` script (or ``start``); a Python entrypoint becomes a uvicorn or
plain ``python`` service. Conventional subfolders (``frontend``, ``backend``,
``client``, ``server``, ``web``, ``api``, ``app``, ``ui``) are looked into
one level down, so a repo with ``frontend/`` and ``backend/`` yields two
services.

Ports: a Vite project is proposed on 5173, Next.js and Express-looking ones
on 3000, Python on 8000. Dev servers bind to localhost by default, which
inside a container means "unreachable"; every proposed command therefore
passes the host and port explicitly where the tool supports it, and the
service gets ``PORT`` and ``HOST`` in its environment anyway.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import DeployService, ProjectDeployment

_SUBFOLDERS = ("frontend", "backend", "client", "server", "web", "api", "app", "ui", "site")
_FRONTEND_HINTS = ("frontend", "client", "web", "ui", "site")
_BACKEND_HINTS = ("backend", "server", "api")
_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - unreadable or invalid: treated as absent
        return None


def _kind_for(folder_name: str, default: str) -> str:
    name = folder_name.lower()
    if any(h in name for h in _FRONTEND_HINTS):
        return "frontend"
    if any(h in name for h in _BACKEND_HINTS):
        return "backend"
    return default


def _node_service(folder: Path, rel: str, name: str) -> Optional[DeployService]:
    pkg = _read_json(folder / "package.json")
    if pkg is None:
        return None
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    scripts = pkg.get("scripts") or {}
    kind = _kind_for(name, "frontend" if ("vite" in deps or "react" in deps or "next" in deps) else "backend")
    port = 3000
    command: str
    if "vite" in deps:
        port = 5173
        command = "npm run dev -- --host 0.0.0.0 --port $PORT"
    elif "next" in deps:
        command = "npm run dev -- --hostname 0.0.0.0 --port $PORT"
    elif "dev" in scripts:
        command = "npm run dev"
    elif "start" in scripts:
        command = "npm start"
    else:
        main = pkg.get("main") or "index.js"
        command = f"node {main}"
    lock = "package-lock.json"
    install = "npm ci" if (folder / lock).exists() else "npm install"
    if (folder / "pnpm-lock.yaml").exists():
        install = "corepack enable && pnpm install"
        command = command.replace("npm run", "pnpm run").replace("npm start", "pnpm start")
    elif (folder / "yarn.lock").exists():
        install = "corepack enable && yarn install"
        command = command.replace("npm run dev --", "yarn dev").replace("npm start", "yarn start")
    return DeployService(
        name=name, kind=kind, path=rel, language="node", port=port,
        install_command=install, command=command,
        dockerfile="Dockerfile" if (folder / "Dockerfile").exists() else None,
    )


def _python_entrypoint(folder: Path) -> Optional[str]:
    for candidate in ("main.py", "app.py", "server.py", "manage.py", "api.py"):
        if (folder / candidate).exists():
            return candidate
    # Only ``src``: the conventional service subfolders are scanned as
    # services of their own, and must not also make the root one.
    for pkg_dir in ("src",):
        for candidate in ("main.py", "app.py"):
            if (folder / pkg_dir / candidate).exists():
                return f"{pkg_dir}/{candidate}"
    return None


def _python_service(folder: Path, rel: str, name: str) -> Optional[DeployService]:
    has_reqs = (folder / "requirements.txt").exists()
    has_pyproject = (folder / "pyproject.toml").exists()
    entry = _python_entrypoint(folder)
    if not (has_reqs or has_pyproject or entry):
        return None
    text = ""
    if entry:
        try:
            text = (folder / entry).read_text(encoding="utf-8", errors="ignore")[:20000]
        except OSError:
            text = ""
    if entry == "manage.py":
        command = "python manage.py runserver 0.0.0.0:$PORT"
    elif entry and re.search(r"\b(FastAPI|Starlette)\(", text):
        module = entry[:-3].replace("/", ".")
        command = f"uvicorn {module}:app --host 0.0.0.0 --port $PORT"
    elif entry and re.search(r"\bFlask\(", text):
        command = f"flask --app {entry} run --host 0.0.0.0 --port $PORT"
    elif entry:
        command = f"python {entry}"
    else:
        command = "python -m http.server $PORT"
    install = None
    if has_reqs:
        install = "pip install -r requirements.txt"
    elif has_pyproject:
        install = "pip install -e ."
    if command.startswith("uvicorn") and install and "uvicorn" not in install:
        install = install + " && pip install uvicorn"
    return DeployService(
        name=name, kind=_kind_for(name, "backend"), path=rel, language="python", port=8000,
        install_command=install, command=command, health_path="/",
        dockerfile="Dockerfile" if (folder / "Dockerfile").exists() else None,
    )


def _static_service(folder: Path, rel: str, name: str) -> Optional[DeployService]:
    if (folder / "index.html").exists() and not (folder / "package.json").exists():
        return DeployService(name=name, kind="frontend", path=rel, language="static", port=8080,
                             command="python -m http.server $PORT --bind 0.0.0.0")
    return None


def _dockerfile_only(folder: Path, rel: str, name: str) -> Optional[DeployService]:
    if not (folder / "Dockerfile").exists():
        return None
    port = 8080
    try:
        text = (folder / "Dockerfile").read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"^\s*EXPOSE\s+(\d+)", text, re.MULTILINE)
        if m:
            port = int(m.group(1))
    except OSError:
        pass
    return DeployService(name=name, kind=_kind_for(name, "other"), path=rel, language="docker",
                         dockerfile="Dockerfile", port=port, health_path=None)


def _service_for(folder: Path, rel: str, name: str) -> Optional[DeployService]:
    for probe in (_node_service, _python_service, _static_service, _dockerfile_only):
        svc = probe(folder, rel, name)
        if svc is not None:
            return svc
    return None


def _compose_services(root: Path) -> Optional[Dict[str, Any]]:
    for candidate in _COMPOSE_FILES:
        path = root / candidate
        if not path.exists():
            continue
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - an unparsable compose file is reported as none
            return {"file": candidate, "services": []}
        out: List[DeployService] = []
        for name, spec in (data.get("services") or {}).items():
            if not isinstance(spec, dict):
                continue
            ports = spec.get("ports") or []
            container_port: Optional[int] = None
            for p in ports:
                text = str(p.get("target") if isinstance(p, dict) else p)
                # "8080:80", "127.0.0.1:8080:80/tcp", "80"
                tail = text.split("/")[0].split(":")[-1]
                if tail.isdigit():
                    container_port = int(tail)
                    break
            if container_port is None:
                continue
            try:
                out.append(DeployService(name=str(name), kind=_kind_for(str(name), "other"),
                                         language="docker", port=container_port, health_path=None))
            except ValueError:
                continue
        return {"file": candidate, "services": out}
    return None


def detect(root: Optional[Path], *, project_id: str, workspace: str,
           name: str = "") -> ProjectDeployment:
    """A proposed deployment for the folder ``root`` (may not exist yet)."""
    dep = ProjectDeployment(project_id=project_id, workspace=workspace, name=name)
    if root is None or not root.is_dir():
        return dep

    compose = _compose_services(root)
    if compose is not None:
        dep.mode = "compose"
        dep.compose_file = compose["file"]
        dep.services = compose["services"]
        return dep

    services: List[DeployService] = []
    top = _service_for(root, "", "app")
    if top is not None:
        top.kind = top.kind if top.kind != "other" else ("frontend" if top.language in ("node", "static") else "backend")
        services.append(top)
    for sub in _SUBFOLDERS:
        folder = root / sub
        if not folder.is_dir():
            continue
        svc = _service_for(folder, sub, sub)
        if svc is not None:
            services.append(svc)
    # Two node services on the same proposed port would collide only in local
    # mode, but a distinct port per service reads better everywhere.
    used = set()
    for svc in services:
        while svc.port in used:
            svc.port += 1
        used.add(svc.port)
    dep.services = services
    return dep


__all__ = ["detect"]
