"""
How the backend starts itself: `python dashboard/backend/main.py`.

The reloader is a development tool and nothing else. It restarts the process on
any write under the trees it watches, and the backend owns singletons — the plan
scheduler, the run watchdog, the Telegram poller — that a restart interrupts
mid-flight. So it is off unless asked for, here and in every packaged start:
the Dockerfile and the compose service run plain uvicorn.
"""
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def entrypoint():
    from dashboard.backend import main

    return main


class TestTheDirectRun:
    def test_no_reloader_by_default(self, entrypoint):
        options = entrypoint.uvicorn_options([])
        assert options["reload"] is False
        assert "reload_dirs" not in options

    def test_the_flag_turns_it_on(self, entrypoint):
        options = entrypoint.uvicorn_options(["--reload"])
        assert options["reload"] is True
        assert options["reload_dirs"] == entrypoint.RELOAD_DIRS

    def test_the_watched_trees_are_the_ones_it_imports_from(self, entrypoint):
        """Watching only dashboard/backend would miss most edits."""
        watched = {Path(d).name for d in entrypoint.RELOAD_DIRS}
        assert {"backend", "agents", "common", "tools", "tasks", "chat", "flow"} <= watched
        for folder in entrypoint.RELOAD_DIRS:
            assert Path(folder).is_dir(), folder

    def test_host_and_port_have_defaults_and_can_be_given(self, entrypoint):
        assert entrypoint.uvicorn_options([])["port"] == 8000
        given = entrypoint.uvicorn_options(["--host", "0.0.0.0", "--port", "9001"])
        assert (given["host"], given["port"]) == ("0.0.0.0", 9001)


class TestThePackagedRuns:
    """The image and the compose service must not carry the flag at all."""

    def test_the_image_runs_without_a_reloader(self):
        body = (REPO / "Dockerfile").read_text()
        command = [line for line in body.splitlines() if line.startswith("CMD ")]
        assert command and "uvicorn" in command[0]
        assert "--reload" not in command[0]

    def test_the_compose_backend_runs_without_a_reloader(self):
        import yaml

        compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
        command = compose["services"]["backend"]["command"]
        assert "uvicorn" in command
        assert "--reload" not in command
