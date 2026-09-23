"""How ``runtime/agent_run.py`` reaches its own run and task records.

Every non-Docker run, and every Docker run whose ``.agents_hub`` mount is still
read-write, has direct SQLite access (``common/db.py``) and just calls the
``managers.run_manager`` / ``tasks.*`` functions in-process, exactly as this
entrypoint always has. That is :class:`DirectStateTransport`, and it is the
default: nothing about its behaviour changed by this module existing.

A run container started with ``AGENT_RUN_STATE_TRANSPORT=http`` instead gets
its ``.agents_hub`` mount read-only (see ``managers.container_manager.
build_run_command``), so it cannot open the database itself. It gets
:class:`HttpStateTransport` instead, which posts the same arguments to the
backend's ``/api/run-state`` routes (``dashboard/backend/routes/run_state.py``)
and lets the backend, which always has direct DB access, call the exact same
functions on the other end.

Scope, deliberately bounded (see docs/containers.md): run records, run
payloads, and the task-side transitions a run's own entrypoint drives directly
(result persistence, ``ask_user`` / approval parking, finalize). Memory pools,
tool state, and session-continuation spawning are untouched: they either
already run entirely on the backend (finalize_task_from_run and everything it
calls execute inside the route handler, not inside the container, once a call
reaches here) or are out of scope.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


class StateTransport:
    """Interface ``runtime/agent_run.py`` calls against. See module docstring."""

    def open_run(self, run_id: str, agent_id: str, **kwargs: Any) -> None:
        raise NotImplementedError

    def update_run(self, run_id: str, updates: Dict[str, Any]) -> None:
        raise NotImplementedError

    def seed_run_input_context(self, run_id: str, system_prompt: str, user_message: str) -> None:
        """Minimal input context recorded when a run starts, so the dashboard
        shows something while the run is still executing. A ``process`` update
        under the hood, so every transport gets it for free from
        :meth:`update_run`, no separate wire call needed. Best-effort, matching
        the direct implementation this replaced (``managers.runs.store.
        seed_run_input_context``): a failed seed must never break the run.
        """
        try:
            self.update_run(run_id, {
                "process": {
                    "input_context": {
                        "system_prompt": system_prompt or "",
                        "history": [],
                        "user_message": user_message or "",
                    },
                },
            })
        except Exception:  # noqa: BLE001 - best-effort, a failed seed must never break the run
            log.debug("seed_run_input_context failed for %s", run_id, exc_info=True)

    def close_run_from_result(self, run_id: str, result: Any, **extra: Any) -> None:
        raise NotImplementedError

    def persist_task_result(self, task_id: str, run_id: str, output: str,
                             agent_id: Optional[str] = None) -> None:
        raise NotImplementedError

    def park_task_awaiting_input(self, run_id: str, question: Dict[str, Any], agent_id: str = "") -> None:
        raise NotImplementedError

    def park_task_awaiting_approval(self, task_id: str, pending: Dict[str, Any], *,
                                     run_id: str = "", agent_id: str = "") -> None:
        raise NotImplementedError

    def finalize_task_from_run(self, run_id: str, status: str, exit_code: int) -> None:
        raise NotImplementedError

    def heartbeat(self, run_id: str) -> Optional[str]:
        """Stamp the run's ``heartbeat_at`` and return its current status (so
        the run learns of a stop requested from another host). Best-effort:
        None when the call did not go through."""
        raise NotImplementedError

    def save_checkpoint(self, run_id: str, checkpoint: Dict[str, Any]) -> None:
        """Store the agent loop's checkpoint (agents/checkpoint.py)."""
        raise NotImplementedError

    def load_checkpoint(self, run_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError


class DirectStateTransport(StateTransport):
    """Calls the existing manager/task functions in-process. The default, and
    what every run used before this module existed: byte-for-byte the same
    calls at the same call sites, just routed through one object instead of
    named imports."""

    def open_run(self, run_id: str, agent_id: str, **kwargs: Any) -> None:
        from managers.run_manager import open_run as _open_run
        _open_run(run_id, agent_id, **kwargs)

    def update_run(self, run_id: str, updates: Dict[str, Any]) -> None:
        from managers.run_manager import update_run as _update_run
        _update_run(run_id, updates)

    def close_run_from_result(self, run_id: str, result: Any, **extra: Any) -> None:
        from managers.run_manager import close_run_from_result as _close
        _close(run_id, result, **extra)

    def persist_task_result(self, task_id: str, run_id: str, output: str,
                             agent_id: Optional[str] = None) -> None:
        from tasks.context import persist_task_result as _persist
        _persist(task_id, run_id, output, agent_id=agent_id)

    def park_task_awaiting_input(self, run_id: str, question: Dict[str, Any], agent_id: str = "") -> None:
        from managers.run_manager import park_task_awaiting_input as _park
        _park(run_id, question, agent_id=agent_id)

    def park_task_awaiting_approval(self, task_id: str, pending: Dict[str, Any], *,
                                     run_id: str = "", agent_id: str = "") -> None:
        from uuid import UUID
        from tasks.service import park_task_awaiting_approval as _park
        _park(UUID(str(task_id)), pending, run_id=run_id, agent_id=agent_id)

    def finalize_task_from_run(self, run_id: str, status: str, exit_code: int) -> None:
        from managers.run_manager import finalize_task_from_run as _finalize
        _finalize(run_id, status, exit_code)

    def heartbeat(self, run_id: str) -> Optional[str]:
        from managers.runs.store import touch_heartbeat
        try:
            return touch_heartbeat(run_id)
        except Exception:  # noqa: BLE001 - best-effort (see docstring): None when the call did not go through
            log.debug("heartbeat failed for %s", run_id, exc_info=True)
            return None

    def save_checkpoint(self, run_id: str, checkpoint: Dict[str, Any]) -> None:
        from managers.runs.store import save_run_checkpoint
        save_run_checkpoint(run_id, checkpoint)

    def load_checkpoint(self, run_id: str) -> Optional[Dict[str, Any]]:
        from managers.runs.store import load_run_checkpoint
        return load_run_checkpoint(run_id)


class HttpStateTransport(StateTransport):
    """Posts the same calls to the backend's ``/api/run-state`` routes.

    Host/port resolution mirrors the two existing same-machine HTTP relays,
    ``agents.callbacks.streaming.SessionPublishCallback`` and
    ``common.session_broker._relay_notify``: ``DASHBOARD_PORT`` (default
    ``8000``) on ``localhost``, rewritten to the Docker host-gateway alias by
    ``common.hostnet.host_service_url`` when this process is itself
    containerized. Every run container gets the ``--add-host
    host.docker.internal:host-gateway`` entry ``build_run_command`` always
    adds, which is what makes that alias resolve.

    Authenticated the same way those relays are: ``common.auth.auth_headers()``,
    which reads ``AGENTS_HUB_API_TOKEN`` straight from the environment, the
    same token ``common.subprocess_env.base_subprocess_env`` already makes sure
    reaches a run's environment, container or not.

    Every call here is best-effort: a failed relay must not crash the run any
    more than a failed direct DB write did (the functions on the other end
    already swallow their own exceptions; a transport-level failure, e.g. the
    backend being briefly unreachable, is swallowed the same way).
    """

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout
        port = os.environ.get("DASHBOARD_PORT", "8000")
        from common.hostnet import host_service_url
        self._base = host_service_url(f"http://localhost:{port}") + "/api/run-state"

    def _call(self, method: str, path: str, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            import requests
            from common.auth import auth_headers
            resp = requests.request(
                method, f"{self._base}{path}", json=body,
                headers=auth_headers(), timeout=self._timeout,
            )
            try:
                data = resp.json()
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001 - every call here is best-effort (see class docstring)
            log.debug("state transport call failed: %s %s", method, path, exc_info=True)
            return None

    def open_run(self, run_id: str, agent_id: str, **kwargs: Any) -> None:
        self._call("POST", f"/runs/{run_id}/open", {"agent_id": agent_id, **kwargs})

    def update_run(self, run_id: str, updates: Dict[str, Any]) -> None:
        self._call("PATCH", f"/runs/{run_id}", {"updates": updates})

    def close_run_from_result(self, run_id: str, result: Any, **extra: Any) -> None:
        # `result` is a rich object (AgentInvocation.result), not JSON: derive
        # the same fields managers.runs.lifecycle.close_run_from_result derives
        # from it, and let the route handler reconstruct an equivalent shim on
        # the backend, where the real derivation (and the DB write) happens.
        ok = bool(getattr(result, "ok", False))
        response_payload = None
        resp_obj = getattr(result, "response", None)
        if resp_obj is not None:
            try:
                response_payload = resp_obj.to_payload() if hasattr(resp_obj, "to_payload") else None
            except Exception:  # noqa: BLE001 - best-effort derivation, a bad payload must not break closing the run
                log.debug("response payload derivation failed for %s", run_id, exc_info=True)
                response_payload = None
        error = getattr(result, "error", None)
        body = {
            "ok": ok,
            "agent_output": getattr(result, "agent_output", None),
            "error": str(error) if error else None,
            "response_payload": response_payload,
            "extra": extra,
        }
        self._call("POST", f"/runs/{run_id}/close", body)

    def persist_task_result(self, task_id: str, run_id: str, output: str,
                             agent_id: Optional[str] = None) -> None:
        self._call("POST", f"/tasks/{task_id}/result",
                    {"run_id": run_id, "output": output, "agent_id": agent_id})

    def park_task_awaiting_input(self, run_id: str, question: Dict[str, Any], agent_id: str = "") -> None:
        self._call("POST", f"/runs/{run_id}/park-awaiting-input",
                    {"question": question, "agent_id": agent_id})

    def park_task_awaiting_approval(self, task_id: str, pending: Dict[str, Any], *,
                                     run_id: str = "", agent_id: str = "") -> None:
        self._call("POST", f"/tasks/{task_id}/park-awaiting-approval",
                    {"pending": pending, "run_id": run_id, "agent_id": agent_id})

    def finalize_task_from_run(self, run_id: str, status: str, exit_code: int) -> None:
        self._call("POST", f"/runs/{run_id}/finalize-task",
                    {"status": status, "exit_code": exit_code})

    def heartbeat(self, run_id: str) -> Optional[str]:
        data = self._call("POST", f"/runs/{run_id}/heartbeat", {})
        status = (data or {}).get("status")
        return str(status) if status else None

    def save_checkpoint(self, run_id: str, checkpoint: Dict[str, Any]) -> None:
        self._call("POST", f"/runs/{run_id}/checkpoint", {"checkpoint": checkpoint})

    def load_checkpoint(self, run_id: str) -> Optional[Dict[str, Any]]:
        data = self._call("GET", f"/runs/{run_id}/checkpoint", {})
        cp = (data or {}).get("checkpoint")
        return cp if isinstance(cp, dict) else None


def get_state_transport() -> StateTransport:
    """The transport this process should use, resolved live.

    ``db`` (the default) is direct DB access; ``http`` is the container-safe
    relay. See ``common.config.run_state_transport`` and
    ``AGENT_RUN_STATE_TRANSPORT``.
    """
    from common.config import run_state_transport
    if run_state_transport() == "http":
        return HttpStateTransport()
    return DirectStateTransport()
