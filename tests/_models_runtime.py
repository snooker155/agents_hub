"""Loads the model runtime service (deploy/models/app.py) fresh, so every test
gets its own settings, jobs, usage counts and loaded-model state."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODELS_SVC_DIR = Path(__file__).resolve().parents[1] / "deploy" / "models"


def load_runtime(name: str = "models_service_app"):
    """Drop every module of the service from ``sys.modules``, then import
    ``app.py`` under ``name``."""
    for key in [k for k in sys.modules if k in ("app", name) or k.startswith("app_")]:
        del sys.modules[key]
    spec = importlib.util.spec_from_file_location(name, MODELS_SVC_DIR / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
