"""
Attached workspaces: a workspace that points at a directory outside the state
root instead of owning one inside it.

The property that matters most here is that the user's directory is never
destroyed. Everything else in these tests exists to pin down the naming rule,
which is not cosmetic: several callers derive the workspace name from
``create_workspace_folder(name).name``, and that resolves the link.
"""
import pytest

from workspace import storage


@pytest.fixture
def ws_root(tmp_path, monkeypatch):
    """Point the workspaces root at a throwaway directory for one test."""
    root = tmp_path / "state" / "workspaces"
    root.mkdir(parents=True)
    monkeypatch.setattr(storage, "WORKSPACES_ROOT", root)
    monkeypatch.setattr(storage, "WORKSPACES_META_FILE", tmp_path / "workspaces.json")
    monkeypatch.setattr(storage, "_WORKSPACES_META_LOCK", str(tmp_path / "workspaces.json.lock"))
    monkeypatch.setattr(storage, "ensure_workspaces_root", lambda: root)
    return root


@pytest.fixture
def outside(tmp_path):
    """A directory with content, standing in for the user's project."""
    d = tmp_path / "elsewhere" / "myapp"
    d.mkdir(parents=True)
    (d / "main.py").write_text("VALUE = 1\n")
    (d / ".git").mkdir()
    return d


def test_attach_links_without_copying(ws_root, outside):
    link = storage.attach_workspace_folder(outside)

    assert link == ws_root / "myapp"
    assert link.is_symlink()
    assert link.resolve() == outside.resolve()
    # The content is visible through the link, and still has one home on disk.
    assert (link / "main.py").read_text() == "VALUE = 1\n"
    assert not (ws_root / "myapp" / "main.py").is_symlink()


def test_attached_workspace_is_discoverable_like_any_other(ws_root, outside):
    storage.attach_workspace_folder(outside)

    assert [p.name for p in storage.list_workspace_folders()] == ["myapp"]
    assert storage.get_workspace_folder("myapp") == outside.resolve()
    assert storage.is_attached_workspace("myapp") is True
    assert storage.workspace_target("myapp") == outside.resolve()


def test_plain_workspace_is_not_reported_as_attached(ws_root):
    storage.create_workspace_folder("managed")

    assert storage.is_attached_workspace("managed") is False
    assert storage.workspace_target("managed") == (ws_root / "managed").resolve()


def test_attach_seeds_metadata_with_the_target(ws_root, outside):
    storage.attach_workspace_folder(outside)

    meta = storage.get_workspace_metadata("myapp")
    assert meta["name"] == "myapp"
    assert meta["attached_path"] == str(outside.resolve())


def test_name_must_match_the_folder(ws_root, outside):
    # A mismatch would make create_workspace_folder(name).name resolve to the
    # target's basename, filing work under a workspace nobody selected.
    with pytest.raises(ValueError, match="must be 'myapp'"):
        storage.attach_workspace_folder(outside, name="something_else")

    # Passing the matching name is accepted, as an assertion.
    assert storage.attach_workspace_folder(outside, name="myapp").name == "myapp"


def test_resolved_name_stays_the_workspace_name(ws_root, outside):
    """The invariant the naming rule protects, stated directly."""
    storage.attach_workspace_folder(outside)

    assert storage.create_workspace_folder("myapp").name == "myapp"


def test_attach_is_idempotent(ws_root, outside):
    first = storage.attach_workspace_folder(outside)
    second = storage.attach_workspace_folder(outside)

    assert first == second
    assert second.resolve() == outside.resolve()


def test_attach_rejects_a_second_target_under_the_same_name(ws_root, tmp_path, outside):
    storage.attach_workspace_folder(outside)
    other = tmp_path / "other" / "myapp"
    other.mkdir(parents=True)

    with pytest.raises(ValueError, match="already attached"):
        storage.attach_workspace_folder(other)


def test_attach_refuses_to_shadow_a_real_workspace(ws_root, outside):
    storage.create_workspace_folder("myapp")

    with pytest.raises(ValueError, match="already exists as a real directory"):
        storage.attach_workspace_folder(outside)


def test_attach_rejects_a_path_inside_the_state_root(ws_root):
    inside = storage.create_workspace_folder("managed")

    with pytest.raises(ValueError, match="already inside the workspaces root"):
        storage.attach_workspace_folder(inside)


def test_attach_rejects_missing_and_non_directories(ws_root, tmp_path):
    with pytest.raises(ValueError, match="No such directory"):
        storage.attach_workspace_folder(tmp_path / "nope")

    afile = tmp_path / "afile.txt"
    afile.write_text("x")
    with pytest.raises(ValueError, match="Not a directory"):
        storage.attach_workspace_folder(afile)


def test_detach_removes_the_link_and_keeps_the_directory(ws_root, outside):
    """The one that must never regress."""
    storage.attach_workspace_folder(outside)

    assert storage.delete_workspace_folder("myapp") is True

    assert not (ws_root / "myapp").exists()
    assert not (ws_root / "myapp").is_symlink()
    # The user's directory, its content and its repo are all still there.
    assert outside.is_dir()
    assert (outside / "main.py").read_text() == "VALUE = 1\n"
    assert (outside / ".git").is_dir()


def test_detach_drops_the_metadata_entry(ws_root, outside):
    storage.attach_workspace_folder(outside)
    storage.delete_workspace_folder("myapp")

    # A workspace recreated under the same name must not inherit attached_path.
    storage.create_workspace_folder("myapp")
    assert "attached_path" not in storage.get_workspace_metadata("myapp")


def test_deleting_a_plain_workspace_still_removes_its_contents(ws_root):
    root = storage.create_workspace_folder("managed")
    (root / "scratch.txt").write_text("disposable")

    assert storage.delete_workspace_folder("managed") is True
    assert not root.exists()


def test_deleting_a_missing_workspace_reports_false(ws_root):
    assert storage.delete_workspace_folder("never_existed") is False


def test_project_subfolder_resolves_into_an_attached_workspace(ws_root, outside):
    """Agents working on a project inside an attached workspace hit the real dir."""
    storage.attach_workspace_folder(outside)

    project_root = storage.resolve_project_root("myapp", "sub")

    assert project_root == (outside / "sub").resolve()
    assert (outside / "sub").is_dir()
