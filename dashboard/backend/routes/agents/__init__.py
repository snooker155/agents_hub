"""Agent-related API routes.

Includes the agent's own definition chat (``/{agent_id}/definition/chat``),
where the Agent Creator edits an agent's instructions, capabilities and usage in
place while the user watches the files change beside the conversation. The
routes are split by domain; ``router`` is the one the app mounts.
"""
from fastapi import APIRouter

from agents.agent_factory import get_factory  # noqa: F401
from workspace import (  # noqa: F401
    create_workspace_folder, get_workspace_metadata, update_workspace_metadata,
)

router = APIRouter()

from . import listing, definition, settings, capabilities, lifecycle, definition_chat

from .listing import (  # noqa: F401
    list_agents,
    list_tools,
    _workspace_capacity_overrides,
    get_all_workspace_capacities,
    capability_check,
    get_agent_details,
)
from .definition import (  # noqa: F401
    get_agent_definition,
    AgentInstructionsUpdate,
    update_agent_definition,
    restore_shipped_definition,
    list_agent_versions,
    diff_agent_version,
    rollback_agent_version,
    ExperimentArm,
    ExperimentUpdate,
    get_agent_experiment,
    put_agent_experiment,
    end_agent_experiment,
    agent_experiment_report,
    list_agent_online_evals,
    agent_online_evals_summary,
    update_agent_description,
    AgentIdentityUpdate,
    update_agent_identity,
)
from .settings import (  # noqa: F401
    get_agent_workspace_capacities,
    get_agent_history,
    get_agent_logs,
    delete_agent,
    update_agent_memory,
    erase_agent_memory,
    update_agent_skills_config,
    get_agent_episodic_config,
    update_agent_episodic_config,
    _personal_memory_config,
    get_agent_personal_memory,
    update_agent_personal_memory,
    get_agent_response_format,
    update_agent_response_format,
    get_agent_clarify_gate,
    update_agent_clarify_gate,
    get_agent_self_delegation,
    update_agent_self_delegation,
    list_agent_skills,
    create_agent_skill,
    delete_agent_skill,
)
from .capabilities import (  # noqa: F401
    _capability_conflict,
    _capability_warning_dict,
    _capability_override_state,
    get_agent_auto_tools,
    _can_edit_agent,
    get_agent_capability_override,
    update_agent_capability_override,
    update_agent_tools,
    get_agent_delegates,
    update_agent_delegates,
    _validated_handoffs,
    _validated_handoff_history,
    _handoffs_dict,
    get_agent_handoffs,
    update_agent_handoffs,
    get_agent_reasoning,
    update_agent_reasoning,
    get_agent_model,
    update_agent_model,
    health_agent,
    set_default_chat_agent,
    clear_default_chat_agent,
)
from .lifecycle import (  # noqa: F401
    create_custom_agent,
    clone_agent_to_workspace,
    update_agent_sharing,
)
from .definition_chat import (  # noqa: F401
    DEFINITION_AGENT_ID,
    DEFINITION_CHAT_KIND,
    _definition_state,
    _tool_catalog,
    _definition_chat_prompt,
    _load_definition_chat,
    _load_definition_send,
    _definition_summarize,
    _definition_context_setup,
    _definition_post_turn,
)
from ._common import (  # noqa: F401
    _agent_visible_in_workspace, _apply_workspace_memory, _get_workspace_default_chat_agent,
    _light_registry_dict, _set_workspace_default_chat_agent, _workspace_for_chat_default,
    _workspace_memory_overrides,
)

router.include_router(listing.router)
router.include_router(definition.router)
router.include_router(settings.router)
router.include_router(capabilities.router)
router.include_router(lifecycle.router)
router.include_router(definition_chat.router)

from routes.agent_inheritance import router as _inheritance_router  # noqa: E402

router.include_router(_inheritance_router, prefix="/api/agents", tags=["agents"])
