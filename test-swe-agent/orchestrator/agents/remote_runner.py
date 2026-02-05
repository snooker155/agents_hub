import requests
import json
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)

def start_remote_run(agent_url: str, task_id: str, instruction: str, workspace: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Send a start request to a remote agent."""
    url = f"{agent_url.rstrip('/')}/run"
    payload = {
        "task_id": task_id,
        "instruction": instruction,
        "workspace": workspace,
        "params": params or {}
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("run_id") or f"remote-{task_id}"
    except Exception as e:
        logger.error(f"Failed to start remote run at {url}: {e}")
        raise RuntimeError(f"Remote agent at {url} unreachable or error: {e}")

def get_remote_status(agent_url: str, run_id: str) -> Dict[str, Any]:
    """Get status from a remote agent."""
    url = f"{agent_url.rstrip('/')}/status/{run_id}"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Failed to get remote status from {url}: {e}")
        return {"status": "error", "error": str(e)}

def stop_remote_run(agent_url: str, run_id: str) -> bool:
    """Send a stop request to a remote agent."""
    url = f"{agent_url.rstrip('/')}/stop/{run_id}"
    try:
        resp = requests.post(url, timeout=5)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Failed to stop remote run at {url}: {e}")
        return False

def check_health(agent_url: str) -> Dict[str, Any]:
    """Check health of a remote agent."""
    url = f"{agent_url.rstrip('/')}/health"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            return {"status": "up", "details": resp.json()}
        return {"status": "down", "code": resp.status_code}
    except Exception as e:
        return {"status": "down", "error": str(e)}
