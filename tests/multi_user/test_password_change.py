"""Self-service password changes preserve identity and revoke stale credentials."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.multi_user import auth_store, identity
from deeptutor.services import auth

PASSWORD = "original-password-123"
NEW_PASSWORD = "replacement-password-456"
URL = "/api/auth/profile/password"


@pytest.fixture(scope="module")
def password_hash():
    return auth.hash_password(PASSWORD)


@pytest.fixture
def password_client(mu_isolated_root, monkeypatch, password_hash):
    from deeptutor.api.routers import auth as router
    from deeptutor.multi_user.request_scope import UserScopeMiddleware

    for module in (auth, router):
        monkeypatch.setattr(module, "AUTH_ENABLED", True)
        monkeypatch.setattr(module, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth, "AUTH_SECRET", "password-change-test-secret")
    monkeypatch.setattr(router, "_SECURE", False)
    monkeypatch.setattr(router, "_SAMESITE", "lax")
    monkeypatch.setattr(router, "terminate_revoked_user", AsyncMock())
    identity.create_user("admin", password_hash, role="admin")
    identity.create_user("learner", password_hash, role="user", preset="learner")
    identity.set_avatar("learner", "icon:leaf:teal")
    identity.set_learner_profile("learner", {"age": 10})
    app = FastAPI()
    app.add_middleware(UserScopeMiddleware)
    app.include_router(router.router, prefix="/api/auth")
    with TestClient(app) as client:
        yield client


def _token(username="learner"):
    record = identity.get_user(username)
    return auth.create_token(username, record["role"], record["id"])


def _headers(username="learner"):
    return {"Authorization": f"Bearer {_token(username)}"}


def _change(client, *, headers=None, current=PASSWORD, new=NEW_PASSWORD, **extra):
    return client.post(
        URL,
        headers=headers,
        json={"current_password": current, "new_password": new, **extra},
    )


@pytest.mark.parametrize("username,secure", [("admin", False), ("learner", True)])
def test_changes_own_password_preserves_account_and_requires_login(
    password_client, monkeypatch, username, secure
):
    from deeptutor.api.routers import auth as router

    monkeypatch.setattr(router, "_SECURE", secure)
    monkeypatch.setattr(router, "_SAMESITE", "none" if secure else "lax")
    old_token = _token(username)
    before = identity.load_users()
    response = _change(
        password_client,
        headers={"Authorization": f"Bearer {old_token}"},
        username="someone-else",  # The request cannot select another account.
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "reauthenticate": True}
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"].lower()
    assert "max-age=0" in cookie and "httponly" in cookie
    assert ("secure" in cookie) is secure
    assert f"samesite={'none' if secure else 'lax'}" in cookie
    after = identity.load_users()
    expected = dict(before[username])
    expected["hash"] = after[username]["hash"]
    expected["token_version"] += 1
    assert after[username] == expected
    other = "learner" if username == "admin" else "admin"
    assert after[other] == before[other]
    assert auth.verify_password(NEW_PASSWORD, after[username]["hash"])
    assert auth.decode_token(old_token) is None
    assert auth.authenticate(username, PASSWORD) is None
    assert auth.authenticate(username, NEW_PASSWORD) is not None
    assert (
        password_client.get(
            "/api/auth/profile", headers={"Authorization": f"Bearer {old_token}"}
        ).status_code
        == 401
    )
    assert (
        password_client.post(
            "/api/auth/login", json={"username": username, "password": NEW_PASSWORD}
        ).status_code
        == 200
    )
    router.terminate_revoked_user.assert_awaited_once_with(
        before[username]["id"], previous_role=before[username]["role"]
    )


def test_wrong_password_and_missing_session_do_not_change_account(password_client):
    before = identity.load_users()
    token = _token()
    assert _change(password_client).status_code == 401
    response = _change(
        password_client, headers={"Authorization": f"Bearer {token}"}, current="incorrect"
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "current_password_incorrect"
    assert identity.load_users() == before
    assert auth.decode_token(token) is not None


@pytest.mark.parametrize("new", ["short", "a" * 73, "密" * 25, "🔐" * 19])
def test_new_password_policy_matches_registration(password_client, new):
    from deeptutor.api.routers.auth import RegisterRequest

    before = identity.load_users()
    with pytest.raises(ValueError):
        RegisterRequest(username="example", password=new)
    response = _change(password_client, headers=_headers(), new=new)
    assert response.status_code == 422
    assert identity.load_users() == before


@pytest.mark.parametrize("new", ["密" * 24, "🔐" * 8])
def test_unicode_passwords_at_valid_character_and_byte_boundaries(password_client, new):
    response = _change(password_client, headers=_headers(), new=new)
    assert response.status_code == 200
    assert auth.authenticate("learner", new) is not None


def test_cookie_authenticated_password_change(password_client):
    token = _token()
    password_client.cookies.set("dt_token", token)
    assert _change(password_client).status_code == 200
    assert auth.decode_token(token) is None


def test_password_change_revokes_device_session_token(password_client):
    from deeptutor.multi_user.device_credentials import issue_device_credential

    _, pairing_code, pin = issue_device_credential(
        user_id=identity.get_user("learner")["id"],
        device_name="Learner tablet",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        daily_limit_minutes=30,
    )
    response = password_client.post(
        "/api/auth/device-login", json={"pairing_code": pairing_code, "pin": pin}
    )
    assert response.status_code == 200
    old_device_token = password_client.cookies.get("dt_token")
    assert auth.decode_token(old_device_token).device_credential_id
    assert _change(password_client).status_code == 200
    assert auth.decode_token(old_device_token) is None
    assert (
        password_client.post(
            "/api/auth/device/heartbeat",
            headers={"Authorization": f"Bearer {old_device_token}"},
        ).status_code
        == 401
    )
    # Pairing credentials are independent of passwords; a new device login
    # receives the new account version, without reviving the previous token.
    assert (
        password_client.post(
            "/api/auth/device-login", json={"pairing_code": pairing_code, "pin": pin}
        ).status_code
        == 200
    )
    assert auth.decode_token(password_client.cookies.get("dt_token")) is not None


@pytest.mark.parametrize("mode", ["disabled", "pocketbase", "bootstrap"])
def test_unsupported_modes_cannot_write_passwords(
    password_client, monkeypatch, mode, password_hash
):
    from deeptutor.api.routers import auth as router

    headers = _headers()
    before = identity.load_users()
    if mode == "disabled":
        monkeypatch.setattr(router, "AUTH_ENABLED", False)
        monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    elif mode == "pocketbase":
        # Use a real local identity to exercise the route-level provider guard.
        monkeypatch.setattr(router, "POCKETBASE_ENABLED", True)
    else:
        monkeypatch.setattr(auth, "AUTH_USERNAME", "operator")
        monkeypatch.setattr(auth, "AUTH_PASSWORD_HASH", password_hash)
        headers = {"Authorization": f"Bearer {auth.create_token('operator', 'admin', 'env-admin')}"}
    response = _change(password_client, headers=headers)
    assert response.status_code == 400
    assert response.json()["error_code"] == "password_change_unsupported"
    assert identity.load_users() == before
    if mode != "disabled":
        profile = password_client.get("/api/auth/profile", headers=headers).json()
        assert profile["password_change_supported"] is False
        assert profile["password_change_unavailable_reason"] == (
            "external_auth" if mode == "pocketbase" else "environment_admin"
        )


def test_profile_advertises_password_change_for_both_roles(password_client):
    for username in ("admin", "learner"):
        profile = password_client.get("/api/auth/profile", headers=_headers(username)).json()
        assert profile["password_change_supported"] is True
        assert profile["password_change_unavailable_reason"] is None


def test_parallel_password_changes_allow_only_one_winner(password_client, monkeypatch):
    current = auth.decode_token(_token())
    barrier = threading.Barrier(2)
    real_hash = auth.hash_password

    def overlapping_hash(password):
        hashed = real_hash(password)
        barrier.wait(timeout=10)
        return hashed

    monkeypatch.setattr(auth, "hash_password", overlapping_hash)

    def change(new):
        try:
            auth.change_password(current, PASSWORD, new)
            return new
        except auth.PasswordChangeError as exc:
            assert exc.code == "session_expired"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, (NEW_PASSWORD, NEW_PASSWORD + "other")))
    winners = [result for result in results if result]
    assert len(winners) == 1
    assert identity.get_user("learner")["token_version"] == 1
    assert auth.authenticate("learner", winners[0]) is not None


@pytest.mark.parametrize("mutation", ["disable", "delete_recreate", "role", "password"])
def test_account_changes_during_hashing_reject_stale_authorization(
    password_client, monkeypatch, mutation, password_hash
):
    headers = _headers()

    def racing_hash(_):
        if mutation == "disable":
            identity.set_disabled("learner", True)
        elif mutation == "delete_recreate":
            identity.delete_user("learner")
            identity.create_user("learner", password_hash)
        elif mutation == "role":
            identity.set_role("learner", "admin")
        else:
            identity.set_password("learner", password_hash)
        return "$2b$12$must-not-be-written"

    monkeypatch.setattr(auth, "hash_password", racing_hash)
    response = _change(password_client, headers=headers)
    assert response.status_code == 401
    assert response.json()["error_code"] == "session_expired"
    assert identity.get_user("learner")["hash"] == password_hash


def test_atomic_write_failure_keeps_existing_password_and_token(password_client, monkeypatch):
    before = identity.USERS_FILE.read_bytes()
    token = _token()

    def fail_replace(*_):
        raise OSError("simulated write failure")

    monkeypatch.setattr(auth_store.os, "replace", fail_replace)
    response = _change(password_client, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503
    assert response.json()["error_code"] == "account_storage_unavailable"
    assert identity.USERS_FILE.read_bytes() == before
    assert auth.decode_token(token) is not None


def test_password_change_signals_live_resources(password_client):
    from deeptutor.multi_user.revocation import register, unregister

    current = auth.decode_token(_token())

    async def scenario():
        revoked = asyncio.Event()
        handle = register(current.user_id, revoked.set)
        try:
            await asyncio.to_thread(auth.change_password, current, PASSWORD, NEW_PASSWORD)
            await asyncio.wait_for(revoked.wait(), timeout=1)
        finally:
            unregister(handle)

    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["password", "delete_recreate"])
def test_login_cannot_mint_new_version_after_old_password_was_verified(
    password_client, monkeypatch, mutation, password_hash
):
    from deeptutor.api.routers import auth as router

    real_authenticate = auth.authenticate

    def racing_authenticate(username, password):
        verified = real_authenticate(username, password)
        if mutation == "password":
            auth.change_password(verified, PASSWORD, NEW_PASSWORD)
        else:
            identity.delete_user(username)
            identity.create_user(username, password_hash)
        return verified

    monkeypatch.setattr(router, "authenticate", racing_authenticate)
    response = password_client.post(
        "/api/auth/login", json={"username": "learner", "password": PASSWORD}
    )
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    if mutation == "password":
        assert real_authenticate("learner", PASSWORD) is None
        assert real_authenticate("learner", NEW_PASSWORD) is not None
