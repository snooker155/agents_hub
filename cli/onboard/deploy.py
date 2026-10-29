"""The production parts of `ah setup`: topology, single sign-on, worker hosts, Kubernetes.

The wizard (wizard.py) started as an install for one person: one backend, SQLite
or one Postgres, the dashboard on a port. A team install needs more, and every
piece of it already exists in the repository; what was missing is someone who
fits them together and picks the passwords. This module is that part:

* **Topology** (:func:`step_scale`). ``single`` is the one backend of a plain
  ``docker compose up``. ``ha`` is the compose ``ha`` profile (docs/deployment.md):
  Postgres, Redis, MinIO, several ``backend-api`` replicas behind the
  dashboard's nginx and several ``worker`` replicas draining the launch queue.
  Replica counts and published ports cannot be set from ``.env``, so the
  wizard writes them into an overlay, ``docker-compose.setup.yml``, and names
  both files in ``COMPOSE_FILE`` so a bare ``docker compose up`` reads them.
  The base files stay untouched and upgrade cleanly.
* **Worker hosts**: further machines that only run workers against the same
  Postgres, Redis and object store. The platform still keeps workspaces and
  run files under ``.agents_hub/`` (docs/workers.md, "What still needs one
  host"), so every host mounts one shared directory (NFS or similar) at the
  same path and ``AGENTS_HUB_ROOT`` points there; the wizard asks for it rather
  than pretending the object store covers it. Each host gets a folder with a
  compose file and an ``.env`` to copy over.
* **Keycloak in the stack** (:func:`keycloak_realm`): the realm the CI signs
  in against (deploy/keycloak/realm-export.json), made safe for a real install:
  no demo users, a generated client secret, this hub's redirect URI. People's
  browsers reach Keycloak at its public URL, the hub's containers at
  ``http://keycloak:8080`` (``AUTH_OIDC_INTERNAL_URL``, ee/oidc.py).
* **Kubernetes** (:func:`kubernetes_values`): values for the chart in
  deploy/helm/agents-hub, split into a values file and a secrets file kept out
  of version control, the way the chart's README asks.

Passwords for the bundled services are generated, never the defaults the
compose files fall back to, and a rerun keeps the ones in force: changing
``POSTGRES_PASSWORD`` after the volume exists would not change the server's.
"""
from __future__ import annotations

import json
import os
import sys
import re
import secrets
import shutil
import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

from cli.onboard.ui import Asker, SetupError

if TYPE_CHECKING:
    from cli.onboard.wizard import Plan

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REALM_TEMPLATE = PROJECT_ROOT / "deploy" / "keycloak" / "realm-export.json"
CHART = PROJECT_ROOT / "deploy" / "helm" / "agents-hub"
# From the setup container the chart is copied next to the values, under a
# name of setup's own: the folder it runs in may be someone's repository with
# a chart/ of its own. The marker is what lets a rerun replace the copy.
CHART_DIR = "agents-hub-chart"
CHART_MARKER = ".written-by-ah-setup"
OVERLAY = "docker-compose.setup.yml"
REALM = "agents-hub"
CLIENT_ID = "agents-hub"
KEYCLOAK_INTERNAL = "http://keycloak:8080"
BACKEND_IMAGE = "ghcr.io/snooker155/agents-hub-backend"

TOPOLOGIES = [
    ("single", "One backend", "a person or a small team; SQLite or Postgres, nothing else to run"),
    ("ha", "A team, under load", "Postgres, Redis, object storage, several API replicas and workers"),
]


def _password() -> str:
    return secrets.token_urlsafe(18)


def _count(value: str) -> Optional[str]:
    return None if value.isdigit() and 0 < int(value) <= 64 else "A number from 1 to 64"


def _url_problem(*schemes: str):
    def check(value: str) -> Optional[str]:
        return None if value.startswith(schemes) else f"It has to start with {' or '.join(schemes)}"
    return check


def _host_of(url: str) -> str:
    return urlparse(url).hostname or "localhost"


def _default_address() -> str:
    """This machine's address on the network, the one worker hosts would dial."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))  # TEST-NET: nothing is sent, the OS just picks a route
            return s.getsockname()[0]
    except OSError:
        return socket.gethostname()


# ---------------------------------------------------------------------------
# The step: how big
# ---------------------------------------------------------------------------

def step_scale(a: Asker, plan: "Plan", number: int, total: int) -> None:
    a.section(number, total, "Scale", "How much load it takes, and what runs beside it (docs/deployment.md).")
    if plan.shape == "kubernetes":
        plan.topology = "ha"
        _scale_kubernetes(a, plan)
        return
    current = "ha" if "ha" in (plan.cur("COMPOSE_PROFILES") or "").split(",") else "single"
    plan.topology = a.choose("topology", "What should it carry?", TOPOLOGIES, default=current)
    if plan.topology == "single":
        if current == "ha":
            _leave_ha(a, plan)
        return
    _load_worker_hosts(plan)

    a.note("[dim]Postgres, Redis and MinIO run in the stack unless you point at your own below.[/dim]")
    plan.api_replicas = int(plan.cur("AGENTS_HUB_SETUP_API_REPLICAS", "2"))
    plan.worker_replicas = int(plan.cur("AGENTS_HUB_SETUP_WORKER_REPLICAS", "2"))
    concurrency = plan.cur("AGENTS_HUB_WORKER_CONCURRENCY", "4")
    if not plan.quick:
        plan.api_replicas = int(a.text("api_replicas", "API replicas (each serves the dashboard and the API)",
                                       default=str(plan.api_replicas), validate=_count))
        plan.worker_replicas = int(a.text("worker_replicas", "Worker replicas (each runs agents from the queue)",
                                          default=str(plan.worker_replicas), validate=_count))
        concurrency = a.text("worker_concurrency", "Runs one worker takes at once", default=concurrency,
                             validate=_count)
    plan.env["AGENTS_HUB_WORKER_CONCURRENCY"] = concurrency
    # Kept in .env only so a rerun offers the same numbers: compose reads the
    # counts from the overlay, not from these.
    plan.env["AGENTS_HUB_SETUP_API_REPLICAS"] = str(plan.api_replicas)
    plan.env["AGENTS_HUB_SETUP_WORKER_REPLICAS"] = str(plan.worker_replicas)
    plan.env["BACKEND_SERVERS"] = "backend-api:8000"

    _ask_redis(a, plan, bundled_ok=True)
    _ask_blobs(a, plan, bundled_ok=True)
    if not plan.quick:
        _ask_worker_hosts(a, plan)
    elif plan.shared_dir:
        # QuickStart asks nothing about worker hosts, so it keeps the ones in
        # force: dropping the shared mount would leave the hub on its old
        # volume while the worker hosts go on writing to the shared directory.
        plan.env["AGENTS_HUB_SETUP_SHARED_DIR"] = plan.shared_dir
        plan.env["AGENTS_HUB_SETUP_ADDRESS"] = plan.main_address
    plan.profiles.append("ha")


def _leave_ha(a: Asker, plan: "Plan") -> None:
    """Back to one backend: unset what only the ha topology set. write_env
    keeps every key it is not given, so without this nginx would still name
    backend-api (no longer started) and the backend would still send events
    and files to the Redis and MinIO of a profile that is off."""
    if plan.cur("BACKEND_SERVERS"):
        plan.env["BACKEND_SERVERS"] = ""  # the compose files fall back to backend:8000
    if _bundled_redis(plan.cur("AGENTS_HUB_BROKER_URL")):
        plan.env["AGENTS_HUB_BROKER_URL"] = ""
    if "minio:9000" in plan.cur("AGENTS_HUB_BLOB_ENDPOINT"):
        plan.env.update({"AGENTS_HUB_BLOB_URL": "", "AGENTS_HUB_BLOB_ENDPOINT": ""})
        if plan.cur("AWS_ACCESS_KEY_ID") and plan.cur("AWS_ACCESS_KEY_ID") == plan.cur("MINIO_ROOT_USER"):
            plan.env.update({"AWS_ACCESS_KEY_ID": "", "AWS_SECRET_ACCESS_KEY": ""})
    if plan.cur("AGENTS_HUB_SETUP_SHARED_DIR"):
        a.warn(f"The hub's state stays in {plan.cur('AGENTS_HUB_SETUP_SHARED_DIR')}: it has to be copied back "
               "before the stack starts, or the hub opens without it (the commands come at the end, and "
               "setup does not start the stack before that).")
    for key in ("AGENTS_HUB_SETUP_SHARED_DIR", "AGENTS_HUB_SETUP_ADDRESS"):
        if plan.cur(key):
            plan.env[key] = ""


def _bundled_redis(url: str) -> bool:
    return "@redis:" in url or "//redis:" in url


def _load_worker_hosts(plan: "Plan") -> None:
    """The worker hosts in force, from the shared directory .env names and
    the folders an earlier run wrote under workers/, so a rerun offers (and
    QuickStart keeps) them. Held even through Start over: the hub's state
    lives in that directory whatever the file says."""
    shared = plan.held("AGENTS_HUB_SETUP_SHARED_DIR")
    if not shared:
        return
    import yaml
    plan.shared_dir = shared
    plan.main_address = plan.held("AGENTS_HUB_SETUP_ADDRESS")
    hosts = []
    folders = plan.target / "workers"
    for compose in sorted(folders.glob("*/docker-compose.yml")) if folders.is_dir() else []:
        try:
            worker = (yaml.safe_load(compose.read_text(encoding="utf-8")) or {})["services"]["worker"]
            replicas = int((worker.get("deploy") or {}).get("replicas") or 1)
        except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError):
            continue
        docker = any(str(v).startswith("/var/run/docker.sock:") for v in worker.get("volumes") or [])
        hosts.append({"name": compose.parent.name, "replicas": replicas, "docker": docker})
    plan.worker_hosts = hosts


def _ask_redis(a: Asker, plan: "Plan", bundled_ok: bool) -> None:
    current = plan.cur("AGENTS_HUB_BROKER_URL")
    options = ([("bundled", "Redis in the stack", "no port published unless worker hosts need it")]
               if bundled_ok else [])
    options.append(("url", "A Redis you already have", "by URL"))
    default = "url" if current and not _bundled_redis(current) else options[0][0]
    if plan.quick and bundled_ok:
        # QuickStart does not ask, so it keeps what is in force: a Redis of
        # the person's own stays theirs.
        kind = default
        if kind == "url":
            plan.redis_kind = kind
            return
    else:
        kind = a.choose("redis", "Redis for the replicas' live events", options, default=default)
    plan.redis_kind = kind
    if kind == "bundled":
        password = plan.cur("REDIS_PASSWORD") or _password()
        plan.env["REDIS_PASSWORD"] = password
        plan.env["AGENTS_HUB_BROKER_URL"] = f"redis://:{password}@redis:6379/0"
    else:
        plan.env["AGENTS_HUB_BROKER_URL"] = a.text("redis_url", "Redis URL", default=current, required=True,
                                                   validate=_url_problem("redis://", "rediss://"))


def _ask_blobs(a: Asker, plan: "Plan", bundled_ok: bool) -> None:
    current = plan.cur("AGENTS_HUB_BLOB_ENDPOINT")
    options = ([("bundled", "MinIO in the stack", "an S3 compatible store beside the hub")] if bundled_ok else [])
    options.append(("s3", "An S3 bucket you already have", "AWS, or any S3 compatible endpoint"))
    if not bundled_ok:
        options.append(("none", "None, a shared volume only", "a ReadWriteMany volume carries every file"))
    # An AWS bucket has no endpoint, so the bucket URL tells as much as the endpoint.
    external = bool(current or plan.cur("AGENTS_HUB_BLOB_URL")) and "minio:9000" not in current
    default = "s3" if external else options[0][0]
    if plan.quick and bundled_ok:
        # As for Redis: an S3 bucket in force stays, keys and all.
        kind = default
        if kind == "s3":
            plan.blob_kind = kind
            return
    else:
        kind = a.choose("object_storage", "Object storage for run logs and view assets", options, default=default)
    plan.blob_kind = kind
    if kind == "bundled":
        user = plan.cur("MINIO_ROOT_USER") or "agents_hub"
        password = plan.cur("MINIO_ROOT_PASSWORD") or _password()
        plan.env.update({"MINIO_ROOT_USER": user, "MINIO_ROOT_PASSWORD": password,
                         "AGENTS_HUB_BLOB_URL": "s3://agents-hub/", "AGENTS_HUB_BLOB_ENDPOINT": "http://minio:9000",
                         "AWS_ACCESS_KEY_ID": user, "AWS_SECRET_ACCESS_KEY": password})
    elif kind == "s3":
        if bundled_ok:
            a.note("[dim]The stack still starts its MinIO (the ha profile waits for it); it stays unused.[/dim]")
        plan.env["AGENTS_HUB_BLOB_URL"] = a.text("s3.url", "Bucket, as s3://bucket/prefix/",
                                                 default=plan.cur("AGENTS_HUB_BLOB_URL"), required=True,
                                                 validate=_url_problem("s3://"))
        plan.env["AGENTS_HUB_BLOB_ENDPOINT"] = a.text("s3.endpoint", "Endpoint URL (empty for AWS itself)",
                                                      default="" if "minio:9000" in current else current)
        plan.env["AWS_ACCESS_KEY_ID"] = a.text("s3.access_key_id", "Access key id",
                                               default=plan.cur("AWS_ACCESS_KEY_ID"), required=True)
        plan.env["AWS_SECRET_ACCESS_KEY"] = a.secret("s3.secret_access_key", "Secret access key",
                                                     current=plan.cur("AWS_SECRET_ACCESS_KEY"), allow_empty=False)
    else:
        for key in ("AGENTS_HUB_BLOB_URL", "AGENTS_HUB_BLOB_ENDPOINT"):
            plan.env[key] = ""


def _ask_worker_hosts(a: Asker, plan: "Plan") -> None:
    if not a.confirm("worker_hosts.enabled", "Run workers on further machines too?",
                     bool(plan.worker_hosts or plan.shared_dir)):
        if plan.shared_dir:
            a.warn(f"The hub's state stays in {plan.shared_dir}: it has to be copied back before the stack "
                   "starts, or the hub opens without it (the commands come at the end, and setup does not "
                   "start the stack before that).")
        plan.worker_hosts, plan.shared_dir, plan.main_address = [], "", ""
        for key in ("AGENTS_HUB_SETUP_SHARED_DIR", "AGENTS_HUB_SETUP_ADDRESS"):
            if plan.cur(key):
                plan.env[key] = ""
        return
    a.note("Each machine runs only workers, against this host's Postgres, Redis and MinIO, which then get "
           "their ports published (behind the passwords generated here; keep them off the internet).")
    a.note("Workspaces and run files stay files: every machine, this one included, mounts one shared "
           "directory (NFS or similar) at the same path, and the hub keeps its state there. Nothing is "
           "copied for you: an install already in use copies its state over first (the commands come at "
           "the end, and setup does not start the stack before that).")
    plan.shared_dir = a.text("worker_hosts.shared_dir", "Shared directory, the same path on every machine",
                             default=plan.cur("AGENTS_HUB_SETUP_SHARED_DIR") or "/mnt/agents-hub", required=True,
                             validate=lambda v: None if v.startswith("/") else "An absolute path")
    plan.main_address = a.text("worker_hosts.address", "This host's address as the other machines reach it",
                               default=plan.cur("AGENTS_HUB_SETUP_ADDRESS") or _default_address(), required=True)
    names = a.text("worker_hosts.names", "Names of the machines, separated by commas",
                   default=",".join(h["name"] for h in plan.worker_hosts) or "worker-1", required=True)
    replicas = a.text("worker_hosts.replicas", "Workers on each machine",
                      default=str(plan.worker_hosts[0]["replicas"]) if plan.worker_hosts else "2", validate=_count)
    docker = a.confirm("worker_hosts.docker", "Run agents in containers there (mounts that machine's Docker "
                                              "socket)?", any(h["docker"] for h in plan.worker_hosts))
    plan.worker_hosts = [{"name": re.sub(r"[^a-zA-Z0-9_.-]", "-", n.strip()), "replicas": int(replicas),
                          "docker": docker} for n in names.split(",") if n.strip()]
    plan.env["AGENTS_HUB_SETUP_SHARED_DIR"] = plan.shared_dir
    plan.env["AGENTS_HUB_SETUP_ADDRESS"] = plan.main_address
    if plan.redis_kind == "bundled" and not plan.env.get("REDIS_PASSWORD"):
        plan.env["REDIS_PASSWORD"] = _password()


def _scale_kubernetes(a: Asker, plan: "Plan") -> None:
    k = plan.k8s
    k["release"] = a.text("k8s.release", "Release name", default=k.get("release") or "hub", required=True,
                          validate=lambda v: None if re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", v)
                          else "Lower case letters, digits and dashes")
    k["namespace"] = a.text("k8s.namespace", "Namespace", default=k.get("namespace") or "agents-hub", required=True)
    k["host"] = a.text("k8s.host", "Host name people open (the ingress host)",
                       default=k.get("host") or "agents-hub.example.com", required=True)
    k["ingress_class"] = a.text("k8s.ingress_class", "Ingress class (empty for the cluster default)",
                                default=k.get("ingress_class", "nginx"))
    k["tls"] = a.choose("k8s.tls", "TLS for that host", [
        ("cert-manager", "cert-manager", "a ClusterIssuer issues the certificate"),
        ("secret", "An existing TLS secret", "you created it in the namespace"),
        ("none", "None", "TLS ends in front of the cluster, or plain HTTP"),
    ], default=k.get("tls", "cert-manager"))
    if k["tls"] == "cert-manager":
        k["issuer"] = a.text("k8s.issuer", "ClusterIssuer name", default=k.get("issuer") or "letsencrypt",
                             required=True)
    elif k["tls"] == "secret":
        k["tls_secret"] = a.text("k8s.tls_secret", "Secret name", default=k.get("tls_secret") or "agents-hub-tls",
                                 required=True)
    plan.api_replicas = int(a.text("api_replicas", "API replicas", default=str(plan.api_replicas),
                                   validate=_count))
    plan.worker_replicas = int(a.text("worker_replicas", "Worker replicas", default=str(plan.worker_replicas),
                                      validate=_count))
    k["frontend_replicas"] = int(a.text("k8s.frontend_replicas", "Dashboard replicas",
                                        default=str(k.get("frontend_replicas") or 2), validate=_count))
    plan.env["AGENTS_HUB_WORKER_CONCURRENCY"] = a.text("worker_concurrency", "Runs one worker takes at once",
                                                       default=plan.cur("AGENTS_HUB_WORKER_CONCURRENCY", "4"),
                                                       validate=_count)
    a.note("[dim]The chart runs no Postgres, Redis or object store of its own: give it yours.[/dim]")
    _ask_redis(a, plan, bundled_ok=False)
    _ask_blobs(a, plan, bundled_ok=False)
    k["storage_class"] = a.text("k8s.storage_class", "Storage class of the shared volume (ReadWriteMany; empty "
                                                     "for the default)", default=k.get("storage_class", ""))
    k["storage_size"] = a.text("k8s.storage_size", "Its size", default=k.get("storage_size") or "20Gi")
    tag = k.get("tag") or plan.cur("AGENTS_HUB_TAG")
    k["tag"] = a.text("k8s.tag", "Image tag (empty for the chart's own release)", default=tag)


# ---------------------------------------------------------------------------
# Single sign-on
# ---------------------------------------------------------------------------

def step_sso(a: Asker, plan: "Plan") -> None:
    """Asked in the Access step when people have accounts (multi mode)."""
    options = [("none", "Passwords only", "accounts made here or on the Accounts page")]
    if plan.shape in ("docker", "compose"):
        options.append(("keycloak", "Keycloak in the stack", "this setup runs it, with a realm ready for the hub"))
    options.append(("external", "A provider you already have", "Keycloak, Entra ID, Google: an OIDC issuer"))
    issuer = plan.cur("AUTH_OIDC_ISSUER")
    default = ("keycloak" if plan.cur("AUTH_OIDC_INTERNAL_URL") == KEYCLOAK_INTERNAL and len(options) == 3
               else "external" if issuer else "none")
    legacy = (getattr(a, "answers", None) or {}).get("sso")
    if isinstance(legacy, bool):
        # Answers files from before this question had a choice said yes or no.
        a.answers["sso"] = "external" if legacy else "none"
    plan.sso = a.choose("sso", "Single sign-on", options, default=default)
    if plan.sso == "none":
        if issuer:
            plan.env.update({"AUTH_OIDC_ISSUER": "", "AUTH_OIDC_INTERNAL_URL": ""})
        return
    public = a.text("public_url", "URL people open the hub at", default=plan.cur("AUTH_PUBLIC_URL") or plan.base_url,
                    required=True, validate=_url_problem("http://", "https://")).rstrip("/")
    plan.public_url = public
    plan.env["AUTH_PUBLIC_URL"] = public
    if plan.sso == "keycloak":
        _keycloak(a, plan, public)
        return
    plan.env["AUTH_OIDC_ISSUER"] = a.text("oidc.issuer", "Issuer URL", default=issuer, required=True)
    plan.env["AUTH_OIDC_CLIENT_ID"] = a.text("oidc.client_id", "Client id",
                                             default=plan.cur("AUTH_OIDC_CLIENT_ID"), required=True)
    plan.env["AUTH_OIDC_CLIENT_SECRET"] = a.secret("oidc.client_secret", "Client secret",
                                                   current=plan.cur("AUTH_OIDC_CLIENT_SECRET"))
    plan.env["AUTH_OIDC_PROVIDER_NAME"] = a.text("oidc.provider_name", "Name on the sign-in button",
                                                 default=plan.cur("AUTH_OIDC_PROVIDER_NAME") or "Company SSO")
    if plan.cur("AUTH_OIDC_INTERNAL_URL") == KEYCLOAK_INTERNAL:
        plan.env["AUTH_OIDC_INTERNAL_URL"] = ""
    a.note(f"Register this redirect URI at the provider: [bold]{public}/api/auth/oidc/callback[/bold]")


def _keycloak(a: Asker, plan: "Plan", public: str) -> None:
    port = plan.cur("KEYCLOAK_PORT") or "8081"
    # Over HTTPS, Caddy serves Keycloak on the hub's own domain (8443);
    # without it, Keycloak's own published port.
    suggested = (f"https://{_host_of(public)}:8443" if public.startswith("https://")
                 else f"http://{_host_of(public)}:{port}")
    kc_public = a.text("keycloak.public_url", "URL people's browsers reach Keycloak at",
                       default=plan.cur("KEYCLOAK_PUBLIC_URL") or suggested,
                       required=True, validate=_url_problem("http://", "https://")).rstrip("/")
    parsed = urlparse(kc_public)
    if parsed.scheme == "http" and parsed.port and not plan.cur("KEYCLOAK_PORT"):
        port = str(parsed.port)
    # Keycloak creates its bootstrap administrator on the first start only,
    # so the password in force stays even through Start over: a new one
    # would neither sign in nor let setup update the realm afterwards.
    admin_password = plan.held("KEYCLOAK_ADMIN_PASSWORD") or _password()
    client_secret = plan.cur("AUTH_OIDC_CLIENT_SECRET") if plan.cur("AUTH_OIDC_INTERNAL_URL") == KEYCLOAK_INTERNAL \
        else ""
    client_secret = client_secret or _password()
    if not plan.held("KEYCLOAK_ADMIN_PASSWORD"):
        plan.generated.append(("Keycloak administrator (user admin)", admin_password))
    # Keycloak keeps its realm in the bundled Postgres even when the hub stays
    # on SQLite, and the server keeps the password its volume was made with.
    plan.env["POSTGRES_PASSWORD"] = plan.held("POSTGRES_PASSWORD") or plan.pg_password or _password()
    plan.env.update({
        "KEYCLOAK_PUBLIC_URL": kc_public, "KEYCLOAK_PORT": port, "KEYCLOAK_ADMIN_PASSWORD": admin_password,
        "AUTH_OIDC_ISSUER": f"{kc_public}/realms/{REALM}", "AUTH_OIDC_CLIENT_ID": CLIENT_ID,
        "AUTH_OIDC_CLIENT_SECRET": client_secret, "AUTH_OIDC_PROVIDER_NAME": "Keycloak",
        "AUTH_OIDC_INTERNAL_URL": KEYCLOAK_INTERNAL,
    })
    plan.profiles.append("keycloak")
    plan.keycloak = {"public_url": kc_public, "client_secret": client_secret}


def keycloak_realm(public_url: str, client_secret: str, template: Path = REALM_TEMPLATE) -> dict:
    """The CI realm, made fit for a real install."""
    realm = json.loads(template.read_text(encoding="utf-8"))
    realm["users"] = []  # alice and bob sign the CI in; a real realm starts empty
    # Keycloak refuses plain HTTP from anything but private addresses by
    # default ("external"), which a LAN install without TLS would hit at the
    # first sign-in from another subnet; over HTTPS that default is right.
    realm["sslRequired"] = "none" if public_url.startswith("http://") else "external"
    for client in realm.get("clients") or []:
        if client.get("clientId") == CLIENT_ID:
            client["secret"] = client_secret
            client["redirectUris"] = [f"{public_url}/api/auth/oidc/callback"]
            client["webOrigins"] = [public_url]
            client["rootUrl"] = public_url
    return realm


def keycloak_base(plan: "Plan") -> str:
    """Where this process reaches Keycloak: its published port, through the
    host gateway from inside the setup container."""
    return api_base(f"http://localhost:{plan.cur('KEYCLOAK_PORT') or '8081'}", plan.in_docker)


def keycloak_realm_changed(plan: "Plan") -> bool:
    """Whether a Keycloak that already ran holds a client this run changed."""
    if plan.sso != "keycloak" or plan.current.get("AUTH_OIDC_INTERNAL_URL") != KEYCLOAK_INTERNAL:
        return False
    return plan.fresh or any(plan.env.get(key) != plan.current.get(key)
                             for key in ("AUTH_PUBLIC_URL", "AUTH_OIDC_CLIENT_SECRET", "KEYCLOAK_PUBLIC_URL"))


def keycloak_manual_steps(plan: "Plan") -> list[str]:
    public = plan.public_url
    return [
        "Keycloak imports keycloak/realm.json on its first start only. If it ran before, open "
        f"{plan.keycloak['public_url']}/admin (user admin), realm {REALM}, Clients, {CLIENT_ID}, and set:",
        f"  Valid redirect URIs: {public}/api/auth/oidc/callback",
        f"  Web origins: {public}",
        f"  Credentials: regenerate the client secret and put it into {plan.env_file} as "
        "AUTH_OIDC_CLIENT_SECRET, then `docker compose up -d`.",
        "Or run `ah setup` again once the stack runs: it makes these changes itself.",
    ]


def sync_keycloak_client(a: Asker, plan: "Plan", wait_seconds: int = 180) -> bool:
    """Bring the realm Keycloak already holds in line with realm.json.

    Keycloak runs ``start --import-realm``, which skips a realm it already
    has, so what a rerun changed (the public URL, the client secret after
    Start over) would never reach it and every sign-in would fail on the
    redirect URI or the secret. Setup holds the bootstrap administrator's
    password, so it makes the same change through the admin REST API. On a
    first start this repeats what the import did, which is harmless."""
    import requests
    base = keycloak_base(plan)
    want = keycloak_realm(plan.public_url, plan.keycloak["client_secret"])
    client_want = next(c for c in want["clients"] if c.get("clientId") == CLIENT_ID)
    token, reason = "", "it did not answer"
    deadline = time.time() + wait_seconds
    with a.console.status("  Waiting for Keycloak…"):
        while True:
            try:
                r = requests.post(f"{base}/realms/master/protocol/openid-connect/token", timeout=10, data={
                    "grant_type": "password", "client_id": "admin-cli", "username": "admin",
                    "password": plan.cur("KEYCLOAK_ADMIN_PASSWORD")})
                if r.ok:
                    token = r.json().get("access_token") or ""
                    break
                if r.status_code == 401:
                    reason = "it refused the administrator password in .env"
                    break
            except (requests.RequestException, ValueError):
                pass  # still starting
            if time.time() >= deadline:
                break
            time.sleep(3)
    if token:
        headers = {"Authorization": f"Bearer {token}"}
        admin = f"{base}/admin/realms/{REALM}"
        try:
            r = requests.get(f"{admin}/clients", params={"clientId": CLIENT_ID}, headers=headers, timeout=15)
            found = r.json() if r.ok else []
            if not found:
                reason = f"no client {CLIENT_ID} in its realm {REALM} ({r.status_code})"
            else:
                client = {**found[0], **{key: client_want[key]
                                         for key in ("secret", "redirectUris", "webOrigins", "rootUrl")}}
                r = requests.put(f"{admin}/clients/{client['id']}", json=client, headers=headers, timeout=15)
                if r.ok:
                    r = requests.put(admin, json={"sslRequired": want["sslRequired"]}, headers=headers, timeout=15)
                if r.ok:
                    a.ok(f"Keycloak's client {CLIENT_ID}: redirect URI, web origins and secret are current.")
                    return True
                reason = f"{r.status_code} {r.text[:200]}"
        except (requests.RequestException, ValueError) as exc:
            reason = exc.__class__.__name__
    a.warn(f"Keycloak's client was not updated: {reason}.")
    for line in keycloak_manual_steps(plan):
        a.note(line)
    return False


# ---------------------------------------------------------------------------
# Files: the compose overlay, the realm, the worker hosts
# ---------------------------------------------------------------------------

def needs_overlay(plan: "Plan") -> bool:
    return plan.topology == "ha" or keycloak_behind_caddy(plan)


def keycloak_behind_caddy(plan: "Plan") -> bool:
    """Keycloak on the hub's HTTPS domain: Caddy, which already holds that
    certificate, serves it on a second port, since Keycloak itself speaks
    plain HTTP inside the stack."""
    return plan.sso == "keycloak" and "https" in plan.profiles and \
        plan.keycloak.get("public_url", "").startswith("https://")


def caddyfile_text(plan: "Plan") -> str:
    port = urlparse(plan.keycloak["public_url"]).port or 443
    return ("# Written by `ah setup`: the dashboard and Keycloak on the hub's domain.\n"
            "{$HUB_DOMAIN} {\n\treverse_proxy frontend:80\n}\n\n"
            f"{{$HUB_DOMAIN}}:{port} {{\n\treverse_proxy keycloak:8080\n}}\n")


def overlay_text(plan: "Plan") -> str:
    """docker-compose.setup.yml: what .env cannot say (replicas, ports, mounts)."""
    lines = [
        "# Written by `ah setup` (cli/onboard/deploy.py), named in COMPOSE_FILE in .env so",
        "# `docker compose` reads it after docker-compose.yml. Run `ah setup` again to change it:",
        "# edits here are overwritten.",
        "services:",
    ]
    if keycloak_behind_caddy(plan):
        port = urlparse(plan.keycloak["public_url"]).port or 443
        lines += [
            "  https:",
            '    command: ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]',
            "    environment:",
            "      HUB_DOMAIN: ${HUB_DOMAIN}",
            "    volumes:",
            "      - ./caddy/Caddyfile:/etc/caddy/Caddyfile:ro",
        ]
        if port != 443:
            lines += ["    ports:", f'      - "{port}:{port}"']
    if plan.topology != "ha":
        return "\n".join(lines) + "\n"
    lines += [
        "  # The ha topology serves through backend-api; the single backend stays at zero.",
        "  backend:",
        "    deploy:",
        "      replicas: 0",
        "  frontend:",
        "    # Not required: a command naming other profiles leaves backend-api out.",
        "    depends_on: !override",
        "      backend-api:",
        "        condition: service_started",
        "        required: false",
        "  backend-api:",
        "    deploy:",
        f"      replicas: {plan.api_replicas}",
    ]
    # The mount follows the shared directory, not the host list: with the
    # host folders gone the state still lives there.
    shared = plan.shared_dir
    if shared:
        lines += _shared_mount(shared)
    lines += ["  worker:", "    deploy:", f"      replicas: {plan.worker_replicas}"]
    if shared:
        lines += _shared_mount(shared)
    if plan.worker_hosts:
        lines.append("  # Worker hosts reach these over the network, behind the generated passwords.")
        if plan.db_kind == "postgres-bundled":
            lines += ["  postgres:", "    ports:", '      - "5432:5432"']
        if plan.redis_kind == "bundled":
            lines += ["  redis:", "    ports:", '      - "6379:6379"']
        if plan.blob_kind == "bundled":
            lines += ["  minio:", "    ports:", '      - "9000:9000"']
    return "\n".join(lines) + "\n"


def _shared_mount(path: str) -> list[str]:
    return ["    environment:", f"      AGENTS_HUB_ROOT: {path}", "    volumes:", f"      - {path}:{path}"]


_INFRA_ONLY = ("COMPOSE_", "WEB_PORT", "BACKEND_PORT", "KEYCLOAK_", "POSTGRES_PASSWORD", "MINIO_ROOT_",
               "BACKEND_SERVERS", "HUB_DOMAIN", "AGENTS_HUB_SETUP_", "REDIS_PASSWORD",
               # The socket's group differs per machine; each host sets its own.
               "DOCKER_GID",
               # The browser service sits on the main host's private network.
               "AGENTS_HUB_BROWSER_")


def worker_env(main_env: dict, plan: "Plan") -> dict:
    """The main .env as a worker host needs it: same keys and secret key, the
    bundled services by this host's address instead of their compose names."""
    addr = plan.main_address
    out = {k: v for k, v in main_env.items() if not k.startswith(_INFRA_ONLY)}
    if plan.db_kind == "postgres-bundled":
        out["AGENTS_HUB_DATABASE_URL"] = out.get("AGENTS_HUB_DATABASE_URL", "").replace("@postgres:", f"@{addr}:")
    if plan.redis_kind == "bundled":
        out["AGENTS_HUB_BROKER_URL"] = out.get("AGENTS_HUB_BROKER_URL", "").replace("@redis:", f"@{addr}:")
    if plan.blob_kind == "bundled":
        out["AGENTS_HUB_BLOB_ENDPOINT"] = f"http://{addr}:9000"
    out["AGENTS_HUB_ROOT"] = plan.shared_dir
    # The model runtime is published on the main host's loopback only.
    out["AGENTS_HUB_MODELS_URL"] = ""
    out["AGENTS_HUB_TAG"] = plan.image_tag
    return out


def worker_compose_text(host: dict, plan: "Plan") -> str:
    modes = "local,docker" if host["docker"] else "local"
    lines = [
        f"# A worker host of the hub at {plan.main_address}, written by `ah setup`.",
        "# Copy this folder to that machine, mount the shared directory at the same path, then:",
        "#   docker compose up -d",
        "services:",
        "  worker:",
        f"    image: {BACKEND_IMAGE}:${{AGENTS_HUB_TAG:-latest}}",
        "    command: [\"python\", \"-m\", \"runtime.worker\"]",
        "    restart: unless-stopped",
        "    env_file:",
        "      - .env",
        "    environment:",
        "      PYTHONPATH: /app",
        "      AGENTS_HUB_ROLE: worker",
        f"      AGENTS_HUB_WORKER_MODES: {modes}",
        "    volumes:",
        f"      - {plan.shared_dir}:{plan.shared_dir}",
    ]
    if host["docker"]:
        # The image runs as uid 1000, which can open the socket only as a
        # member of its group (the same group_add as docker-compose.yml).
        lines += ["      - /var/run/docker.sock:/var/run/docker.sock",
                  "    group_add:", '      - "${DOCKER_GID:-999}"']
    lines += ["    healthcheck:", "      disable: true", "    deploy:", f"      replicas: {host['replicas']}"]
    return "\n".join(lines) + "\n"


def worker_host_env_text(body: str, host: dict) -> str:
    """The host's .env: the shared body, plus the socket's group where agents
    run in containers, which only that machine can tell."""
    from cli.onboard.wizard import _env_line
    if not host["docker"]:
        return body
    return body + ("# The group of /var/run/docker.sock on that machine: `stat -c %g /var/run/docker.sock`\n"
                   "# (0 with Docker Desktop).\n" + _env_line("DOCKER_GID", "999") + "\n")


def keep_local_keys(text: str, existing: dict, owned: dict) -> str:
    """The worker host's .env as written, plus what the person set in the
    one already there that setup does not own (every key but those it takes
    from the main .env): DOCKER_GID above all, which only that machine can
    tell and which the 999 written here would otherwise reset on every run,
    breaking the socket for the containers it starts."""
    from cli.onboard.wizard import set_env_text
    kept = {k: v for k, v in existing.items() if k not in owned}
    return set_env_text(text, kept) if kept else text


def _write(path: Path, text: str, private: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if private:
        path.chmod(0o600)
    return path


def write_compose_extras(a: Asker, plan: "Plan") -> None:
    """After .env: the overlay, the Keycloak realm and the worker host folders."""
    from cli.onboard.wizard import read_env, _env_line
    target = plan.target
    if needs_overlay(plan):
        _write(target / OVERLAY, overlay_text(plan))
        a.ok(f"Wrote {target / OVERLAY}.")
    if keycloak_behind_caddy(plan):
        _write(target / "caddy" / "Caddyfile", caddyfile_text(plan))
        a.ok(f"Wrote {target / 'caddy' / 'Caddyfile'}.")
    if plan.sso == "keycloak":
        realm = keycloak_realm(plan.public_url, plan.keycloak["client_secret"])
        path = _write(target / "keycloak" / "realm.json", json.dumps(realm, indent=2) + "\n", private=True)
        # Keycloak reads it as its own user, not as the file's owner.
        path.chmod(0o644)
        a.ok(f"Wrote {path}.")
    if plan.worker_hosts:
        main_env = read_env(plan.env_file)
        env = worker_env(main_env, plan)
        body = "# Written by `ah setup` for a worker host; holds secrets, keep it private.\n" + \
            "".join(_env_line(k, v) + "\n" for k, v in sorted(env.items()))
        for host in plan.worker_hosts:
            folder = target / "workers" / host["name"]
            text = worker_host_env_text(body, host)
            _write(folder / "docker-compose.yml", worker_compose_text(host, plan))
            _write(folder / ".env", keep_local_keys(text, read_env(folder / ".env"), env), private=True)
            a.ok(f"Wrote {folder} (docker-compose.yml, .env).")


def worker_host_notes(plan: "Plan") -> list[str]:
    if not plan.worker_hosts:
        return []
    lines = [
        f"Shared directory: mount it at {plan.shared_dir} on this host and every worker host,",
        f"  owned by uid 1000 (sudo chown 1000:1000 {plan.shared_dir}), before starting anything.",
        f"Worker hosts: copy {plan.target / 'workers'}/<name> to each machine, then `docker compose up -d` there.",
    ]
    if any(h["docker"] for h in plan.worker_hosts):
        lines.append("  Where agents run in containers, set DOCKER_GID in that host's .env to the group of its "
                     "/var/run/docker.sock.")
    return lines


def state_move(plan: "Plan") -> list[str]:
    """The commands that copy the hub's state into the shared directory, when
    this run points the hub at one it did not use before. Setup does not copy
    it itself: the stack has to be down, and the directory may not even be
    mounted on this host yet."""
    if not plan.shared_dir or not plan.current:
        return []
    before = plan.current.get("AGENTS_HUB_SETUP_SHARED_DIR") or ""
    if before == plan.shared_dir:
        return []
    shared = plan.shared_dir
    if before:
        copy = f"sudo cp -a {before}/. {shared}/"
    elif plan.shape == "docker":
        copy = f"docker run --rm -v {_data_volume(plan)}:/from -v {shared}:/to alpine cp -a /from/. /to/"
    else:
        copy = f"sudo cp -a {plan.target / '.agents_hub'}/. {shared}/"
    return [f"cd {plan.target}", "docker compose down", copy]


def _data_volume(plan: "Plan") -> str:
    # Compose names a volume after the project, the folder's name by default.
    project = plan.current.get("COMPOSE_PROJECT_NAME") or re.sub(r"[^a-z0-9_-]", "", plan.target.name.lower())
    return f"{project}_agents_hub_data"


def state_move_back(plan: "Plan") -> list[str]:
    """The reverse of state_move, when this run takes the hub off the shared
    directory it used (no worker hosts any more, or back to one backend).
    The hub then reads its own volume again, which holds the state from
    before the move or nothing, so started before the copy it would open on
    that and new state would mix with it."""
    if plan.shape not in ("docker", "compose") or plan.shared_dir or not plan.current:
        return []
    before = plan.current.get("AGENTS_HUB_SETUP_SHARED_DIR") or ""
    if not before:
        return []
    if plan.shape == "docker":
        copy = f"docker run --rm -v {before}:/from -v {_data_volume(plan)}:/to alpine cp -a /from/. /to/"
    else:
        copy = f"sudo cp -a {before}/. {plan.target / '.agents_hub'}/"
    return [f"cd {plan.target}", "docker compose down", copy]


def state_copy(plan: "Plan") -> tuple[str, list[str]]:
    """Where the hub's state has to be copied before the stack may start,
    and the commands that do it: into the shared directory, or back out of
    it. Empty when the state stays where it is."""
    move = state_move(plan)
    if move:
        return f"into {plan.shared_dir}", move
    back = state_move_back(plan)
    if back:
        where = "the hub's own volume" if plan.shape == "docker" else str(plan.target / ".agents_hub")
        return f"back into {where}", back
    return "", []


# ---------------------------------------------------------------------------
# Kubernetes
# ---------------------------------------------------------------------------

_SECRET_KEY = re.compile(r"(_KEY|_TOKEN|_SECRET|_PASSWORD|_KEY_ID|DATABASE_URL|BROKER_URL)$")


def kubernetes_values(plan: "Plan") -> tuple[dict, dict]:
    """(values, secrets) for deploy/helm/agents-hub."""
    k = plan.k8s
    public = f"{'http' if k['tls'] == 'none' else 'https'}://{k['host']}"
    # BACKEND_SERVERS is left to the chart: the dashboard's Deployment derives
    # it from the release name itself.
    env: dict = {"AGENT_EXECUTION_MODE": "local", "AGENTS_HUB_ROOT": "/data/agents_hub",
                 "ALLOW_ORIGINS": public, "AUTH_PUBLIC_URL": public}
    secret: dict = {}
    # As write_env does for .env: what this run did not ask about stays as it
    # was in the files in force, the derived keys above excepted.
    carried = {} if plan.fresh else {key: value for key, value in plan.current.items() if key not in env}
    for key, value in sorted({**carried, **plan.env}.items()):
        if key.startswith(("COMPOSE_", "AGENTS_HUB_SETUP_", "WEB_PORT")) or value == "":
            continue
        (secret if _SECRET_KEY.search(key) else env)[key] = value
    values: dict = {
        "image": {"tag": k.get("tag") or ""},
        "frontend": {"image": {"tag": k.get("tag") or ""}, "replicas": k.get("frontend_replicas", 2)},
        "backend": {"replicas": plan.api_replicas},
        "worker": {"replicas": plan.worker_replicas,
                   "workerConcurrency": int(plan.env.get("AGENTS_HUB_WORKER_CONCURRENCY") or 4)},
        "env": env,
        "persistence": {"enabled": True, "storageClassName": k.get("storage_class", ""),
                        "accessMode": "ReadWriteMany", "size": k.get("storage_size") or "20Gi"},
        "ingress": {"enabled": True, "className": k.get("ingress_class", ""), "host": k["host"],
                    "annotations": {}, "tls": []},
    }
    if k["tls"] == "cert-manager":
        values["ingress"]["annotations"]["cert-manager.io/cluster-issuer"] = k["issuer"]
        values["ingress"]["tls"] = [{"hosts": [k["host"]], "secretName": f"{k['release']}-agents-hub-tls"}]
    elif k["tls"] == "secret":
        values["ingress"]["tls"] = [{"hosts": [k["host"]], "secretName": k["tls_secret"]}]
    return values, {"secret": secret}


def kubernetes_command(plan: "Plan") -> list[str]:
    k = plan.k8s
    return ["helm", "upgrade", "--install", k["release"], str(plan.k8s_chart), "--namespace", k["namespace"],
            "--create-namespace", "-f", str(plan.target / "values.yaml"), "-f", str(plan.target / "secrets.yaml")]


def read_kubernetes(target: Path) -> Optional[dict]:
    """What an earlier run wrote into ``target``: the env and secrets in
    force (as a .env would hold them), the questions' answers and the replica
    counts. None when there is nothing there."""
    import yaml
    values_path, secrets_path = target / "values.yaml", target / "secrets.yaml"
    if not values_path.exists() and not secrets_path.exists():
        return None
    try:
        head = values_path.read_text(encoding="utf-8") if values_path.exists() else ""
        values = yaml.safe_load(head) or {}
        secret = (yaml.safe_load(secrets_path.read_text(encoding="utf-8")) if secrets_path.exists()
                  else None) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise SetupError(f"could not read the values in {target}: {exc}") from exc

    def part(node, *keys):
        # Values someone else wrote may have any shape; a missing part is empty.
        for key in keys:
            node = node.get(key) if isinstance(node, dict) else None
        return node if node is not None else {}

    env: dict = {}
    for found_env in (part(values, "env"), part(secret, "secret")):
        if isinstance(found_env, dict):
            env.update({str(key): str(value) for key, value in found_env.items()})
    issuer = part(values, "ingress", "annotations", "cert-manager.io/cluster-issuer") or ""
    tls = part(values, "ingress", "tls") or []
    k8s = {"host": str(part(values, "ingress", "host") or ""),
           "ingress_class": str(part(values, "ingress", "className") or ""),
           "tls": "cert-manager" if issuer else "secret" if tls else "none",
           "storage_class": str(part(values, "persistence", "storageClassName") or ""),
           "storage_size": str(part(values, "persistence", "size") or ""),
           "tag": str(part(values, "image", "tag") or ""),
           "frontend_replicas": _replicas(part(values, "frontend", "replicas"))}
    if issuer:
        k8s["issuer"] = str(issuer)
    elif tls and isinstance(tls, list) and isinstance(tls[0], dict):
        k8s["tls_secret"] = str(tls[0].get("secretName") or "")
    found = re.search(r"--install (\S+) .*--namespace (\S+)", head)
    if found:
        k8s["release"], k8s["namespace"] = found.group(1), found.group(2)
    return {"env": env, "k8s": k8s, "api_replicas": _replicas(part(values, "backend", "replicas")),
            "worker_replicas": _replicas(part(values, "worker", "replicas"))}


def _replicas(value) -> int:
    return value if isinstance(value, int) and 0 < value <= 64 else 2


def write_kubernetes(a: Asker, plan: "Plan") -> None:
    import yaml
    copy_chart = plan.k8s_chart != CHART
    if copy_chart and plan.k8s_chart.exists() and not (plan.k8s_chart / CHART_MARKER).exists():
        # Checked before anything is written: the folder is not setup's to replace.
        raise SetupError(f"{plan.k8s_chart} exists and was not written by setup; move it away and run again")
    values, secret = kubernetes_values(plan)
    target = plan.target
    head = ("# Written by `ah setup` for deploy/helm/agents-hub. Run it again to change this file.\n"
            # Read back by a rerun for the release and namespace, which the values do not hold.
            f"# Install: {' '.join(kubernetes_command(plan))}\n")
    _write(target / "values.yaml", head + yaml.safe_dump(values, sort_keys=False))
    _write(target / "secrets.yaml", head + "# Holds secrets: keep it out of version control.\n" +
           yaml.safe_dump(secret, sort_keys=False), private=True)
    a.ok(f"Wrote {target / 'values.yaml'} and {target / 'secrets.yaml'}.")
    if not copy_chart:
        return
    # From the setup container the chart is inside the image: put a copy next
    # to the values, where the host's helm can read it.
    if plan.k8s_chart.exists():
        shutil.rmtree(plan.k8s_chart)
    shutil.copytree(CHART, plan.k8s_chart)
    (plan.k8s_chart / CHART_MARKER).write_text("Copied by `ah setup`; a rerun replaces this folder.\n",
                                               encoding="utf-8")
    a.ok(f"Copied the chart to {plan.k8s_chart}.")


def apply_kubernetes(a: Asker, plan: "Plan", run_helm: bool) -> bool:
    from cli.onboard.wizard import _run
    write_kubernetes(a, plan)
    if not run_helm:
        return False
    _run(a, kubernetes_command(plan))
    return True


# ---------------------------------------------------------------------------
# Inside the setup container
# ---------------------------------------------------------------------------

def hand_back(target: Path, since: float) -> None:
    """The setup container runs as root so it can use the Docker socket; give
    what it wrote back to whoever owns the folder, or the person cannot edit
    their own .env afterwards."""
    try:
        st = target.stat()
    except OSError:
        return
    if os.geteuid() != 0 or st.st_uid == 0:
        return
    # Only what setup writes: the files at the top of the folder and its own
    # subfolders, never a checkout's tree.
    paths = [p for p in target.iterdir() if p.is_file() or p.is_symlink()]
    for sub in ("keycloak", "workers", CHART_DIR, "caddy"):
        root = target / sub
        if root.is_dir():
            paths.append(root)
            paths += list(root.rglob("*"))
    for path in paths:
        try:
            info = path.lstat()
            # A backup is copied with the old file's time (shutil.copy2), so
            # it is recognised by its name rather than by when it was written.
            ours = info.st_mtime >= since - 1 or _BACKUP.search(path.name) is not None
            if info.st_uid == 0 and ours:
                os.lchown(path, st.st_uid, st.st_gid)
        except OSError:
            continue


_BACKUP = re.compile(r"\.bak-\d{8}-\d{6}$")


def socket_gid(in_docker: bool, sock: Path = Path("/var/run/docker.sock")) -> Optional[str]:
    """The Docker socket's group as a container sees it, for DOCKER_GID: the
    backend runs as uid 1000 and reaches the socket only through that group,
    and the default 999 is not what every Linux gives it. Only where the view
    from here is the container's: inside the setup container, or on Linux.
    On a Mac host the socket is a per-user link whose group means nothing to
    the VM (Docker Desktop needs 0, which the setup container sees)."""
    if not (in_docker or sys.platform.startswith("linux")):
        return None
    try:
        return str(sock.stat().st_gid)
    except OSError:
        return None


def api_base(base_url: str, in_docker: bool) -> str:
    """Where this process reaches the published dashboard: through the host
    gateway from inside the setup container."""
    if not in_docker:
        return base_url
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://host.docker.internal" + (f":{parsed.port}" if parsed.port else "")


__all__ = ["OVERLAY", "TOPOLOGIES", "api_base", "apply_kubernetes", "hand_back", "keycloak_realm",
           "kubernetes_command", "kubernetes_values", "overlay_text", "read_kubernetes", "state_copy",
           "state_move", "state_move_back", "step_scale", "step_sso", "socket_gid", "sync_keycloak_client", "worker_compose_text", "worker_env",
           "worker_host_notes", "write_compose_extras", "write_kubernetes", "SetupError"]
