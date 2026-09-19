"""
Chat core logic.

In-process, on-request conversation with an agent (or a multi-agent flow). This
package holds the *core* chat processing; the FastAPI routing layer lives
separately in ``dashboard/backend/routes/chat.py`` and imports from here.

Modules:
- :mod:`context`     — prompt/history/attachment/workspace builders
- :mod:`attachments` — attachment validation + workspace materialization
- :mod:`runs`        — run records, request validation, model overrides, journaling
- :mod:`streaming`   — the shared streaming drive loop (``drive_streaming_run``)
- :mod:`pipelines`   — the single-agent and flow chat pipelines

The pipelines are the public entry points (also consumed directly by the
Telegram adapter), so they are re-exported here.
"""
from .pipelines import run_chat_pipeline, run_chat_flow_pipeline

__all__ = ["run_chat_pipeline", "run_chat_flow_pipeline"]
