"""The environment record and its validation.

Everything an operator types into an environment ends up somewhere sharp: a
package name lands on a ``RUN pip install`` line of a generated Dockerfile, an
image tag and the limits land on a ``docker run`` command line, a variable
lands in a child process's environment, a host lands in an allowlist the web
tools and the egress proxy trust. So validation is strict and happens here,
once, in the model: a record that exists is a record that is safe to hand to
any of those places without re-checking. The rules are deliberately narrow
(no option-looking values, no shell metacharacters, no variables the launcher
owns); widening one is a conscious edit here, not an accident elsewhere.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

Mode = Literal["inherit", "local", "docker"]
NetworkType = Literal["unrestricted", "none", "limited"]

#: Package registries a ``limited`` network may reach when
#: ``allow_package_managers`` is on: pip's index and file host, npm's registry
#: (and yarn's mirror of it). Enough for ``pip install`` and ``npm install``
#: inside a run; anything else a package manager needs is an explicit host.
PACKAGE_MANAGER_HOSTS = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "registry.yarnpkg.com",
)

_NAME_MAX = 80
_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
# A docker image reference: registry/path:tag or @digest. No leading dash, so
# it can never be read as an option by `docker run` or `docker build`.
_IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/:@-]{0,254}$")
# A pip requirement as it may appear on one RUN line: a name, extras, version
# specifiers. No whitespace, quotes, shell metacharacters, URLs or options.
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,-]+\])?([<>=!~]=?[A-Za-z0-9.*+!_-]+(,[<>=!~]=?[A-Za-z0-9.*+!_-]+)*)?$")
_MEMORY_RE = re.compile(r"^\d+(\.\d+)?[bkmgBKMG]?$")
_CPUS_RE = re.compile(r"^\d+(\.\d+)?$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

#: Variables an environment may not set: the launcher, the run budget and the
#: environment itself own them, and a profile that could override the relay
#: token, the run ids or the proxy variables could undo the fence it exists
#: to draw.
_RESERVED_ENV_PREFIXES = ("AGENTS_HUB_", "AGENT_", "DOCKER_")
_RESERVED_ENV_EXACT = {
    "PATH", "HOME", "PYTHONPATH", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy",
    "https_proxy", "NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy",
    "HOST_PROJECT_ROOT", "LD_PRELOAD", "LD_LIBRARY_PATH",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_host(raw: str) -> str:
    """One allowlist entry as a bare lowercase hostname.

    Accepts what people paste: ``https://api.example.com/v1``, ``*.example.com``,
    ``example.com:443``. The scheme, path, port and a leading ``*.`` are
    dropped (an entry already covers its subdomains). Raises ValueError for
    anything that is not a hostname or an IPv4 literal once cleaned.
    """
    host = str(raw or "").strip().lower()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0].split("?", 1)[0]
    if "@" in host:
        raise ValueError(f"allowed host {raw!r} must not carry credentials")
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    if host.startswith("*."):
        host = host[2:]
    host = host.strip(".")
    if not host or not (_HOST_RE.match(host) or _IPV4_RE.match(host)):
        raise ValueError(f"allowed host {raw!r} is not a hostname")
    return host


class NetworkPolicy(BaseModel):
    """What the run may reach.

    ``unrestricted``: nothing added. ``limited``: the hub's tools refuse
    hosts outside ``allowed_hosts`` (plus the package registries when
    ``allow_package_managers``), and with the egress proxy enabled every
    client that honours the proxy variables is held to the same list.
    ``none``: no internet for the agent, which is ``limited`` with an empty
    list: the hub's web and browser tools refuse every host, and through the
    proxy only the model providers and the hub's own services stay reachable
    (the agent still has to talk to its model). It is not docker's
    ``--network none``, which would cut the model off too and leave an LLM
    agent unable to do anything.
    """
    type: NetworkType = "unrestricted"
    allowed_hosts: List[str] = Field(default_factory=list)
    allow_package_managers: bool = False

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _hosts(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [v for v in re.split(r"[\s,]+", value) if v]
        out: List[str] = []
        for item in value:
            host = normalize_host(item)
            if host not in out:
                out.append(host)
        if len(out) > 200:
            raise ValueError("at most 200 allowed hosts")
        return out

    def effective_hosts(self) -> List[str]:
        """The allowlist as enforced: the hosts plus the package registries."""
        hosts = list(self.allowed_hosts)
        if self.allow_package_managers:
            hosts += [h for h in PACKAGE_MANAGER_HOSTS if h not in hosts]
        return hosts


class Limits(BaseModel):
    """Container limits; None keeps the run profile's defaults
    (managers.container_manager.DEFAULT_RUN_*, or the Settings values)."""
    memory: Optional[str] = None
    cpus: Optional[str] = None
    pids_limit: Optional[int] = None

    @field_validator("memory", mode="before")
    @classmethod
    def _memory(cls, value):
        if value in (None, ""):
            return None
        value = str(value).strip()
        if not _MEMORY_RE.match(value):
            raise ValueError("memory must look like 512m, 2g or a number of bytes")
        return value

    @field_validator("cpus", mode="before")
    @classmethod
    def _cpus(cls, value):
        if value in (None, ""):
            return None
        value = str(value).strip()
        if not _CPUS_RE.match(value) or float(value) <= 0:
            raise ValueError("cpus must be a positive number such as 0.5 or 2")
        return value

    @field_validator("pids_limit", mode="before")
    @classmethod
    def _pids(cls, value):
        if value in (None, ""):
            return None
        value = int(value)
        if value < 16:
            raise ValueError("pids_limit below 16 cannot start an agent")
        return value


def validate_packages(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [v for v in re.split(r"[\s,]+", value) if v]
    out: List[str] = []
    for item in value:
        pkg = str(item or "").strip()
        if not pkg:
            continue
        if not _PACKAGE_RE.match(pkg):
            raise ValueError(f"package {pkg!r} is not a plain pip requirement (name, extras, version)")
        if pkg not in out:
            out.append(pkg)
    if len(out) > 100:
        raise ValueError("at most 100 packages")
    return out


def validate_image(value) -> Optional[str]:
    if value in (None, ""):
        return None
    image = str(value).strip()
    if not _IMAGE_RE.match(image):
        raise ValueError(f"image {image!r} is not a docker image reference")
    return image


def validate_env(value) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("env must be an object of NAME: value")
    out: Dict[str, str] = {}
    for key, val in value.items():
        key = str(key).strip()
        if not _ENV_KEY_RE.match(key):
            raise ValueError(f"{key!r} is not a valid environment variable name")
        if key in _RESERVED_ENV_EXACT or any(key.startswith(p) for p in _RESERVED_ENV_PREFIXES):
            raise ValueError(f"{key} is set by the hub and cannot be set by an environment")
        text = "" if val is None else str(val)
        if "\x00" in text or len(text) > 4096:
            raise ValueError(f"the value of {key} is too long or holds a NUL byte")
        out[key] = text
    if len(out) > 100:
        raise ValueError("at most 100 variables")
    return out


def validate_name(value) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError("name is required")
    if len(name) > _NAME_MAX:
        raise ValueError(f"name is longer than {_NAME_MAX} characters")
    return name


class Environment(BaseModel):
    """One execution profile. ``workspace`` None means every workspace may use
    it; ``is_default`` makes it the one a run gets when it names none (at most
    one per scope, the workspace's own default winning over a global one)."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: str = ""
    workspace: Optional[str] = None
    mode: Mode = "inherit"
    image: Optional[str] = None
    packages: List[str] = Field(default_factory=list)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    limits: Limits = Field(default_factory=Limits)
    env: Dict[str, str] = Field(default_factory=dict)
    is_default: bool = False
    archived_at: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value):
        return validate_name(value)

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value):
        text = str(value or "").strip()
        if len(text) > 2000:
            raise ValueError("description is longer than 2000 characters")
        return text

    @field_validator("workspace", mode="before")
    @classmethod
    def _workspace(cls, value):
        text = str(value or "").strip()
        return text or None

    @field_validator("image", mode="before")
    @classmethod
    def _image(cls, value):
        return validate_image(value)

    @field_validator("packages", mode="before")
    @classmethod
    def _packages(cls, value):
        return validate_packages(value)

    @field_validator("env", mode="before")
    @classmethod
    def _env(cls, value):
        return validate_env(value)

    @property
    def archived(self) -> bool:
        return bool(self.archived_at)


__all__ = [
    "Environment", "NetworkPolicy", "Limits", "Mode", "NetworkType",
    "PACKAGE_MANAGER_HOSTS", "normalize_host", "validate_packages",
    "validate_image", "validate_env", "validate_name", "now_iso",
]
