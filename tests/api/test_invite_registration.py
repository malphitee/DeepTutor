"""HTTP acceptance tests for built-in invitation registration and administration."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

PASSWORD = "invitation-password-123"
INVALID_INVITE = {
    "detail": "A valid invitation code is required.",
    "error_code": "invalid_invite",
}


@pytest.fixture
def invite_root(tmp_path, monkeypatch):
    """Keep all identity, account, audit, and invitation state off the real tree."""
    from deeptutor.multi_user import audit, device_credentials, grants, guardians, identity, paths
    from deeptutor.services import auth as auth_service

    admin_root = tmp_path / "data"
    system_root = admin_root / "system"
    for name, value in {
        "PROJECT_ROOT": tmp_path,
        "ADMIN_WORKSPACE_ROOT": admin_root,
        "USERS_ROOT": admin_root / "users",
        "SYSTEM_ROOT": system_root,
        "LEGACY_MULTI_USER_ROOT": tmp_path / "multi-user",
        "_path_services": {},
    }.items():
        monkeypatch.setattr(paths, name, value)
    for name, value in {
        "PROJECT_ROOT": tmp_path,
        "SYSTEM_ROOT": system_root,
        "AUTH_DIR": system_root / "auth",
        "USERS_FILE": system_root / "auth" / "users.json",
        "SECRET_FILE": system_root / "auth" / "auth_secret",
        "REVOKED_USERS_FILE": system_root / "auth" / "revoked_users.json",
        "LEGACY_USERS_FILE": admin_root / "user" / "auth_users.json",
        "LEGACY_SECRET_FILE": admin_root / "user" / "auth_secret",
    }.items():
        monkeypatch.setattr(identity, name, value)
    monkeypatch.setattr(grants, "GRANTS_DIR", system_root / "grants")
    monkeypatch.setattr(guardians, "GUARDIANS_FILE", system_root / "guardians.json")
    monkeypatch.setattr(audit, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(
        device_credentials,
        "DEVICE_CREDENTIALS_FILE",
        system_root / "auth" / "device_credentials.json",
    )
    monkeypatch.setattr(auth_service, "AUTH_USERNAME", "")
    monkeypatch.setattr(auth_service, "AUTH_PASSWORD_HASH", "")
    monkeypatch.setattr(auth_service, "AUTH_SECRET", "invitation-test-signing-secret")
    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_service, "POCKETBASE_ENABLED", False)
    admin_root.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def invite_rate_clock():
    return [1000.0]


@pytest.fixture
def invite_client(invite_root, monkeypatch, invite_rate_clock):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.multi_user.registration_limits import RegistrationLimiter
    from deeptutor.multi_user.request_scope import UserScopeMiddleware
    from deeptutor.services import config as config_service

    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth_router, "_SECURE", False)
    monkeypatch.setattr(auth_router, "_SAMESITE", "lax")
    monkeypatch.setattr(
        auth_router, "load_auth_settings", lambda: {"registration_trusted_proxies": []}
    )
    monkeypatch.setattr(config_service, "load_system_settings", lambda: {"backend_workers": 1})
    monkeypatch.setattr(
        auth_router, "registration_limiter", RegistrationLimiter(clock=lambda: invite_rate_clock[0])
    )
    app = FastAPI()
    app.add_middleware(UserScopeMiddleware)
    app.include_router(auth_router.router, prefix="/api/auth")
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        yield client


def _register(client, username="new-user", **extra):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": PASSWORD, **extra},
    )


def _headers(username, record):
    from deeptutor.services.auth import create_token

    token = create_token(username, role=record["role"], user_id=record["id"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def invite_admin(invite_client):
    from deeptutor.multi_user.identity import save_user

    record = save_user("admin", "$2b$12$placeholder", role="admin")
    return _headers("admin", record), record


def _issue(client, admin, **settings):
    response = client.post("/api/auth/invites", headers=admin[0], json=settings)
    assert response.status_code == 201, response.text
    return response.json()["invites"]


def _list(client, admin, **query):
    response = client.get("/api/auth/invites", headers=admin[0], params=query)
    assert response.status_code == 200, response.text
    return response.json()


def _assert_no_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            assert key not in {"code", "invite_code", "hash", "code_hash"}
            _assert_no_secrets(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_secrets(child)


def test_first_registration_is_admin_and_subsequent_registration_needs_invite(invite_client):
    status = invite_client.get("/api/auth/registration-status")
    assert status.status_code == 200
    assert status.json() == {
        "available": True,
        "is_first_user": True,
        "invite_required": False,
    }

    first = _register(invite_client, "first-admin")
    assert first.status_code == 201, first.text
    assert first.json() == {
        "ok": True,
        "user_id": first.json()["user_id"],
        "username": "first-admin",
        "role": "admin",
        "is_first_user": True,
        "is_admin": True,
        "preset": "standard",
    }
    assert first.json()["user_id"]
    assert invite_client.get("/api/auth/registration-status").json() == {
        "available": True,
        "is_first_user": False,
        "invite_required": True,
    }
    rejected = _register(invite_client, "second-user")
    assert rejected.status_code == 403
    assert rejected.json() == INVALID_INVITE


def test_valid_invite_creates_standard_user_and_records_redemption(invite_client, invite_admin):
    issued = _issue(invite_client, invite_admin, max_uses=2, note="Study group")[0]
    response = _register(invite_client, "student", invite_code=issued["code"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "user"
    assert body["preset"] == "standard"
    assert body["is_admin"] is False
    assert body["is_first_user"] is False

    row = _list(invite_client, invite_admin)["items"][0]
    assert row["id"] == issued["id"]
    assert row["used_count"] == 1
    assert row["status"] == "active"
    assert row["redemptions"] == [
        {
            "user_id": body["user_id"],
            "username": "student",
            "redeemed_at": row["redemptions"][0]["redeemed_at"],
        }
    ]
    datetime.fromisoformat(row["redemptions"][0]["redeemed_at"])

    login = invite_client.post(
        "/api/auth/login", json={"username": "student", "password": PASSWORD}
    )
    assert login.status_code == 200
    assert login.json()["role"] == "user"
    assert "dt_token=" in login.headers["set-cookie"]


@pytest.mark.parametrize("code", [None, "", "wrong-code", "   "])
def test_missing_or_invalid_invite_has_uniform_error(invite_client, invite_admin, code):
    from deeptutor.multi_user.identity import get_user

    response = _register(invite_client, invite_code=code)
    assert response.status_code == 403
    assert response.json() == INVALID_INVITE
    assert get_user("new-user") is None


def test_exhausted_invite_does_not_create_a_second_user(invite_client, invite_admin):
    from deeptutor.multi_user.identity import get_user

    issued = _issue(invite_client, invite_admin, max_uses=1)[0]
    assert _register(invite_client, "first-student", invite_code=issued["code"]).status_code == 201
    rejected = _register(invite_client, "second-student", invite_code=issued["code"])
    assert rejected.status_code == 403
    assert rejected.json() == INVALID_INVITE
    assert get_user("second-student") is None
    row = _list(invite_client, invite_admin)["items"][0]
    assert row["used_count"] == 1
    assert row["status"] == "exhausted"


def test_expired_invite_has_uniform_error_and_remains_unused(
    invite_client, invite_admin, monkeypatch
):
    from deeptutor.multi_user import invites

    issued = _issue(invite_client, invite_admin, expires_in_days=1)[0]
    expiry = datetime.fromisoformat(issued["expires_at"])
    monkeypatch.setattr(invites, "utc_now", lambda: expiry)
    rejected = _register(invite_client, invite_code=issued["code"])
    assert rejected.status_code == 403
    assert rejected.json() == INVALID_INVITE
    row = _list(invite_client, invite_admin)["items"][0]
    assert row["used_count"] == 0
    assert row["status"] == "expired"


def test_registration_accepts_normalized_invite_code(invite_client, invite_admin):
    issued = _issue(invite_client, invite_admin)[0]
    pasted_code = "  " + issued["code"].replace("-", "").lower() + "  "
    response = _register(invite_client, invite_code=pasted_code)
    assert response.status_code == 201, response.text
    assert _list(invite_client, invite_admin)["items"][0]["used_count"] == 1


def test_revocation_is_idempotent_and_prevents_registration(invite_client, invite_admin):
    issued = _issue(invite_client, invite_admin)[0]
    endpoint = f"/api/auth/invites/{issued['id']}/revoke"
    first = invite_client.post(endpoint, headers=invite_admin[0])
    assert first.status_code == 200
    assert first.json()["ok"] is True
    row = first.json()["invite"]
    assert row["status"] == "revoked"
    assert row["revoked_by"] == invite_admin[1]["id"]
    assert row["revoked_at"]
    _assert_no_secrets(row)
    second = invite_client.post(endpoint, headers=invite_admin[0])
    assert second.status_code == 200
    assert second.json() == first.json()
    rejected = _register(invite_client, invite_code=issued["code"])
    assert rejected.status_code == 403
    assert rejected.json() == INVALID_INVITE


def test_revoke_missing_invite_returns_404(invite_client, invite_admin):
    response = invite_client.post("/api/auth/invites/missing/revoke", headers=invite_admin[0])
    assert response.status_code == 404


def test_duplicate_username_requires_valid_invite_without_consuming_it(invite_client, invite_admin):
    from deeptutor.multi_user.identity import get_user

    original = get_user("admin")
    invalid = _register(invite_client, "admin", invite_code="invalid-code")
    assert invalid.status_code == 403
    assert invalid.json() == INVALID_INVITE
    issued = _issue(invite_client, invite_admin)[0]
    duplicate = _register(invite_client, "admin", invite_code=issued["code"])
    assert duplicate.status_code == 409
    assert duplicate.json()["error_code"] == "username_taken"
    assert get_user("admin") == original
    row = _list(invite_client, invite_admin)["items"][0]
    assert row["used_count"] == 0
    assert row["redemptions"] == []
    assert _register(invite_client, "student", invite_code=issued["code"]).status_code == 201


def test_registration_request_cannot_choose_role_or_preset(invite_client, invite_admin):
    from deeptutor.multi_user.identity import get_user

    issued = _issue(invite_client, invite_admin)[0]
    response = _register(
        invite_client, "student", invite_code=issued["code"], role="admin", preset="custom"
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "user"
    assert response.json()["preset"] == "standard"
    assert get_user("student")["role"] == "user"
    assert get_user("student")["preset"] == "standard"


def test_list_is_paginated_and_never_reveals_raw_codes_or_hashes(
    invite_client, invite_admin, invite_root
):
    issued = _issue(invite_client, invite_admin, batch_count=3, max_uses=3, note="September")
    assert len(issued) == 3
    assert len({row["code"] for row in issued}) == 3
    assert len({row["id"] for row in issued}) == 3
    for row in issued:
        assert row["code_hint"]
        assert row["code_hint"] != row["code"]
        assert row["created_by"] == invite_admin[1]["id"]
        assert row["note"] == "September"
        assert row["used_count"] == 0
        assert row["max_uses"] == 3
        assert row["status"] == "active"
        assert row["redemptions"] == []
        assert row["revoked_at"] is None
        assert datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(
            row["created_at"]
        ) == timedelta(days=7)
        _assert_no_secrets({key: value for key, value in row.items() if key != "code"})

    page = _list(invite_client, invite_admin, offset=1, limit=1)
    assert page["total"] == 3
    assert len(page["items"]) == 1
    full = _list(invite_client, invite_admin)
    assert page["items"] == full["items"][1:2]
    assert _list(invite_client, invite_admin, offset=3, limit=1) == {"items": [], "total": 3}
    _assert_no_secrets(full)

    assert _register(invite_client, "student", invite_code=issued[0]["code"]).status_code == 201
    invite_client.post(f"/api/auth/invites/{issued[1]['id']}/revoke", headers=invite_admin[0])
    audit_path = invite_root / "data" / "system" / "audit" / "usage.jsonl"
    assert audit_path.exists()
    audit_text = audit_path.read_text(encoding="utf-8")
    assert audit_text.strip()
    for line in audit_text.splitlines():
        _assert_no_secrets(json.loads(line))
    for row in issued:
        assert row["code"] not in audit_text
        assert hashlib.sha256(row["code"].encode()).hexdigest() not in audit_text
        normalized = row["code"].replace("-", "").upper()
        assert hashlib.sha256(normalized.encode()).hexdigest() not in audit_text
        assert row["code"] not in json.dumps(full)


@pytest.mark.parametrize("actor", ["anonymous", "user"])
@pytest.mark.parametrize(
    "method,path",
    [("get", "/invites"), ("post", "/invites"), ("post", "/invites/missing/revoke")],
)
def test_invite_management_requires_real_admin_dependency(
    invite_client, invite_admin, actor, method, path
):
    from deeptutor.multi_user.identity import save_user

    headers = {}
    if actor == "user":
        record = save_user("student", "$2b$12$placeholder")
        headers = _headers("student", record)
    request = {"headers": headers}
    if path == "/invites" and method == "post":
        request["json"] = {}
    response = getattr(invite_client, method)(f"/api/auth{path}", **request)
    assert response.status_code == (401 if actor == "anonymous" else 403)


@pytest.mark.parametrize(
    "payload",
    [
        {"batch_count": 0},
        {"batch_count": 101},
        {"max_uses": 0},
        {"max_uses": 1001},
        {"expires_in_days": 0},
        {"expires_in_days": 366},
        {"note": "n" * 201},
    ],
)
def test_invite_creation_rejects_out_of_range_values(invite_client, invite_admin, payload):
    response = invite_client.post("/api/auth/invites", headers=invite_admin[0], json=payload)
    assert response.status_code == 422
    assert _list(invite_client, invite_admin) == {"items": [], "total": 0}


def test_invite_creation_accepts_bounds_and_unlimited_expiry(invite_client, invite_admin):
    first = _issue(
        invite_client, invite_admin, batch_count=1, max_uses=1, expires_in_days=1, note=""
    )[0]
    assert datetime.fromisoformat(first["expires_at"]) - datetime.fromisoformat(
        first["created_at"]
    ) == timedelta(days=1)
    last = _issue(
        invite_client,
        invite_admin,
        batch_count=100,
        max_uses=1000,
        expires_in_days=365,
        note="n" * 200,
    )
    assert len(last) == 100
    assert all(row["max_uses"] == 1000 for row in last)
    assert all(row["note"] == "n" * 200 for row in last)
    assert datetime.fromisoformat(last[0]["expires_at"]) - datetime.fromisoformat(
        last[0]["created_at"]
    ) == timedelta(days=365)
    forever = _issue(invite_client, invite_admin, expires_in_days=None)[0]
    assert forever["expires_at"] is None


@pytest.mark.parametrize("query", [{"offset": -1}, {"limit": 0}])
def test_invite_listing_validates_pagination(invite_client, invite_admin, query):
    response = invite_client.get("/api/auth/invites", headers=invite_admin[0], params=query)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "ab", "password": PASSWORD},
        {"username": "bad/name", "password": PASSWORD},
        {"username": "student", "password": "short"},
    ],
)
def test_register_still_validates_username_and_password(invite_client, payload):
    response = invite_client.post("/api/auth/register", json=payload)
    assert response.status_code == 422


def test_admin_can_still_create_user_without_invite_and_cannot_overwrite(
    invite_client, invite_admin
):
    from deeptutor.multi_user.identity import get_user

    response = invite_client.post(
        "/api/auth/users",
        headers=invite_admin[0],
        json={"username": "managed", "password": PASSWORD, "preset": "custom"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "user"
    assert response.json()["preset"] == "custom"
    original = get_user("managed")
    duplicate = invite_client.post(
        "/api/auth/users",
        headers=invite_admin[0],
        json={"username": "managed", "password": "replacement-password", "preset": "standard"},
    )
    assert duplicate.status_code == 409
    assert get_user("managed") == original
    assert _list(invite_client, invite_admin) == {"items": [], "total": 0}


def test_bootstrap_overlay_blocks_admin_promotion_but_can_issue_invites(invite_client, monkeypatch):
    from deeptutor.services import auth as auth_service

    monkeypatch.setattr(auth_service, "AUTH_USERNAME", "configured-admin")
    monkeypatch.setattr(auth_service, "AUTH_PASSWORD_HASH", auth_service.hash_password(PASSWORD))
    assert invite_client.get("/api/auth/registration-status").json() == {
        "available": True,
        "is_first_user": False,
        "invite_required": True,
    }
    denied = _register(invite_client, "student")
    assert denied.status_code == 403
    assert denied.json() == INVALID_INVITE
    login = invite_client.post(
        "/api/auth/login", json={"username": "configured-admin", "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    issued = invite_client.post("/api/auth/invites", json={})
    assert issued.status_code == 201, issued.text
    code = issued.json()["invites"][0]["code"]
    response = _register(invite_client, "student", invite_code=code)
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "user"
    assert response.json()["is_first_user"] is False


@pytest.mark.parametrize("auth_enabled,pocketbase", [(False, False), (True, True)])
def test_unavailable_auth_modes_close_registration_and_invite_management(
    invite_client, invite_admin, monkeypatch, auth_enabled, pocketbase
):
    from deeptutor.api.routers import auth as auth_router

    # Exercise the route's mode gate with an already authenticated admin; the
    # built-in test token avoids any dependency on an external PocketBase.
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", auth_enabled)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", pocketbase)
    status = invite_client.get("/api/auth/registration-status")
    assert status.status_code == 200
    assert status.json() == {
        "available": False,
        "is_first_user": False,
        "invite_required": False,
    }
    registered = _register(invite_client)
    assert registered.status_code == 403
    for method, path, body in (
        ("get", "/invites", None),
        ("post", "/invites", {}),
        ("post", "/invites/missing/revoke", None),
    ):
        request = {"headers": invite_admin[0]}
        if body is not None:
            request["json"] = body
        response = getattr(invite_client, method)(f"/api/auth{path}", **request)
        assert response.status_code == 403, response.text


@pytest.mark.parametrize("endpoint", ["registration-status", "register"])
def test_corrupt_identity_store_returns_503_instead_of_reopening_bootstrap(
    invite_client, invite_root, endpoint
):
    users_file = invite_root / "data" / "system" / "auth" / "users.json"
    users_file.parent.mkdir(parents=True, exist_ok=True)
    users_file.write_text("{broken", encoding="utf-8")
    response = (
        invite_client.get("/api/auth/registration-status")
        if endpoint == "registration-status"
        else _register(invite_client)
    )
    assert response.status_code == 503, response.text
    assert users_file.read_text(encoding="utf-8") == "{broken"


@pytest.mark.parametrize("operation", ["register", "list", "create", "revoke"])
def test_corrupt_invite_store_returns_503_without_modifying_accounts(
    invite_client, invite_admin, invite_root, operation
):
    from deeptutor.multi_user.identity import get_user

    invites_file = invite_root / "data" / "system" / "auth" / "invites.json"
    invites_file.write_text("{broken", encoding="utf-8")
    if operation == "register":
        response = _register(invite_client, invite_code="WRNG-WRNG-WRNG")
    elif operation == "list":
        response = invite_client.get("/api/auth/invites", headers=invite_admin[0])
    elif operation == "create":
        response = invite_client.post("/api/auth/invites", headers=invite_admin[0], json={})
    else:
        response = invite_client.post("/api/auth/invites/missing/revoke", headers=invite_admin[0])
    assert response.status_code == 503, response.text
    assert invites_file.read_text(encoding="utf-8") == "{broken"
    assert get_user("new-user") is None


def test_registration_rate_limit_expires_and_ignores_spoofed_forwarding_headers(
    invite_client, invite_admin, invite_rate_clock
):
    from deeptutor.multi_user.registration_limits import MAX_FAILURES, WINDOW_SECONDS

    issued = _issue(invite_client, invite_admin)[0]
    for attempt in range(MAX_FAILURES):
        response = invite_client.post(
            "/api/auth/register",
            headers={"X-Forwarded-For": f"198.51.100.{attempt + 1}"},
            json={"username": "student", "password": PASSWORD, "invite_code": "wrong-code"},
        )
        assert response.status_code == (429 if attempt == MAX_FAILURES - 1 else 403)
    blocked = _register(invite_client, "student", invite_code=issued["code"])
    assert blocked.status_code == 429
    assert blocked.json()["error_code"] == "registration_rate_limited"
    assert int(blocked.headers["retry-after"]) == WINDOW_SECONDS
    assert _list(invite_client, invite_admin)["items"][0]["used_count"] == 0
    invite_rate_clock[0] += WINDOW_SECONDS
    accepted = _register(invite_client, "student", invite_code=issued["code"])
    assert accepted.status_code == 201, accepted.text


def test_successful_registration_clears_previous_failures(invite_client, invite_admin):
    from deeptutor.multi_user.registration_limits import MAX_FAILURES

    issued = _issue(invite_client, invite_admin, max_uses=2)[0]
    for _ in range(MAX_FAILURES - 1):
        assert _register(invite_client, invite_code="wrong-code").status_code == 403
    accepted = _register(invite_client, "first-student", invite_code=issued["code"])
    assert accepted.status_code == 201, accepted.text
    assert _register(invite_client, invite_code="wrong-code").status_code == 403
    accepted = _register(invite_client, "second-student", invite_code=issued["code"])
    assert accepted.status_code == 201, accepted.text


@pytest.mark.parametrize("store", ["users.json", "registration-journal.json"])
@pytest.mark.parametrize("operation", ["list", "create", "revoke"])
def test_protected_invite_apis_fail_closed_on_broken_identity_or_journal(
    invite_client, invite_admin, invite_root, store, operation
):
    path = invite_root / "data/system/auth" / store
    path.write_text("{broken", encoding="utf-8")
    if operation == "list":
        result = invite_client.get("/api/auth/invites", headers=invite_admin[0])
    elif operation == "create":
        result = invite_client.post("/api/auth/invites", headers=invite_admin[0], json={})
    else:
        result = invite_client.post("/api/auth/invites/missing/revoke", headers=invite_admin[0])
    assert result.status_code == 503, result.text
    assert "Account storage" in result.json()["detail"]
    assert path.read_text() == "{broken"


@pytest.mark.parametrize("mutation", ["disable", "demote", "password"])
def test_admin_creation_rechecks_authority_after_password_hashing(
    invite_client, invite_admin, monkeypatch, mutation
):
    from deeptutor.api.routers import auth as router
    from deeptutor.multi_user import identity

    def hash_after_revocation(_password):
        if mutation == "disable":
            identity.set_disabled("admin", True)
        elif mutation == "demote":
            identity.set_role("admin", "user")
        else:
            identity.set_password("admin", "$2b$12$changed-placeholder")
        return "$2b$12$new-placeholder"

    monkeypatch.setattr(router, "hash_password", hash_after_revocation)
    result = invite_client.post(
        "/api/auth/users",
        headers=invite_admin[0],
        json={"username": "student", "password": PASSWORD},
    )
    assert result.status_code == 401, result.text
    assert identity.get_user("student") is None


def test_encoded_register_path_cannot_bypass_peer_cooldown(invite_client, invite_admin):
    for attempt in range(10):
        result = invite_client.post(
            "/api/auth/%72egister",
            headers={"X-Forwarded-For": f"192.0.2.{attempt + 1}"},
            json={"username": "student", "password": PASSWORD, "invite_code": "wrong"},
        )
    assert result.status_code == 429
