"""The project deployment record: what to run for a project, and how it is
going. See docs/project-deployments.md.

One deployment per project. It names the *services* of the project (a
frontend, a backend, anything else that listens on a port), how each is
started, and in which mode the whole thing runs:

* ``docker``: one container per service on the hub's ``agents-hub`` network,
  built from the service's Dockerfile when it has one, else a stock image for
  its language with the project folder mounted at ``/app`` and the service's
  install and start commands run inside.
* ``compose``: the project's own ``docker-compose.yml`` brought up as one
  compose project; the services listed here are the ones the hub proxies to.
* ``local``: plain subprocesses on the hub's host, no isolation, for a
  developer's own machine.

Validation is strict for the same reason environments/models.py's is: a
command lands on a shell line, an image on ``docker run``, a port on ``-p``.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

Mode = Literal["docker", "compose", "local"]
ServiceKind = Literal["frontend", "backend", "other"]
Visibility = Literal["private", "public"]

#: Desired state, set by the operator; ``status`` is what the runner sees.
Desired = Literal["running", "stopped"]
Status = Literal["stopped", "building", "starting", "running", "unhealthy", "failed", "paused"]

STATUSES = ("stopped", "building", "starting", "running", "unhealthy", "failed", "paused")

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")
_IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/:@-]{0,254}$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_REL_PATH_RE = re.compile(r"^(?!/)(?!.*(^|/)\.\.(/|$))[^\x00]{1,512}$")

EVENTS_KEEP = 200

#: Stock images for a service that has no Dockerfile, by the language the
#: detector (or the operator) names. ``node`` gets npm and npx, ``python``
#: gets pip; ``static`` serves a built folder with a tiny http server.
DEFAULT_IMAGES: Dict[str, str] = {
    "node": "node:20-bookworm-slim",
    "python": "python:3.12-slim-bookworm",
    "static": "python:3.12-slim-bookworm",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_slug(name: str) -> str:
    """A URL slug for ``/apps/<slug>/``: the project name, lowercased and
    hyphenated, plus a short random tail so two projects called "app" in two
    workspaces never collide (and the slug is not guessable from the name)."""
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40] or "app"
    return f"{base}-{secrets.token_hex(3)}"


def new_share_token() -> str:
    return secrets.token_urlsafe(24)


def _validate_command(value: Optional[str], *, what: str) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "\x00" in text or "\n" in text or len(text) > 2000:
        raise ValueError(f"{what} must be one line of at most 2000 characters")
    return text


class DeployService(BaseModel):
    """One process of the deployment. ``port`` is the port it listens on
    *inside* its container (or on the host, in local mode); the hub finds
    where that is published on its own."""
    name: str
    kind: ServiceKind = "other"
    #: Where the service lives, relative to the project folder ("" = root).
    path: str = ""
    #: ``node`` / ``python`` / ``static``: picks the stock image and what the
    #: detector proposes. Free text is allowed for an image the operator names.
    language: str = "node"
    #: A Dockerfile relative to ``path``; when set, the image is built from it
    #: and ``install_command`` / ``command`` are not used in docker mode.
    dockerfile: Optional[str] = None
    image: Optional[str] = None
    install_command: Optional[str] = None
    command: Optional[str] = None
    port: int = 3000
    #: Path the health check fetches; None means "the port accepts a connection".
    health_path: Optional[str] = "/"
    env: Dict[str, str] = Field(default_factory=dict)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value):
        name = str(value or "").strip().lower()
        if not _NAME_RE.match(name):
            raise ValueError("a service name is lowercase letters, digits, - and _ (1 to 40 characters)")
        return name

    @field_validator("path", mode="before")
    @classmethod
    def _path(cls, value):
        text = str(value or "").strip().strip("/")
        if text and not _REL_PATH_RE.match(text):
            raise ValueError("path must be relative to the project folder and may not climb out of it")
        return text

    @field_validator("dockerfile", mode="before")
    @classmethod
    def _dockerfile(cls, value):
        if value in (None, ""):
            return None
        text = str(value).strip()
        if not _REL_PATH_RE.match(text):
            raise ValueError("dockerfile must be a relative path inside the service")
        return text

    @field_validator("image", mode="before")
    @classmethod
    def _image(cls, value):
        if value in (None, ""):
            return None
        text = str(value).strip()
        if not _IMAGE_RE.match(text):
            raise ValueError(f"image {text!r} is not a docker image reference")
        return text

    @field_validator("install_command", mode="before")
    @classmethod
    def _install(cls, value):
        return _validate_command(value, what="install_command")

    @field_validator("command", mode="before")
    @classmethod
    def _command(cls, value):
        return _validate_command(value, what="command")

    @field_validator("port", mode="before")
    @classmethod
    def _port(cls, value):
        port = int(value)
        if not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return port

    @field_validator("health_path", mode="before")
    @classmethod
    def _health(cls, value):
        if value in (None, ""):
            return None
        text = str(value).strip()
        if not text.startswith("/") or len(text) > 512:
            raise ValueError("health_path must start with /")
        return text

    @field_validator("env", mode="before")
    @classmethod
    def _env(cls, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("env must be an object of NAME: value")
        out: Dict[str, str] = {}
        for key, val in value.items():
            key = str(key).strip()
            if not _ENV_KEY_RE.match(key):
                raise ValueError(f"{key!r} is not a valid environment variable name")
            text = "" if val is None else str(val)
            if "\x00" in text or len(text) > 4096:
                raise ValueError(f"the value of {key} is too long or holds a NUL byte")
            out[key] = text
        if len(out) > 100:
            raise ValueError("at most 100 variables per service")
        return out

    @property
    def resolved_image(self) -> str:
        return self.image or DEFAULT_IMAGES.get(self.language, DEFAULT_IMAGES["node"])


class ServiceRuntime(BaseModel):
    """What the runner knows about one service right now."""
    state: str = "stopped"          # stopped | starting | running | exited | failed
    container: Optional[str] = None
    pid: Optional[int] = None
    host_port: Optional[int] = None
    url: Optional[str] = None       # where the hub reaches it (localhost:host_port)
    image: Optional[str] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    healthy: Optional[bool] = None
    #: Why ``healthy`` is False, for the page and the agent.
    health_error: Optional[str] = None
    started_at: Optional[str] = None
    log_file: Optional[str] = None


class DeployEvent(BaseModel):
    at: str = Field(default_factory=now_iso)
    kind: str
    detail: str = ""
    service: Optional[str] = None


class ProjectDeployment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    workspace: str
    name: str = ""
    mode: Mode = "docker"
    #: ``compose`` mode: the compose file, relative to the project folder.
    compose_file: Optional[str] = None
    services: List[DeployService] = Field(default_factory=list)
    environment_id: Optional[str] = None
    #: Variables every service gets, on top of its own.
    env: Dict[str, str] = Field(default_factory=dict)
    #: Which service the "open" links point at; default: the frontend, else the first.
    primary_service: Optional[str] = None
    #: Restart a service that exited on its own; a crash loop pauses the deployment.
    restart_on_exit: bool = True

    desired: Desired = "stopped"
    status: Status = "stopped"
    paused_reason: Optional[str] = None
    last_error: Optional[str] = None
    runtime: Dict[str, ServiceRuntime] = Field(default_factory=dict)
    events: List[DeployEvent] = Field(default_factory=list)
    restart_count: int = 0

    slug: str = ""
    share_token: str = Field(default_factory=new_share_token)
    visibility: Visibility = "private"

    created_by: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    deployed_at: Optional[str] = None
    #: Who started the last deploy (a user id or an agent's run id).
    deployed_by: Optional[str] = None

    @field_validator("slug", mode="before")
    @classmethod
    def _slug(cls, value):
        text = str(value or "").strip().lower()
        if text and not _SLUG_RE.match(text):
            raise ValueError("slug is lowercase letters, digits and hyphens, 3 to 63 characters")
        return text

    @field_validator("compose_file", mode="before")
    @classmethod
    def _compose(cls, value):
        if value in (None, ""):
            return None
        text = str(value).strip()
        if not _REL_PATH_RE.match(text):
            raise ValueError("compose_file must be a relative path inside the project")
        return text

    @field_validator("env", mode="before")
    @classmethod
    def _env(cls, value):
        return DeployService.model_validate({"name": "x", "env": value}).env

    @field_validator("services")
    @classmethod
    def _unique(cls, value):
        seen = set()
        for svc in value:
            if svc.name in seen:
                raise ValueError(f"service {svc.name!r} is listed twice")
            seen.add(svc.name)
        if len(value) > 20:
            raise ValueError("at most 20 services")
        return value

    # ── helpers ──────────────────────────────────────────────────────────

    def service(self, name: Optional[str]) -> Optional[DeployService]:
        if not name:
            return self.primary()
        for svc in self.services:
            if svc.name == name:
                return svc
        return None

    def primary(self) -> Optional[DeployService]:
        if self.primary_service:
            for svc in self.services:
                if svc.name == self.primary_service:
                    return svc
        for svc in self.services:
            if svc.kind == "frontend":
                return svc
        return self.services[0] if self.services else None

    def add_event(self, kind: str, detail: str = "", service: Optional[str] = None) -> None:
        self.events.append(DeployEvent(kind=kind, detail=detail[:2000], service=service))
        if len(self.events) > EVENTS_KEEP:
            del self.events[: len(self.events) - EVENTS_KEEP]
        self.updated_at = now_iso()

    def touch(self) -> None:
        self.updated_at = now_iso()

    def to_dict(self, *, with_token: bool = True) -> Dict[str, Any]:
        data = self.model_dump(mode="json")
        if not with_token:
            data.pop("share_token", None)
        return data


__all__ = [
    "DEFAULT_IMAGES", "DeployEvent", "DeployService", "Desired", "Mode", "ProjectDeployment",
    "ServiceKind", "ServiceRuntime", "STATUSES", "Status", "Visibility", "new_share_token",
    "new_slug", "now_iso",
]
