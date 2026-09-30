# PyInstaller spec for the REST-only `ah` binary (scripts/build_cli_binary.sh).
#
# Bundles the CLI, typer, rich and requests. Excludes every top-level service
# package so the build never needs the service's own dependencies installed,
# and the resulting binary never touches them even indirectly. That is safe
# because cli/rest_main.py, the entry point below, refuses to run at all
# unless AGENTS_HUB_URL is set: direct mode, the only path that would ever
# reach an excluded package (cli/backend.py's DirectBackend imports them one
# call at a time, from inside its own methods), is never entered.
#
# Run through scripts/build_cli_binary.sh, not `pyinstaller` directly: that
# script also puts the CLI's own dependencies and PyInstaller itself in a
# throwaway venv first.
from pathlib import Path

block_cipher = None
REPO_ROOT = Path(SPECPATH).resolve().parent

# Every top-level package under the repository that belongs to the service
# rather than the CLI (docs/cli.md lists the same split). None of these are
# importable from HttpBackend, the only backend this build ever constructs;
# a genuine runtime need would fail loudly with ModuleNotFoundError, not
# silently do the wrong thing, so excluding them costs nothing this build
# actually does.
SERVICE_EXCLUDES = [
    "agents", "a2a", "bootstrap", "chat", "clients", "connections", "connectors",
    "dashboard", "evals", "flow", "instances", "loops", "managers", "mcp_client",
    "memory", "notify", "plans", "playground", "projects", "providers",
    "reasoning", "runtime", "tasks", "teams", "tools", "views", "workspace",
    # Heavy third-party packages reachable only through the excludes above, or
    # through common's own lazier submodules (db drivers, model SDKs, RAG).
    "anthropic", "openai", "langchain", "langchain_core", "langchain_community",
    "torch", "transformers", "sentence_transformers", "chromadb", "psycopg2",
    "psycopg", "redis", "boto3", "botocore", "docker", "playwright", "grpc",
]

a = Analysis(
    [str(REPO_ROOT / "cli" / "rest_main.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    excludes=SERVICE_EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# One file, no COLLECT() step: every one of a.binaries/zipfiles/datas is
# passed straight to EXE(), which is the single-executable recipe.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ah",
    console=True,
    strip=False,
    upx=False,
)
