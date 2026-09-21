"""Request scope failures must never fall back to administrator storage."""

from __future__ import annotations

import pytest


def test_active_request_without_identity_rejects_path_service(mu_isolated_root):
    from deeptutor.multi_user.context import MissingUserScope, _request_active
    from deeptutor.services.path_service import get_path_service

    token = _request_active.set(True)
    try:
        with pytest.raises(MissingUserScope):
            get_path_service()
    finally:
        _request_active.reset(token)


def test_active_request_does_not_use_rag_admin_fallback(mu_isolated_root, monkeypatch):
    from deeptutor.multi_user.context import _request_active
    from deeptutor.services.rag.service import RAGService

    def fail_scope_resolution():
        raise PermissionError("missing request scope")

    monkeypatch.setattr(
        "deeptutor.services.path_service.get_path_service", fail_scope_resolution
    )
    token = _request_active.set(True)
    try:
        with pytest.raises(PermissionError, match="missing request scope"):
            RAGService()
    finally:
        _request_active.reset(token)


def test_user_workspace_symlink_is_rejected(mu_isolated_root):
    from deeptutor.multi_user import paths

    target = mu_isolated_root / "outside"
    target.mkdir()
    paths.USERS_ROOT.mkdir(parents=True, exist_ok=True)
    (paths.USERS_ROOT / "u_alice").symlink_to(target, target_is_directory=True)

    with pytest.raises(PermissionError, match="symbolic link"):
        paths.scope_for_user("u_alice", is_admin=False)


@pytest.mark.parametrize("malformed_id", ["../u_bob", "u_bob\\\\escape", "u_bob\x00escape"])
def test_user_workspace_identifier_rejects_non_component_values(mu_isolated_root, malformed_id):
    from deeptutor.multi_user import paths

    with pytest.raises(PermissionError, match="Invalid user workspace id"):
        paths.scope_for_user(malformed_id, is_admin=False)


def test_user_linked_path_cannot_escape_own_workspace(mu_isolated_root, as_user, tmp_path):
    from deeptutor.services.rag.linked_kb import assert_path_allowed

    outside = tmp_path / "outside"
    outside.mkdir()
    with as_user("u_alice"):
        with pytest.raises(ValueError, match="own workspace"):
            assert_path_allowed(str(outside))


def test_scoped_identifier_cannot_escape_path_service(mu_isolated_root, as_user):
    from deeptutor.services.path_service import get_path_service

    with as_user("u_alice"):
        service = get_path_service()
        with pytest.raises(ValueError):
            service.get_session_workspace("chat", "../../u_bob")
        with pytest.raises(ValueError):
            service.get_settings_file("../../system.json")


def test_user_workspace_feature_symlink_is_rejected(mu_isolated_root, as_user, tmp_path):
    from deeptutor.services.path_service import get_path_service

    outside = tmp_path / "shared-reading"
    outside.mkdir()
    with as_user("u_alice"):
        service = get_path_service()
        reading = service.get_workspace_feature_dir("reading")
        reading.parent.mkdir(parents=True, exist_ok=True)
        if reading.exists():
            reading.rmdir()
        reading.symlink_to(outside, target_is_directory=True)
        with pytest.raises(PermissionError, match="symbolic link"):
            get_path_service()


def test_user_chat_feature_symlink_is_rejected(mu_isolated_root, as_user, tmp_path):
    from deeptutor.services.path_service import get_path_service

    outside = tmp_path / "shared-chat"
    outside.mkdir()
    with as_user("u_alice"):
        service = get_path_service()
        chat_root = service.get_chat_workspace_root()
        chat_root.mkdir(parents=True, exist_ok=True)
        existing = chat_root / "deep_solve"
        if existing.is_dir():
            existing.rmdir()
        (chat_root / "deep_solve").symlink_to(outside, target_is_directory=True)
        with pytest.raises(PermissionError, match="symbolic link"):
            service.get_solve_dir()


def test_memory_child_symlink_is_rejected(mu_isolated_root, as_user, tmp_path):
    from deeptutor.services.memory import paths as memory_paths
    from deeptutor.services.path_service import get_path_service

    outside = tmp_path / "shared-memory"
    outside.mkdir()
    with as_user("u_alice"):
        memory = get_path_service().get_memory_dir()
        memory.mkdir(parents=True, exist_ok=True)
        (memory / "L2").symlink_to(outside, target_is_directory=True)
        with pytest.raises(PermissionError, match="symbolic link"):
            memory_paths.l2_file("chat")


def test_public_output_root_symlink_is_rejected(mu_isolated_root, as_user, tmp_path):
    from deeptutor.services.path_service import get_path_service

    outside = tmp_path / "other-user"
    outside.mkdir()
    with as_user("u_alice"):
        user_root = get_path_service().get_user_root()
        user_root.mkdir(parents=True, exist_ok=True)
        workspace = user_root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        chat_dir = workspace / "chat" / "chat"
        chat_dir.mkdir(parents=True, exist_ok=True)
        (chat_dir / "evil").symlink_to(outside, target_is_directory=True)
        assert get_path_service().resolve_public_output_path(
            "workspace/chat/chat/evil/media.png"
        ) is None


def test_attachment_branch_symlink_is_rejected(mu_isolated_root, as_user, tmp_path):
    from deeptutor.services.storage.attachment_store import (
        get_attachment_store,
        reset_attachment_store,
    )
    from deeptutor.services.path_service import get_path_service

    outside = tmp_path / "other-user-attachments"
    outside.mkdir()
    with as_user("u_alice"):
        user_root = get_path_service().get_user_root()
        chat_root = user_root / "workspace"
        chat_root.mkdir(parents=True, exist_ok=True)
        chat_dir = chat_root / "chat"
        chat_dir.mkdir(parents=True, exist_ok=True)
        (chat_dir / "attachments").symlink_to(outside, target_is_directory=True)
        reset_attachment_store()
        try:
            with pytest.raises(PermissionError, match="symbolic link"):
                get_attachment_store()
        finally:
            reset_attachment_store()


def test_file_library_branch_symlink_is_rejected(mu_isolated_root, as_user, tmp_path):
    from deeptutor.services.storage.file_library import (
        get_file_library_store,
        reset_file_library_store,
    )
    from deeptutor.services.path_service import get_path_service

    outside = tmp_path / "other-user-library"
    outside.mkdir()
    with as_user("u_alice"):
        user_root = get_path_service().get_user_root()
        library_parent = user_root / "workspace"
        library_parent.mkdir(parents=True, exist_ok=True)
        (library_parent / "library").symlink_to(outside, target_is_directory=True)
        reset_file_library_store()
        try:
            with pytest.raises(PermissionError, match="symbolic link"):
                get_file_library_store()
        finally:
            reset_file_library_store()


def test_persisted_linked_kb_pointer_cannot_escape_user_scope(
    mu_isolated_root, as_user, tmp_path
):
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.services.path_service import get_path_service

    outside = tmp_path / "outside-linked-index"
    outside.mkdir()
    with as_user("u_alice"):
        base = get_path_service().get_knowledge_bases_root()
        base.mkdir(parents=True, exist_ok=True)
        manager = KnowledgeBaseManager(base_dir=str(base))
        manager.config.setdefault("knowledge_bases", {})["linked"] = {
            "type": "linked",
            "external_path": str(outside),
            "path": "linked",
        }
        manager._save_config()
        with pytest.raises(ValueError, match="outside your scope"):
            manager.get_knowledge_base_path("linked")


def test_knowledge_raw_directory_symlink_is_rejected(
    mu_isolated_root, as_user, tmp_path, monkeypatch
):
    from fastapi import HTTPException

    from deeptutor.api.routers import knowledge as knowledge_router
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.services.path_service import get_path_service

    outside = tmp_path / "other-user-raw"
    outside.mkdir()
    (outside / "private.txt").write_text("secret", encoding="utf-8")
    with as_user("u_alice"):
        base = get_path_service().get_knowledge_bases_root()
        manager = KnowledgeBaseManager(base_dir=str(base))
        kb_dir = base / "own-kb"
        kb_dir.mkdir()
        (kb_dir / "raw").symlink_to(outside, target_is_directory=True)
        manager.config.setdefault("knowledge_bases", {})["own-kb"] = {
            "path": "own-kb",
            "status": "ready",
        }
        manager._save_config()
        monkeypatch.setattr(knowledge_router, "kb_manager", manager)

        with pytest.raises(HTTPException) as exc_info:
            knowledge_router._resolve_kb_raw_dir("own-kb")

        assert exc_info.value.status_code == 404


def test_grant_path_rejects_traversal_and_symlinks(mu_isolated_root, tmp_path):
    from deeptutor.multi_user import grants

    with pytest.raises(ValueError):
        grants.grant_path("../../outside")
    grants.GRANTS_DIR.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (grants.GRANTS_DIR / "u_alice.json").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic link"):
        grants.grant_path("u_alice")


def test_owner_secrets_id_is_a_safe_component(mu_isolated_root):
    from deeptutor.multi_user import paths

    with pytest.raises(PermissionError, match="Invalid owner id"):
        paths.owner_secrets_dir("../../outside")
    with pytest.raises(PermissionError, match="Invalid owner id"):
        paths.owner_secrets_dir(r"u_alice\\other")


def test_owner_secrets_symlink_root_and_leaf_are_rejected(mu_isolated_root, tmp_path):
    from deeptutor.multi_user import paths

    secrets_root = paths.SYSTEM_ROOT / paths.USER_SECRETS_DIRNAME
    outside = tmp_path / "outside-secrets"
    outside.mkdir()
    secrets_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        secrets_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this platform")
    with pytest.raises(PermissionError, match="symbolic link"):
        paths.owner_secrets_dir("u_alice")

    secrets_root.unlink()
    secrets_root.mkdir(parents=True)
    (secrets_root / "u_alice").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError, match="symbolic link"):
        paths.owner_secrets_dir("u_alice")


def test_ordinary_users_cannot_use_direct_manim_execution(mu_isolated_root, as_user):
    from deeptutor.multi_user.execution_access import (
        assert_capability_execution_allowed,
        assert_manim_execution_allowed,
    )

    with as_user("u_alice"):
        with pytest.raises(PermissionError, match="cannot run Manim"):
            assert_capability_execution_allowed("math_animator")
        with pytest.raises(PermissionError, match="cannot run Manim"):
            assert_capability_execution_allowed("visualize", render_mode="manim_video")
        # SVG/Chart/HTML visualization remains available; it does not launch
        # generated Python through the Manim renderer.
        assert_capability_execution_allowed("visualize", render_mode="svg")
        with pytest.raises(PermissionError, match="cannot run Manim"):
            assert_manim_execution_allowed()


def test_admin_can_use_direct_manim_execution(mu_isolated_root, as_user):
    from deeptutor.multi_user.execution_access import (
        assert_capability_execution_allowed,
        assert_manim_execution_allowed,
    )

    with as_user("local-admin", role="admin"):
        assert_capability_execution_allowed("math_animator")
        assert_capability_execution_allowed("visualize", render_mode="manim_video")
        assert_manim_execution_allowed()


def test_ordinary_exec_is_rejected_before_workspace_mutation(
    mu_isolated_root, as_user, tmp_path
):
    import asyncio

    from deeptutor.tools.exec_tool import ExecTool

    forged_workdir = tmp_path / "foreign-workdir"
    with as_user("u_alice"):
        with pytest.raises(PermissionError, match="cannot run code"):
            asyncio.run(
                ExecTool().execute(
                    code="print('must not be written')",
                    language="python",
                    _sandbox_code_workdir=str(forged_workdir),
                    _sandbox_user_id="u_bob",
                )
            )

    assert not forged_workdir.exists()


def test_ordinary_cli_app_is_rejected_before_executable_lookup(
    mu_isolated_root, as_user, monkeypatch
):
    import asyncio

    from deeptutor.services.cli_apps import runner
    from deeptutor.services.cli_apps.models import AppRuntime, InstallKind
    from deeptutor.services.cli_apps.state import InstalledApp

    app = InstalledApp(
        id="example",
        entry_point="example",
        runtime=AppRuntime.PYTHON,
        kind=InstallKind.PIP,
        target="example",
        pin="",
        abi="",
        installed_at="",
    )

    def _unexpected_lookup(*_args, **_kwargs):
        raise AssertionError("ordinary users must be rejected before executable lookup")

    monkeypatch.setattr(runner, "executable_path", _unexpected_lookup)
    with as_user("u_alice"):
        with pytest.raises(PermissionError, match="cannot run code"):
            asyncio.run(runner.run_app(app, [], user_id="u_bob"))


def test_renderer_rejects_ordinary_user_before_subprocess(
    mu_isolated_root, as_user, tmp_path, monkeypatch
):
    import asyncio

    from deeptutor.agents.math_animator.renderer import ManimRenderService

    def _unexpected_popen(*_args, **_kwargs):
        raise AssertionError("ordinary users must be rejected before Popen")

    monkeypatch.setattr(
        "deeptutor.agents.math_animator.renderer.subprocess.Popen",
        _unexpected_popen,
    )
    renderer = ManimRenderService("turn-1", output_dir=tmp_path / "render")
    with as_user("u_alice"):
        with pytest.raises(PermissionError, match="cannot run Manim"):
            asyncio.run(
                renderer._run_manim(
                    code_path=tmp_path / "scene.py",
                    scene_name="Scene",
                    quality="low",
                    save_last_frame=False,
                )
            )
