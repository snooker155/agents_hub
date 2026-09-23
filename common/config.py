from __future__ import annotations

import os
from typing import Tuple, List, Optional, Union, Literal
from pathlib import Path
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from dataclasses import dataclass, field

from common.paths import PROJECT_ROOT

DEFAULT_IGNORE: List[str] = [
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".mypy_cache",
    ".DS_Store",
]

@dataclass
class SweAgentConfig:
    workspace_root: Optional[Path] = None
    max_read_bytes: int = 1_000_000
    ignore_globs: List[str] = field(default_factory=lambda: list(DEFAULT_IGNORE))
    allow_delete: bool = True
    binary_threshold: int = 4096

_swe_config = SweAgentConfig()

def get_swe_config() -> SweAgentConfig:
    return _swe_config

def update_swe_config(
    *,
    workspace_root: Optional[Union[str, Path]] = None,
    max_read_bytes: Optional[int] = None,
    ignore_globs: Optional[List[str]] = None,
    allow_delete: Optional[bool] = None,
    binary_threshold: Optional[int] = None,
) -> SweAgentConfig:
    global _swe_config

    ws = None
    if workspace_root is not None:
        ws = Path(workspace_root).resolve()
    else:
        ws = _swe_config.workspace_root

    max_r = max_read_bytes if max_read_bytes is not None else _swe_config.max_read_bytes
    allow_del = allow_delete if allow_delete is not None else _swe_config.allow_delete
    bin_thr = binary_threshold if binary_threshold is not None else _swe_config.binary_threshold

    if ignore_globs is None:
        ig = list(_swe_config.ignore_globs)
    else:
        seen = set()
        ig = []
        for item in (ignore_globs or []) + DEFAULT_IGNORE:
            if item not in seen:
                seen.add(item)
                ig.append(item)

    _swe_config = SweAgentConfig(
        workspace_root=ws,
        max_read_bytes=int(max_r),
        ignore_globs=ig,
        allow_delete=bool(allow_del),
        binary_threshold=int(bin_thr),
    )
    return _swe_config


class Settings(BaseSettings):
    """
    Unified environment-driven settings shared across packages.
    Merges logic from original tasks/config.py and common/config.py.
    """
    # Core LLM settings
    default_provider: str = Field(default="lmstudio")
    openai_api_key: Optional[str] = Field(default=None)
    model: str = Field(default="gpt-4o",
                       validation_alias=AliasChoices("OPENAI_MODEL", "model"))
    temperature: float = Field(default=0.0,
                               validation_alias=AliasChoices("LLM_TEMPERATURE", "temperature"))
    # NOTE: pydantic-settings v2 ignores the v1 ``env=`` kwarg and maps each field
    # to an env var by its *field name*. So an env alias that differs from the
    # field name (LLM_MAX_TOKENS vs max_tokens) must be declared via
    # ``validation_alias`` or it is silently ignored. AliasChoices keeps the
    # field-name fallback so code that sets ``max_tokens=`` still works.
    max_tokens: int = Field(default=10000,
                            validation_alias=AliasChoices("LLM_MAX_TOKENS", "max_tokens"))
    # Per-request timeout for LLM calls, in seconds. Without it a hung backend
    # (e.g. a wedged local server) blocks an agent run forever — the process
    # stays "running" and the task never progresses. Generous default: single
    # steps on busy local models can legitimately take several minutes.
    llm_request_timeout: int = Field(default=600,
                                     validation_alias=AliasChoices("LLM_REQUEST_TIMEOUT", "llm_request_timeout"))
    # Wall-clock budget for the blocking /api/chat/message endpoint, in seconds.
    # Must comfortably exceed a single LLM call (a tool-looping agent makes
    # several), so the effective budget is max(this, llm_request_timeout + 60).
    chat_request_timeout: int = Field(default=900,
                                      validation_alias=AliasChoices("CHAT_REQUEST_TIMEOUT", "chat_request_timeout"))
    # Other cloud providers
    anthropic_api_key: Optional[str] = Field(default=None)
    google_api_key: Optional[str] = Field(default=None)
    # Local models
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="")
    lmstudio_base_url: str = Field(default="http://localhost:1234")
    lmstudio_model: str = Field(default="")

    # Application settings
    mode_full: bool = True          # full (extensions/validations) or simple
    backend_lang_default: str = "python"  # fallback

    # Workspaces always live under .agents_hub/workspaces/ (see common.paths); the
    # location is fixed, not configurable, so there is no workspace_root setting.

    # Policies / safety
    allow_shell: Tuple[str, ...] = Field(
        default_factory=lambda: tuple(("python,pytest,ruff,black").split(",")),
    )

    # Orchestration logging level, applied by common.logging_config at process
    # start. (ORCH_POLL_INTERVAL used to live here too; nothing polls, so it went.)
    orch_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )

    # Tasks storage is fixed at .agents_hub/tasks.json (see common.paths) — not configurable.

    # Agent mode: "local" runs agents as local subprocesses,
    # "docker" wraps each agent in a docker run invocation.
    agent_mode: Literal["local", "docker"] = Field(
        default="local",
        validation_alias=AliasChoices("AGENT_EXECUTION_MODE", "agent_mode"),
        validate_default=False,
    )

    @field_validator("agent_mode", mode="before")
    @classmethod
    def _default_agent_mode(cls, v: object) -> object:
        if v == "" or v is None:
            return "local"
        return v
    # How a run's own entrypoint (runtime/agent_run.py) reaches its run and
    # task records: "db" (default) opens the shared SQLite database directly,
    # same as always. "http" instead relays those writes to the backend's
    # /api/run-state routes: the mode a run container uses when its
    # .agents_hub mount is read-only (see common/state_transport.py,
    # managers/container_manager.py's build_run_command, docs/containers.md).
    run_state_transport: Literal["db", "http"] = Field(
        default="db",
        validation_alias=AliasChoices("AGENT_RUN_STATE_TRANSPORT", "run_state_transport"),
        validate_default=False,
    )

    @field_validator("run_state_transport", mode="before")
    @classmethod
    def _default_run_state_transport(cls, v: object) -> object:
        if v == "" or v is None:
            return "db"
        return v
    # Docker image to use when agent_mode = "docker"
    agent_docker_image: str = Field(default="")
    # Optional Docker network (e.g. "host" or a named bridge network)
    agent_docker_network: str = Field(default="")
    # Extra flags passed verbatim to `docker run` (e.g. "--memory 2g --cpus 1")
    agent_docker_extra_args: str = Field(default="")

    # ── Security ──────────────────────────────────────────────────────────────
    # Optional bearer token. When set, every /api request must carry
    # ``Authorization: Bearer <token>`` (or ``X-Api-Token: <token>``). Empty
    # (default) keeps the API open, preserving the current local-only behaviour.
    api_token: str = Field(default="", validation_alias=AliasChoices("AGENTS_HUB_API_TOKEN", "api_token"))
    # Identity posture. Three explicit modes, documented in docs/identity.md:
    #   "single" (default) — exactly one operator on this machine or host. No
    #     login, no users, no roles, no owner checks; the API behaves as it
    #     always has and every ownable record is owned by the constant
    #     ``common.auth.LOCAL_OPERATOR_ID``.
    #   "token"  — the shared ``AGENTS_HUB_API_TOKEN`` below gates /api. Still
    #     one operator: no users, no roles.
    #   "multi"  — named users with passwords, sessions, a global role and
    #     per-workspace membership roles.
    # Backwards compatibility: leaving this unset (or "single") while a token
    # *is* configured resolves to "token", so a deployment that only ever set
    # AGENTS_HUB_API_TOKEN keeps working exactly as before. Resolve it through
    # ``common.auth.effective_auth_mode``, never off this field directly.
    auth_mode: str = Field(
        default="single",
        validation_alias=AliasChoices("AUTH_MODE", "auth_mode"),
    )
    # How long a login stays valid, in hours. Sessions are opaque random tokens
    # stored hashed (no JWT, nothing to sign), so there is no signing secret to
    # configure: revoking one is deleting its row.
    auth_session_hours: int = Field(
        default=24 * 14,
        validation_alias=AliasChoices("AUTH_SESSION_HOURS", "auth_session_hours"),
    )
    # ── Corporate identity (stage 3, docs/identity.md) ─────────────────────
    # OIDC single sign-on, Authorization Code + PKCE. Setting the issuer turns
    # it on; the client secret may be empty for a public client.
    auth_oidc_issuer: str = Field(
        default="", validation_alias=AliasChoices("AUTH_OIDC_ISSUER", "auth_oidc_issuer"))
    auth_oidc_client_id: str = Field(
        default="", validation_alias=AliasChoices("AUTH_OIDC_CLIENT_ID", "auth_oidc_client_id"))
    auth_oidc_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices("AUTH_OIDC_CLIENT_SECRET", "auth_oidc_client_secret"))
    auth_oidc_scopes: str = Field(
        default="openid profile email",
        validation_alias=AliasChoices("AUTH_OIDC_SCOPES", "auth_oidc_scopes"))
    # The id-token claim that carries group names (Keycloak: ``groups`` with
    # the mapper on; Entra ID: ``groups``; Google Workspace has none, so map
    # by ``email`` domain or provision with SCIM instead).
    auth_oidc_groups_claim: str = Field(
        default="groups",
        validation_alias=AliasChoices("AUTH_OIDC_GROUPS_CLAIM", "auth_oidc_groups_claim"))
    # A label for the sign-in button ("Sign in with Keycloak").
    auth_oidc_provider_name: str = Field(
        default="", validation_alias=AliasChoices("AUTH_OIDC_PROVIDER_NAME", "auth_oidc_provider_name"))
    # OIDC sessions are shorter than password ones: the provider's own
    # session makes a re-login silent, so there is no cost to asking again.
    auth_oidc_session_hours: int = Field(
        default=8, validation_alias=AliasChoices("AUTH_OIDC_SESSION_HOURS", "auth_oidc_session_hours"))
    # Link an OIDC login to an existing local account with the same email
    # (or username) instead of creating a second account for the same person.
    auth_oidc_link_by_email: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTH_OIDC_LINK_BY_EMAIL", "auth_oidc_link_by_email"))
    # Password login. Off, only administrators may still sign in with a
    # password (the emergency door when the provider is down).
    auth_local_passwords: bool = Field(
        default=True, validation_alias=AliasChoices("AUTH_LOCAL_PASSWORDS", "auth_local_passwords"))
    # Failed logins per account (or per address) inside the window before
    # further attempts are refused with 429.
    auth_login_max_attempts: int = Field(
        default=10, validation_alias=AliasChoices("AUTH_LOGIN_MAX_ATTEMPTS", "auth_login_max_attempts"))
    auth_login_window_minutes: int = Field(
        default=15, validation_alias=AliasChoices("AUTH_LOGIN_WINDOW_MINUTES", "auth_login_window_minutes"))
    # The URL the browser reaches this hub at, for OIDC redirect URIs behind a
    # reverse proxy. Empty: derived from the request (honouring
    # X-Forwarded-Proto and X-Forwarded-Host).
    auth_public_url: str = Field(
        default="", validation_alias=AliasChoices("AUTH_PUBLIC_URL", "auth_public_url"))
    # Mark the short-lived OIDC state cookie Secure. Auto: on when the public
    # URL (or the request) is https.
    auth_cookie_secure: str = Field(
        default="auto", validation_alias=AliasChoices("AUTH_COOKIE_SECURE", "auth_cookie_secure"))
    # SCIM 2.0 provisioning: the bearer token an identity provider presents on
    # /scim/v2. Empty turns the endpoints off.
    auth_scim_token: str = Field(
        default="", validation_alias=AliasChoices("AUTH_SCIM_TOKEN", "auth_scim_token"))
    # Audit log: record every write request from the middleware. "auto" is on
    # in token and multi mode, off in single (where the key points are still
    # recorded: login, role changes, launches, approvals, policy, budget).
    audit_requests: str = Field(
        default="auto", validation_alias=AliasChoices("AUDIT_REQUESTS", "audit_requests"))
    # Days an audit row is kept; 0 keeps everything.
    audit_retention_days: int = Field(
        default=365, validation_alias=AliasChoices("AUDIT_RETENTION_DAYS", "audit_retention_days"))
    # Secrets at rest (common/secrets.py): the encryption key, and the backend
    # ("local" encrypts into the database; "vault" reads from HashiCorp Vault's
    # KV v2 at AGENTS_HUB_VAULT_URL / AGENTS_HUB_VAULT_TOKEN through the same
    # interface).
    secret_key: str = Field(
        default="", validation_alias=AliasChoices("AGENTS_HUB_SECRET_KEY", "secret_key"))
    secret_backend: str = Field(
        default="local", validation_alias=AliasChoices("AGENTS_HUB_SECRET_BACKEND", "secret_backend"))
    # GitHub App (connectors/git/github_app.py, docs/github-app.md): the hub
    # issues installation tokens and user-to-server tokens itself. Configured
    # when the app id, the private key and the client id are set. The private
    # key is PEM text, or a path to the .pem file in GITHUB_APP_PRIVATE_KEY_FILE.
    github_app_id: str = Field(
        default="", validation_alias=AliasChoices("GITHUB_APP_ID", "github_app_id"))
    github_app_slug: str = Field(
        default="", validation_alias=AliasChoices("GITHUB_APP_SLUG", "github_app_slug"))
    github_app_client_id: str = Field(
        default="", validation_alias=AliasChoices("GITHUB_APP_CLIENT_ID", "github_app_client_id"))
    github_app_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices("GITHUB_APP_CLIENT_SECRET", "github_app_client_secret"))
    github_app_private_key: str = Field(
        default="",
        validation_alias=AliasChoices("GITHUB_APP_PRIVATE_KEY", "github_app_private_key"))
    github_app_private_key_file: str = Field(
        default="",
        validation_alias=AliasChoices("GITHUB_APP_PRIVATE_KEY_FILE", "github_app_private_key_file"))
    # GitHub Enterprise Server: https://<host>/api/v3 and https://<host>.
    github_api_url: str = Field(
        default="https://api.github.com",
        validation_alias=AliasChoices("GITHUB_API_URL", "github_api_url"))
    github_url: str = Field(
        default="https://github.com", validation_alias=AliasChoices("GITHUB_URL", "github_url"))
    # When true, ``run_shell`` only permits commands whose first word is in
    # ``allow_shell``. Off by default so existing agent shell usage is unchanged;
    # a workspace can opt in via its settings (shell_allowlist_enabled).
    shell_allowlist_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("SHELL_ALLOWLIST_ENABLED", "shell_allowlist_enabled"),
    )
    # Tool-capability guard (the "lethal trifecta" check). Classifies each tool
    # by what it grants — ingests_untrusted / reads_private / can_exfiltrate —
    # and refuses agent tool sets that compose into a data-exfiltration
    # primitive. See tools/capabilities.py.
    #   "block" (default) — a violating tool set cannot be saved or built
    #   "warn"            — logged, allowed
    #   "off"             — not checked
    capability_guard: str = Field(
        default="block",
        validation_alias=AliasChoices("CAPABILITY_GUARD", "capability_guard"),
    )
    # Hardened posture. When true, a per-agent ``capability_override`` is only
    # honoured at build time for agents that run container-isolated on a
    # no-network container. On by default now that the run sandbox exists:
    # an override with no real isolation behind it is not a deliberate,
    # bounded exception, it is the guard turned off. Set to false only for a
    # deployment that runs every agent in-process and still needs an override
    # to work everywhere.
    capability_override_requires_container: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "CAPABILITY_OVERRIDE_REQUIRES_CONTAINER", "capability_override_requires_container"
        ),
    )

    # Playground (simulation worlds/scenarios) is ~17% of the backend by line
    # count; this lets a deployment that does not use it skip loading it.
    playground_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("PLAYGROUND_ENABLED", "playground_enabled"),
    )

    # When true, the ``view_serve`` tool may *launch* a generated backend as a
    # workspace-scoped subprocess (killed with the view). Off by default — the
    # proxy-only registration path is always available; launching is the opt-in,
    # matching the shell-allowlist posture.
    views_serve_launch_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("VIEWS_SERVE_LAUNCH_ENABLED", "views_serve_launch_enabled"),
    )

    # ── Web access (tools/web.py) ─────────────────────────────────────────────
    # Search provider for the ``web_search`` tool. Empty (default) leaves the
    # tool inert — it reports that no provider is configured rather than
    # failing mid-run. Supported: "brave", "tavily", "exa".
    web_search_provider: str = Field(
        default="", validation_alias=AliasChoices("WEB_SEARCH_PROVIDER", "web_search_provider"))
    web_search_api_key: str = Field(
        default="", validation_alias=AliasChoices("WEB_SEARCH_API_KEY", "web_search_api_key"))
    # Results per search. Kept small: every result is untrusted text entering
    # the context window, and injection surface scales with it.
    web_search_max_results: int = Field(
        default=5, validation_alias=AliasChoices("WEB_SEARCH_MAX_RESULTS", "web_search_max_results"))
    # Hard cap on the text ``fetch_url`` returns, after HTML is stripped.
    web_fetch_max_chars: int = Field(
        default=20_000, validation_alias=AliasChoices("WEB_FETCH_MAX_CHARS", "web_fetch_max_chars"))
    web_fetch_timeout: float = Field(
        default=20.0, validation_alias=AliasChoices("WEB_FETCH_TIMEOUT", "web_fetch_timeout"))
    # Redirect hops followed by ``fetch_url``. Every hop is re-validated against
    # the SSRF and domain rules, so this bounds work, not trust.
    web_fetch_max_redirects: int = Field(
        default=5, validation_alias=AliasChoices("WEB_FETCH_MAX_REDIRECTS", "web_fetch_max_redirects"))
    # Opt-in domain policy, mirroring shell_allowlist_enabled. When on, only
    # hosts matching ``web_allow_domains`` may be fetched or returned by search.
    # A workspace can turn it on via its settings (web_domain_policy_enabled)
    # and supply its own web_allow_domains / web_deny_domains.
    web_domain_policy_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("WEB_DOMAIN_POLICY_ENABLED", "web_domain_policy_enabled"))
    web_allow_domains: Tuple[str, ...] = Field(
        default_factory=tuple,
        validation_alias=AliasChoices("WEB_ALLOW_DOMAINS", "web_allow_domains"))
    # Denied always, whether or not the allow-policy is on.
    web_deny_domains: Tuple[str, ...] = Field(
        default_factory=tuple,
        validation_alias=AliasChoices("WEB_DENY_DOMAINS", "web_deny_domains"))

    # ── Web access log (tools/web_log.py) ─────────────────────────────────────
    # Every search and fetch is recorded with the text the agent received, so a
    # bad answer can be traced to what the tool actually read, and retrieved
    # content can be reviewed for injection attempts. On by default: the whole
    # point of wrapping untrusted content is being able to inspect it later.
    web_log_enabled: bool = Field(
        default=True, validation_alias=AliasChoices("WEB_LOG_ENABLED", "web_log_enabled"))
    # Entries kept when the log is trimmed.
    web_log_max_entries: int = Field(
        default=2000, validation_alias=AliasChoices("WEB_LOG_MAX_ENTRIES", "web_log_max_entries"))
    # Per-entry cap on the stored response text. Large enough to review a page,
    # small enough that the log stays a log.
    web_log_body_chars: int = Field(
        default=20_000, validation_alias=AliasChoices("WEB_LOG_BODY_CHARS", "web_log_body_chars"))

    # ── Retention ─────────────────────────────────────────────────────────────
    # Daily maintenance deletes terminal run records (and their payloads/logs)
    # finished more than this many days ago. 0 disables run retention.
    run_retention_days: int = Field(default=30,
                                    validation_alias=AliasChoices("RUN_RETENTION_DAYS", "run_retention_days"))
    # How many runs a *connection* keeps, newest first. A day-based limit is the
    # wrong instrument for a production graph reporting hundreds of runs an
    # hour: thirty days of that is a quarter of a million rows before anything
    # is pruned. A count cap is what bounds it. A connection may override this;
    # 0 disables the cap for every connection that has not set its own.
    connection_retention_runs: int = Field(
        default=2000,
        validation_alias=AliasChoices("CONNECTION_RETENTION_RUNS", "connection_retention_runs"))

    # ── Awaiting-input escalation ─────────────────────────────────────────────
    # Tasks parked by ``ask_user`` (status=awaiting_input) otherwise wait forever.
    # A scheduled sweep reminds the user after this many hours (0 = no reminders)
    # and, if auto-answer is enabled, resumes the task with a default answer after
    # ``awaiting_input_auto_answer_hours`` (0 = never auto-answer). Auto-answer is
    # opt-in and conservatively skips questions that look destructive.
    awaiting_input_reminder_hours: int = Field(
        default=0, validation_alias=AliasChoices("AWAITING_INPUT_REMINDER_HOURS", "awaiting_input_reminder_hours"))
    awaiting_input_auto_answer: bool = Field(
        default=False, validation_alias=AliasChoices("AWAITING_INPUT_AUTO_ANSWER", "awaiting_input_auto_answer"))
    awaiting_input_auto_answer_hours: int = Field(
        default=0, validation_alias=AliasChoices("AWAITING_INPUT_AUTO_ANSWER_HOURS", "awaiting_input_auto_answer_hours"))
    awaiting_input_default_answer: str = Field(
        default="", validation_alias=AliasChoices("AWAITING_INPUT_DEFAULT_ANSWER", "awaiting_input_default_answer"))

    # ── Live token streaming ──────────────────────────────────────────────────
    # When on, every agent is built with a streaming LLM: tokens arrive one by
    # one instead of in a single blocking response. Two consequences, and the
    # second is the reason this exists — a stop request lands on the next token
    # (milliseconds) instead of waiting for the whole completion, and non-chat
    # surfaces (tasks, flows, nodes) can show the agent's output as it is
    # produced. Off by default: it multiplies callback traffic, so it is opt-in
    # from the Settings page. Read through ``streaming_enabled()``, never off
    # this field directly — the value must stay live for the running server.
    agent_streaming: bool = Field(
        default=False, validation_alias=AliasChoices("AGENT_STREAMING", "agent_streaming"))

    # ── Agent build cache ─────────────────────────────────────────────────────
    # Reuse a built agent across runs (Docker-layer style): the first build is
    # cached and reused while the agent's definition inputs are unchanged
    # (markdown files, registry spec incl. tools/memory binding, workspace
    # instructions, resolved model config). Any change recaches automatically.
    agent_cache_enabled: bool = Field(
        default=True, validation_alias=AliasChoices("AGENT_CACHE_ENABLED", "agent_cache_enabled"))
    # Safety bound on drift of the live-state-derived parts of the prompt (memory
    # hints, skills catalog): a cached agent older than this is rebuilt even if
    # its definition inputs are unchanged. 0 disables the time bound (pure
    # content-addressed: rebuild only when inputs change).
    agent_cache_ttl: int = Field(
        default=900, validation_alias=AliasChoices("AGENT_CACHE_TTL", "agent_cache_ttl"))

    # ── Cross-replica event bridge (common/broker_bridge.py) ──────────────────
    # Empty (default, off): the session broker stays in-process only, exactly as
    # today. Set to a Redis URL (e.g. redis://redis:6379/0) when more than one
    # backend replica is running behind a load balancer: every local publish is
    # then also fanned out over Redis pub/sub, so a browser tab connected to a
    # different replica than the one that produced an event still receives it.
    # See docs/scaling.md for when this is needed and how to run it.
    broker_url: str = Field(
        default="", validation_alias=AliasChoices("AGENTS_HUB_BROKER_URL", "broker_url"))

    # ── Database (common/db.py) ───────────────────────────────────────────────
    # Empty (default): SQLite at <AGENTS_HUB_ROOT>/agents_hub.db, one host. A
    # postgresql:// URL puts the same schema in Postgres, which is what lets
    # backend replicas and workers run on different hosts. Every process that
    # touches state (backend, agent subprocesses, node workers, the CLI in
    # direct mode) reads this, so set it in .env rather than per service.
    database_url: str = Field(
        default="", validation_alias=AliasChoices("AGENTS_HUB_DATABASE_URL", "database_url"))
    # Postgres only: connections in this process's pool. The backend serves
    # requests from a thread pool and needs ten or so; a run subprocess needs
    # one or two, so runtime entrypoints may set it lower in their environment.
    db_pool_size: int = Field(
        default=10, validation_alias=AliasChoices("AGENTS_HUB_DB_POOL_SIZE", "db_pool_size"))

    # ── Object store mirror (common/blobs.py) ─────────────────────────────────
    # Empty (default): run logs, node logs, flow logs, view assets and generated
    # Dockerfiles live only under AGENTS_HUB_ROOT on the one host that wrote
    # them, exactly as today. Set to an s3://bucket/prefix URL (real AWS S3, or
    # MinIO via blob_endpoint below) and every writer also mirrors its file
    # there, so a backend replica or worker on another host can read a
    # finished run's log or a view's asset it does not have locally. See
    # docs/storage.md.
    blob_url: str = Field(
        default="", validation_alias=AliasChoices("AGENTS_HUB_BLOB_URL", "blob_url"))
    # MinIO (or any other S3-compatible) endpoint; empty targets real AWS S3.
    # Region and credentials come from the usual AWS env vars (AWS_ACCESS_KEY_ID,
    # AWS_SECRET_ACCESS_KEY, AWS_REGION / AWS_DEFAULT_REGION), not a setting here.
    blob_endpoint: str = Field(
        default="", validation_alias=AliasChoices("AGENTS_HUB_BLOB_ENDPOINT", "blob_endpoint"))

    # ── Process role (docs/workers.md) ────────────────────────────────────────
    # "all" (default): this backend launches runs itself, exactly as it always
    # has; one process does everything a laptop needs. "api": the backend only
    # prepares runs and puts the launch on the queue (common/run_queue.py) for
    # a worker on any host. "worker": a process started with `ah worker` that
    # claims launches from that queue and spawns them; it serves no HTTP.
    role: str = Field(
        default="all", validation_alias=AliasChoices("AGENTS_HUB_ROLE", "role"))
    # What a worker can run, comma separated: "local", "docker" or both. A
    # worker without a Docker socket says "local" and never claims a run that
    # wants a container.
    worker_modes: str = Field(
        default="local,docker",
        validation_alias=AliasChoices("AGENTS_HUB_WORKER_MODES", "worker_modes"))
    # How many launches one worker keeps alive at once.
    worker_concurrency: int = Field(
        default=4, validation_alias=AliasChoices("AGENTS_HUB_WORKER_CONCURRENCY", "worker_concurrency"))

    # ── OTel export (common/otel_export.py) ───────────────────────────────────
    # Empty (default, off): finished runs are recorded here and nowhere else.
    # Set to a collector's OTLP/HTTP traces URL (including another Agents Hub's
    # own .../api/ingest/v1/traces) and every run that reaches a terminal
    # status is also posted there as one span, best-effort. See
    # docs/service-health.md, "Exporting runs as spans".
    otel_export_url: str = Field(
        default="", validation_alias=AliasChoices("AGENTS_HUB_OTEL_EXPORT_URL", "otel_export_url"))
    # Extra headers the export POST carries, as "k=v,k=v" (a collector token,
    # say). Empty sends none beyond Content-Type.
    otel_export_headers: str = Field(
        default="",
        validation_alias=AliasChoices("AGENTS_HUB_OTEL_EXPORT_HEADERS", "otel_export_headers"))

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

# Global settings instance
settings = Settings()

# Ensure API key is available (strip quotes if present)
if settings.openai_api_key:
    _api_key = settings.openai_api_key.strip('"\'')
    os.environ["OPENAI_API_KEY"] = _api_key
    settings.openai_api_key = _api_key

class Paths:
    """Helper to resolve standardized paths under the shared workspaces root."""
    def __init__(self):
        from common.paths import WORKSPACES_ROOT
        self.root = WORKSPACES_ROOT.resolve()

class Models:
    """Helper for role-based model names (defaults to main model)."""
    def __init__(self):
        self.main = settings.model


def read_dot_env() -> dict:
    """Read the .env file and return only explicitly configured key-value pairs.

    This is intentionally separate from the Settings object, which merges in
    field defaults that are indistinguishable from user-configured values.
    Use this when you need to know what the user has *actually set* via the UI.

    Delegates to common.dotenv, which caches the parse by (mtime, size) so
    repeated calls within a request do not re-read the file.
    """
    from common.dotenv import read_env
    return read_env()


def streaming_enabled() -> bool:
    """Effective global agent-streaming flag, resolved live from .env.

    Deliberately not read off ``settings.agent_streaming``: that value is frozen
    when the module is imported, so the long-running dashboard process would keep
    whatever the flag was at startup and the Settings toggle would take a restart
    to apply. The .env file is always current, so it wins; os.environ is the
    fallback (it lets a subprocess or CI force the flag without touching .env),
    and the field default is the floor.
    """
    raw = read_dot_env().get("AGENT_STREAMING")
    if raw is None:
        raw = os.environ.get("AGENT_STREAMING")
    # Not ``settings.agent_streaming`` as the fallback: that field absorbed .env
    # at import time, so it would keep reporting "on" after the key is removed
    # from the file. The two live sources above are the whole truth; absent both,
    # the feature is off (the field's own default).
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def live_setting(env_key: str, default: str = "") -> str:
    """Effective value of an .env-backed setting, resolved on every call.

    The generalisation of what ``streaming_enabled`` does, and for the same
    reason: a field on ``settings`` froze when this module was imported, so a
    dashboard that has been up for an hour would answer with whatever was in
    the file at startup, and a Settings change would need a restart. The file
    wins because that is what the Settings API writes; os.environ is the
    fallback, so a subprocess, a container or CI can force a value without
    editing the file.

    An empty value in the file is a real answer, not a miss: clearing a field
    in the UI must not resurrect whatever the environment happens to hold.
    """
    raw = read_dot_env().get(env_key)
    if raw is None:
        raw = os.environ.get(env_key)
    value = (raw or "").strip()
    return value or default


def playground_enabled() -> bool:
    """Whether the playground (simulation worlds/scenarios) feature is on.

    Resolved live like ``agent_execution_mode``, so flipping the .env value
    takes effect without a restart. Default is on, so an existing install
    that has never set the flag sees no change.
    """
    default = "true" if settings.playground_enabled else "false"
    raw = live_setting("PLAYGROUND_ENABLED", default)
    return raw.strip().lower() not in ("0", "false", "no", "off")


def blob_url() -> str:
    """Effective ``AGENTS_HUB_BLOB_URL``, resolved live like the other
    .env-backed settings (see :func:`live_setting`). Empty means the local,
    no-op mirror (``common.blobs.LocalBlobStore``); an ``s3://bucket/prefix``
    value is what ``common.blobs.store()`` builds an ``S3BlobStore`` from.
    """
    return live_setting("AGENTS_HUB_BLOB_URL", settings.blob_url)


def agent_execution_mode() -> str:
    """How agents are launched: "local" subprocess or "docker" container.

    Resolved live, so flipping the mode on the Settings page takes effect on
    the next node start rather than on the next restart. Anything unrecognised
    (a typo, a half-written file) reads as local: the mode that needs no Docker
    daemon is the safe one to fall back to.
    """
    mode = live_setting("AGENT_EXECUTION_MODE", settings.agent_mode).lower()
    return mode if mode in ("local", "docker") else "local"


def run_state_transport() -> str:
    """How a run's own entrypoint reaches its run/task records: "db" (direct
    database access) or "http" (relayed through the backend's /api/run-state
    routes). Resolved live, the same way as ``agent_execution_mode``. Left
    unset, it is "db" on SQLite and "http" on Postgres (docs/containers.md,
    docs/scaling.md); an explicit value wins on either. Unrecognised reads as
    the unset case.
    """
    mode = live_setting("AGENT_RUN_STATE_TRANSPORT", "").lower()
    if mode in ("db", "http"):
        return mode
    # Not set anywhere: under Postgres the relay is the default, so a run
    # container is never handed the database URL and password just to write
    # its own status; under SQLite direct access stays the default, as the
    # state directory is mounted into the container anyway.
    try:
        from common import db
        if db.is_postgres():
            return "http"
    except Exception:
        pass
    return settings.run_state_transport if settings.run_state_transport in ("db", "http") else "db"


def hub_role() -> str:
    """This process's role: "all", "api" or "worker" (docs/workers.md).

    Read from the environment first so a worker started with
    ``AGENTS_HUB_ROLE=worker ah worker`` never inherits the backend's ``.env``
    value, then from settings. Anything unrecognised is "all", the mode that
    needs nothing else running.
    """
    raw = (os.environ.get("AGENTS_HUB_ROLE") or settings.role or "all").strip().lower()
    return raw if raw in ("all", "api", "worker") else "all"


def worker_execution_modes() -> list:
    raw = os.environ.get("AGENTS_HUB_WORKER_MODES") or settings.worker_modes or "local,docker"
    modes = [m.strip().lower() for m in raw.split(",") if m.strip()]
    return [m for m in modes if m in ("local", "docker")] or ["local"]


# Convenience accessors to align with previous orchestrator.config API
def get_settings() -> Settings:
    return settings


def require_openai_key(st: Settings) -> str:
    if not st.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Please export it in the environment or put it in a .env file."
        )
    return st.openai_api_key
