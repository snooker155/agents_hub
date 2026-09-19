#!/usr/bin/env bash
#
# Install Agents Hub on this machine: a virtualenv, the service, the `ah`
# command, and the shell integration that makes it available in new terminals.
#
# Safe to re-run. It never overwrites a .env you already have, and the shell
# block it adds is rewritten in place rather than appended again.
#
#   ./install.sh                  # service + agents, venv in .venv, shell hook
#   ./install.sh --cli-only       # just the terminal client (for AGENTS_HUB_URL)
#   ./install.sh --with-rag       # add the RAG extras (pulls in torch)
#   ./install.sh --frontend       # also npm install the dashboard
#   ./install.sh --no-venv        # install into the environment already active
#   ./install.sh --no-shell       # skip the shell integration
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

VENV=".venv"
EXTRAS="backend,agents"
USE_VENV=1
DO_SHELL=1
DO_FRONTEND=0
PYTHON="${PYTHON:-python3}"

say()  { printf '\033[1m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[33m==>\033[0m %s\n' "$1"; }
die()  { printf '\033[31m==>\033[0m %s\n' "$1" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --cli-only)  EXTRAS="" ;;
        --with-rag)  EXTRAS="${EXTRAS:+$EXTRAS,}rag" ;;
        --frontend)  DO_FRONTEND=1 ;;
        --no-venv)   USE_VENV=0 ;;
        --no-shell)  DO_SHELL=0 ;;
        --venv)      shift; VENV="${1:?--venv needs a path}" ;;
        --python)    shift; PYTHON="${1:?--python needs an interpreter}" ;;
        -h|--help)   sed -n '3,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)           die "Unknown option: $1 (try --help)" ;;
    esac
    shift
done

# ---------------------------------------------------------------------------
# 1. Python
# ---------------------------------------------------------------------------

command -v "$PYTHON" >/dev/null 2>&1 || die "No '$PYTHON' on PATH. Install Python 3.10+, or pass --python."
"$PYTHON" - <<'PY' || die "Python 3.10 or newer is required."
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

if [ "$USE_VENV" -eq 1 ]; then
    if [ ! -d "$VENV" ]; then
        say "Creating the virtualenv in $VENV"
        "$PYTHON" -m venv "$VENV"
    else
        say "Reusing the virtualenv in $VENV"
    fi
    PY_BIN="$VENV/bin/python"
else
    say "Installing into the active environment ($("$PYTHON" -c 'import sys; print(sys.prefix)'))"
    PY_BIN="$PYTHON"
fi

# ---------------------------------------------------------------------------
# 2. The service and the command
# ---------------------------------------------------------------------------

say "Upgrading pip"
"$PY_BIN" -m pip install --upgrade pip --quiet

if [ -n "$EXTRAS" ]; then
    say "Installing the service and the ah command (extras: $EXTRAS)"
    "$PY_BIN" -m pip install -e ".[$EXTRAS]"
else
    say "Installing the terminal client only"
    "$PY_BIN" -m pip install -e .
fi

BIN_DIR="$("$PY_BIN" -c 'import sysconfig; print(sysconfig.get_path("scripts"))')"
AH="$BIN_DIR/ah"
[ -x "$AH" ] || die "The ah command was not installed. Check the pip output above."

# ---------------------------------------------------------------------------
# 3. Configuration
# ---------------------------------------------------------------------------

if [ ! -f .env ]; then
    say "Creating .env from .env.example"
    cp .env.example .env
    warn "Put a provider key in .env before running an agent (DEFAULT_PROVIDER, OPENAI_API_KEY, OPENAI_MODEL)."
else
    say "Keeping the .env you already have"
fi

# ---------------------------------------------------------------------------
# 4. The dashboard, on request
# ---------------------------------------------------------------------------

if [ "$DO_FRONTEND" -eq 1 ]; then
    if command -v npm >/dev/null 2>&1; then
        say "Installing dashboard dependencies"
        (cd dashboard/frontend && npm install)
    else
        warn "No npm on PATH, so the dashboard was skipped. Install Node 22+ and re-run with --frontend."
    fi
fi

# ---------------------------------------------------------------------------
# 5. Shell integration
# ---------------------------------------------------------------------------

if [ "$DO_SHELL" -eq 1 ]; then
    say "Adding the shell integration"
    # Written by the CLI itself, between markers, so re-running replaces the
    # block instead of stacking another copy.
    "$AH" shell-init --install
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

cat <<EOF

$(say "Installed.")

  Next, in a NEW terminal (or after: exec \$SHELL)

    cd /path/to/your/git/project
    ah workspace init                 # register it, in place, nothing is copied
    ah agent run swe_agent "..."      # let an agent edit its files
    ah up                             # backend + dashboard, one command

EOF

if [ "$DO_SHELL" -eq 0 ] || [ "$USE_VENV" -eq 1 ]; then
    echo "  The command lives at $AH"
    if [ "$DO_SHELL" -eq 1 ]; then
        echo "  and the shell integration points at it, so the venv needs no activating."
    else
        echo "  Put that directory on PATH, or run: $AH shell-init --install"
    fi
    echo
fi
