"""`ah setup`: install and configure the hub from the terminal.

A guided first run in six steps: how to run it (this checkout, or Docker from
the published images), the model providers and their default models, the
assistant's voice (speech and transcription models), a few features, a
review, and then the work itself.

Nothing is written until the review is confirmed. Every question is asked
through :mod:`cli.onboard.ui`, so the same steps run from an answers file for
an unattended install (``ah setup --answers setup.json --yes``), and
``--dry-run`` stops after the review.

What it writes is what the rest of the service already reads: ``.env`` (the
same keys the Settings page edits), the model catalog the Models page edits
(``providers/catalog.py``), and the ``default`` workspace's speech and
transcription models (the Special models tab, ``providers/special.py``). It
holds no state of its own, so running it again is how a setting is changed
later: every question then defaults to the value in force.
"""
from __future__ import annotations

import base64
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.columns import Columns
from rich.table import Table
from rich import box

from cli.onboard import probe as P
# The voice choices, shared with the assistant's guided setup in the hub.
from cli.onboard.presets import VOICE_CLOUD
from cli.onboard.ui import Asker, SetupError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STEPS = 6

SHAPES = [
    ("local", "This machine, from this checkout", "a virtualenv, `ah up`, the dashboard on :5173"),
]


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _gen_fernet_key() -> str:
    # A Fernet key is 32 random bytes, url-safe base64: the same thing
    # `ah secrets keygen` prints, without needing cryptography importable here.
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


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
    generated: list = field(default_factory=list)  # (label, value) shown once at the end
    generated_notes: list = field(default_factory=list)  # warnings printed with them
    providers: dict = field(default_factory=dict)  # provider -> {model, models, presets}
    pip_extras: list = field(default_factory=list)
    # The assistant's voice: mode and the special model entries for the default
    # workspace.
    voice: dict = field(default_factory=dict)

    def cur(self, key: str, default: str = "") -> str:
        """The value in force: what this run already decided, then the file."""
        if key in self.env:
            return self.env[key]
        return "" if self.fresh else (self.current.get(key) or default)

    def held(self, key: str) -> str:
        """The value in force even when this run starts over from the template:
        for what state outside the file was made with (stored secrets are
        encrypted with the secret key), so a new value would not reach that
        state but lock it out."""
        return self.cur(key) or self.current.get(key) or ""


# ---------------------------------------------------------------------------
# Step 1: how to run it
# ---------------------------------------------------------------------------


def step_install(a: Asker, plan: Plan, shape: Optional[str], directory: Optional[str],
                 advanced: bool) -> bool:
    """Returns False when the person cancels at the existing-config question."""
    a.section(1, STEPS, "Install", "How this hub should run. Nothing is written before the review.")
    names = [s[0] for s in SHAPES]
    if shape:
        if shape not in names:
            raise SetupError(f"--shape must be one of {', '.join(names)}")
        plan.shape = shape
        a.note(f"Shape: [bold]{dict((s[0], s[1]) for s in SHAPES)[shape]}[/bold]")
    else:
        plan.shape = a.choose("shape", "How should Agents Hub run?", SHAPES, default="local")

    plan.quick = not advanced and a.choose("flow", "How many questions?", [
        ("quick", "QuickStart", "only what cannot be guessed; the assistant sets up the rest with you"),
        ("advanced", "Advanced", "every choice: execution, web search, RAG"),
    ], default="quick") == "quick"

    plan.target = PROJECT_ROOT
    plan.env_file = PROJECT_ROOT / ".env"
    plan.template = PROJECT_ROOT / ".env.example"

    if not (_has_module("fastapi") and _has_module("langchain_core")):
        a.warn(f"The service's dependencies are not installed for {sys.executable}.")
        if a.confirm("install_deps", "Install them during setup (pip install -e \".[backend,agents]\")?", True):
            plan.pip_extras += ["backend", "agents"]
        else:
            a.note("Then run ./install.sh before `ah up`.")

    if plan.env_file.exists():
        plan.current = read_env(plan.env_file)
        choice = a.choose("existing", f"There is already a configuration at {plan.env_file}.", [
            ("update", "Update it", "every question defaults to the value in force"),
            ("fresh", "Start over", "from the template; the old file is kept as a .bak copy"),
            ("cancel", "Cancel", "change nothing"),
        ], default="update")
        if choice == "cancel":
            return False
        plan.fresh = choice == "fresh"

    # Encrypts workspace secrets and stored tokens at rest (docs/secrets.md).
    # Generated once and never rotated by a re-run, Start over included:
    # changing it would make every stored secret unreadable.
    if not plan.cur("AGENTS_HUB_SECRET_KEY"):
        plan.env["AGENTS_HUB_SECRET_KEY"] = plan.held("AGENTS_HUB_SECRET_KEY") or _gen_fernet_key()
    return True


# ---------------------------------------------------------------------------
# Step 2: providers and models
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
    a.section(2, STEPS, "Providers and models",
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
# Step 3: the assistant's voice
# ---------------------------------------------------------------------------


def step_voice(a: Asker, plan: Plan) -> None:
    """The assistant listens and speaks with the ``default`` workspace's
    transcription and speech models; every personal workspace falls back to
    them. Saved once the hub's database (or, in Docker, its API) is there."""
    a.section(3, STEPS, "Assistant voice",
              "What the assistant hears you with and reads its answers aloud with "
              "(the Special models tab of the Models page, default workspace).")
    if plan.quick and not a.answered("voice.mode"):
        a.note("Left to the assistant: it offers a voice on its first run in the dashboard, and the "
               "browser's own voice works until then.")
        return
    cloud = [p for p in VOICE_CLOUD if p in plan.providers or _configured(plan, p)]
    options = []
    if cloud:
        options.append(("cloud", "A cloud provider", ", ".join(P.LABELS[p] for p in cloud) + "; paid per use"))
    options += [
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
        # The same entries common/setup_ops.voice_cloud saves when the
        # assistant sets the voice in the dashboard; a price only when known.
        plan.voice.update({
            "speech": {"provider": provider, "model": spec["speech"], "options": {"voice": voice},
                       **({"price_usd": spec["speech_price"]} if spec["speech_price"] is not None else {})},
            "transcription": {"provider": provider, "model": spec["transcription"], "options": {},
                              **({"price_usd": spec["transcription_price"]}
                                 if spec["transcription_price"] is not None else {})},
        })
    elif mode == "browser":
        a.note("The assistant page uses the browser's own recognition and voice; Firefox has neither.")


def _voice_entries(plan: Plan) -> dict:
    return {k: plan.voice[k] for k in ("speech", "transcription")}


def _voice_actions(plan: Plan) -> list[str]:
    v = plan.voice
    if not v.get("speech"):
        return []
    where = "in the database"
    return [f"set the default workspace's speech model to {v['speech']['provider']}/{v['speech']['model']} "
            f"and transcription to {v['transcription']['provider']}/{v['transcription']['model']} {where}"]


def _voice_done(a: Asker, plan: Plan) -> None:
    a.ok("Assistant voice: " + ", ".join(f"{k} {e['provider']}/{e['model']}"
                                         for k, e in _voice_entries(plan).items()) + ".")


def _apply_voice_local(a: Asker, plan: Plan) -> None:
    if not plan.voice.get("speech"):
        return
    try:
        # The same write the assistant's setup_step makes: the default
        # workspace's other special models stay.
        from common.setup_ops import _save_voice
        _save_voice(_voice_entries(plan))
        _voice_done(a, plan)
    except Exception as exc:  # noqa: BLE001 - the rest of the setup stands; say what to do
        a.warn(f"The assistant's voice was not set ({exc}). Add it on the Models page, Special models tab.")



def step_features(a: Asker, plan: Plan) -> None:
    a.section(4, STEPS, "Features")
    if not plan.quick or a.answered("demo"):
        demo = a.confirm("demo", "Seed the demo workspace on the first start (four agents, chats, views)?",
                         plan.cur("DEMO_WORKSPACE") in ("1", "true", "True"))
        plan.env["DEMO_WORKSPACE"] = "1" if demo else "0"
    else:
        a.note("Web search, the demo workspace and the rest: the assistant offers them in the "
               "dashboard, one at a time.")

    if plan.quick:
        return

    plan.env["AGENT_EXECUTION_MODE"] = a.choose("execution", "Where do agents run?", [
        ("local", "As processes of the backend", "simplest"),
        ("docker", "Each in its own container", "needs a reachable Docker daemon"),
    ], default=plan.cur("AGENT_EXECUTION_MODE", "local"))

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
                    "or install grows by about 2 GB)?", plan.cur("WITH_RAG", "false").lower() == "true")
    if rag:
        plan.env["RAG_VECTOR_DB"] = "chroma"
        if not _has_module("chromadb"):
            plan.pip_extras.append("rag")
    else:
        plan.env["RAG_VECTOR_DB"] = "none"


# ---------------------------------------------------------------------------
# Step 5: review
# ---------------------------------------------------------------------------

_SECRET_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")


def _shown(key: str, value: str) -> str:
    if not value:
        return "[dim](empty)[/dim]"
    if key.endswith(_SECRET_SUFFIXES):
        return P.mask(value)
    if key.endswith("_URL"):
        # Any URL may carry a password: the review is printed before --yes or
        # --dry-run is looked at.
        return re.sub(r"://([^:@/]*):[^@]+@", r"://\1:****@", value)
    return value


def actions(plan: Plan) -> list[str]:
    out: list[str] = []
    if plan.pip_extras:
        out.append(f"pip install -e \".[{','.join(dict.fromkeys(plan.pip_extras))}]\" into {sys.executable}")
    out.append(f"write {plan.env_file}" + (" (the current one is backed up)" if plan.env_file.exists() else ""))
    if plan.providers:
        out.append("put the chosen models into the Models catalog, each provider's default starred")
    out += _voice_actions(plan)
    return out


def step_review(a: Asker, plan: Plan) -> None:
    a.section(5, STEPS, "Review")
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
# Step 6: apply
# ---------------------------------------------------------------------------


def _run(a: Asker, cmd: list[str], cwd: Optional[Path] = None, env: Optional[dict] = None) -> None:
    a.console.print(f"  [dim]$ {' '.join(cmd)}[/dim]")
    code = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env).returncode
    if code != 0:
        raise SetupError(f"`{' '.join(cmd)}` failed with status {code}")


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


def apply_local(a: Asker, plan: Plan) -> None:
    if plan.pip_extras:
        extras = ",".join(dict.fromkeys(plan.pip_extras))
        _run(a, [sys.executable, "-m", "pip", "install", "-e", f".[{extras}]"], cwd=PROJECT_ROOT)
        importlib.invalidate_caches()

    backup = write_env(plan.env_file, plan.template, plan.env, plan.fresh)
    a.ok(f"Wrote {plan.env_file}" + (f" (previous: {backup.name})" if backup else "") + ".")

    from cli.backend import _ensure_importable
    _ensure_importable()
    try:
        from common import db
        db.get_conn()
        a.ok("Database schema is current.")
    except Exception as exc:  # noqa: BLE001 - the backend retries on start; say what happened
        a.warn(f"Could not open the database yet: {exc}")
        return
    if plan.providers:
        _apply_catalog(a, plan)
    _apply_voice_local(a, plan)


# ---------------------------------------------------------------------------
# The whole run
# ---------------------------------------------------------------------------


def _farewell(a: Asker, plan: Plan, started: bool) -> None:
    if plan.generated:
        body = "\n".join(f"[bold]{label}:[/bold] {value}" for label, value in plan.generated)
        body += "".join(f"\n[yellow]{note}[/yellow]" for note in plan.generated_notes)
        a.panel(body + "\n[dim]Shown once. Store them now.[/dim]", title="Generated credentials", style="yellow")
    lines = ["ah up                     backend on :8000, dashboard on :5173",
             "open http://localhost:5173",
             "ah doctor                 check every part"]
    lines += ["ah setup                  change any of this later"]
    # From here the assistant leads (common/setup_guide.py): the welcome window
    # hands over to it, or asks for a model key first when none was given.
    lines += ["", "In the dashboard, choose Talk or Type in the welcome window: the assistant takes you"]
    lines += (["through the rest of the setup (the default model, its voice, web search, a first",
               "agent and task)."] if plan.providers else
              ["through the rest. It asks for a model key first, since none was given here."])
    a.panel("\n".join(lines), title="Next", style="green")


def _banner(a: Asker) -> None:
    a.panel("[bold]Agents Hub setup[/bold]\n"
            "How it runs, the model providers, the assistant's voice, a few features.\n"
            "[dim]Nothing is written until you confirm the review. Ctrl-C stops at any point.[/dim]",
            style="cyan")


def run(a: Asker, *, shape: Optional[str] = None, directory: Optional[str] = None, advanced: bool = False,
        dry_run: bool = False, yes: bool = False, no_start: bool = False) -> int:
    _banner(a)
    plan = Plan()
    if not step_install(a, plan, shape, directory, advanced):
        a.note("Cancelled; nothing was changed.")
        return 0
    step_providers(a, plan)
    step_voice(a, plan)
    step_features(a, plan)
    step_review(a, plan)
    if dry_run:
        a.note("[bold]Dry run[/bold]: nothing was written.")
        return 0
    if not yes and not a.confirm("apply", "Apply this?", True):
        a.note("Cancelled; nothing was changed.")
        return 0

    a.section(6, STEPS, "Applying")
    apply_local(a, plan)
    _farewell(a, plan, started=False)
    if not no_start and a.interactive and a.confirm("start", "Start it now (ah up)?", True):
        a.console.print()
        os.execv(sys.executable, [sys.executable, "-m", "cli", "up"])
    return 0
