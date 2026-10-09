#!/usr/bin/env bash
# Re-resolve requirements.lock from the three top-level requirement files.
#
#   scripts/refresh_lock.sh                 keep every pin that still satisfies the ranges
#   scripts/refresh_lock.sh --upgrade       move everything to the newest allowed version
#   scripts/refresh_lock.sh --upgrade-package multidict
#
# uv writes its own header and drops ours, so the explanation is put back on top.
# The weekly workflow .github/workflows/lock-refresh.yml runs it with --upgrade.
set -euo pipefail
cd "$(dirname "$0")/.."

header=$(cat <<'TXT'
# A full, pinned resolution of the three top-level requirement files
# (requirements-agents.txt, dashboard/backend/requirements.txt,
# requirements-cli.txt), for reproducing an environment exactly. This is what
# the backend image (Dockerfile) and CI install from; requirements-postgres.txt
# and requirements-rag.txt are not part of it and are still installed
# separately (docs/scaling.md, docs/memory.md say why). It is universal (built
# with --universal, markers cover the platform/version differences), so pip
# can install it unmodified on Python 3.11 and 3.12.
#
# To refresh after a requirement file changes:
#   scripts/refresh_lock.sh
#
TXT
)

# Compiled in place: uv reads the pins already in the file as preferences,
# so without --upgrade nothing moves that does not have to.
uv pip compile requirements-agents.txt dashboard/backend/requirements.txt requirements-cli.txt \
  -o requirements.lock --python-version 3.11 --universal --quiet "$@"
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
{ printf '%s\n' "$header"; cat requirements.lock; } > "$tmp"
cat "$tmp" > requirements.lock
