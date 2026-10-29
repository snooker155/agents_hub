"""`ah setup`: install and configure the whole service from the terminal.

A guided first run in ten steps: how to run it (this checkout, Docker from
the published images, Compose from the checkout, Kubernetes with the Helm
chart, or a hub that already runs elsewhere), how much load it carries (one
backend, or the team topology with replicas, workers and worker hosts:
deploy.py), the database, who may sign in, the model providers and their
default models, the assistant's voice (speech and transcription models), a
few features, another hub to link to (catalog, two-way sync with or without
end-to-end encryption, or its models and agents: link.py), a review, and then
the work itself.

Nothing is written until the review is confirmed. Every question is asked
through :mod:`cli.onboard.ui`, so the same steps run from an answers file for
an unattended install (``ah setup --answers setup.json --yes``), and
``--dry-run`` stops after the review.

What it writes is what the rest of the service already reads: ``.env``
(the same keys the Settings page edits), the model catalog the Models page
edits (``providers/catalog.py``), accounts through ``common/identity.py`` or,
for a hub in Docker, through ``/api/auth/bootstrap`` and ``/api/auth/users``,
and the ``default`` workspace's speech and transcription models (the Special
models tab, ``providers/special.py``), with the engines and models of the
hub's own runtime when the voice runs on this machine, and the hub link
record with its sync settings (``hublink/store.py``, ``hubsync/client.py``).
It holds no state of its own, so running it again is how a setting is changed
later: every question then defaults to the value in force.
"""
from __future__ import annotations

import base64
import importlib.util
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.columns import Columns
from rich.table import Table
from rich import box

from cli.onboard import deploy as D
from cli.onboard import link as L
from cli.onboard import probe as P
# The voice choices, shared with the assistant's guided setup in the hub.
from cli.onboard.presets import HUB_LOCAL, VOICE_CLOUD, VOICE_LOCAL_SPEECH, VOICE_LOCAL_TRANSCRIPTION
from cli.onboard.ui import Asker, SetupError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
QUICKSTART = PROJECT_ROOT / "deploy" / "quickstart"
STEPS = 10

SHAPES = [
    ("local", "This machine, from this checkout", "a virtualenv, `ah up`, the dashboard on :5173"),
    ("docker", "Docker, from the published images", "two files in a folder, dashboard on :8080, nothing to build"),
    ("compose", "Docker Compose, from this checkout", "built from source, plus Redis, the browser, local models"),
    ("kubernetes", "Kubernetes, with the Helm chart", "writes values for deploy/helm/agents-hub; helm installs"),
    ("remote", "Connect to a hub that already runs", "this `ah` talks to it over its API"),
]

PG_CONTAINER = "agents-hub-postgres"
PG_IMAGE = "postgres:17-alpine"


def _has_docker() -> bool:
    return shutil.which("docker") is not None


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _gen_password() -> str:
    return secrets.token_urlsafe(15)


def _gen_fernet_key() -> str:
    # A Fernet key is 32 random bytes, url-safe base64: the same thing
    # `ah secrets keygen` prints, without needing cryptography importable here.
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------


def read_env(path: Path) -> dict[str, str]:
    """Parsed with the service's own parser when it is importable."""
    if not path.exists():
        return {}
    try:
        from common.dotenv import read_env as _read
        return dict(_read(path))
    except ImportError:
        out: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                out[m.group(1)] = m.group(2).strip("\"'")
        return out


def _env_line(key: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


def set_env_text(content: str, updates: dict[str, str]) -> str:
    """Set each key in place: its live line, else its commented-out line in the
    template, else a new line at the end, so the file keeps its sections."""
    for key, value in updates.items():
        line = _env_line(key, value)
        live = re.compile(rf"^[ \t]*(?:export[ \t]+)?{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
        commented = re.compile(rf"^[ \t]*#[ \t]*{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
        if live.search(content):
            content = live.sub(lambda _m: line, content, count=1)
        elif commented.search(content):
            content = commented.sub(lambda _m: line, content, count=1)
        else:
            content = content.rstrip("\n") + ("\n" if content else "") + line + "\n"
    return content


def write_env(path: Path, template: Optional[Path], updates: dict[str, str], fresh: bool) -> Optional[Path]:
    """Write ``updates`` into ``path``; returns the backup made, if any."""
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(path, backup)
    if path.exists() and not fresh:
        content = path.read_text(encoding="utf-8")
    elif template is not None and template.exists():
        content = template.read_text(encoding="utf-8")
    else:
        content = ""
    content = set_env_text(content, updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    return backup


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    shape: str = "local"
    quick: bool = True
    target: Path = PROJECT_ROOT
    env_file: Path = PROJECT_ROOT / ".env"
    template: Optional[Path] = PROJECT_ROOT / ".env.example"
    current: dict = field(default_factory=dict)
    fresh: bool = False
    env: dict = field(default_factory=dict)
    db_kind: str = "sqlite"
    db_url: str = ""
    pg_port: int = 5432
    pg_password: str = ""
    migrate_sqlite: bool = False
    profiles: list = field(default_factory=list)
    auth: str = "single"
    admin: Optional[dict] = None
    users: list = field(default_factory=list)
    generated: list = field(default_factory=list)  # (label, value) shown once at the end
    generated_notes: list = field(default_factory=list)  # warnings printed with them
    providers: dict = field(default_factory=dict)  # provider -> {model, models, presets}
    pip_extras: list = field(default_factory=list)
    remote: Optional[dict] = None
    web_port: str = "8080"
    # The assistant's voice: mode, the special model entries for the default
    # workspace, and what the hub's own runtime installs and downloads.
    voice: dict = field(default_factory=dict)
    # Another hub this one links to (link.py): url, key, mode, sync mappings.
    link: dict = field(default_factory=dict)
    # Inside the setup container (`docker compose run --rm setup`): the folder
    # it was started in, mounted at the same path as on the host.
    in_docker: bool = False
    workdir: Optional[Path] = None
    # The production parts (deploy.py).
    topology: str = "single"
    api_replicas: int = 2
    worker_replicas: int = 2
    redis_kind: str = ""
    blob_kind: str = ""
    worker_hosts: list = field(default_factory=list)
    shared_dir: str = ""
    main_address: str = ""
    sso: str = "none"
    public_url: str = ""
    keycloak: dict = field(default_factory=dict)
    k8s: dict = field(default_factory=dict)
    k8s_chart: Path = D.CHART

    def cur(self, key: str, default: str = "") -> str:
        """The value in force: what this run already decided, then the file."""
        if key in self.env:
            return self.env[key]
        return "" if self.fresh else (self.current.get(key) or default)

    def held(self, key: str) -> str:
        """The value in force even when this run starts over from the template:
        for what state outside the file was made with (the Postgres and
        Keycloak volumes take their passwords on the first start only, stored
        secrets are encrypted with the secret key), so a new value would
        not reach that state but lock it out."""
        return self.cur(key) or self.current.get(key) or ""

    @property
    def base_url(self) -> str:
        """Where people open the hub."""
        if self.public_url:
            return self.public_url
        domain = self.env.get("HUB_DOMAIN") if "https" in self.profiles else ""
        return f"https://{domain}" if domain else f"http://localhost:{self.web_port}"

    @property
    def api_base(self) -> str:
        """Where this process reaches the running stack: the published port,
        through the host gateway from inside the setup container."""
        return D.api_base(f"http://localhost:{self.web_port}", self.in_docker)

    @property
    def image_tag(self) -> str:
        from common.version import app_version
        version = app_version()
        return self.cur("AGENTS_HUB_TAG") or (version if version[:1].isdigit() else "latest")


# ---------------------------------------------------------------------------
# Step 1: how to run it
# ---------------------------------------------------------------------------


def step_install(a: Asker, plan: Plan, shape: Optional[str], directory: Optional[str],
                 advanced: bool) -> bool:
    """Returns False when the person cancels at the existing-config question."""
    a.section(1, STEPS, "Install", "How this hub should run. Nothing is written before the review.")
    shapes = SHAPES
    if plan.in_docker:
        # From the setup container only what it can write and start through the
        # socket: the folder it was started in, as published images or, when
        # that folder is a checkout, built from it; or a values file.
        checkout = plan.workdir is not None and (plan.workdir / "Dockerfile").exists() \
            and (plan.workdir / "dashboard").is_dir()
        shapes = [s for s in SHAPES if s[0] in ("docker", "kubernetes") or (s[0] == "compose" and checkout)]
    names = [s[0] for s in shapes]
    if shape:
        if shape not in names:
            raise SetupError(f"--shape must be one of {', '.join(names)}")
        plan.shape = shape
        a.note(f"Shape: [bold]{dict((s[0], s[1]) for s in SHAPES)[shape]}[/bold]")
    else:
        saved = {} if plan.in_docker else _saved_remote()
        default = "remote" if saved else ("compose" if "compose" in names else "docker") if plan.in_docker \
            else "local"
        plan.shape = a.choose("shape", "How should Agents Hub run?", shapes, default=default)

    if plan.shape in ("docker", "compose") and not _has_docker():
        a.warn("No `docker` on PATH: the files will be written, and starting the stack is left to you.")
    if plan.shape == "remote":
        return True

    plan.quick = plan.shape != "kubernetes" and not advanced and a.choose("flow", "How many questions?", [
        ("quick", "QuickStart", "only what cannot be guessed; the assistant sets up the rest with you"),
        ("advanced", "Advanced", "every choice: ports, SSO, execution, web search, RAG, extra services"),
    ], default="quick") == "quick"

    if plan.shape == "kubernetes":
        # Nothing is written to .env: what the hub needs goes into the values
        # files, and an earlier run's files are the configuration in force.
        plan.quick = False
        default_dir = directory or str(plan.workdir or Path.cwd() / "agents-hub-k8s")
        plan.target = Path(directory or (str(plan.workdir) if plan.in_docker else a.text(
            "dir", "Folder for values.yaml and secrets.yaml", default=default_dir))).expanduser().resolve()
        plan.env_file = plan.target / ".env.k8s"
        plan.template = None
        if plan.in_docker:
            plan.k8s_chart = plan.target / D.CHART_DIR
        existing = D.read_kubernetes(plan.target)
        if existing is None:
            plan.fresh = True
            return True
        # Read back so a rerun keeps the secret key (every stored secret is
        # encrypted with it) and the API token, and offers the rest as defaults.
        plan.current = existing["env"]
        choice = _existing_choice(a, f"There are already values in {plan.target}.")
        if choice == "cancel":
            return False
        plan.fresh = choice == "fresh"
        if not plan.fresh:
            plan.k8s = existing["k8s"]
            plan.api_replicas, plan.worker_replicas = existing["api_replicas"], existing["worker_replicas"]
        return True
    if plan.shape == "docker":
        default_dir = directory or str(Path.home() / "agents-hub")
        if plan.in_docker:
            plan.target = (Path(directory) if directory else plan.workdir).resolve()
        else:
            plan.target = Path(directory or a.text("dir", "Folder for docker-compose.yml and .env",
                                                    default=default_dir)).expanduser().resolve()
        plan.env_file = plan.target / ".env"
        plan.template = QUICKSTART / "env.example"
    elif plan.in_docker:
        plan.target = plan.workdir
        plan.env_file = plan.target / ".env"
        plan.template = plan.target / ".env.example"
    else:
        plan.target = PROJECT_ROOT
        plan.env_file = PROJECT_ROOT / ".env"
        plan.template = PROJECT_ROOT / ".env.example"

    if plan.shape == "local" and not (_has_module("fastapi") and _has_module("langchain_core")):
        a.warn(f"The service's dependencies are not installed for {sys.executable}.")
        if a.confirm("install_deps", "Install them during setup (pip install -e \".[backend,agents]\")?", True):
            plan.pip_extras += ["backend", "agents"]
        else:
            a.note("Then run ./install.sh before `ah up`.")

    if plan.env_file.exists():
        plan.current = read_env(plan.env_file)
        choice = _existing_choice(a, f"There is already a configuration at {plan.env_file}.")
        if choice == "cancel":
            return False
        plan.fresh = choice == "fresh"
    return True


def _existing_choice(a: Asker, question: str) -> str:
    return a.choose("existing", question, [
        ("update", "Update it", "every question defaults to the value in force"),
        ("fresh", "Start over", "from the template; the old file is kept as a .bak copy"),
        ("cancel", "Cancel", "change nothing"),
    ], default="update")


# ---------------------------------------------------------------------------
# Step 3: the database (step 2, scale, is deploy.py)
# ---------------------------------------------------------------------------


def _check_pg(url: str) -> Optional[str]:
    """None when the URL connects, else why not (or None when there is no driver to try with)."""
    if not _has_module("psycopg"):
        return None
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=5) as conn:
            conn.execute("SELECT 1")
        return None
    except Exception as exc:  # noqa: BLE001 - reported to the person, not raised
        return str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__


def _pg_url_problem(value: str) -> Optional[str]:
    if not value.startswith(("postgresql://", "postgres://")):
        return "It has to start with postgresql://"
    return None


def _sqlite_has_state() -> bool:
    db_file = PROJECT_ROOT / ".agents_hub" / "agents_hub.db"
    return db_file.exists() and db_file.stat().st_size > 0


def step_database(a: Asker, plan: Plan) -> None:
    a.section(3, STEPS, "Database", "Where accounts, runs, chats and the catalog are kept.")
    current_url = plan.cur("AGENTS_HUB_DATABASE_URL")
    in_docker = plan.shape in ("docker", "compose", "kubernetes")
    if plan.shape == "kubernetes":
        options = [("postgres-url", "Your Postgres", "managed or in the cluster; the chart runs none")]
        default = "postgres-url"
    elif in_docker:
        options = [
            ("sqlite", "SQLite on the data volume", "one host, nothing else to run"),
            ("postgres-bundled", "Postgres in the same stack", "a postgres container beside the backend"),
            ("postgres-url", "A Postgres you already have", "managed or self-hosted, by URL"),
        ]
        default = ("postgres-bundled" if "@postgres:" in current_url
                   else "postgres-url" if current_url else "sqlite")
        if plan.topology == "ha":
            # Replicas on one SQLite file would contend for its lock; the ha
            # profile needs a server.
            options = options[1:]
            default = "postgres-url" if default == "postgres-url" else "postgres-bundled"
    else:
        options = [("sqlite", "SQLite file in .agents_hub", "one machine, nothing else to run")]
        if _has_docker():
            options.append(("postgres-docker", "Postgres in a Docker container", "setup starts it for you"))
        options.append(("postgres-url", "A Postgres you already have", "managed or self-hosted, by URL"))
        default = "postgres-url" if current_url else "sqlite"
    plan.db_kind = (options[0][0] if len(options) == 1 and not a.answered("database")
                    else a.choose("database", "Which database?", options, default=default))

    if plan.db_kind == "sqlite":
        plan.db_url = ""
    elif plan.db_kind == "postgres-bundled":
        # Whatever the hub used before: the keycloak profile starts the same
        # Postgres, and the server keeps the password its volume was made with.
        plan.pg_password = plan.held("POSTGRES_PASSWORD") or _gen_password()
        plan.db_url = f"postgresql://agents_hub:{plan.pg_password}@postgres:5432/agents_hub"
        plan.env["POSTGRES_PASSWORD"] = plan.pg_password
        plan.profiles.append("postgres")
    elif plan.db_kind == "postgres-docker":
        if _container_exists(PG_CONTAINER):
            a.note(f"A container named {PG_CONTAINER} already exists and will be reused.")
            plan.db_url = a.text("database_url", "Its URL", default=current_url or
                                 "postgresql://agents_hub:PASSWORD@127.0.0.1:5432/agents_hub",
                                 required=True, validate=_pg_url_problem)
            plan.db_kind = "postgres-url"
        else:
            port = 5432
            if not plan.quick or not _port_free(port):
                if not _port_free(port):
                    a.warn("Port 5432 is taken on this machine.")
                port = int(a.text("pg_port", "Host port for Postgres", default="5433" if not _port_free(5432) else "5432",
                                  validate=lambda v: None if v.isdigit() else "A port number"))
            plan.pg_port = port
            plan.pg_password = _gen_password()
            plan.db_url = f"postgresql://agents_hub:{plan.pg_password}@127.0.0.1:{port}/agents_hub"
    else:
        while True:
            plan.db_url = a.text("database_url", "Postgres URL", default=current_url,
                                 required=True, validate=_pg_url_problem)
            if in_docker:
                a.note("[dim]Not tested from here: the URL is the one the hub will use.[/dim]")
                break
            problem = _check_pg(plan.db_url)
            if problem is None:
                if _has_module("psycopg"):
                    a.ok("Connected.")
                break
            a.warn(f"Could not connect: {problem}")
            if not a.interactive or a.confirm("pg_keep", "Keep it anyway?", False):
                break

    plan.env["AGENTS_HUB_DATABASE_URL"] = plan.db_url
    if plan.shape == "local" and plan.db_url:
        if not _has_module("psycopg") and "postgres" not in plan.pip_extras:
            plan.pip_extras.append("postgres")
        if _sqlite_has_state() and current_url != plan.db_url:
            plan.migrate_sqlite = a.confirm(
                "migrate_sqlite", "Copy the existing SQLite state into it (ah db migrate)?", True)


def _container_exists(name: str) -> bool:
    if not _has_docker():
        return False
    try:
        out = subprocess.run(["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
                             capture_output=True, text=True, timeout=10)
        return name in out.stdout.split()
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------------------
# Step 4: who may use it
# ---------------------------------------------------------------------------


def _username_problem(value: str) -> Optional[str]:
    if any(c.isspace() for c in value) or len(value) > 128:
        return "One word, at most 128 characters"
    return None


def _ask_account(a: Asker, plan: Plan, key: str, role: str, default_name: str) -> dict:
    username = a.text(f"{key}.username", "Username", default=default_name, required=True,
                      validate=_username_problem).lower()
    display = a.text(f"{key}.display_name", "Display name", default=username.capitalize())
    while True:
        password = a.secret(f"{key}.password", "Password (Enter generates one)", confirm=True)
        if not password or len(password) >= 8:
            break
        if not a.interactive:
            raise SetupError(f"'{key}.password' is shorter than 8 characters")
        a.warn("At least 8 characters, please.")
    if not password:
        password = _gen_password()
        plan.generated.append((f"password for {username}", password))
    return {"username": username, "display_name": display, "password": password, "role": role}


def _existing_users(plan: Plan) -> Optional[int]:
    """How many accounts the hub already has, when that can be known cheaply:
    a local hub whose database this run keeps, or a Docker hub already up."""
    if plan.shape == "local":
        if plan.fresh or plan.db_url != (plan.current.get("AGENTS_HUB_DATABASE_URL") or ""):
            return None
        if not plan.db_url and not _sqlite_has_state():
            return 0
        try:
            from cli.backend import _ensure_importable
            _ensure_importable()
            os.environ["AGENTS_HUB_DATABASE_URL"] = plan.db_url
            from common import identity
            return identity.user_count()
        except Exception:  # noqa: BLE001 - unknown is a fine answer here
            return None
    if plan.shape == "kubernetes":
        return None
    import requests
    try:
        r = requests.get(f"{plan.api_base}/api/auth/mode", timeout=2)
        if r.ok and r.json().get("mode") == "multi":
            return 0 if r.json().get("bootstrap_required") else 1
    except (requests.RequestException, ValueError, AttributeError):
        pass  # the stack is simply not up yet, or answers something else
    return None


def step_access(a: Asker, plan: Plan) -> None:
    a.section(4, STEPS, "Access", "Who may use this hub (docs/identity.md).")
    current = (plan.cur("AUTH_MODE") or "single").lower()
    if current == "single" and plan.cur("AGENTS_HUB_API_TOKEN"):
        current = "token"
    plan.auth = a.choose("auth", "Who signs in?", [
        ("single", "Just me, no sign-in", "one operator, the API is open; right for localhost"),
        ("token", "One shared token", "every request carries it; the browser asks for it once"),
        ("multi", "Accounts", "named users with passwords and roles, single sign-on optional"),
    ], default=current if current in ("single", "token", "multi") else "single")
    plan.env["AUTH_MODE"] = plan.auth

    if plan.auth == "single":
        plan.env["AGENTS_HUB_API_TOKEN"] = ""
        if plan.shape in ("docker", "compose", "kubernetes"):
            a.warn("Without sign-in, anyone who can reach the dashboard's port has full access to it.")
    elif plan.auth == "token":
        token = plan.cur("AGENTS_HUB_API_TOKEN")
        if not plan.quick:
            token = a.secret("api_token", "Token (Enter generates one)", current=token)
        if not token:
            token = secrets.token_urlsafe(32)
            plan.generated.append(("API token (also in .env as AGENTS_HUB_API_TOKEN)", token))
        plan.env["AGENTS_HUB_API_TOKEN"] = token
    else:
        plan.env["AGENTS_HUB_API_TOKEN"] = ""
        existing = _existing_users(plan)
        if plan.shape == "kubernetes":
            a.note("The dashboard asks for the first administrator when it is first opened.")
        elif existing:
            a.note("This hub already has accounts, so there is no first administrator to create. "
                   "Add people on the Accounts page or with `ah user create`.")
        else:
            a.note("[bold]The first administrator[/bold]")
            plan.admin = _ask_account(a, plan, "admin", "admin", "admin")
            if not a.interactive:
                # An answers file lists the further accounts under "users".
                for i, _ in enumerate(getattr(a, "answers", {}).get("users") or []):
                    role = a.choose(f"users.{i}.role", "Role", [("member", "member", ""), ("admin", "admin", "")],
                                    default="member")
                    plan.users.append(_ask_account(a, plan, f"users.{i}", role, ""))
            else:
                while a.confirm("more_users", "Add another account now?", False):
                    role = a.choose("role", "Role", [
                        ("member", "Member", "works in the workspaces they are added to"),
                        ("admin", "Administrator", "everything, including accounts and settings"),
                    ], default="member")
                    plan.users.append(_ask_account(a, plan, "user", role, ""))
        # Keycloak in force is asked about in QuickStart too: skipped, its
        # profile would drop out of COMPOSE_PROFILES and sign-in go with it.
        if (not plan.quick or plan.topology == "ha" or a.answered("sso")
                or "keycloak" in _profiles_in_force(plan)):
            D.step_sso(a, plan)

    # Encrypts workspace secrets and stored tokens at rest (docs/secrets.md).
    # Generated once and never rotated by a re-run, Start over included:
    # changing it would make every stored secret unreadable.
    if not plan.cur("AGENTS_HUB_SECRET_KEY"):
        plan.env["AGENTS_HUB_SECRET_KEY"] = plan.held("AGENTS_HUB_SECRET_KEY") or _gen_fernet_key()


# ---------------------------------------------------------------------------
# Step 5: providers and models
# ---------------------------------------------------------------------------


def _configured(plan: Plan, provider: str) -> bool:
    if provider in P.KEY_VAR:
        return bool(plan.cur(P.KEY_VAR[provider]))
    return bool(plan.cur(P.MODEL_VAR[provider]))


def _pick_from_list(a: Asker, models: list[str]) -> str:
    cells = [f"[bold]{i:>3}[/bold] {m}" for i, m in enumerate(models, 1)]
    a.console.print(Columns(cells, padding=(0, 2), equal=True))
    while True:
        raw = a.text("model_pick", "Number or model id", required=True)
        if raw.isdigit() and 1 <= int(raw) <= len(models):
            return models[int(raw) - 1]
        if raw in models:
            return raw
        if a.confirm("model_unlisted", f"{raw} is not in the list. Use it anyway?", False):
            return raw


def _choose_model(a: Asker, plan: Plan, provider: str, models: list[str]) -> str:
    current = plan.cur(P.MODEL_VAR[provider])
    found = P.presets(provider, models)
    suggestion = (found.get("balanced") or next(iter(found.values()), ("", False)))[0]
    if not a.interactive:
        return a.text(f"providers.{provider}.model", "Default model", default=current or suggestion, required=True)

    options: list = []
    seen: set = set()
    if current and (not models or current in models):
        options.append((current, current, "current"))
        seen.add(current)
    for tier in ("balanced", "strong", "fast"):
        if tier in found and found[tier][0] not in seen:
            mid, verified = found[tier]
            options.append((mid, mid, P.TIER_LABELS[tier] + ("" if verified else ", not verified")))
            seen.add(mid)
    if models:
        options.append(("__list__", f"Pick from the {len(models)} it lists", ""))
    options.append(("__type__", "Type a model id", ""))
    choice = a.choose(f"providers.{provider}.model", "Default model for this provider",
                      options, default=options[0][0])
    if choice == "__list__":
        return _pick_from_list(a, models)
    if choice == "__type__":
        return a.text("model_id", "Model id", default=current or suggestion, required=True)
    return choice


def _configure_provider(a: Asker, plan: Plan, provider: str) -> Optional[dict]:
    a.console.print(f"\n  [bold cyan]{P.LABELS[provider]}[/bold cyan]")
    key = base = ""
    while True:
        if provider in P.KEY_VAR:
            key = a.secret(f"providers.{provider}.api_key", "API key", current=plan.cur(P.KEY_VAR[provider]))
            if not key:
                a.warn("No key, so this provider is skipped. Add one later in Settings.")
                return None
        if provider in P.URL_VAR and (provider in P.LOCAL or not plan.quick):
            default = plan.cur(P.URL_VAR[provider]) or P.DEFAULT_URL.get(provider, "")
            hint = "Base URL" if provider in P.LOCAL else "Base URL, for an OpenAI-compatible gateway (empty: api.openai.com)"
            base = a.text(f"providers.{provider}.base_url", hint, default=default)
        elif provider in P.URL_VAR:
            base = plan.cur(P.URL_VAR[provider])

        with a.console.status(f"  Asking {P.LABELS[provider]} for its models…"):
            result = P.probe(provider, key, base)
        if result.ok:
            a.ok(f"Connected in {result.latency_ms} ms, {len(result.models)} model(s) available.")
            if not result.models and provider in P.LOCAL:
                a.warn("It serves no models yet: pull or load one, then rerun `ah setup` or use Discover "
                       "on the Models page.")
            break
        a.warn(f"Could not check it: {result.error}.")
        if provider in P.LOCAL and plan.shape in ("docker", "compose"):
            a.note("[dim]A container reaches it as host.docker.internal; the server must listen beyond "
                   "loopback (Ollama: OLLAMA_HOST=0.0.0.0).[/dim]")
        if not a.interactive:
            break
        again = a.choose("probe_failed", "What now?", [
            ("retry", "Enter it again", ""),
            ("keep", "Keep it unchecked", "models are offered from the presets"),
            ("skip", "Skip this provider", ""),
        ], default="retry")
        if again == "skip":
            return None
        if again == "keep":
            break
        plan.env.pop(P.KEY_VAR.get(provider, ""), None)

    model = _choose_model(a, plan, provider, result.models if result.ok else [])
    if provider in P.KEY_VAR:
        plan.env[P.KEY_VAR[provider]] = key
    if provider in P.URL_VAR and (base or provider == "openai"):
        plan.env[P.URL_VAR[provider]] = base
    plan.env[P.MODEL_VAR[provider]] = model
    return {"model": model, "models": result.models if result.ok else [],
            "presets": P.presets(provider, result.models) if result.ok else {}}


def step_providers(a: Asker, plan: Plan) -> None:
    a.section(5, STEPS, "Providers and models",
              "The default model of each provider is the fallback every workspace inherits; "
              "the Models page curates the rest.")
    options = [(p, P.LABELS[p], "API key" if p in P.CLOUD else "a server on this machine") for p in P.PROVIDERS]
    default = [p for p in P.PROVIDERS if _configured(plan, p)] or ["openai"]
    chosen = a.multi("providers", "Which providers will you use?", options, default=default)
    if not chosen:
        a.warn("No provider: the hub starts, but no agent can answer until one is added in Settings.")
        return
    for provider in chosen:
        info = _configure_provider(a, plan, provider)
        if info:
            plan.providers[provider] = info
    if not plan.providers:
        return
    names = list(plan.providers)
    current = plan.cur("DEFAULT_PROVIDER")
    if len(names) == 1:
        plan.env["DEFAULT_PROVIDER"] = names[0]
    else:
        plan.env["DEFAULT_PROVIDER"] = a.choose(
            "default_provider", "Which one is the default?",
            [(p, P.LABELS[p], plan.providers[p]["model"]) for p in names],
            default=current if current in names else names[0])

    if not plan.quick:
        plan.env["LLM_TEMPERATURE"] = a.text("temperature", "Temperature",
                                             default=plan.cur("LLM_TEMPERATURE", "0.0"))
        plan.env["LLM_MAX_TOKENS"] = a.text("max_tokens", "Max tokens per reply",
                                            default=plan.cur("LLM_MAX_TOKENS", "15000"))
        plan.env["AGENT_STREAMING"] = "True" if a.confirm(
            "streaming", "Stream tokens to the UI as they arrive?",
            plan.cur("AGENT_STREAMING", "False").lower() == "true") else "False"


# ---------------------------------------------------------------------------
# Step 6: the assistant's voice
# ---------------------------------------------------------------------------


def _local_speech_default() -> str:
    lang = (os.environ.get("LC_ALL") or os.environ.get("LANG") or "").lower()
    return "piper-ru" if lang.startswith("ru") else "piper-de" if lang.startswith("de") else "piper-en"


def step_voice(a: Asker, plan: Plan) -> None:
    """The assistant listens and speaks with the ``default`` workspace's
    transcription and speech models; every personal workspace falls back to
    them. Saved once the hub's database (or, in Docker, its API) is there."""
    a.section(6, STEPS, "Assistant voice",
              "What the assistant hears you with and reads its answers aloud with "
              "(the Special models tab of the Models page, default workspace).")
    if plan.shape == "kubernetes":
        a.note("Set once the hub runs: the assistant offers it, or the Models page, Special models tab.")
        return
    if plan.quick and not a.answered("voice.mode"):
        a.note("Left to the assistant: it offers a voice on its first run in the dashboard, and the "
               "browser's own voice works until then.")
        return
    cloud = [p for p in VOICE_CLOUD if p in plan.providers or _configured(plan, p)]
    options = []
    if cloud:
        options.append(("cloud", "A cloud provider", ", ".join(P.LABELS[p] for p in cloud) + "; paid per use"))
    options += [
        ("local", "On this hub, its own model runtime",
         "Whisper and Piper, Kokoro, Kitten or Supertonic; free, downloads 0.5 to 2 GB"),
        ("browser", "The browser only", "Chrome or Safari listen and speak themselves; nothing on the server"),
        ("skip", "Not now", "add the models later on the Models page"),
    ]
    mode = a.choose("voice.mode", "How should the assistant hear and speak?", options,
                    default="cloud" if cloud else "browser")
    plan.voice = {"mode": mode}
    if mode == "cloud":
        provider = cloud[0] if len(cloud) == 1 else a.choose(
            "voice.provider", "Which provider?", [(p, P.LABELS[p], VOICE_CLOUD[p]["speech"]) for p in cloud],
            default=cloud[0])
        spec = VOICE_CLOUD[provider]
        voice = a.choose("voice.voice", "Its voice (the assistant page and the Models page can change it)",
                         [(v, v, "") for v in spec["voices"]], default=spec["voices"][0])
        plan.voice.update({
            "speech": {"provider": provider, "model": spec["speech"], "price_usd": spec["speech_price"],
                       "options": {"voice": voice}},
            "transcription": {"provider": provider, "model": spec["transcription"],
                              "price_usd": spec["transcription_price"], "options": {}},
        })
    elif mode == "local":
        speech_id = a.choose("voice.speech", "The voice that reads answers aloud",
                             [(i, label, size) for i, label, size, *_ in VOICE_LOCAL_SPEECH],
                             default=_local_speech_default())
        hear_id = a.choose("voice.transcription", "The model that hears you",
                           [(i, label, size) for i, label, size, *_ in VOICE_LOCAL_TRANSCRIPTION],
                           default="whisper-small")
        _, speech_label, _, s_repo, s_package, s_engine = next(x for x in VOICE_LOCAL_SPEECH if x[0] == speech_id)
        _, hear_label, _, h_repo, h_package = next(x for x in VOICE_LOCAL_TRANSCRIPTION if x[0] == hear_id)
        plan.voice.update({
            "speech": {"provider": HUB_LOCAL, "model": s_package, "options": {}},
            "transcription": {"provider": HUB_LOCAL, "model": h_package, "options": {}},
            "engines": ["whisper", s_engine],
            "packages": [(h_repo, h_package, hear_label), (s_repo, s_package, speech_label)],
        })
    elif mode == "browser":
        a.note("The assistant page uses the browser's own recognition and voice; Firefox has neither.")


def _voice_actions(plan: Plan) -> list[str]:
    v = plan.voice
    if not v.get("speech"):
        return []
    out = []
    if v.get("engines"):
        out.append("install the " + " and ".join(v["engines"]) + " engines into the hub's model runtime, "
                   "then download " + ", ".join(label for _, _, label in v["packages"]))
    where = "in the database" if plan.shape == "local" else "once the stack answers"
    out.append(f"set the default workspace's speech model to {v['speech']['provider']}/{v['speech']['model']} "
               f"and transcription to {v['transcription']['provider']}/{v['transcription']['model']} {where}")
    return out


def _voice_entries(plan: Plan) -> dict:
    return {k: {kk: vv for kk, vv in plan.voice[k].items() if vv is not None}
            for k in ("speech", "transcription")}


def _voice_done(a: Asker, plan: Plan) -> None:
    a.ok("Assistant voice: " + ", ".join(f"{k} {e['provider']}/{e['model']}"
                                         for k, e in _voice_entries(plan).items()) + ".")


def _wait_job(a: Asker, label: str, fetch, job_id: Optional[str], minutes: int = 45) -> bool:
    """Follow a job of the model runtime until it ends; ``fetch(job_id)``
    returns its record. Returns whether it finished."""
    if not job_id:
        return True
    deadline = time.time() + minutes * 60
    with a.console.status(f"  {label}…") as status:
        while time.time() < deadline:
            try:
                job = fetch(job_id)
            except Exception as exc:  # noqa: BLE001 - the runtime may be restarting; keep asking
                job = {"status": "running", "message": str(exc)[:80]}
            state = job.get("status")
            if state == "done":
                a.ok(f"{label}: done.")
                return True
            if state == "error":
                a.warn(f"{label} failed: {job.get('error') or job.get('message') or 'see the Models page'}")
                return False
            pct = job.get("percent") or 0
            status.update(f"  {label}… {pct:.0f}%" if pct else f"  {label}… {job.get('message') or ''}".rstrip())
            time.sleep(2)
    a.warn(f"{label} is still running; the Models page (Local tab) shows how it goes.")
    return False


def _runtime_work(a: Asker, plan: Plan, installed: dict, install, download, fetch) -> bool:
    """Install the engines the runtime lacks and download the models it does
    not have; the callables reach it directly (this checkout) or through the
    hub's API. Returns whether everything finished."""
    done = True
    for engine in dict.fromkeys(plan.voice.get("engines") or []):
        if installed.get("engines", {}).get(engine):
            a.ok(f"The {engine} engine is already installed.")
            continue
        done = _wait_job(a, f"Installing the {engine} engine", fetch, install(engine).get("job_id")) and done
    have = set(installed.get("models") or ())
    for repo, package, label in plan.voice.get("packages") or []:
        if package in have:
            a.ok(f"{label} is already downloaded.")
            continue
        done = _wait_job(a, f"Downloading {label}", fetch, download(repo, package).get("job_id")) and done
    return done


def _what_runtime_has(listing: dict) -> dict:
    return {"engines": listing.get("engines") or {},
            "models": [m.get("name") for m in listing.get("models") or [] if isinstance(m, dict)]}


def _apply_voice_local(a: Asker, plan: Plan) -> None:
    v = plan.voice
    if not v.get("speech"):
        return
    try:
        if v.get("packages"):
            from providers import local_models as lm
            from providers import model_runtime_host as host
            if host.active():
                with a.console.status("  Starting the model runtime (its first start sets up its own Python)…"):
                    host.ensure(wait=True, explicit=True)
            if not lm.runtime_configured():
                a.warn("No model runtime here (AGENTS_HUB_MODELS_MANAGED is off and AGENTS_HUB_MODELS_URL "
                       "is empty); the assistant's voice was not set.")
                return
            client = lm.RuntimeClient(timeout=60)
            if not _runtime_work(a, plan, _what_runtime_has(client.listing()), client.install_engine,
                                 lambda repo, package: client.download(repo, package=package), client.job):
                a.note("The models are set anyway; they answer once the runtime has them.")
            lm.ensure_hub_local_backend()
        from providers import special
        from workspace import create_workspace_folder, get_workspace_folder
        if not get_workspace_folder("default"):
            create_workspace_folder("default")
        special.save("default", {**special.stored("default"), **_voice_entries(plan)})
        _voice_done(a, plan)
    except Exception as exc:  # noqa: BLE001 - the rest of the setup stands; say what to do
        a.warn(f"The assistant's voice was not set ({exc}). Add it on the Models page, Special models tab.")


def _apply_voice_http(a: Asker, plan: Plan, session: Optional[str]) -> None:
    import requests
    v = plan.voice
    if not v.get("speech"):
        return
    if plan.auth == "multi" and not session:
        a.note("The assistant's voice was not set: no administrator session (the hub already had accounts). "
               "Add it on the Models page, Special models tab.")
        return
    token = session or plan.env.get("AGENTS_HUB_API_TOKEN") or plan.cur("AGENTS_HUB_API_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token and plan.auth != "single" else {}

    def call(method: str, path: str, **kw):
        r = requests.request(method, f"{plan.api_base}{path}", headers=headers, timeout=60, **kw)
        if not r.ok:
            raise SetupError(f"{method} {path}: {r.status_code} {r.text[:200]}")
        return r.json() if r.content else {}

    try:
        if v.get("packages"):
            runtime = "/api/models/local/runtime"
            # The models service may still be starting after the backend answers.
            listing: dict = {}
            with a.console.status("  Waiting for the model runtime…"):
                for _ in range(60):
                    listing = call("GET", runtime)
                    if listing.get("ok"):
                        break
                    time.sleep(2)
            if not listing.get("ok"):
                raise SetupError(f"the model runtime did not answer: {listing.get('error') or 'not running'}")
            if not _runtime_work(
                    a, plan, _what_runtime_has(listing),
                    lambda e: call("POST", f"{runtime}/engines/{e}/install"),
                    lambda repo, package: call("POST", f"{runtime}/download", json={"repo": repo, "package": package}),
                    lambda job_id: call("GET", f"{runtime}/jobs/{job_id}")):
                a.note("The models are set anyway; they answer once the runtime has them.")
        own = call("GET", "/api/workspaces/default/special-models").get("own") or {}
        call("PUT", "/api/workspaces/default/special-models", json={**own, **_voice_entries(plan)})
        _voice_done(a, plan)
    except (SetupError, requests.RequestException) as exc:
        a.warn(f"The assistant's voice was not set ({exc}). Add it on the Models page, Special models tab.")


# ---------------------------------------------------------------------------
# Step 7: features
# ---------------------------------------------------------------------------


# Said before the execution question where agent containers cannot share the
# hub's SQLite database with a hub run from this checkout (Docker Desktop).
_DOCKER_DESKTOP_NOTE = (
    "Docker mode on this machine: the hub runs from this checkout, so its database sits in a "
    "folder Docker Desktop shares into its VM, and SQLite breaks across that boundary. Tasks, "
    "flows, teams, loops and instances therefore run as local processes even in docker mode; "
    "only the agents' shell commands go into containers.\n"
    "For full isolation run the hub itself in Docker with its state on a volume:\n"
    "  docker compose -f docker-compose.yml -f docker-compose.volume.yml up --build\n"
    "Then:\n{how}"
)


def _containers_share_db() -> bool:
    """Whether agent containers could share the database of a hub run from
    this checkout (managers.container_manager.db_shared_with_containers)."""
    try:
        from managers.container_manager import db_shared_with_containers
        return db_shared_with_containers()
    except Exception:  # noqa: BLE001 - unknown reads as the cautious answer
        return False


def step_features(a: Asker, plan: Plan) -> None:
    a.section(7, STEPS, "Features")
    if not plan.quick or a.answered("demo"):
        demo = a.confirm("demo", "Seed the demo workspace on the first start (four agents, chats, views)?",
                         plan.cur("DEMO_WORKSPACE") in ("1", "true", "True"))
        plan.env["DEMO_WORKSPACE"] = "1" if demo else "0"
    else:
        a.note("Web search, the demo workspace, channels and the rest: the assistant offers them in the "
               "dashboard, one at a time.")

    if plan.shape == "kubernetes":
        _features_kubernetes(a, plan)
        return

    if plan.shape in ("docker", "compose"):
        plan.web_port = plan.cur("WEB_PORT", "8080")
        if not plan.quick:
            plan.web_port = a.text("web_port", "Port for the dashboard", default=plan.web_port,
                                   validate=lambda v: None if v.isdigit() else "A port number")
        if plan.web_port != "8080" or plan.cur("WEB_PORT"):
            plan.env["WEB_PORT"] = plan.web_port
    in_force = _profiles_in_force(plan)
    if plan.shape == "docker" and (not plan.quick or plan.topology == "ha"):
        # Caddy in the quickstart stack, with a Let's Encrypt certificate.
        if a.confirm("https", "Serve it over HTTPS on a domain of yours (ports 80 and 443, DNS pointing here)?",
                     "https" in in_force):
            plan.env["HUB_DOMAIN"] = a.text("domain", "The domain", default=plan.cur("HUB_DOMAIN"), required=True)
            plan.profiles.append("https")
    elif plan.shape == "docker" and "https" in in_force:
        # QuickStart on one backend does not ask, so HTTPS in force stays:
        # COMPOSE_PROFILES is written whole, and without it Caddy would not start.
        plan.env["HUB_DOMAIN"] = plan.cur("HUB_DOMAIN")
        plan.profiles.append("https")

    if plan.quick:
        _extra_services(a, plan, ask=False)
        return

    if plan.shape in ("local", "compose"):
        # A hub run from this checkout may not share its database with agent
        # containers (Docker Desktop); under compose the hub is a container too.
        shared = plan.shape != "local" or _containers_share_db()
        if not shared:
            from common.state_sync import describe
            a.note(_DOCKER_DESKTOP_NOTE.format(how=describe()))
        plan.env["AGENT_EXECUTION_MODE"] = a.choose("execution", "Where do agents run?", [
            ("local", "As processes of the backend", "simplest"),
            ("docker", "Each in its own container",
             "needs a reachable Docker daemon" if shared
             else "on this machine only the agents' shell commands; see the note above"),
        ], default=plan.cur("AGENT_EXECUTION_MODE", "local"))

    # Off by default in the service; offered on where runs are containers,
    # since a run that is a host process can reach the network around it.
    current = plan.cur("AGENTS_HUB_EGRESS_PROXY")
    proxy_default = (current.lower() in ("1", "true", "yes", "on") if current
                     else plan.env.get("AGENT_EXECUTION_MODE", plan.cur("AGENT_EXECUTION_MODE")) == "docker")
    proxy = a.confirm("egress_proxy", "Send agents' network traffic through the hub's egress proxy? It "
                      "enforces environment network policies, and a secret bound to hosts reaches a run "
                      "only as a placeholder the proxy swaps for the real value", proxy_default)
    plan.env["AGENTS_HUB_EGRESS_PROXY"] = "1" if proxy else "0"

    search = a.choose("web_search", "Web search for the web_search tool", [
        ("auto", "Automatic", "an Anthropic or OpenAI key searches through that provider; fetch_url always works"),
        ("anthropic", "Anthropic", "its own search tool, on the model key"),
        ("openai", "OpenAI", "its own search tool, on the model key"),
        ("brave", "Brave Search", "API key"),
        ("tavily", "Tavily", "API key"),
        ("exa", "Exa", "API key"),
    ], default=plan.cur("WEB_SEARCH_PROVIDER") or "auto")
    if search in ("anthropic", "openai"):
        plan.env.update({"WEB_SEARCH_PROVIDER": search})
    elif search != "auto":
        key = a.secret("web_search_key", "Its API key", current=plan.cur("WEB_SEARCH_API_KEY"))
        plan.env.update({"WEB_SEARCH_PROVIDER": search, "WEB_SEARCH_API_KEY": key})
    elif plan.cur("WEB_SEARCH_PROVIDER"):
        plan.env.update({"WEB_SEARCH_PROVIDER": "", "WEB_SEARCH_API_KEY": ""})

    rag = a.confirm("rag", "Retrieval over workspace files (RAG with a local vector store; the image "
                    "or install grows by about 2 GB)?", plan.cur("WITH_RAG", "false").lower() == "true"
                    or (plan.shape == "docker" and plan.cur("AGENTS_HUB_TAG").endswith("-rag")))
    if plan.shape == "local":
        if rag:
            plan.env["RAG_VECTOR_DB"] = "chroma"
            if not _has_module("chromadb"):
                plan.pip_extras.append("rag")
        else:
            plan.env["RAG_VECTOR_DB"] = "none"
    elif plan.shape == "compose":
        plan.env["WITH_RAG"] = "true" if rag else "false"
        plan.env["RAG_VECTOR_DB"] = "chroma" if rag else "none"
    else:
        tag = plan.cur("AGENTS_HUB_TAG", "latest").removesuffix("-rag") or "latest"
        plan.env["AGENTS_HUB_TAG"] = f"{tag}-rag" if rag else tag
        plan.env["RAG_VECTOR_DB"] = "chroma" if rag else "none"

    _extra_services(a, plan, ask=True)
    if plan.topology == "ha":
        limit = a.text("rate_limit", "Requests per minute one person or API key may make (0 = no limit)",
                       default=plan.cur("AGENTS_HUB_RATE_LIMIT_PER_MINUTE", "0"),
                       validate=lambda v: None if v.isdigit() else "A number")
        plan.env["AGENTS_HUB_RATE_LIMIT_PER_MINUTE"] = limit


def _profiles_in_force(plan: Plan) -> list[str]:
    return [p for p in (plan.cur("COMPOSE_PROFILES") or "").split(",") if p]


def _extra_services(a: Asker, plan: Plan, ask: bool) -> None:
    """The browser and Redis services beside the stack. QuickStart does not
    ask (``ask`` false) and keeps the ones in force: step_review writes
    COMPOSE_PROFILES whole, so a rerun that left them out stopped starting
    them, and the backend kept a browser URL nothing answered."""
    if plan.shape not in ("docker", "compose"):
        return
    services = [("browser", "Browser", "headless Chromium for the browser tools")]
    if plan.shape == "compose" and plan.topology != "ha":
        services.append(("scale", "Redis", "for more than one backend replica"))
    in_force = [p for p, _, _ in services if p in _profiles_in_force(plan)]
    extra = a.multi("services", "Extra services in the stack", services + [
        # No entry for the model runtime: it is part of every stack (the
        # `models` service, its token shared over its volume).
    ], default=in_force) if ask else in_force
    plan.profiles += extra
    if "browser" in extra:
        plan.env["AGENTS_HUB_BROWSER_URL"] = "http://browser:3000"
        plan.env["AGENTS_HUB_BROWSER_TOKEN"] = plan.cur("AGENTS_HUB_BROWSER_TOKEN") or secrets.token_urlsafe(24)
    if "scale" in extra:
        # The same redis service: it asks for REDIS_PASSWORD whenever .env sets one.
        password = plan.cur("REDIS_PASSWORD")
        bundled = f"redis://:{password}@redis:6379/0" if password else "redis://redis:6379/0"
        # Unasked, the URL in force stays as the person left it.
        plan.env["AGENTS_HUB_BROKER_URL"] = bundled if ask else (plan.cur("AGENTS_HUB_BROKER_URL") or bundled)


def _features_kubernetes(a: Asker, plan: Plan) -> None:
    """A cluster runs agents as worker subprocesses (no host Docker socket
    there), so no execution mode or egress proxy question; the rest as for
    Docker, written into the values instead of .env."""
    if a.confirm("rag", "Retrieval over workspace files (the -rag image, about 2 GB larger)?",
                 (plan.k8s.get("tag") or "").endswith("-rag")):
        plan.k8s["tag"] = (plan.k8s.get("tag") or plan.image_tag).removesuffix("-rag") + "-rag"
        plan.env["RAG_VECTOR_DB"] = "chroma"
    else:
        plan.k8s["tag"] = (plan.k8s.get("tag") or "").removesuffix("-rag")
        plan.env["RAG_VECTOR_DB"] = "none"
    limit = a.text("rate_limit", "Requests per minute one person or API key may make (0 = no limit)",
                   default=plan.cur("AGENTS_HUB_RATE_LIMIT_PER_MINUTE", "0"),
                   validate=lambda v: None if v.isdigit() else "A number")
    plan.env["AGENTS_HUB_RATE_LIMIT_PER_MINUTE"] = limit


# ---------------------------------------------------------------------------
# Step 9: review (step 8, the linked hub, is link.py)
# ---------------------------------------------------------------------------

_SECRET_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")


def _shown(key: str, value: str) -> str:
    if not value:
        return "[dim](empty)[/dim]"
    if key.endswith(_SECRET_SUFFIXES):
        return P.mask(value)
    if key.endswith("_URL"):
        # Any URL may carry a password (the broker's has no user name before
        # it): the review is printed before --yes or --dry-run is looked at.
        return re.sub(r"://([^:@/]*):[^@]+@", r"://\1:****@", value)
    return value


def actions(plan: Plan) -> list[str]:
    out: list[str] = []
    if plan.pip_extras:
        out.append(f"pip install -e \".[{','.join(dict.fromkeys(plan.pip_extras))}]\" into {sys.executable}")
    if plan.db_kind == "postgres-docker":
        out.append(f"start {PG_IMAGE} as container {PG_CONTAINER} on 127.0.0.1:{plan.pg_port}")
    if plan.migrate_sqlite:
        out.append("copy the SQLite state into Postgres (ah db migrate)")
    if plan.shape == "kubernetes":
        out.append(f"write {plan.target / 'values.yaml'} and {plan.target / 'secrets.yaml'} (private)")
        if plan.k8s_chart != D.CHART:
            out.append(f"copy the chart to {plan.k8s_chart}")
        out.append("then: " + " ".join(D.kubernetes_command(plan)))
        return out
    if plan.shape == "docker":
        out.append(f"write {plan.target / 'docker-compose.yml'}")
    out.append(f"write {plan.env_file}" + (" (the current one is backed up)" if plan.env_file.exists() else ""))
    if plan.topology == "ha":
        out.append(f"write {plan.target / D.OVERLAY}: {plan.api_replicas} API and {plan.worker_replicas} worker "
                   "replicas" + (", ports published for worker hosts" if plan.worker_hosts else ""))
    if D.keycloak_behind_caddy(plan):
        out.append(f"write {plan.target / 'caddy' / 'Caddyfile'}: Keycloak over HTTPS at "
                   f"{plan.keycloak['public_url']}")
    if plan.sso == "keycloak":
        out.append(f"write {plan.target / 'keycloak' / 'realm.json'} (realm {D.REALM}, client {D.CLIENT_ID})")
    for host in plan.worker_hosts:
        out.append(f"write {plan.target / 'workers' / host['name']}: {host['replicas']} workers")
    where, _copy = D.state_copy(plan)
    if where:
        out.append(f"leave the stack stopped: the hub's state has to be copied {where} first "
                   "(the commands come at the end)")
    if plan.admin:
        names = ", ".join([plan.admin["username"] + " (admin)"] +
                          [f"{u['username']} ({u['role']})" for u in plan.users])
        where = "in the database" if plan.shape == "local" else "once the stack answers"
        out.append(f"create accounts {where}: {names}")
    if plan.shape == "local" and plan.providers:
        out.append("put the chosen models into the Models catalog, each provider's default starred")
    if plan.sso == "keycloak" and plan.admin:
        out.append("map the Keycloak group hub-admins to the administrator role, once the stack answers")
    if plan.sso == "keycloak":
        out.append(f"update the client {D.CLIENT_ID} in Keycloak (redirect URI, web origins, secret) through its "
                   "admin API, once the stack answers")
    out += _voice_actions(plan)
    out += L.actions(plan)
    return out


def step_review(a: Asker, plan: Plan) -> None:
    a.section(9, STEPS, "Review")
    if plan.shape == "kubernetes":
        pass
    elif plan.profiles:
        plan.env["COMPOSE_PROFILES"] = ",".join(dict.fromkeys(plan.profiles))
    elif plan.shape in ("docker", "compose") and plan.cur("COMPOSE_PROFILES"):
        plan.env["COMPOSE_PROFILES"] = ""
    if plan.shape in ("docker", "compose"):
        if D.needs_overlay(plan):
            plan.env["COMPOSE_FILE"] = f"docker-compose.yml:{D.OVERLAY}"
        elif plan.cur("COMPOSE_FILE"):
            # Nothing to overlay any more: the file would still scale the stack.
            plan.env["COMPOSE_FILE"] = "docker-compose.yml"
    table = Table(box=box.SIMPLE, show_header=True, pad_edge=False)
    table.add_column("setting", style="bold")
    table.add_column("value")
    table.add_column("", style="dim")
    for key in sorted(plan.env):
        before = plan.current.get(key, "") if not plan.fresh else ""
        state = "" if before == plan.env[key] else ("new" if not before else "changed")
        table.add_row(key, _shown(key, plan.env[key]), state)
    a.console.print(table)
    a.console.print("[bold]Then:[/bold]")
    for line in actions(plan):
        a.console.print(f"  • {line}")


# ---------------------------------------------------------------------------
# Step 10: apply
# ---------------------------------------------------------------------------


def _run(a: Asker, cmd: list[str], cwd: Optional[Path] = None, env: Optional[dict] = None) -> None:
    a.console.print(f"  [dim]$ {' '.join(cmd)}[/dim]")
    code = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env).returncode
    if code != 0:
        raise SetupError(f"`{' '.join(cmd)}` failed with status {code}")


def _start_pg_container(a: Asker, plan: Plan) -> None:
    _run(a, ["docker", "run", "-d", "--name", PG_CONTAINER, "--restart", "unless-stopped",
             "-e", "POSTGRES_USER=agents_hub", "-e", f"POSTGRES_PASSWORD={plan.pg_password}",
             "-e", "POSTGRES_DB=agents_hub", "-p", f"127.0.0.1:{plan.pg_port}:5432",
             "-v", "agents_hub_pg:/var/lib/postgresql/data", PG_IMAGE])
    with a.console.status("  Waiting for Postgres…"):
        for _ in range(60):
            # Over TCP on purpose: during first-time init the image runs a
            # temporary server on the socket only, which would answer too early.
            r = subprocess.run(["docker", "exec", PG_CONTAINER, "pg_isready", "-h", "127.0.0.1",
                                "-U", "agents_hub", "-d", "agents_hub"], capture_output=True)
            if r.returncode == 0:
                a.ok("Postgres is up.")
                return
            time.sleep(1)
    raise SetupError(f"Postgres did not come up; see `docker logs {PG_CONTAINER}`")


def _create_accounts_local(a: Asker, plan: Plan) -> None:
    from common import identity
    accounts = ([plan.admin] if plan.admin else []) + plan.users
    for acc in accounts:
        try:
            identity.create_user(acc["username"], acc["password"], role=acc["role"],
                                 display_name=acc["display_name"])
            a.ok(f"Account {acc['username']} ({acc['role']}).")
        except ValueError as exc:
            a.warn(f"{acc['username']}: {exc}")


def _apply_catalog(a: Asker, plan: Plan) -> None:
    """Merge what the providers listed into the Models catalog: everything
    listed, disabled, as Discover would add it; the presets and the chosen
    default enabled; the default starred."""
    try:
        from dashboard.backend.routes import models as m
    except Exception as exc:  # noqa: BLE001 - .env already seeds the catalog on first start
        a.warn(f"The catalog was left to seed itself from .env on the first start ({exc}).")
        return
    catalog = m._load_catalog()
    for provider, info in plan.providers.items():
        entry = catalog.get(provider) or {"default": "", "models": []}
        have = {x["id"]: x for x in entry["models"]}
        wanted = {info["model"]} | {mid for mid, ok in info["presets"].values() if ok}
        for mid in list(info["models"]) + sorted(wanted):
            if mid in have:
                if mid in wanted:
                    have[mid]["enabled"] = True
                continue
            in_price, out_price, cached = m._default_prices3(provider, mid)
            have[mid] = {"id": mid, "enabled": mid in wanted, "input_price": in_price,
                         "output_price": out_price, "cached_input_price": cached,
                         "price_source": "auto", "context_window": m._fallback_ctx(provider, mid),
                         "released_at": 0}
        entry["models"] = m._sort_models(list(have.values()))
        entry["default"] = info["model"]
        catalog[provider] = entry
    m._save_catalog(catalog)
    a.ok("Models catalog: " + ", ".join(f"{p} → {i['model']}" for p, i in plan.providers.items()) + ".")


def _wait_healthy(a: Asker, base: str, seconds: int = 300) -> bool:
    import requests
    with a.console.status(f"  Waiting for {base} to answer…"):
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                if requests.get(f"{base}/api/health", timeout=3).status_code < 500:
                    return True
            except requests.RequestException:
                pass
            time.sleep(2)
    return False


def _bootstrap_http(a: Asker, plan: Plan) -> Optional[str]:
    """Create the accounts through the API; returns the admin's session token."""
    import requests
    mode = requests.get(f"{plan.api_base}/api/auth/mode", timeout=10).json()
    if not mode.get("bootstrap_required"):
        a.note("The hub already has accounts; none were created.")
        return None
    r = requests.post(f"{plan.api_base}/api/auth/bootstrap", timeout=20, json={
        "username": plan.admin["username"], "password": plan.admin["password"],
        "display_name": plan.admin["display_name"]})
    if not r.ok:
        a.warn(f"Could not create {plan.admin['username']}: {r.text[:200]}")
        return None
    token = r.json().get("token")
    a.ok(f"Account {plan.admin['username']} (admin).")
    for u in plan.users:
        r = requests.post(f"{plan.api_base}/api/auth/users", timeout=20,
                          headers={"Authorization": f"Bearer {token}"},
                          json={"username": u["username"], "password": u["password"], "role": u["role"],
                                "display_name": u["display_name"]})
        if r.ok:
            a.ok(f"Account {u['username']} ({u['role']}).")
        else:
            a.warn(f"{u['username']}: {r.text[:200]}")
    return token


def _map_admin_group(a: Asker, plan: Plan, token: Optional[str]) -> None:
    """People in the realm's hub-admins group sign in as administrators."""
    import requests
    if not token:
        return
    r = requests.post(f"{plan.api_base}/api/auth/group-mappings", timeout=20,
                      headers={"Authorization": f"Bearer {token}"},
                      json={"group_name": "hub-admins", "target": "role", "role": "admin"})
    if r.ok:
        a.ok("Keycloak group hub-admins signs in as administrators.")
    else:
        a.warn(f"Could not map the group hub-admins: {r.text[:200]}")


def apply_local(a: Asker, plan: Plan) -> None:
    if plan.pip_extras:
        extras = ",".join(dict.fromkeys(plan.pip_extras))
        _run(a, [sys.executable, "-m", "pip", "install", "-e", f".[{extras}]"], cwd=PROJECT_ROOT)
        importlib.invalidate_caches()
    if plan.db_kind == "postgres-docker":
        _start_pg_container(a, plan)
    if plan.migrate_sqlite:
        # The source is the current SQLite file: an empty variable pins it
        # whatever .env says (common/db.py, database_url).
        _run(a, [sys.executable, "-m", "cli", "db", "migrate", "--to", plan.db_url], cwd=PROJECT_ROOT,
             env={**os.environ, "AGENTS_HUB_DATABASE_URL": ""})

    backup = write_env(plan.env_file, plan.template, plan.env, plan.fresh)
    a.ok(f"Wrote {plan.env_file}" + (f" (previous: {backup.name})" if backup else "") + ".")

    os.environ["AGENTS_HUB_DATABASE_URL"] = plan.db_url
    from cli.backend import _ensure_importable
    _ensure_importable()
    try:
        from common import db
        db.get_conn()
        a.ok("Database schema is current.")
    except Exception as exc:  # noqa: BLE001 - the backend retries on start; say what happened
        a.warn(f"Could not open the database yet: {exc}")
        return
    if plan.admin or plan.users:
        _create_accounts_local(a, plan)
    if plan.providers:
        _apply_catalog(a, plan)
    _apply_voice_local(a, plan)
    L.apply_local(a, plan)
    if _saved_remote():
        _forget_remote()
        a.note("This `ah` no longer points at the remote hub saved earlier; it runs this checkout.")


def apply_docker(a: Asker, plan: Plan, start: bool) -> bool:
    """Write the files, and start the stack when asked. Returns whether it is up."""
    if plan.shape == "docker":
        plan.target.mkdir(parents=True, exist_ok=True)
        compose = plan.target / "docker-compose.yml"
        source = (QUICKSTART / "docker-compose.yml").read_text(encoding="utf-8")
        if compose.exists() and compose.read_text(encoding="utf-8") != source:
            shutil.copy2(compose, compose.with_name(f"docker-compose.yml.bak-{datetime.now():%Y%m%d-%H%M%S}"))
        compose.write_text(source, encoding="utf-8")
        a.ok(f"Wrote {compose}.")
    backup = write_env(plan.env_file, plan.template, plan.env, plan.fresh)
    a.ok(f"Wrote {plan.env_file}" + (f" (previous: {backup.name})" if backup else "") + ".")
    D.write_compose_extras(a, plan)
    if not start:
        return False
    cmd = ["docker", "compose", "up", "-d"] + (["--build"] if plan.shape == "compose" else [])
    # docker-compose.yml resolves HOST_PROJECT_ROOT from $PWD, which compose
    # takes from the environment, not from the directory it runs in; from the
    # setup container that is /app, and agent containers would mount paths
    # that do not exist on the host.
    _run(a, cmd, cwd=plan.target, env={**os.environ, "PWD": str(plan.target)})
    if not _wait_healthy(a, plan.api_base):
        logs = "backend-api" if plan.topology == "ha" else "backend"
        a.warn(f"{plan.base_url} did not answer in time; see `docker compose logs {logs}`.")
        return False
    a.ok(f"The hub answers at {plan.base_url}.")
    return True


# ---------------------------------------------------------------------------
# The remote shape: point this CLI at a hub elsewhere
# ---------------------------------------------------------------------------


def _saved_remote() -> dict:
    from cli.main import _read_state
    remote = _read_state().get("remote")
    return remote if isinstance(remote, dict) else {}


def _forget_remote() -> None:
    from cli.main import _write_state
    _write_state(remote=None)


def _save_remote(url: str, api_key: str = "", api_token: str = "") -> None:
    from cli.main import _write_state
    remote = {"url": url.rstrip("/")}
    if api_key:
        remote["api_key"] = api_key
    if api_token:
        remote["api_token"] = api_token
    _write_state(remote=remote)


def _key_for(a: Asker, base: str, session_token: Optional[str]) -> str:
    """A personal API key for this CLI, signing in first when needed."""
    import requests
    if not session_token:
        username = a.text("remote.username", "Username", required=True)
        password = a.secret("remote.password", "Password", allow_empty=False)
        r = requests.post(f"{base}/api/auth/login", json={"username": username, "password": password}, timeout=15)
        if not r.ok:
            raise SetupError(f"sign-in failed: {r.json().get('detail', r.text) if r.content else r.status_code}")
        session_token = r.json()["token"]
    r = requests.post(f"{base}/api/auth/keys", headers={"Authorization": f"Bearer {session_token}"},
                      json={"name": f"ah on {socket.gethostname()}"}, timeout=15)
    if not r.ok:
        raise SetupError(f"could not create an API key: {r.text[:200]}")
    return r.json()["key"]


def connect_remote(a: Asker, base: str, session_token: Optional[str] = None,
                   api_token: str = "") -> None:
    import requests
    mode = requests.get(f"{base}/api/auth/mode", timeout=10).json().get("mode", "single")
    if mode == "multi":
        _save_remote(base, api_key=_key_for(a, base, session_token))
    elif mode == "token":
        token = api_token or a.secret("remote.api_token", "The hub's API token", allow_empty=False)
        if requests.get(f"{base}/api/auth/me", headers={"Authorization": f"Bearer {token}"},
                        timeout=10).status_code == 401:
            raise SetupError("the hub rejected that token")
        _save_remote(base, api_token=token)
    else:
        _save_remote(base)
    a.ok(f"`ah` now talks to {base} (sign-in: {mode}). `ah setup --disconnect` undoes it.")


def run_remote(a: Asker) -> None:
    import requests
    a.section(0, 0, "Connect: where is it?")
    saved = _saved_remote()
    while True:
        base = a.text("remote.url", "The hub's URL (the dashboard's address)",
                      default=saved.get("url") or "http://localhost:8080", required=True).rstrip("/")
        try:
            r = requests.get(f"{base}/api/health", timeout=10)
            if r.status_code < 500:
                a.ok(f"{base} answers.")
                break
            a.warn(f"{base} answered {r.status_code}.")
        except requests.RequestException as exc:
            a.warn(f"Could not reach {base}: {exc.__class__.__name__}.")
        if not a.interactive or not a.confirm("remote.retry", "Try another address?", True):
            raise SetupError(f"{base} is not reachable")
    a.section(0, 0, "Connect: sign in")
    connect_remote(a, base)


# ---------------------------------------------------------------------------
# The whole run
# ---------------------------------------------------------------------------


def _farewell(a: Asker, plan: Plan, started: bool) -> None:
    if plan.generated:
        body = "\n".join(f"[bold]{label}:[/bold] {value}" for label, value in plan.generated)
        body += "".join(f"\n[yellow]{note}[/yellow]" for note in plan.generated_notes)
        a.panel(body + "\n[dim]Shown once. Store them now.[/dim]", title="Generated credentials", style="yellow")
    lines = []
    if plan.shape == "kubernetes":
        if not started:
            lines += [" ".join(D.kubernetes_command(plan))]
        lines += [f"open {D.kubernetes_values(plan)[0]['env']['AUTH_PUBLIC_URL']}",
                  f"kubectl -n {plan.k8s['namespace']} get pods",
                  "ah setup --shape kubernetes      change any of this later"]
        a.panel("\n".join(lines), title="Next", style="green")
        return
    lines += D.worker_host_notes(plan)
    where, move = D.state_copy(plan) if not started else ("", [])
    if move:
        lines += [f"Copy the hub's state {where} before starting (it reads it from there now):"]
        lines += [f"  {line}" for line in move]
    if plan.sso == "keycloak":
        lines += [f"Keycloak: {plan.keycloak['public_url']}/admin (user admin). Add people to the realm "
                  f"{D.REALM}; the group hub-admins makes administrators."]
    if plan.shape == "local":
        lines += ["ah up                     backend on :8000, dashboard on :5173"]
    elif not started:
        lines += [f"cd {plan.target}", "docker compose up -d" + (" --build" if plan.shape == "compose" else "")]
    lines += [f"open {'http://localhost:5173' if plan.shape == 'local' else plan.base_url}",
              "ah doctor                 check every part" if plan.shape == "local" else
              "docker compose logs -f " + ("backend-api" if plan.topology == "ha" else "backend"),
              "ah setup                  change any of this later"]
    # From here the assistant leads (common/setup_guide.py): the welcome window
    # hands over to it, or asks for a model key first when none was given.
    lines += ["", "In the dashboard, choose Talk or Type in the welcome window: the assistant takes you"]
    lines += (["through the rest of the setup (the default model, its voice, web search, channels, a first",
               "agent and task)."] if plan.providers else
              ["through the rest. It asks for a model key first, since none was given here."])
    a.panel("\n".join(lines), title="Next", style="green")


def _banner(a: Asker) -> None:
    a.panel("[bold]Agents Hub setup[/bold]\n"
            "How it runs, the database, who signs in, the model providers.\n"
            "[dim]Nothing is written until you confirm the review. Ctrl-C stops at any point.[/dim]",
            style="cyan")


def run(a: Asker, *, shape: Optional[str] = None, directory: Optional[str] = None, advanced: bool = False,
        dry_run: bool = False, yes: bool = False, no_start: bool = False, in_docker: bool = False,
        workdir: Optional[str] = None) -> int:
    _banner(a)
    plan = Plan(in_docker=in_docker, workdir=Path(workdir).resolve() if workdir else None)
    if in_docker and plan.workdir is None:
        raise SetupError("--in-docker needs --workdir, the folder the container was started in")
    began = time.time()
    try:
        return _run_steps(a, plan, shape, directory, advanced, dry_run, yes, no_start)
    finally:
        if plan.in_docker:
            D.hand_back(plan.target, began)


def _step_scale(a: Asker, plan: Plan) -> None:
    if plan.shape in ("docker", "compose", "kubernetes"):
        D.step_scale(a, plan, 2, STEPS)
        return
    a.section(2, STEPS, "Scale")
    a.note("One backend from this checkout. The team topology (replicas, workers, worker hosts) runs in "
           "Docker: choose Docker or Compose in step 1.")


def _run_steps(a: Asker, plan: Plan, shape: Optional[str], directory: Optional[str], advanced: bool,
               dry_run: bool, yes: bool, no_start: bool) -> int:
    if not step_install(a, plan, shape, directory, advanced):
        a.note("Cancelled; nothing was changed.")
        return 0
    if plan.shape == "remote":
        run_remote(a)
        return 0

    _step_scale(a, plan)
    step_database(a, plan)
    step_access(a, plan)
    step_providers(a, plan)
    step_voice(a, plan)
    step_features(a, plan)
    L.step_link(a, plan, 8, STEPS)
    step_review(a, plan)
    if dry_run:
        a.note("[bold]Dry run[/bold]: nothing was written.")
        return 0
    if not yes and not a.confirm("apply", "Apply this?", True):
        a.note("Cancelled; nothing was changed.")
        return 0

    a.section(10, STEPS, "Applying")
    started = False
    if plan.shape == "kubernetes":
        run_helm = (not no_start and not plan.in_docker and shutil.which("helm") is not None
                    and a.confirm("start", "Install it now (helm upgrade --install)?", True))
        started = D.apply_kubernetes(a, plan, run_helm)
        _farewell(a, plan, started)
        return 0
    if plan.shape == "local":
        apply_local(a, plan)
        _farewell(a, plan, started)
        if not no_start and a.interactive and a.confirm("start", "Start it now (ah up)?", True):
            a.console.print()
            os.execv(sys.executable, [sys.executable, "-m", "cli", "up"])
        return 0

    where, move = D.state_copy(plan)
    if move and not no_start:
        # Started now, the hub would open on a state without the one in use
        # (a new, empty one in the shared directory, or the old one in its own
        # volume), and copying it in afterwards would mix the two.
        a.warn(f"Not starting the stack: the hub's state has to be copied {where} first "
               "(the commands are below).")
    start = (not no_start and not move and _has_docker()
             and a.confirm("start", "Start it now (docker compose up)?", True))
    started = apply_docker(a, plan, start)
    session = None
    if started and plan.admin:
        session = _bootstrap_http(a, plan)
    if started and plan.sso == "keycloak":
        _map_admin_group(a, plan, session)
        D.sync_keycloak_client(a, plan)
    elif D.keycloak_realm_changed(plan):
        a.warn("Keycloak keeps the realm it imported first, so the client there still has the old "
               "redirect URI or secret.")
        for line in D.keycloak_manual_steps(plan):
            a.note(line)
    if started:
        _apply_voice_http(a, plan, session)
        L.apply_http(a, plan, session)
    # The CLI inside the setup container is thrown away with it.
    if started and not plan.in_docker and a.confirm("point_cli", "Point this `ah` at it, so commands go to the stack?", True):
        try:
            connect_remote(a, plan.base_url, session_token=session,
                           api_token=plan.env.get("AGENTS_HUB_API_TOKEN", ""))
        except SetupError as exc:
            a.warn(str(exc))
    if not started and plan.admin:
        # Accounts go in through the running API, so none exist yet: do not
        # hand out passwords for them.
        plan.generated = [g for g in plan.generated if not g[0].startswith("password for")]
        a.note("No accounts were created, since the stack is not running yet. The dashboard asks for "
               "the first administrator when it is first opened, or run `ah setup` again once it is up.")
    if not started and plan.voice.get("speech"):
        a.note("The assistant's voice is set once the stack runs: run `ah setup` again then, or add it on "
               "the Models page, Special models tab.")
    if not started and plan.link:
        a.note("The linked hub is set once the stack runs: run `ah setup` again then, or set it in "
               "Settings, Remote hub.")
    _farewell(a, plan, started)
    return 0
