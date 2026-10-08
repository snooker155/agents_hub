"""Projects API routes.

A project lives inside a workspace and can be backed by a git repo, a frontend
with a live preview and a backend with Swagger docs and a request proxy. The
routes are split by domain; ``router`` is the one the app mounts.
"""
from fastapi import APIRouter

from common.paths import PROJECTS_FILE
from projects.storage import ProjectStore

router = APIRouter()

_store = ProjectStore(path=PROJECTS_FILE)
_PREVIEW_CHARS = 200_000

from . import crud, files, graph, planner, repo, registry

from .crud import (  # noqa: F401
    list_projects,
    create_project,
    get_project,
    update_project,
    delete_project,
)
from .files import (  # noqa: F401
    _skip_dir,
    _MAX_LISTED_FILES,
    _MAX_PDF_PREVIEW_BYTES,
    _ACTIVE_TYPES,
    list_project_files,
    _project_file,
    get_project_file_id,
    get_project_file_content,
    get_project_file_raw,
    _EXTRACT_SCRIPT,
    _detect_port_from_source,
    get_spec_from_code,
    get_project_tasks,
)
from .graph import (  # noqa: F401
    _validate_view,
    _project_tasks_for,
    get_project_graph,
    save_project_graph,
    relayout_project_graph,
    reset_project_graph,
    generate_project_graph,
    _SSE_HEADERS,
    _sse,
    _REPLY_GARBAGE_MARKERS,
    _clean_agent_reply,
    _STREAM_DONE,
    _register_run,
    _cancel_runs,
    _spawn_detached,
    _RecordingQueue,
    _trace_item_for,
    _relay_queue,
    generate_project_graph_stream,
    get_graph_messages,
    clear_graph_messages,
    chat_project_graph,
    stop_project_graph_chat,
    _run_turn_guarded,
)
from .planner import (  # noqa: F401
    _PLANNER_AGENT_ID,
    _TASKS_VIEW,
    _PLANNER_USER_MSG,
    get_tasks_chat,
    clear_tasks_chat,
    generate_project_tasks,
)
from .repo import (  # noqa: F401
    _attach_allowed_roots,
    _check_attach_allowed,
    import_from_repo,
    connect_repo,
    sync_issues,
    clone_repo,
    attach_project,
    git_status,
    git_pull,
    GitPublishRequest,
    git_publish_route,
    get_swagger_spec,
    proxy_api_request,
)
from .registry import (  # noqa: F401
    REGISTRY_AGENT_ID,
    REGISTRY_CHAT_KIND,
    RegistryChatIn,
    _registry_chat_id,
    _registry_state,
    _registry_chat_prompt,
    _load_registry_chat,
    _load_registry_send,
    _registry_summarize,
    _registry_context_setup,
    _registry_post_turn,
)

from ._common import _project_root_path, _project_to_dict, _task_to_dict, _graph_store  # noqa: F401


router.include_router(crud.router)
router.include_router(files.router)
router.include_router(graph.router)
router.include_router(planner.router)
router.include_router(repo.router)
router.include_router(registry.router)
