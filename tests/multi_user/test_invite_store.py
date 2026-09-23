"""Admission invariants, concurrency, and recovery of committed registrations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys
import threading

import pytest

from deeptutor.multi_user import auth_store, identity, invites

PASSWORD_HASH = "$2b$12$test-only-password-hash"


@pytest.fixture(scope="module")
def valid_password_hash():
    from deeptutor.services.auth import hash_password

    return hash_password("valid-regression-password")


@pytest.fixture
def issuer(mu_isolated_root):
    return identity.create_user("admin", PASSWORD_HASH)


def _issue(issuer, **kwargs):
    return invites.create_invites(issuer["id"], **kwargs)[0]


def test_code_defaults_private_storage_and_public_views(issuer):
    issued = _issue(issuer)
    assert len(issued["code"]) == 14
    assert issued["code_hint"] == issued["code"][-4:]
    assert issued["max_uses"] == 1
    assert issued["used_count"] == 0
    assert issued["status"] == "active"
    assert issued["revoked_by"] is None
    assert datetime.fromisoformat(issued["expires_at"]) - datetime.fromisoformat(
        issued["created_at"]
    ) == timedelta(days=7)
    raw = (identity.AUTH_DIR / "invites.json").read_text()
    assert issued["code"] not in raw
    assert issued["code"].replace("-", "") not in raw
    assert "code_hash" in raw
    listed = invites.list_invites()
    assert listed["total"] == 1
    assert "code" not in listed["items"][0]
    assert "code_hash" not in listed["items"][0]
    if os.name != "nt":
        assert (identity.AUTH_DIR.stat().st_mode & 0o777) == 0o700
        assert ((identity.AUTH_DIR / "invites.json").stat().st_mode & 0o777) == 0o600
        assert (identity.USERS_FILE.stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_uses": 0},
        {"max_uses": 1001},
        {"max_uses": True},
        {"batch_count": 0},
        {"batch_count": 101},
        {"batch_count": 1.5},
        {"expires_in_days": 0},
        {"expires_in_days": 366},
        {"expires_in_days": True},
        {"note": "n" * 201},
    ],
)
def test_create_validation_has_no_write(issuer, kwargs):
    with pytest.raises(ValueError):
        invites.create_invites(issuer["id"], **kwargs)
    assert not (identity.AUTH_DIR / "invites.json").exists()


def test_normalization_and_permanent_invite(issuer, monkeypatch):
    characters = iter("0123456789AB")
    monkeypatch.setattr(invites.secrets, "choice", lambda alphabet: next(characters))
    issued = _issue(issuer, expires_in_days=None)
    assert issued["expires_at"] is None
    assert invites.normalize_code(" OI23-4567-89ab ") == "0123456789AB"
    assert invites.normalize_code("ol23456789ab") == "0123456789AB"
    created = invites.register_user("alice", PASSWORD_HASH, "ol23456789ab")
    assert created["role"] == "user"
    assert created["preset"] == "standard"
    assert created["disabled"] is False
    assert created["is_first_user"] is False
    assert invites.list_invites()["items"][0]["status"] == "exhausted"


def test_bootstrap_is_atomic_and_code_free(mu_isolated_root):
    barrier = threading.Barrier(8)

    def register(index):
        barrier.wait()
        try:
            return invites.register_user(f"user{index}", PASSWORD_HASH)
        except invites.InvalidInviteError:
            return None

    assert identity.is_bootstrap_available() is True
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(register, range(8)))
    successes = [result for result in results if result]
    assert len(successes) == 1
    assert successes[0]["role"] == "admin"
    assert successes[0]["is_first_user"] is True
    assert len(identity.load_users()) == 1
    assert identity.is_bootstrap_available() is False


def test_configured_admin_requires_code_and_name_stays_reserved(mu_isolated_root, monkeypatch):
    from deeptutor.services import auth

    monkeypatch.setattr(auth, "AUTH_USERNAME", "operator")
    monkeypatch.setattr(auth, "AUTH_PASSWORD_HASH", PASSWORD_HASH)
    assert identity.is_bootstrap_available() is False
    with pytest.raises(invites.InvalidInviteError):
        invites.register_user("alice", PASSWORD_HASH)
    code = invites.create_invites("env-admin", max_uses=2)[0]
    with pytest.raises(identity.UserAlreadyExistsError):
        invites.register_user("operator", PASSWORD_HASH, code["code"])
    with pytest.raises(identity.UserAlreadyExistsError):
        identity.create_user("operator", PASSWORD_HASH)
    created = invites.register_user("alice", PASSWORD_HASH, code["code"])
    assert created["role"] == "user"
    assert "operator" not in identity.load_users()
    assert "operator" in identity.load_users("operator", PASSWORD_HASH)
    assert invites.list_invites()["items"][0]["used_count"] == 1


def test_create_only_cannot_overwrite_existing_user(issuer):
    before = identity.get_user("admin")
    with pytest.raises(identity.UserAlreadyExistsError):
        identity.create_user("admin", "different-hash")
    assert identity.get_user("admin") == before
    # Existing save_user callers explicitly retain their update semantics.
    identity.save_user("admin", "different-hash", role="admin")
    assert identity.get_user("admin")["hash"] == "different-hash"


def test_admin_create_and_invite_registration_share_uniqueness_lock(issuer):
    code = _issue(issuer)
    barrier = threading.Barrier(2)

    def create(source):
        barrier.wait()
        try:
            if source == "admin":
                return identity.create_user("alice", "admin-chosen-hash")
            return invites.register_user("alice", "self-chosen-hash", code["code"])
        except identity.UserAlreadyExistsError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, ("admin", "invite")))
    assert sum(result is not None for result in results) == 1
    stored = identity.get_user("alice")
    assert stored["hash"] in {"admin-chosen-hash", "self-chosen-hash"}
    assert invites.list_invites()["items"][0]["used_count"] == (
        1 if stored["hash"] == "self-chosen-hash" else 0
    )


@pytest.mark.parametrize("max_uses", [1, 3])
def test_parallel_redemptions_never_exceed_capacity(issuer, max_uses):
    code = _issue(issuer, max_uses=max_uses)
    barrier = threading.Barrier(10)

    def register(index):
        barrier.wait()
        try:
            return invites.register_user(f"alice{index}", PASSWORD_HASH, code["code"])
        except invites.InvalidInviteError:
            return None

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(register, range(10)))
    assert sum(result is not None for result in results) == max_uses
    record = invites.list_invites()["items"][0]
    assert record["used_count"] == len(record["redemptions"]) == max_uses
    assert len(identity.load_users()) == max_uses + 1


def test_conflict_does_not_consume_and_deletion_does_not_refund(issuer):
    code = _issue(issuer, max_uses=2)
    with pytest.raises(identity.UserAlreadyExistsError):
        invites.register_user("admin", PASSWORD_HASH, code["code"])
    assert invites.list_invites()["items"][0]["used_count"] == 0
    created = invites.register_user("alice", PASSWORD_HASH, code["code"])
    with pytest.raises(identity.UserAlreadyExistsError):
        invites.register_user("alice", "replacement-hash", code["code"])
    assert identity.get_user("alice")["hash"] == PASSWORD_HASH
    assert identity.delete_user("alice") is True
    record = invites.list_invites()["items"][0]
    assert record["used_count"] == 1
    assert record["redemptions"][0]["user_id"] == created["id"]
    recreated = invites.register_user("alice", PASSWORD_HASH, code["code"])
    assert recreated["id"] != created["id"]
    assert invites.list_invites()["items"][0]["used_count"] == 2


def test_expiry_revocation_and_exhaustion_have_same_error(issuer, monkeypatch):
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(invites, "utc_now", lambda: now)
    expired = _issue(issuer, expires_in_days=1)
    revoked = _issue(issuer, expires_in_days=None)
    exhausted = _issue(issuer, expires_in_days=None)
    invites.register_user("alice", PASSWORD_HASH, exhausted["code"])
    first_revoke = invites.revoke_invite(revoked["id"], issuer["id"])
    second_revoke = invites.revoke_invite(revoked["id"], "another-admin")
    assert first_revoke == second_revoke
    assert invites.revoke_invite("absent", issuer["id"]) is None
    now += timedelta(days=1)
    messages = []
    for code in [expired["code"], revoked["code"], exhausted["code"], "", "Z" * 12]:
        with pytest.raises(invites.InvalidInviteError) as error:
            invites.register_user("bob", PASSWORD_HASH, code)
        messages.append(str(error.value))
    assert len(set(messages)) == 1


@pytest.mark.parametrize("bad_data", ["not json", "[]", '{"alice": null}', '{"alice": {}}'])
def test_damaged_users_never_reopen_bootstrap(mu_isolated_root, bad_data):
    identity.AUTH_DIR.mkdir(parents=True)
    identity.USERS_FILE.write_text(bad_data)
    with pytest.raises(identity.IdentityStoreError):
        identity.is_bootstrap_available()
    with pytest.raises(identity.IdentityStoreError):
        invites.register_user("newadmin", PASSWORD_HASH)
    assert identity.USERS_FILE.read_text() == bad_data


def test_unreadable_users_fail_closed(issuer, monkeypatch):
    original = type(identity.USERS_FILE).read_text

    def unreadable(path, *args, **kwargs):
        if path == identity.USERS_FILE:
            raise PermissionError("injected read failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(identity.USERS_FILE), "read_text", unreadable)
    with pytest.raises(identity.IdentityStoreError):
        identity.is_bootstrap_available()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("role", "usr"),
        ("role", ""),
        ("role", None),
        ("role", 1),
        ("role", ["admin"]),
        ("preset", "standrd"),
        ("preset", ""),
        ("preset", None),
        ("preset", 1),
        ("preset", {"preset": "standard"}),
    ],
)
def test_invalid_explicit_role_or_preset_never_migrates_to_privileged_defaults(
    mu_isolated_root, valid_password_hash, field, invalid_value
):
    identity.AUTH_DIR.mkdir(parents=True)
    original = json.dumps(
        {
            "alice": {
                "hash": valid_password_hash,
                "role": "user",
                "preset": "standard",
                field: invalid_value,
            }
        }
    )
    identity.USERS_FILE.write_text(original)
    with pytest.raises(identity.IdentityStoreError):
        identity.load_users()
    with pytest.raises(identity.IdentityStoreError):
        identity.is_bootstrap_available()
    with pytest.raises(identity.IdentityStoreError):
        invites.register_user("newadmin", valid_password_hash)
    assert identity.USERS_FILE.read_text() == original
    assert not (identity.AUTH_DIR / auth_store.JOURNAL_FILENAME).exists()


def test_missing_legacy_role_and_preset_still_migrate(mu_isolated_root, valid_password_hash):
    identity.AUTH_DIR.mkdir(parents=True)
    identity.USERS_FILE.write_text(
        json.dumps({"alice": {"hash": valid_password_hash}, "bob": {"hash": valid_password_hash}})
    )
    users = identity.load_users()
    assert users["alice"]["role"] == "admin"
    assert users["bob"]["role"] == "user"
    assert users["alice"]["preset"] == users["bob"]["preset"] == "standard"
    assert json.loads(identity.USERS_FILE.read_text()) == users


def test_unreadable_bootstrap_configuration_never_opens_registration(mu_isolated_root, monkeypatch):
    from deeptutor.services import auth

    class UnreadableCredential:
        def __str__(self):
            raise OSError("injected bootstrap configuration failure")

    monkeypatch.setattr(auth, "AUTH_USERNAME", UnreadableCredential())
    with pytest.raises(identity.IdentityStoreError, match="bootstrap"):
        identity.is_bootstrap_available()
    with pytest.raises(identity.IdentityStoreError, match="bootstrap"):
        invites.register_user("newadmin", PASSWORD_HASH)
    with pytest.raises(identity.IdentityStoreError, match="bootstrap"):
        identity.create_user("newadmin", PASSWORD_HASH)
    assert not identity.USERS_FILE.exists()
    assert not (identity.AUTH_DIR / auth_store.JOURNAL_FILENAME).exists()


@pytest.mark.parametrize("bad_data", ["not json", "[]", '{"inv_bad": {}}'])
def test_damaged_invites_are_not_replaced(issuer, bad_data):
    invite_path = identity.AUTH_DIR / "invites.json"
    invite_path.write_text(bad_data)
    with pytest.raises(identity.IdentityStoreError):
        invites.create_invites(issuer["id"])
    with pytest.raises(identity.IdentityStoreError):
        invites.register_user("alice", PASSWORD_HASH, "A" * 12)
    assert invite_path.read_text() == bad_data


@pytest.mark.parametrize("failure_filename", ["users.json", "invites.json"])
def test_journal_recovers_after_interruption_between_store_writes(
    issuer, monkeypatch, failure_filename
):
    code = _issue(issuer)
    write = auth_store.write_private_json

    class ProcessInterrupted(BaseException):
        pass

    def crash(path, data):
        if path.name == failure_filename:
            raise ProcessInterrupted()
        return write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(auth_store, "write_private_json", crash)
        with pytest.raises(ProcessInterrupted):
            invites.register_user("alice", PASSWORD_HASH, code["code"])
    journal = identity.AUTH_DIR / auth_store.JOURNAL_FILENAME
    assert journal.exists()
    assert code["code"] not in journal.read_text()
    if os.name != "nt":
        assert (journal.stat().st_mode & 0o777) == 0o600
    # Reading the identity store performs recovery before any state is exposed.
    recovered = identity.get_user("alice")
    assert recovered is not None
    assert not journal.exists()
    record = invites.list_invites()["items"][0]
    assert record["used_count"] == 1
    assert record["redemptions"][0]["user_id"] == recovered["id"]
    with pytest.raises(invites.InvalidInviteError):
        invites.register_user("alice", PASSWORD_HASH, code["code"])
    assert invites.list_invites()["items"][0]["used_count"] == 1
    # A later identity mutation must not be undone by a journal replay.
    identity.set_password("alice", "new-password-hash")
    assert identity.get_user("alice")["hash"] == "new-password-hash"


def test_failure_before_journal_commit_changes_neither_store(issuer, monkeypatch):
    code = _issue(issuer)
    original_users = identity.USERS_FILE.read_text()

    def fail_before_commit(path, data):
        raise identity.IdentityStoreError("injected storage failure")

    with monkeypatch.context() as patch:
        patch.setattr(auth_store, "write_private_json", fail_before_commit)
        with pytest.raises(identity.IdentityStoreError):
            invites.register_user("alice", PASSWORD_HASH, code["code"])
    assert identity.USERS_FILE.read_text() == original_users
    assert invites.list_invites()["items"][0]["used_count"] == 0
    assert not (identity.AUTH_DIR / auth_store.JOURNAL_FILENAME).exists()


def test_failed_recovery_blocks_other_identity_mutations(issuer, monkeypatch):
    code = _issue(issuer)
    write = auth_store.write_private_json

    def fail_invites(path, data):
        if path.name == "invites.json":
            raise identity.IdentityStoreError("injected storage failure")
        return write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(auth_store, "write_private_json", fail_invites)
        with pytest.raises(identity.IdentityStoreError):
            invites.register_user("alice", PASSWORD_HASH, code["code"])
        with pytest.raises(identity.IdentityStoreError):
            identity.create_user("bob", PASSWORD_HASH)
        with pytest.raises(identity.IdentityStoreError):
            identity.set_disabled("admin", True)
        with pytest.raises(identity.IdentityStoreError):
            invites.list_invites()
    assert "bob" not in identity.load_users()
    assert identity.get_user("admin")["disabled"] is False
    assert identity.get_user("alice") is not None
    assert invites.list_invites()["items"][0]["used_count"] == 1


def test_corrupt_journal_fails_closed(issuer):
    original = identity.USERS_FILE.read_text()
    journal = identity.AUTH_DIR / auth_store.JOURNAL_FILENAME
    journal.write_text(json.dumps({"version": 999, "users": {}, "invites": {}}))
    with pytest.raises(identity.IdentityStoreError):
        identity.load_users()
    with pytest.raises(identity.IdentityStoreError):
        invites.list_invites()
    assert identity.USERS_FILE.read_text() == original


def test_committed_journal_recovers_in_a_fresh_process(issuer, monkeypatch):
    code = _issue(issuer)
    write = auth_store.write_private_json

    def fail_users(path, data):
        if path.name == "users.json":
            raise identity.IdentityStoreError("injected storage failure")
        return write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(auth_store, "write_private_json", fail_users)
        with pytest.raises(identity.IdentityStoreError):
            invites.register_user("alice", PASSWORD_HASH, code["code"])
    # A fresh interpreter has neither the originating lock nor any cached state.
    script = """
from pathlib import Path
import sys
from deeptutor.multi_user.auth_store import transaction
with transaction(Path(sys.argv[1]), Path(sys.argv[2])):
    pass
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(identity.AUTH_DIR), str(identity.USERS_FILE)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert not (identity.AUTH_DIR / auth_store.JOURNAL_FILENAME).exists()
    assert identity.get_user("alice") is not None
    assert invites.list_invites()["items"][0]["used_count"] == 1


def test_journal_cleanup_failure_rolls_forward_only_once(issuer, monkeypatch):
    code = _issue(issuer, max_uses=2)
    journal = identity.AUTH_DIR / auth_store.JOURNAL_FILENAME
    unlink = type(journal).unlink

    def fail_cleanup(path, *args, **kwargs):
        if path == journal:
            raise PermissionError("injected cleanup failure")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(type(journal), "unlink", fail_cleanup)
        with pytest.raises(identity.IdentityStoreError):
            invites.register_user("alice", PASSWORD_HASH, code["code"])
        assert journal.exists()
    # The next mutation must complete recovery before applying its own update.
    identity.set_password("alice", "updated-password-hash")
    assert not journal.exists()
    assert identity.get_user("alice")["hash"] == "updated-password-hash"
    assert invites.list_invites()["items"][0]["used_count"] == 1


def test_journal_checksum_blocks_corrupt_snapshot_replay(issuer, monkeypatch):
    code = _issue(issuer)
    write = auth_store.write_private_json
    before = identity.USERS_FILE.read_text()

    def fail_users(path, data):
        if path.name == "users.json":
            raise identity.IdentityStoreError("injected storage failure")
        return write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(auth_store, "write_private_json", fail_users)
        with pytest.raises(identity.IdentityStoreError):
            invites.register_user("alice", PASSWORD_HASH, code["code"])
    journal = identity.AUTH_DIR / auth_store.JOURNAL_FILENAME
    content = json.loads(journal.read_text())
    content["users"]["admin"]["hash"] = "corrupted-hash"
    journal.write_text(json.dumps(content))
    with pytest.raises(identity.IdentityStoreError, match="checksum"):
        identity.load_users()
    assert identity.USERS_FILE.read_text() == before


def test_preflight_is_advisory_and_final_redemption_rechecks(issuer):
    code = _issue(issuer)
    assert invites.validate_registration_invite(code["code"]) is False
    invites.revoke_invite(code["id"], issuer["id"])
    with pytest.raises(invites.InvalidInviteError):
        invites.register_user("alice", PASSWORD_HASH, code["code"])
    assert identity.get_user("alice") is None


def test_revoke_and_redeem_have_one_serial_order(issuer):
    code = _issue(issuer)
    barrier = threading.Barrier(2)

    def operation(kind):
        barrier.wait()
        if kind == "revoke":
            return invites.revoke_invite(code["id"], issuer["id"])
        try:
            return invites.register_user("alice", PASSWORD_HASH, code["code"])
        except invites.InvalidInviteError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(operation, ("revoke", "register")))
    record = invites.list_invites()["items"][0]
    assert record["status"] == "revoked"
    assert record["used_count"] == (1 if identity.get_user("alice") else 0)
    with pytest.raises(invites.InvalidInviteError):
        invites.register_user("bob", PASSWORD_HASH, code["code"])


def test_pagination_and_batched_creation(issuer):
    issued = invites.create_invites(issuer["id"], batch_count=5)
    assert len({item["code"] for item in issued}) == 5
    assert invites.list_invites(offset=0, limit=2)["total"] == 5
    pages = [invites.list_invites(offset=index, limit=2)["items"] for index in (0, 2, 4)]
    assert len({item["id"] for page in pages for item in page}) == 5
