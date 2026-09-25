"""The local fallback: a plain subprocess, no isolation.

Used only when docker is unavailable and an operator has explicitly opted in
(``CODE_RUNNER_FALLBACK=local``, checked by ``sandbox/registry.py`` before
this provider is ever selected) or when an environment names ``local`` as its
``sandbox_provider`` outright. No network fence, no filesystem fence: the
snippet runs as this process's own user with a scrubbed environment
(``tools.shell.scrubbed_env``) in a throwaway directory. The network policy on
the request is not enforced here at all — there is nothing to enforce it
with, short of not running the snippet, so ``is_available`` never refuses on
that account, and a caller that actually needs the fence should not have
resolved to this provider in the first place (``sandbox/registry.py``'s
``resolve`` only reaches ``local`` through an explicit choice).
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Tuple

from sandbox.base import LANGS, SandboxProvider, SandboxRequest, SandboxResult, truncate_output
from sandbox.docker import _as_text, _write_snippet  # shared temp-dir/snippet-writing logic


def _max_output() -> int:
    from tools.shell import _MAX_OUTPUT
    return _MAX_OUTPUT


class LocalProvider(SandboxProvider):
    name = "local"

    def is_available(self) -> Tuple[bool, str]:
        # Always "available" in the sense of "runnable": whether a given
        # language has an interpreter on this host is checked per-run, since
        # it varies by language and this check has none to name.
        return True, ""

    def run(self, request: SandboxRequest) -> SandboxResult:
        language = (request.language or "").strip().lower()
        if language not in LANGS:
            return SandboxResult(exit_code=-1, provider=self.name,
                                 error=f"unsupported language {language!r} (expected python, node or bash)")
        file_name, _, candidates = LANGS[language]
        interpreter = next((shutil.which(c) for c in candidates if shutil.which(c)), None)
        if interpreter is None:
            return SandboxResult(exit_code=-1, provider=self.name,
                                 error=f"no {language} interpreter on this host")

        from tools.shell import scrubbed_env
        env = scrubbed_env()
        code_dir = _write_snippet(language, request.code or "", request.extra_files)
        env["HOME"] = str(code_dir)
        if request.workspace:
            ws = Path(request.workspace)
            if not ws.is_dir():
                shutil.rmtree(code_dir, ignore_errors=True)
                return SandboxResult(exit_code=-1, provider=self.name,
                                     error=f"mount_workspace: {request.workspace} is not a directory")
            # Unlike docker's :ro bind mount, local mode has no filesystem
            # fence at all: $WORK is writable. See docs/sandboxes.md.
            env["WORK"] = str(ws)

        started = time.monotonic()
        try:
            proc = subprocess.run([interpreter, str(code_dir / file_name)], cwd=str(code_dir),
                                  input=request.stdin, capture_output=True, text=True,
                                  timeout=request.timeout, env=env)
        except subprocess.TimeoutExpired as exc:
            shutil.rmtree(code_dir, ignore_errors=True)
            out, out_trunc = truncate_output(_as_text(exc.stdout), _max_output())
            err, err_trunc = truncate_output(_as_text(exc.stderr), _max_output())
            return SandboxResult(
                exit_code=-1, stdout=out, stderr=err, truncated_stdout=out_trunc,
                truncated_stderr=err_trunc, duration_ms=int((time.monotonic() - started) * 1000),
                timed_out=True, provider=self.name, error=f"timed out after {request.timeout}s")
        except Exception as exc:  # noqa: BLE001 - reported as the result's error, never raised
            shutil.rmtree(code_dir, ignore_errors=True)
            return SandboxResult(exit_code=-1, provider=self.name, error=str(exc))
        shutil.rmtree(code_dir, ignore_errors=True)

        out, out_trunc = truncate_output(proc.stdout or "", _max_output())
        err, err_trunc = truncate_output(proc.stderr or "", _max_output())
        return SandboxResult(exit_code=proc.returncode, stdout=out, stderr=err,
                             truncated_stdout=out_trunc, truncated_stderr=err_trunc,
                             duration_ms=int((time.monotonic() - started) * 1000),
                             provider=self.name)


__all__ = ["LocalProvider"]
