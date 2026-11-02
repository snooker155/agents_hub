"""Run a workspace file from its own folder (views/file_run.py), the route
``POST /api/views/code/run-file`` and Save ``PUT /api/workspaces/{name}/file-content``.

Where a file runs follows the workspace: an isolated workspace's sandbox, a
container of its own in docker execution mode (never the host, docker down
means refused), else the run_code sandbox. The launchers themselves run here
for real (python, and node when this machine has it).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common.paths import WORKSPACES_ROOT  # noqa: E402
from views import file_run  # noqa: E402

WS = "filerun"


@pytest.fixture
def root() -> Path:
    folder = WORKSPACES_ROOT / WS
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "pkg").mkdir(parents=True)
    (folder / "pkg" / "helper.py").write_text("X = 42\n")
    (folder / "pkg" / "data.txt").write_text("hello\n")
    (folder / "pkg" / "main.py").write_text(
        "import os, helper\n"
        "print(os.getcwd()); print(__file__); print(helper.X); print(open('data.txt').read().strip())\n"
        "if __name__ == '__main__':\n    print('main')\n")
    (folder / "pkg" / "lib.js").write_text("module.exports = 7;\n")
    (folder / "pkg" / "app.js").write_text(
        "#!/usr/bin/env node\nconst lib = require('./lib');\n"
        "console.log(process.cwd(), __filename, lib, require.main === module);\n")
    (folder / "notes.md").write_text("# n\n")
    return folder


@pytest.fixture
def mode(monkeypatch):
    """Pin where files run: ``mode("container")`` etc."""
    def pin(name: str):
        monkeypatch.setattr(file_run, "run_mode", lambda ws: name)
    return pin


# ── languages and launchers ──────────────────────────────────────────────────

def test_language_for():
    assert file_run.language_for("a/b/main.py") == "python"
    assert file_run.language_for("app.JS") == "node"
    assert file_run.language_for("x.cjs") == "node"
    assert file_run.language_for("run.sh") == "bash"
    assert file_run.language_for("mod.mjs") is None
    assert file_run.language_for("Dockerfile") is None
    assert file_run.language_for("notes.md") is None


def test_python_launcher_runs_the_file_from_its_folder(root):
    code = file_run.launcher("python", str(root), "pkg", "main.py")
    src = (root / "pkg" / "main.py").read_text()
    proc = subprocess.run([sys.executable, "-c", code], input=src, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.split()
    assert Path(lines[0]).resolve() == (root / "pkg").resolve()
    assert lines[1] == str(root / "pkg" / "main.py")
    assert lines[2:] == ["42", "hello", "main"]


def test_python_launcher_reads_the_base_from_work(root):
    code = file_run.launcher("python", "", "pkg", "main.py", base_from_env=True)
    proc = subprocess.run([sys.executable, "-c", code], input="print(open('data.txt').read().strip())",
                          capture_output=True, text=True, timeout=60, env={"WORK": str(root), "PATH": ""})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "hello"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed here")
def test_node_launcher_runs_the_file_as_main(root):
    code = file_run.launcher("node", str(root), "pkg", "app.js")
    src = (root / "pkg" / "app.js").read_text()
    proc = subprocess.run(["node", "-e", code], input=src, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    cwd, filename, lib, is_main = proc.stdout.split()
    assert Path(cwd).resolve() == (root / "pkg").resolve()
    assert filename == str(root / "pkg" / "app.js")
    assert (lib, is_main) == ("7", "true")


def test_bash_launcher_changes_into_the_folder():
    assert file_run.launcher("bash", "/workspace", "a b", "run.sh") == "cd -- '/workspace/a b' || exit 1\nexec bash -s\n"
    assert file_run.launcher("bash", "/workspace", "", "run.sh").startswith("cd -- /workspace ||")
    assert file_run.launcher("bash", "", "pkg", "run.sh", base_from_env=True).startswith('cd -- "$WORK"/pkg ||')


# ── requests that cannot run ─────────────────────────────────────────────────

def test_refuses_what_is_not_a_runnable_file_of_the_workspace(root, mode):
    mode("sandbox")
    for path, status in (("notes.md", 400), ("../outside.py", 400), ("pkg/missing.py", 404)):
        with pytest.raises(file_run.FileRunError) as err:
            file_run.run_file(WS, path)
        assert err.value.status == status
    with pytest.raises(file_run.FileRunError) as err:
        file_run.run_file("no-such-workspace", "main.py")
    assert err.value.status == 404


# ── where it runs ────────────────────────────────────────────────────────────

def test_run_mode_follows_the_workspace(monkeypatch):
    from common import config, isolation
    monkeypatch.setattr(isolation, "is_isolated", lambda ws: ws == "iso")
    monkeypatch.setattr(config, "agent_execution_mode", lambda ws=None: "docker" if ws == "dock" else "local")
    assert file_run.run_mode("iso") == "isolated"
    assert file_run.run_mode("dock") == "container"
    assert file_run.run_mode("plain") == "sandbox"


def test_container_mode_runs_in_its_own_container(root, mode, monkeypatch):
    """Docker mode: the shell container run_shell uses outside an agent
    container, stdin open for the text, the folder at /workspace, the agent
    network; the editor's unsaved text is what runs."""
    from managers import container_manager as cm
    import sandbox.docker as sd
    mode("container")
    monkeypatch.setattr(sd, "docker_available", lambda: True)
    monkeypatch.setattr(cm, "image_exists", lambda tag: tag == cm.BASE_IMAGE)
    monkeypatch.setattr(cm, "agent_network", lambda: "agents-hub-net")
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, stdout="out\n", stderr="")
    monkeypatch.setattr(file_run.subprocess, "run", fake_run)

    result = file_run.run_file(WS, "pkg/main.py", "print('edited')")
    argv, kw = calls[0]
    assert argv[:4] == ["docker", "run", "--rm", "-i"]
    assert argv[argv.index("--network") + 1] == "agents-hub-net"
    assert "agents-hub.container-type=shell" in argv
    mount = argv[argv.index("-v") + 1]
    assert mount.endswith(":/workspace") and mount.split(":")[0].endswith(WS)
    assert cm.BASE_IMAGE in argv
    assert argv[-3:-1] == ["sh", "-c"] and argv[-1].startswith("exec python -c ")
    assert "'/workspace'" in argv[-1] and "'pkg'" in argv[-1]
    assert kw["input"] == "print('edited')"
    assert result["sandbox"] == "container" and result["exit_code"] == 0 and result["stdout"] == "out\n"

    calls.clear()
    file_run.run_file(WS, "pkg/app.js")
    argv, kw = calls[0]
    assert file_run._node_image() in argv and argv[-1].startswith("exec node -e ")
    assert kw["input"] == (root / "pkg" / "app.js").read_text()


def test_container_mode_without_docker_is_refused_not_run_here(root, mode, monkeypatch):
    import sandbox.docker as sd
    mode("container")
    monkeypatch.setattr(sd, "docker_available", lambda: False)
    monkeypatch.setattr(file_run.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    from tools import run_code
    monkeypatch.setattr(run_code, "run_snippet", lambda *a, **k: pytest.fail("no sandbox fallback"))
    result = file_run.run_file(WS, "pkg/main.py")
    assert result["exit_code"] == -1 and "docker" in result["error"] and not result["ok"]


def test_isolated_mode_uses_the_no_network_sandbox(root, mode, monkeypatch):
    import sandbox.docker as sd
    mode("isolated")
    monkeypatch.setattr(sd, "docker_available", lambda: True)
    calls = []
    monkeypatch.setattr(file_run.subprocess, "run",
                        lambda argv, **kw: calls.append((argv, kw)) or subprocess.CompletedProcess(argv, 1, "", "boom"))
    result = file_run.run_file(WS, "pkg/main.py")
    argv, kw = calls[0]
    assert "-i" in argv and argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("-v") + 1].endswith(":/work")
    assert "'/work'" in argv[-1]
    assert result["sandbox"] == "isolated" and result["exit_code"] == 1 and result["stderr"] == "boom"


def test_timeout_removes_the_container(root, mode, monkeypatch):
    import sandbox.docker as sd
    from managers import container_manager as cm
    mode("container")
    monkeypatch.setattr(sd, "docker_available", lambda: True)
    monkeypatch.setattr(cm, "image_exists", lambda tag: True)
    monkeypatch.setattr(cm, "agent_network", lambda: "agents-hub-net")
    seen = []

    def fake_run(argv, **kw):
        seen.append(argv)
        if argv[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(file_run.subprocess, "run", fake_run)
    result = file_run.run_file(WS, "pkg/main.py", timeout=2)
    name = seen[0][seen[0].index("--name") + 1]
    assert seen[1] == ["docker", "rm", "-f", name]
    assert "timed out" in result["error"]


def test_sandbox_mode_hands_the_text_to_run_code(root, mode, monkeypatch):
    from tools import run_code
    mode("sandbox")
    got = {}

    def fake_snippet(language, code, **kw):
        got.update(kw, language=language, code=code)
        return {"exit_code": 0, "stdout": "ok", "stderr": "", "duration_ms": 3, "sandbox": "docker", "error": ""}
    monkeypatch.setattr(run_code, "run_snippet", fake_snippet)
    result = file_run.run_file(WS, "pkg/main.py", "print(1)")
    assert got["language"] == "python" and got["stdin"] == "print(1)"
    assert got["mount_workspace"] is True and Path(got["workspace"]).resolve() == root.resolve()
    assert "_os.environ['WORK']" in got["code"]
    assert result["sandbox"] == "docker" and result["stdout"] == "ok"


def test_run_snippet_container_only_refuses_the_local_provider(monkeypatch):
    from sandbox import registry
    from tools import run_code
    monkeypatch.setattr(registry, "resolve", lambda env, s=None: "local")
    monkeypatch.setattr(registry.get_provider("local"), "run", lambda req: pytest.fail("ran on the host"))
    result = run_code.run_snippet("python", "print(1)", container_only=True)
    assert result["ok"] is False and "execution mode is docker" in result["error"]


# ── routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import views, workspaces
    app = FastAPI()
    app.include_router(views.router)
    app.include_router(workspaces.router)
    return TestClient(app)


def test_route_runs_a_file(client, root, mode, monkeypatch):
    mode("container")
    monkeypatch.setattr(file_run, "_run_in_container",
                        lambda folder, lang, rel_dir, name, src, timeout: file_run._result(
                            lang, "container", exit_code=0, stdout=f"{rel_dir}/{name}:{src}"))
    r = client.post("/api/views/code/run-file", json={"workspace": WS, "path": "pkg/main.py", "body": "B"})
    assert r.status_code == 200
    assert r.json()["stdout"] == "pkg/main.py:B" and r.json()["mode"] == "container"
    assert client.post("/api/views/code/run-file", json={"workspace": WS, "path": "notes.md"}).status_code == 400
    assert client.post("/api/views/code/run-file", json={"workspace": WS, "path": "nope.py"}).status_code == 404


def test_route_saves_an_existing_file_only(client, root):
    r = client.put(f"/api/workspaces/{WS}/file-content", json={"path": "pkg/helper.py", "content": "X = 1\n"})
    assert r.status_code == 200 and r.json()["content"] == "X = 1\n"
    assert (root / "pkg" / "helper.py").read_text() == "X = 1\n"
    assert client.put(f"/api/workspaces/{WS}/file-content", json={"path": "new.py", "content": ""}).status_code == 404
    assert not (root / "new.py").exists()
    assert client.put(f"/api/workspaces/{WS}/file-content",
                      json={"path": "../escape.py", "content": ""}).status_code in (400, 404)
    assert client.put(f"/api/workspaces/{WS}/file-content", json={"path": "pkg/helper.py"}).status_code == 400
