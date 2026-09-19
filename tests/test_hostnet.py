"""
`localhost` on the wrong side of a container boundary.

A local model server runs on the machine, and every URL naming it is written
from the machine's point of view. A containerized process reading that same URL
reaches itself instead, so the address has to be rewritten to the gateway alias
— but only there, because on a host-run backend `localhost` is already right.

Two directions, two functions, and the difference between them is the whole
point: see common/hostnet.py.
"""
import pytest

from common import hostnet


@pytest.fixture
def on_the_host(monkeypatch, tmp_path):
    """No container markers anywhere."""
    monkeypatch.setenv("AGENTS_HUB_IN_CONTAINER", "0")
    return monkeypatch


@pytest.fixture
def in_a_container(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_IN_CONTAINER", "1")
    return monkeypatch


class TestTheRewriteItself:
    @pytest.mark.parametrize("url,expected", [
        ("http://localhost:11434", "http://host.docker.internal:11434"),
        ("http://127.0.0.1:1234/v1", "http://host.docker.internal:1234/v1"),
        ("http://0.0.0.0:8000/v1", "http://host.docker.internal:8000/v1"),
        ("http://[::1]:11434", "http://host.docker.internal:11434"),
    ])
    def test_every_spelling_of_loopback_is_covered(self, url, expected):
        assert hostnet.to_host_gateway(url) == expected

    @pytest.mark.parametrize("url", [
        "https://api.openai.com/v1",
        "http://gpu-box:8000/v1",
        "http://host.docker.internal:11434",
    ])
    def test_a_remote_address_is_left_alone(self, url):
        assert hostnet.to_host_gateway(url) == url

    def test_only_the_host_part_matches(self):
        """A path that mentions localhost is not an address."""
        url = "https://proxy.example.com/for/localhost/v1"
        assert hostnet.to_host_gateway(url) == url

    def test_an_empty_url_survives(self):
        assert hostnet.to_host_gateway("") == ""


class TestWhoDoesTheRewriting:
    def test_a_host_run_backend_changes_nothing(self, on_the_host):
        assert hostnet.in_container() is False
        assert hostnet.host_service_url("http://localhost:11434") == "http://localhost:11434"

    def test_a_containerized_backend_reaches_for_the_gateway(self, in_a_container):
        assert hostnet.in_container() is True
        assert hostnet.host_service_url("http://localhost:11434") == "http://host.docker.internal:11434"

    def test_compose_is_recognised_by_the_root_it_mounts(self, monkeypatch):
        """HOST_PROJECT_ROOT is set by our own compose file and nothing else."""
        monkeypatch.delenv("AGENTS_HUB_IN_CONTAINER", raising=False)
        monkeypatch.setenv("HOST_PROJECT_ROOT", "/Users/dev/agents_hub")
        assert hostnet.in_container() is True

    def test_the_override_wins_in_both_directions(self, monkeypatch):
        monkeypatch.setenv("HOST_PROJECT_ROOT", "/Users/dev/agents_hub")
        monkeypatch.setenv("AGENTS_HUB_IN_CONTAINER", "0")
        assert hostnet.in_container() is False

    def test_it_is_read_per_call_not_cached(self, monkeypatch):
        monkeypatch.setenv("AGENTS_HUB_IN_CONTAINER", "0")
        assert hostnet.host_service_url("http://localhost:1234") == "http://localhost:1234"
        monkeypatch.setenv("AGENTS_HUB_IN_CONTAINER", "1")
        assert hostnet.host_service_url("http://localhost:1234") == "http://host.docker.internal:1234"


class TestTheAgentContainerEnvironment:
    """The host preparing an environment for a container it is about to start.
    Rewritten unconditionally: the address is for the container to use, so what
    this machine can reach is beside the point."""

    def test_local_urls_are_rewritten_even_on_a_host_run_backend(self, on_the_host):
        from managers import container_manager as cm

        env = cm._point_local_models_at_the_host({"OLLAMA_BASE_URL": "http://127.0.0.1:11434"})
        assert env["OLLAMA_BASE_URL"] == "http://host.docker.internal:11434"

    def test_the_compiled_in_defaults_are_rewritten_too(self, on_the_host):
        """The variable being unset is the common case, and its default is
        localhost — which inside the container is the container."""
        from managers import container_manager as cm

        env = cm._point_local_models_at_the_host({})
        assert env["OLLAMA_BASE_URL"] == "http://host.docker.internal:11434"
        assert env["LMSTUDIO_BASE_URL"] == "http://host.docker.internal:1234"

    def test_a_server_elsewhere_on_the_network_is_untouched(self, on_the_host):
        from managers import container_manager as cm

        env = cm._point_local_models_at_the_host({"OLLAMA_BASE_URL": "http://gpu-box:11434"})
        assert env["OLLAMA_BASE_URL"] == "http://gpu-box:11434"

    def test_the_caller_s_environment_is_not_mutated(self, on_the_host):
        from managers import container_manager as cm

        original = {"OLLAMA_BASE_URL": "http://localhost:11434"}
        cm._point_local_models_at_the_host(original)
        assert original == {"OLLAMA_BASE_URL": "http://localhost:11434"}


class TestTheCustomBackends:
    """The gap this closes: a registry entry pointing at localhost used to be
    handed to a container verbatim, while OLLAMA_BASE_URL was rewritten."""

    def test_a_registry_base_url_follows_the_same_rule(self, in_a_container):
        assert hostnet.host_service_url("http://localhost:8000/v1") == \
            "http://host.docker.internal:8000/v1"

    def test_and_is_left_alone_on_a_host_run_backend(self, on_the_host):
        assert hostnet.host_service_url("http://localhost:8000/v1") == "http://localhost:8000/v1"
