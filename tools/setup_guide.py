"""
The assistant's guided setup (common/setup_guide.py, docs/assistant.md "Guided
setup"): three tools.

* ``setup_guide`` reads the person's guide (every step with its status, what is
  next and how) and the choices for the next step (``options``), and marks
  steps: skipped, done (what the hub cannot see, like the health check), or
  the whole guide finished. Nothing in it changes the hub.
* ``setup_step`` makes one change of the setup (the default model, the
  assistant's voice, the demo workspace). Every call waits for the person's
  yes on a card (tools/approval.py ``ALWAYS_GATED``), and runs as the person,
  who must be an administrator. A key is never set here: that is
  ``propose_connection`` with kind ``provider``.
* ``show_on_screen`` opens a page of the hub beside the conversation on the
  Assistant page, so the person sees what is being set up while the
  assistant talks about it. It changes nothing.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools._json import json_err, json_ok

_PATH_OK = re.compile(r"^/(?!/)[A-Za-z0-9_\-./?=&%#:~+]*$")


def _principal() -> Any:
    from chat.lookup import principal_of
    from common.attribution import launching_user
    return principal_of(launching_user())


class SetupGuideInput(BaseModel):
    action: str = Field("status", description="status (the guide), options (the choices for the model, voice "
                                              "and web search steps), skip, unskip, done (mark a step the hub "
                                              "cannot see as done), undo, start, finish or dismiss")
    step: Optional[str] = Field(None, description="skip, unskip, done, undo: the step id from status")


@tool("setup_guide", args_schema=SetupGuideInput)
def setup_guide(action: str = "status", step: Optional[str] = None) -> str:
    """The person's guided setup of the hub: which steps are done, skipped or open,
    what comes next, why it matters and how you do it. `options` gives the choices
    for the next step (each connected provider's strongest, balanced and fastest
    model, the cloud and local voices, the web search providers). Mark a step
    skipped when the person does not want it, done when it is done in a way the hub
    cannot see, and finish when every step is done or skipped. Changes nothing in
    the hub itself.
    """
    from common import setup_guide as guide
    from common import setup_ops
    action = str(action or "status").strip().lower()
    principal = _principal()
    try:
        if action == "options":
            return json_ok(setup_ops.options(), default=str)
        if action == "status":
            setup_ops.advance_work(principal)
            g = guide.guide(principal)
            by_id = {s.id: s for s in guide.STEPS}
            if g["next"]:
                g["next_how"] = by_id[g["next"]].how
                g["next_page"] = by_id[g["next"]].page
            return json_ok(g, default=str)
        return json_ok(guide.act(principal, action, step), default=str)
    except ValueError as exc:
        return json_err(str(exc), code="bad_request")
    except Exception as exc:  # noqa: BLE001 - the agent gets the reason, not a stack
        return json_err(f"The setup guide could not be read: {exc}", code="internal")


class SetupStepInput(BaseModel):
    operation: str = Field(..., description="choose_model, voice_cloud, voice_local or seed_demo")
    provider: Optional[str] = Field(None, description="choose_model, voice_cloud: the provider")
    model: Optional[str] = Field(None, description="choose_model: the model id from setup_guide options")
    voice: Optional[str] = Field(None, description="voice_cloud: one of the provider's voices")
    speech: Optional[str] = Field(None, description="voice_local: a speech preset id from setup_guide options")
    transcription: Optional[str] = Field(None, description="voice_local: whisper-small or whisper-turbo")


def _call_args(provider=None, model=None, voice=None, speech=None, transcription=None) -> dict:
    return {k: v for k, v in (("provider", provider), ("model", model), ("voice", voice), ("speech", speech),
                              ("transcription", transcription)) if v}


@tool("setup_step", args_schema=SetupStepInput)
def setup_step(operation: str, provider: Optional[str] = None, model: Optional[str] = None,
               voice: Optional[str] = None, speech: Optional[str] = None,
               transcription: Optional[str] = None) -> str:
    """Make one change of the hub's setup: choose_model (the default model for every
    workspace without its own), voice_cloud (the assistant hears and speaks with a
    connected OpenAI or Google key), voice_local (it does so with the hub's own
    runtime, downloaded in the background) or seed_demo (the demo workspace). Say in
    one sentence what will change, then call it: the person answers a card (or says
    yes) and only then it runs. Keys are never set here: use propose_connection with
    kind provider.
    """
    from common import setup_ops
    try:
        return json_ok(setup_ops.perform(operation, _call_args(provider, model, voice, speech, transcription),
                                         principal=_principal()), default=str)
    except setup_ops.SetupOpError as exc:
        return json_err(str(exc), code=exc.code)
    except Exception as exc:  # noqa: BLE001 - the agent gets the reason, not a stack
        return json_err(f"The setup step failed: {exc}", code="internal")


def describe_call(tool_input: Any) -> str:
    """The approval card's sentence for one ``setup_step`` call."""
    from common import setup_ops
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            return ""
    if not isinstance(tool_input, dict):
        return ""
    args = {k: tool_input.get(k) for k in ("provider", "model", "voice", "speech", "transcription")}
    return setup_ops.describe(str(tool_input.get("operation") or "").strip().lower(), args)


class ShowInput(BaseModel):
    path: str = Field(..., description="A page of the hub, starting with /, e.g. /models?tab=special or /connectors")


@tool("show_on_screen", args_schema=ShowInput)
def show_on_screen(path: str) -> str:
    """Open a page of the hub beside the conversation on the Assistant page (on a phone,
    the person gets a link), so they see what you are talking about or setting up.
    Changes nothing. Use the page a setup_guide step names.
    """
    path = str(path or "").strip()
    if not _PATH_OK.match(path) or "://" in path:
        return json_err("Give a page of the hub, starting with a single /, like /models.", code="bad_path")
    return json_ok({"showing": path, "note": "The page is open beside the conversation."})


SETUP_GUIDE_TOOLS = [setup_guide, setup_step, show_on_screen]

__all__ = ["SETUP_GUIDE_TOOLS", "describe_call", "setup_guide", "setup_step", "show_on_screen"]
