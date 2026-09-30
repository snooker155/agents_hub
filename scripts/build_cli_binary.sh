#!/usr/bin/env bash
#
# Build a single-file `ah` binary that talks to a running Agents Hub backend
# over AGENTS_HUB_URL and nothing else: no Python install, no venv, and no
# service code bundled (cli/rest_main.py, scripts/ah_rest.spec).
#
#   scripts/build_cli_binary.sh                       # dist/ah, a throwaway build venv
#   scripts/build_cli_binary.sh --venv .venv-build     # reuse (or create) this venv instead
#
# Needs Python 3.10+ on PATH. Never installs into the environment already
# active: PyInstaller and the CLI's own dependencies go into the build venv
# above, so a developer's normal environment is untouched either way.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

VENV=""
CLEANUP_VENV=1
PYTHON="${PYTHON:-python3}"

say()  { printf '\033[1m==>\033[0m %s\n' "$1"; }
die()  { printf '\033[31m==>\033[0m %s\n' "$1" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --venv)   shift; VENV="${1:?--venv needs a path}"; CLEANUP_VENV=0 ;;
        --python) shift; PYTHON="${1:?--python needs an interpreter}" ;;
        -h|--help) sed -n '3,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $1 (try --help)" ;;
    esac
    shift
done

command -v "$PYTHON" >/dev/null 2>&1 || die "No '$PYTHON' on PATH."

if [ -z "$VENV" ]; then
    VENV="$(mktemp -d)/ah-rest-build-venv"
fi

if [ ! -d "$VENV" ]; then
    say "Creating a build venv in $VENV"
    "$PYTHON" -m venv "$VENV"
else
    say "Reusing the build venv in $VENV"
fi
PY_BIN="$VENV/bin/python"

say "Installing the CLI's own dependencies and PyInstaller"
"$PY_BIN" -m pip install --upgrade pip >/dev/null
"$PY_BIN" -m pip install -r requirements-cli.txt pyinstaller

say "Building dist/ah (scripts/ah_rest.spec)"
"$PY_BIN" -m PyInstaller --noconfirm --clean scripts/ah_rest.spec

say "Built: dist/ah"
echo "    AGENTS_HUB_URL=http://localhost:8000 dist/ah agent list"

if [ "$CLEANUP_VENV" -eq 1 ]; then
    say "The build venv ($VENV) is a scratch directory outside the checkout; remove it whenever."
fi
