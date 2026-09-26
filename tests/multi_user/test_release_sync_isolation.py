"""Release-sync gate: real auth and storage must preserve account boundaries.

The small ASGI app mounts the production routers and their authentication
dependency, without starting unrelated integrations or external model services.
All identities, catalogs, documents, and SQLite stores live under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def release_accounts(mu_isolated_root, monkeypatch, seed_user):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.services import auth

    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTH_SECRET", "release-isolation-test-secret")
    monkeypatch.setattr(auth, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr("deeptutor.services.pocketbase_client.is_pocketbase_enabled", lambda: False)

    # An explicit administrator prevents first-account promotion from making
    # either test participant an administrator with intentionally shared data.
    seed_user("root", role="admin")
    accounts = {}
    for username in ("alice", "bob"):
        record = seed_user(username)
        assert record["role"] == "user"
        token = auth.create_token(username, role="user", user_id=record["id"])
        accounts[username] = {
            "id": record["id"],
            "username": username,
            "headers": {"Authorization": f"Bearer {token}"},
        }
    return accounts


@pytest.fixture
def release_client(release_accounts, mu_isolated_root, monkeypatch):
    from deeptutor.services import config

    # The knowledge router loads main.yaml on import. Provide an isolated
    # configuration even in a fresh checkout with no developer data/ tree.
    settings = mu_isolated_root / "data" / "user" / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    (settings / "main.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ROOT", mu_isolated_root)

    from deeptutor.api.routers import auth, knowledge, sessions, workspace
    from deeptutor.multi_user.request_scope import UserScopeMiddleware
    from deeptutor.services.workspace.activity import WorkspaceActivityMiddleware

    # Exercise the production manager resolver, including its per-user cache.
    monkeypatch.setattr(knowledge, "kb_manager", None)
    app = FastAPI()
    app.add_middleware(WorkspaceActivityMiddleware)
    app.add_middleware(UserScopeMiddleware)
    authenticated = [Depends(auth.require_learning_surface)]
    app.include_router(knowledge.router, prefix="/api", dependencies=authenticated)
    app.include_router(sessions.router, prefix="/api/sessions", dependencies=authenticated)
    app.include_router(
        workspace.settings_router,
        prefix="/api/settings/workspace",
        dependencies=authenticated,
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def shared_deployment_folder(mu_isolated_root, monkeypatch):
    shared = mu_isolated_root / "shared-deployment-workspace"
    shared.mkdir()
    monkeypatch.setenv("DEEPTUTOR_WORKSPACE_ROOT", str(shared))
    monkeypatch.setenv("DEEPTUTOR_WORKSPACE_ALLOWED_ROOTS", str(shared))
    return shared


@pytest.fixture
def release_content(release_accounts, as_user, workspace_kind, shared_deployment_folder):
    from deeptutor.multi_user.knowledge_access import current_kb_manager
    from deeptutor.services.path_service import get_path_service
    from deeptutor.services.session import get_sqlite_session_store
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    for username, account in release_accounts.items():
        with as_user(account["id"], username=username):
            workspace_id = ""
            if workspace_kind == "managed":
                workspace = ContentWorkspaceService().create_workspace("Private study")
                workspace_id = workspace["workspace_id"]
            account["workspace_id"] = workspace_id
            account["headers"]["X-DeepTutor-Workspace"] = workspace_id
            with workspace_context(workspace_id):
                manager = current_kb_manager()
                kb_name = f"{username}-private"
                content = f"Private knowledge belonging to {username}."
                raw = Path(manager.base_dir) / kb_name / "raw"
                raw.mkdir(parents=True)
                (raw / "private.txt").write_text(content, encoding="utf-8")
                manager.register_knowledge_base(kb_name)
                store = get_sqlite_session_store()
                session = asyncio.run(store.create_session(title=f"{username} private chat"))
                asyncio.run(store.add_message(session["id"], "user", content))
                account.update(
                    kb_name=kb_name,
                    kb_root=Path(manager.base_dir),
                    session_id=session["id"],
                    content=content,
                    memory_root=get_path_service().get_memory_dir(),
                )
    return release_accounts


@pytest.mark.parametrize("workspace_kind", ["general", "managed"])
def test_authenticated_kb_list_and_document_reads_are_private(release_client, release_content):
    client = release_client
    assert client.get("/api/knowledge-bases").status_code == 401

    for username, foreign_name in (("alice", "bob"), ("bob", "alice")):
        own, foreign = release_content[username], release_content[foreign_name]
        headers = own["headers"]
        response = client.get("/api/knowledge-bases", headers=headers)
        assert response.status_code == 200, response.text
        assert {item["name"] for item in response.json()} == {own["kb_name"]}

        # The global resource picker traverses all the caller's workspaces.
        library = client.get("/api/knowledge-bases?resource_library=true", headers=headers)
        assert library.status_code == 200, library.text
        assert {item["name"] for item in library.json()} == {own["kb_name"]}

        own_url = f"/api/knowledge-bases/{own['kb_name']}"
        assert client.get(own_url, headers=headers).status_code == 200
        document = client.get(f"{own_url}/files/private.txt", headers=headers)
        assert document.status_code == 200, document.text
        assert document.text == own["content"]

        foreign_url = f"/api/knowledge-bases/{foreign['kb_name']}"
        for suffix in ("", "/files", "/files/private.txt"):
            denied = client.get(f"{foreign_url}{suffix}", headers=headers)
            assert denied.status_code == 404, denied.text
            assert foreign["content"] not in denied.text

        if foreign["workspace_id"]:
            # A learned workspace id must not switch the authenticated owner.
            denied = client.get(
                foreign_url,
                headers={**headers, "X-DeepTutor-Workspace": foreign["workspace_id"]},
            )
            assert denied.status_code == 404, denied.text


@pytest.mark.parametrize("workspace_kind", ["general", "managed"])
def test_authenticated_session_list_and_read_are_private(release_client, release_content):
    client = release_client
    assert client.get("/api/sessions").status_code == 401

    for username, foreign_name in (("alice", "bob"), ("bob", "alice")):
        own, foreign = release_content[username], release_content[foreign_name]
        headers = own["headers"]
        for suffix in ("", "?all_workspaces=true"):
            response = client.get(f"/api/sessions{suffix}", headers=headers)
            assert response.status_code == 200, response.text
            assert {item["id"] for item in response.json()["sessions"]} == {own["session_id"]}

        response = client.get(f"/api/sessions/{own['session_id']}", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["messages"][0]["content"] == own["content"]
        denied = client.get(f"/api/sessions/{foreign['session_id']}", headers=headers)
        assert denied.status_code == 404, denied.text
        assert foreign["content"] not in denied.text

        if foreign["workspace_id"]:
            denied = client.get(
                f"/api/sessions/{foreign['session_id']}",
                headers={**headers, "X-DeepTutor-Workspace": foreign["workspace_id"]},
            )
            assert denied.status_code == 404, denied.text

    assert release_content["alice"]["memory_root"] != release_content["bob"]["memory_root"]


@pytest.mark.parametrize("workspace_kind", ["general", "managed"])
def test_rag_tool_rejects_foreign_knowledge_before_provider_query(
    release_content, as_user, monkeypatch
):
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.services.workspace.knowledge import qualified_kb_id
    from deeptutor.tools.builtin import RAGTool

    search = AsyncMock(return_value={"answer": "Own knowledge result"})
    provider = Mock(return_value=Mock(search=search))
    monkeypatch.setattr("deeptutor.tools.rag_tool.RAGService", provider)

    for username, foreign_name in (("alice", "bob"), ("bob", "alice")):
        own, foreign = release_content[username], release_content[foreign_name]
        with as_user(own["id"], username=username), workspace_context(own["workspace_id"]):
            provider.reset_mock()
            search.reset_mock()
            result = asyncio.run(RAGTool().execute(query="private fact", kb_name=own["kb_name"]))
            assert result.content == "Own knowledge result"
            provider.assert_called_once_with(kb_base_dir=str(own["kb_root"]), provider=None)
            search.assert_awaited_once()

            # Bare names and stable qualified references both enforce ownership.
            for kb_ref in (
                foreign["kb_name"],
                qualified_kb_id(foreign["kb_name"], foreign["workspace_id"]),
            ):
                provider.reset_mock()
                search.reset_mock()
                with pytest.raises(HTTPException) as denied:
                    asyncio.run(RAGTool().execute(query="private fact", kb_name=kb_ref))
                assert denied.value.status_code == 404
                provider.assert_not_called()
                search.assert_not_awaited()


def test_shared_deployment_roots_do_not_become_user_workspace_roots(
    release_client, release_accounts, shared_deployment_folder, mu_isolated_root
):
    client = release_client
    created = {}
    for username, account in release_accounts.items():
        headers = account["headers"]
        response = client.post(
            "/api/settings/workspace/registrations", headers=headers, json={"name": "Study"}
        )
        assert response.status_code == 200, response.text
        workspace = response.json()["workspace"]
        root = Path(workspace["path"])
        root.relative_to(mu_isolated_root / "data" / "users" / account["id"])
        assert root.is_dir()
        created[username] = workspace

        denied = client.post(
            "/api/settings/workspace/registrations",
            headers=headers,
            json={"name": "Shared", "path": str(shared_deployment_folder)},
        )
        assert denied.status_code == 400, denied.text

        catalog = client.get("/api/settings/workspace/registrations", headers=headers)
        assert catalog.status_code == 200, catalog.text
        assert str(shared_deployment_folder) not in {
            row["path"] for row in catalog.json()["workspaces"]
        }

    assert created["alice"]["path"] != created["bob"]["path"]
    assert created["alice"]["workspace_id"] != created["bob"]["workspace_id"]
    denied = client.post(
        "/api/settings/workspace/registrations",
        headers=release_accounts["bob"]["headers"],
        json={"name": "Alice's folder", "path": created["alice"]["path"]},
    )
    assert denied.status_code == 400, denied.text
