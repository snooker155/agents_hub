"""
The CLI's two backends.

The point of the abstraction is that a command written once behaves the same
whichever backend serves it, so these tests are mostly about agreement: the same
method names, and the same *shape* of result. The direct path is exercised for
real against a throwaway state root; the HTTP path is checked structurally,
since standing up a server per test would only be testing FastAPI.
"""
import inspect

import pytest

from cli import backend as cli_backend
from cli.backend import BackendError, DirectBackend, HttpBackend, get_backend


# Everything cli/main.py calls. Kept explicit so adding a command to one backend and
# forgetting the other fails here rather than at someone's terminal.
OPERATIONS = [
    "list_agents", "get_agent",
    "send_message",
    "list_tasks", "get_task", "create_task", "assign_task", "decompose_task",
    "stop_task", "delete_task",
    "list_workspaces", "create_workspace", "attach_workspace", "delete_workspace",
    "list_projects", "attach_project", "project_git_status",
    "list_nodes", "start_node", "stop_node",
    "get_settings",
    "describe",
]


@pytest.mark.parametrize("name", OPERATIONS)
def test_both_backends_offer_the_operation(name):
    assert callable(getattr(DirectBackend, name, None)), f"DirectBackend is missing {name}"
    assert callable(getattr(HttpBackend, name, None)), f"HttpBackend is missing {name}"


@pytest.mark.parametrize("name", OPERATIONS)
def test_the_two_signatures_agree(name):
    """A command passes the same arguments either way, so the parameters must match."""
    direct = inspect.signature(getattr(DirectBackend, name))
    http = inspect.signature(getattr(HttpBackend, name))
    assert list(direct.parameters) == list(http.parameters), (
        f"{name} takes {list(direct.parameters)} directly but {list(http.parameters)} over HTTP"
    )


def test_url_selects_http(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_URL", "http://example.invalid:9999")
    backend = get_backend()
    assert isinstance(backend, HttpBackend)
    assert backend.kind == "http"
    assert backend.base_url == "http://example.invalid:9999"


def test_no_url_selects_direct(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    backend = get_backend()
    assert isinstance(backend, DirectBackend)
    assert backend.kind == "direct"


def test_trailing_slash_does_not_double_up(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_URL", "http://example.invalid:9999/")
    assert get_backend().base_url == "http://example.invalid:9999"


def test_blank_url_is_not_a_url(monkeypatch):
    """An exported-but-empty variable is a very easy way to get a confusing failure."""
    monkeypatch.setenv("AGENTS_HUB_URL", "   ")
    assert isinstance(get_backend(), DirectBackend)


def test_unreachable_http_backend_explains_both_options(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_URL", "http://127.0.0.1:1")
    backend = get_backend()
    with pytest.raises(BackendError) as excinfo:
        backend.list_workspaces()
    message = str(excinfo.value)
    assert "Cannot reach the backend" in message
    # The fix is either to start it or to stop asking for HTTP at all.
    assert "AGENTS_HUB_URL" in message


class TestDirectBackend:
    """Against a real, throwaway state root."""

    @pytest.fixture
    def hub(self, monkeypatch):
        monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
        return get_backend()

    def test_describe_names_the_state_root(self, hub):
        assert "in-process" in hub.describe()

    def test_lists_the_shipped_agents(self, hub):
        agents = hub.list_agents()
        assert agents, "a fresh install should have seeded system agents"
        assert all(isinstance(a, dict) and a.get("id") for a in agents)

    def test_get_agent_refuses_an_unknown_id(self, hub):
        with pytest.raises(BackendError, match="not found"):
            hub.get_agent("no_such_agent_at_all")

    def test_results_are_json_shaped(self, hub):
        """Enums and datetimes must arrive as they would over HTTP."""
        import json
        agent = hub.get_agent(hub.list_agents()[0]["id"])
        json.dumps(agent)  # raises TypeError if a raw enum or datetime leaked through

    def test_workspace_round_trip(self, hub):
        hub.create_workspace("cli_backend_ws")
        names = [w["name"] for w in hub.list_workspaces()]
        assert "cli_backend_ws" in names

        entry = next(w for w in hub.list_workspaces() if w["name"] == "cli_backend_ws")
        assert entry["attached"] is False
        assert entry["target"] is None

        assert hub.delete_workspace("cli_backend_ws")["deleted"] is True
        assert "cli_backend_ws" not in [w["name"] for w in hub.list_workspaces()]

    def test_the_default_workspace_is_protected(self, hub):
        hub.create_workspace("default")
        with pytest.raises(BackendError, match="cannot be deleted"):
            hub.delete_workspace("default")

    def test_attach_reports_where_it_points(self, hub, tmp_path):
        outside = tmp_path / "attached_via_backend"
        outside.mkdir()
        result = hub.attach_workspace(str(outside))

        assert result["attached"] is True
        assert result["target"] == str(outside.resolve())
        entry = next(w for w in hub.list_workspaces() if w["name"] == outside.name)
        assert entry["attached"] is True

        # Detaching leaves the directory alone — the same promise the API makes.
        detached = hub.delete_workspace(outside.name)
        assert detached["detached"] is True
        assert outside.is_dir()

    def test_attach_surfaces_a_refusal_as_backend_error(self, hub, tmp_path):
        with pytest.raises(BackendError, match="No such directory"):
            hub.attach_workspace(str(tmp_path / "does_not_exist"))

    def test_task_round_trip(self, hub):
        hub.create_workspace("cli_backend_tasks")
        created = hub.create_task({"title": "from the backend", "workspace_name": "cli_backend_tasks"})
        assert created["title"] == "from the backend"

        listed = hub.list_tasks("cli_backend_tasks")
        assert [t["id"] for t in listed] == [created["id"]]

        fetched = hub.get_task(created["id"])
        assert fetched["id"] == created["id"]
        assert fetched["subtasks"] == []

        hub.delete_task(created["id"])
        assert hub.list_tasks("cli_backend_tasks") == []

    def test_get_task_refuses_an_unknown_id(self, hub):
        import uuid
        with pytest.raises(BackendError, match="not found"):
            hub.get_task(str(uuid.uuid4()))

    def test_assign_refuses_an_unknown_agent(self, hub):
        hub.create_workspace("cli_backend_assign")
        task = hub.create_task({"title": "unassignable", "workspace_name": "cli_backend_assign"})
        with pytest.raises(BackendError, match="Agent not found"):
            hub.assign_task(task["id"], "no_such_agent_at_all")

    def test_nodes_and_settings_are_readable(self, hub):
        assert isinstance(hub.list_nodes(), list)
        assert isinstance(hub.get_settings(), dict)


def test_agent_timeout_default_and_overrides(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_AGENT_TIMEOUT", raising=False)
    assert cli_backend.agent_timeout() == 900.0

    monkeypatch.setenv("AGENTS_HUB_AGENT_TIMEOUT", "30")
    assert cli_backend.agent_timeout() == 30.0

    # 0 means wait as long as it takes.
    monkeypatch.setenv("AGENTS_HUB_AGENT_TIMEOUT", "0")
    assert cli_backend.agent_timeout() is None

    # Nonsense must not crash a command that only wanted to list something.
    monkeypatch.setenv("AGENTS_HUB_AGENT_TIMEOUT", "soon")
    assert cli_backend.agent_timeout() == 900.0


class TestChangeNotifications:
    """A write from a short-lived process must still reach a running dashboard.

    The relay debounces by 250 ms on a daemon timer. That suits an agent
    subprocess, which lives for minutes; a CLI command exits inside the window
    and the timer dies with the process, so the change lands on disk but nothing
    tells an open dashboard about it.

    The queueing itself is deliberately disabled suite-wide (conftest stubs
    ``_relay_notify`` so tests never spawn HTTP relays), so these seed the
    pending queue directly and exercise the flush.
    """

    @pytest.fixture
    def pending(self):
        """A seeded relay queue: N entries, each recording when it is sent."""
        import threading
        import common.session_broker as sb

        sent = []
        sb._relay_timers.clear()

        def seed(count=1):
            for i in range(count):
                timer = threading.Timer(99, lambda: None)  # never fires on its own
                timer.daemon = True
                sb._relay_timers[f"key{i}"] = (timer, lambda i=i: sent.append(i))
            return sent

        yield seed, sent
        for timer, _send in sb._relay_timers.values():
            timer.cancel()
        sb._relay_timers.clear()

    def test_a_pending_event_goes_nowhere_on_its_own(self, pending):
        """What the bug looked like: queued, then the process exits, and it is gone."""
        import common.session_broker as sb

        seed, sent = pending
        seed(1)
        assert len(sb._relay_timers) == 1
        assert sent == [], "nothing should have been delivered yet"

    def test_flush_delivers_what_is_pending(self, pending):
        import common.session_broker as sb

        seed, sent = pending
        seed(1)

        assert sb.flush_relayed_notifications() == 1
        assert sent == [0]
        assert sb._relay_timers == {}, "the queue is emptied, not left to fire twice"

    def test_flush_delivers_every_pending_key(self, pending):
        seed, sent = pending
        seed(3)

        assert cli_backend  # module under test is imported
        import common.session_broker as sb
        assert sb.flush_relayed_notifications() == 3
        assert sorted(sent) == [0, 1, 2]

    def test_flush_is_harmless_when_nothing_is_pending(self, pending):
        import common.session_broker as sb

        assert sb.flush_relayed_notifications() == 0

    def test_flush_respects_its_deadline(self, pending):
        """A command must not hang on the way out if the backend went unreachable."""
        import common.session_broker as sb

        seed, sent = pending
        seed(3)

        # A zero budget delivers nothing, but still clears the queue and cancels
        # the timers rather than leaving them to fire during interpreter teardown.
        assert sb.flush_relayed_notifications(timeout=0) == 0
        assert sent == []
        assert sb._relay_timers == {}

    def test_direct_backend_registers_the_flush(self, monkeypatch):
        """Every direct-mode command gets it, without each one remembering to."""
        monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
        registered = []
        import atexit
        monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: registered.append(fn))

        get_backend()

        import common.session_broker as sb
        assert sb.flush_relayed_notifications in registered

    def test_workspace_writes_notify_like_the_routes_do(self, monkeypatch, tmp_path):
        """The routes notify on create/attach/delete; direct calls must match.

        Without this the two backends disagree: creating a workspace over HTTP
        refreshes the dashboard's picker and creating it directly does not.
        """
        monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
        hub = get_backend()

        notified = []
        monkeypatch.setattr(cli_backend, "_notify_workspaces", lambda: notified.append("workspaces"))

        hub.create_workspace("notify_probe")
        outside = tmp_path / "notify_attached"
        outside.mkdir()
        hub.attach_workspace(str(outside))
        hub.delete_workspace("notify_probe")

        assert notified == ["workspaces"] * 3
