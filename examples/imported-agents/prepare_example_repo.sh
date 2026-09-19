#!/usr/bin/env bash
# Make a bundled example importable without a remote.
#
# The examples live inside the Agents Hub repository, so they are not git
# repositories of their own and `git clone` cannot reach them. This copies one
# into a temp directory, initialises it as a standalone repo, and prints the
# path to paste into Agents -> Import from repo.
#
# Usage:  ./prepare_example_repo.sh aider-agenthub

set -euo pipefail

example="${1:-aider-agenthub}"
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/${example}"

if [[ ! -d "$src" ]]; then
  echo "No such example: ${example}" >&2
  echo "Available:" >&2
  find "$(dirname "$src")" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; >&2
  exit 1
fi

dest="$(mktemp -d "${TMPDIR:-/tmp}/agenthub-example-${example}-XXXXXX")"
cp -R "$src/." "$dest/"

git -C "$dest" init --quiet
git -C "$dest" add -A
git -C "$dest" -c user.email=examples@agents-hub.local \
               -c user.name="Agents Hub examples" \
               commit --quiet -m "Example: ${example}"

echo "$dest"
