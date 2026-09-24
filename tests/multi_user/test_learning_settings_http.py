"""Personal preferences stay usable without widening learning-account access."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import auth, settings, workspace
from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.grants import learner_grant, save_grant
from deeptutor.multi_user.identity import save_user, set_preset
from deeptutor.multi_user.request_scope import UserScopeMiddleware


@pytest.fixture
def settings_client(mu_isolated_root, make_user, monkeypatch):
    records = {
        name: save_user(name, "unused-test-password", role="admin" if name == "admin" else "user")
        for name in ("admin", "standard", "policy", "learner")
    }
    # The original bug also affects standard accounts with a grant policy.
    assert records["policy"]["preset"] == "standard"
    grant = learner_grant(records["policy"]["id"])
    grant["learning_policy"]["allowed_surfaces"] = ["reading"]
    save_grant(records["policy"]["id"], grant)
    # The conservative fallback is effective even before a grant is saved.
    assert set_preset("learner", "learner")
    users = {
        name: make_user(record["id"], username=name, role=record["role"])
        for name, record in records.items()
    }

    async def install_user(request: Request):
        user = users.get(request.headers.get("x-test-user", ""))
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        token = set_current_user(user)
        try:
            yield auth.TokenPayload(username=user.username, role=user.role, user_id=user.id)
        finally:
            reset_current_user(token)

    app = FastAPI()
    app.add_middleware(UserScopeMiddleware)
    app.dependency_overrides[auth.require_auth] = install_user
    app.include_router(auth.router, prefix="/api/auth")
    dependencies = [Depends(auth.require_learning_surface)]
    app.include_router(settings.router, prefix="/api/settings", dependencies=dependencies)
    app.include_router(
        workspace.settings_router,
        prefix="/api/settings/workspace",
        dependencies=dependencies,
    )

    @app.api_route("/api/settings/ui/private", methods=["GET"], dependencies=dependencies)
    @app.api_route("/api/settings/theme", methods=["POST"], dependencies=dependencies)
    async def guard_probe():
        return {"ok": True}

    monkeypatch.setattr(
        settings,
        "get_model_catalog_service",
        lambda: SimpleNamespace(load=lambda: {"version": 1, "services": {}}),
    )
    monkeypatch.setattr(settings, "_provider_choices", lambda: [])
    monkeypatch.setattr(settings, "_connection_targets", lambda: [])
    monkeypatch.setattr(
        workspace,
        "get_content_workspace_service",
        lambda: SimpleNamespace(describe_current=lambda: {"workspace": "available"}),
    )
    return TestClient(app), users


@pytest.mark.parametrize("actor", ["admin", "standard", "policy", "learner"])
def test_personal_preferences_persist_in_the_callers_scope(settings_client, actor):
    client, _users = settings_client
    headers = {"x-test-user": actor}
    response = client.put(
        "/api/settings/ui",
        headers=headers,
        json={"theme": "dark", "language": "zh", "response_language": "en"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["language"] == "zh"

    loaded = client.get("/api/settings", headers=headers)
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["ui"]["theme"] == "dark"
    assert loaded.json()["ui"]["language"] == "zh"
    assert loaded.json()["ui"]["response_language"] == "en"
    if actor == "admin":
        assert "catalog" in loaded.json()
    else:
        assert set(loaded.json()) == {"ui"}
        admin_ui = client.get("/api/settings", headers={"x-test-user": "admin"}).json()["ui"]
        assert admin_ui["theme"] == "snow"
        assert admin_ui["language"] == "en"
    if actor in {"policy", "learner"}:
        assert "enabled_optional_tools" not in loaded.json()["ui"]

    assert client.get("/api/settings/themes", headers=headers).status_code == 200
    for path, body in (
        ("theme", {"theme": "light"}),
        ("language", {"language": "en"}),
        ("voice-autoplay", {"voice_autoplay": True}),
    ):
        assert client.put(f"/api/settings/{path}", headers=headers, json=body).status_code == 200


@pytest.mark.parametrize("actor", ["policy", "learner"])
def test_learning_ui_draft_save_apply_and_discard(settings_client, actor, monkeypatch):
    client, _users = settings_client
    headers = {"x-test-user": actor}

    def forbidden_catalog_read():
        pytest.fail("Learning-account preferences must not load the model catalog")

    monkeypatch.setattr(settings, "get_model_catalog_service", forbidden_catalog_read)
    ui = {"theme": "dark", "language": "zh", "code_block_show_line_numbers": True}
    saved = client.put(
        "/api/settings/draft", headers=headers, json={"catalog": None, "extensions": {"ui": ui}}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft"]["catalog"] is None
    assert saved.json()["draft"]["extensions"] == {"ui": ui}
    assert client.get("/api/settings/draft", headers=headers).json() == saved.json()
    assert client.get("/api/settings", headers=headers).json()["ui"]["theme"] == "snow"

    assert client.put("/api/settings/ui", headers=headers, json=ui).status_code == 200
    assert client.get("/api/settings", headers=headers).json()["ui"]["theme"] == "dark"
    assert client.delete("/api/settings/draft", headers=headers).json() == {"draft": None}
    assert client.get("/api/settings/draft", headers=headers).json() == {"draft": None}


def test_learning_draft_hides_configuration_saved_before_policy(settings_client, as_user):
    from deeptutor.services.config.settings_draft import get_settings_draft_service

    client, users = settings_client
    with as_user(users["policy"].id):
        get_settings_draft_service().save(
            {
                "catalog": {"private_config": "old-provider"},
                "extensions": {
                    "ui": {"theme": "dark", "enabled_optional_tools": ["web_search"]},
                    "mineru": {"api_token": "old-private-token"},
                },
            }
        )
    response = client.get("/api/settings/draft", headers={"x-test-user": "policy"})
    assert response.status_code == 200
    assert response.json()["draft"]["catalog"] is None
    assert response.json()["draft"]["extensions"] == {"ui": {"theme": "dark"}}
    assert "old-private-token" not in response.text


def test_learner_profile_drafts_follow_the_profile_endpoint_boundary(settings_client):
    client, _users = settings_client
    headers = {"x-test-user": "learner"}
    profile = {"age": 10, "language": "zh", "explanation_style": "Short examples"}
    payload = {"extensions": {"learner-profile": profile}}
    saved = client.put("/api/settings/draft", headers=headers, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft"]["extensions"] == payload["extensions"]
    assert client.get("/api/settings/draft", headers=headers).json() == saved.json()
    applied = client.put("/api/auth/profile/learner-profile", headers=headers, json=profile)
    assert applied.status_code == 200, applied.text
    assert client.get("/api/auth/profile/learner-profile", headers=headers).json() == {
        "learner_profile": {"schema_version": 1, **profile}
    }
    round_trip = client.put(
        "/api/settings/draft",
        headers=headers,
        json={"extensions": {"learner-profile": applied.json()["learner_profile"]}},
    )
    assert round_trip.status_code == 200, round_trip.text
    assert round_trip.json()["draft"]["extensions"] == payload["extensions"]

    # A standard account with a policy cannot use preset-only profile storage.
    assert (
        client.put(
            "/api/settings/draft", headers={"x-test-user": "policy"}, json=payload
        ).status_code
        == 403
    )
    invalid = client.put(
        "/api/settings/draft",
        headers=headers,
        json={"extensions": {"learner-profile": {"age": 200}}},
    )
    assert invalid.status_code == 422
    assert client.get("/api/settings/draft", headers=headers).json() == round_trip.json()


@pytest.mark.parametrize(
    ("payload", "status_code"),
    [
        ({"catalog": {"version": 1}}, 403),
        ({"extensions": {"workspace": {"path": "/tmp"}}}, 403),
        ({"extensions": {"ui": {"enabled_optional_tools": ["web_search"]}}}, 403),
        ({"extensions": {"ui": {"theme": "unknown-theme"}}}, 422),
        ({"extensions": {"ui": ["invalid"]}}, 422),
    ],
)
def test_learning_drafts_reject_nonpersonal_and_invalid_payloads(
    settings_client, payload, status_code
):
    client, _users = settings_client
    response = client.put("/api/settings/draft", headers={"x-test-user": "policy"}, json=payload)
    assert response.status_code == status_code, response.text


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/settings/catalog", None),
        ("GET", "/api/settings/llm-options", None),
        ("POST", "/api/settings/providers/openai-codex/oauth/start", None),
        ("POST", "/api/settings/apply", {"catalog": {}}),
        ("PUT", "/api/settings/enabled-tools", {"enabled_tools": ["web_search"]}),
        ("GET", "/api/settings/workspace", None),
        ("POST", "/api/settings/workspace/data/migrate", {"features": ["sessions"]}),
        ("POST", "/api/settings/workspace/data/export", {"features": ["sessions"]}),
        ("POST", "/api/settings/reset", None),
        ("GET", "/api/settings/ui/private", None),
        ("POST", "/api/settings/theme", None),
    ],
)
def test_learning_settings_allowlist_does_not_expose_managed_routes(
    settings_client, method, path, payload
):
    client, _users = settings_client
    response = client.request(method, path, headers={"x-test-user": "policy"}, json=payload)
    assert response.status_code == 403, response.text


def test_standard_and_admin_managed_boundaries_are_unchanged(settings_client):
    client, _users = settings_client
    assert client.get("/api/settings/catalog", headers={"x-test-user": "admin"}).status_code == 200
    assert (
        client.get("/api/settings/catalog", headers={"x-test-user": "standard"}).status_code == 403
    )
    for actor in ("admin", "standard"):
        assert (
            client.get("/api/settings/workspace", headers={"x-test-user": actor}).status_code == 200
        )


def test_personal_settings_allowlist_still_requires_authentication(settings_client):
    client, _users = settings_client
    assert client.get("/api/settings").status_code == 401
    assert client.put("/api/settings/ui", json={"theme": "dark"}).status_code == 401
    assert client.get("/api/settings/draft").status_code == 401
