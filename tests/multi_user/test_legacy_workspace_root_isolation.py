"""Persisted workspace paths must not expand an ordinary account's boundary."""

import json
from pathlib import Path

import pytest

from deeptutor.multi_user.knowledge_access import current_kb_manager
from deeptutor.services.workspace import ContentWorkspaceService, WorkspaceError
from deeptutor.services.workspace.context import workspace_context


def _save_legacy_workspace(service, root, workspace_id, *, managed_root=None):
    """Model a registry written by an older version that allowed shared roots."""
    from deeptutor.multi_user.context import get_current_user

    row = {
        "workspace_id": workspace_id,
        "owner_id": get_current_user().scope.user_id,
        "path": str(root),
        "display_name": "Legacy workspace",
        "kind": "workspace",
        "follows_root": True,
        "archived": False,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    with service._catalog_connection() as conn:
        conn.execute("INSERT INTO workspaces VALUES (?, ?)", (workspace_id, json.dumps(row)))
        if managed_root is not None:
            conn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('root', ?)",
                (json.dumps(str(managed_root)),),
            )


@pytest.mark.parametrize("root_kind", ["external", "other_account", "symlink", "parent"])
def test_two_accounts_cannot_reopen_shared_legacy_root(
    as_user, make_user, mu_isolated_root, root_kind
):
    external = mu_isolated_root / "shared-legacy"
    if root_kind == "other_account":
        external = make_user("owner").scope.root / "private-workspaces"
    shared = external / "project"
    shared.mkdir(parents=True)
    marker = shared / "private.txt"
    marker.write_text("Keep the original owner's data untouched.")

    for uid in ("alice", "bob"):
        with as_user(uid):
            service = ContentWorkspaceService()
            configured = external
            if root_kind == "symlink":
                configured = make_user(uid).scope.root / "legacy-link"
                configured.parent.mkdir(parents=True, exist_ok=True)
                configured.symlink_to(external, target_is_directory=True)
            elif root_kind == "parent":
                configured = make_user(uid).scope.root / ".." / ".." / ".." / external.name
            _save_legacy_workspace(service, shared, f"ws_{uid}_legacy", managed_root=configured)

            with pytest.raises(WorkspaceError):
                service.binding_by_id(f"ws_{uid}_legacy")
            with pytest.raises(WorkspaceError):
                with workspace_context(f"ws_{uid}_legacy"):
                    current_kb_manager()
            with pytest.raises(WorkspaceError):
                service.create_workspace("New workspace")

            # Fail closed without rewriting the registry or moving existing data.
            assert service._catalog_metadata("root") == str(configured)
            assert service._catalog()[0]["path"] == str(shared)

    assert marker.read_text() == "Keep the original owner's data untouched."
    assert sorted(path.name for path in external.iterdir()) == ["project"]
    assert sorted(path.name for path in shared.iterdir()) == ["private.txt"]


@pytest.mark.parametrize("symlink", [False, True])
def test_legacy_foreign_workspace_cannot_be_copied_into_own_scope(
    as_user, make_user, mu_isolated_root, symlink
):
    foreign = make_user("alice").scope.root / "private-project"
    foreign.mkdir(parents=True)
    (foreign / "private.txt").write_text("alice only")
    with as_user("bob"):
        service = ContentWorkspaceService()
        source = foreign
        if symlink:
            source = service._managed_root() / "old-link"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.symlink_to(foreign, target_is_directory=True)
        _save_legacy_workspace(service, source, "ws_legacy_foreign")
        destination = service._managed_root() / "copied"
        with pytest.raises(WorkspaceError):
            service.migrate_workspace("ws_legacy_foreign", str(destination))
        assert not destination.exists()
        assert service._catalog()[0]["path"] == str(source)
    assert (foreign / "private.txt").read_text() == "alice only"


def test_own_scoped_managed_roots_remain_distinct_and_usable(as_user, make_user):
    roots = []
    for uid in ("alice", "bob"):
        with as_user(uid):
            service = ContentWorkspaceService()
            custom = make_user(uid).scope.root / "custom-workspaces"
            with service._catalog_connection() as conn:
                conn.execute("INSERT INTO metadata VALUES ('root', ?)", (json.dumps(str(custom)),))
            row = service.create_workspace("Same display name")
            binding = service.binding_by_id(row["workspace_id"])
            assert binding.root.is_relative_to(custom)
            (binding.root / "private.txt").write_text(uid)
            destination = custom / "moved"
            service.migrate_workspace(row["workspace_id"], str(destination))
            assert service.binding_by_id(row["workspace_id"]).root == destination
            assert (destination / "private.txt").read_text() == uid
            roots.append(destination)
    assert roots[0] != roots[1]


def test_builtin_workspace_symlink_is_rejected_before_writing_external_files(
    as_user, mu_isolated_root
):
    external = mu_isolated_root / "external-builtin"
    external.mkdir()
    with as_user("alice"):
        service = ContentWorkspaceService()
        linked = service._managed_root() / service._builtin_id("system")
        linked.parent.mkdir(parents=True, exist_ok=True)
        linked.symlink_to(external, target_is_directory=True)
        with pytest.raises(WorkspaceError):
            service.system_binding()
    assert list(external.iterdir()) == []


def test_admin_keeps_custom_external_managed_root(as_user, mu_isolated_root):
    with as_user("admin", role="admin"):
        service = ContentWorkspaceService()
        custom = mu_isolated_root / "admin-custom"
        with service._catalog_connection() as conn:
            conn.execute("INSERT INTO metadata VALUES ('root', ?)", (json.dumps(str(custom)),))
        row = service.create_workspace("Admin workspace")
        assert Path(row["path"]).is_relative_to(custom)
        destination = mu_isolated_root / "another-admin-location"
        service.migrate_workspace(row["workspace_id"], str(destination))
        assert service.binding_by_id(row["workspace_id"]).root == destination
