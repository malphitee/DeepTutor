"""Abuse control identifies actual peers, never arbitrary browser headers."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from deeptutor.multi_user import registration_limits as limits


def test_tenth_failure_locks_for_full_window_and_success_clears():
    now = [100.0]
    limiter = limits.RegistrationLimiter(clock=lambda: now[0])
    for _ in range(9):
        assert limiter.failure("alice") == 0
    now[0] += 800
    assert limiter.failure("alice") == 900
    assert limiter.check("bob") == 0
    now[0] += 101
    assert limiter.check("alice") == 799
    limiter.success("alice")
    assert limiter.check("alice") == 0


def test_window_expiry_and_cooldown_expiry():
    now = [0.0]
    limiter = limits.RegistrationLimiter(clock=lambda: now[0])
    for _ in range(9):
        limiter.failure("alice")
    now[0] = 900
    assert limiter.failure("alice") == 0
    for _ in range(9):
        limiter.failure("alice")
    assert limiter.check("alice") == 900
    now[0] = 1800
    assert limiter.check("alice") == 0


def test_full_table_cannot_evict_locked_peers(monkeypatch):
    monkeypatch.setattr(limits, "MAX_BUCKETS", 1)
    now = [0.0]
    limiter = limits.RegistrationLimiter(clock=lambda: now[0])
    for _ in range(10):
        limiter.failure("alice")
    assert limiter.check("new") == 60
    assert limiter.failure("new") == 60
    assert limiter.check("alice") == 900
    now[0] += 900
    assert limiter.check("new") == 0


def test_backend_never_trusts_unsigned_xff_even_from_loopback():
    assert (
        limits.registration_peer("::ffff:127.0.0.1", {"x-forwarded-for": "192.0.2.1"}, b"{}")
        == "127.0.0.1"
    )


def _signed(secret, ip, timestamp, body):
    message = f"{timestamp}\nPOST\n/api/auth/register\n{ip}\n{hashlib.sha256(body).hexdigest()}"
    return {
        "x-deeptutor-registration-ip": ip,
        "x-deeptutor-registration-time": str(timestamp),
        "x-deeptutor-registration-signature": hmac.new(
            secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest(),
    }


def test_signed_next_proof_preserves_distinct_clients(mu_isolated_root):
    secret = limits.proxy_secret(create=True)
    body = b'{"username":"alice"}'
    for client in ["192.0.2.1", "192.0.2.2", "2001:db8::2"]:
        headers = _signed(secret, client, 1234, body)
        headers["x-forwarded-for"] = "203.0.113.42"
        assert limits.registration_peer("127.0.0.1", headers, body, now=1234) == client


@pytest.mark.parametrize("mutation", ["body", "ip", "signature", "unicode", "stale", "future"])
def test_forged_or_stale_peer_proofs_do_not_change_bucket(mu_isolated_root, mutation):
    secret = limits.proxy_secret(create=True)
    body = b'{"username":"alice"}'
    headers = _signed(secret, "192.0.2.1", 1234, body)
    now = 1234
    if mutation == "body":
        body += b" "
    elif mutation == "ip":
        headers["x-deeptutor-registration-ip"] = "192.0.2.2"
    elif mutation == "signature":
        headers["x-deeptutor-registration-signature"] = "fake"
    elif mutation == "unicode":
        headers["x-deeptutor-registration-signature"] = "ÿ" * 64
    elif mutation == "stale":
        now += 61
    else:
        now -= 61
    assert limits.registration_peer("127.0.0.1", headers, body, now=now) == "127.0.0.1"


def test_proxy_secret_is_separate_stable_and_private(mu_isolated_root):
    from deeptutor.multi_user import identity

    first = limits.proxy_secret(create=True)
    assert limits.proxy_secret(create=True) == first
    path = identity.AUTH_DIR / limits.PROXY_SECRET_FILENAME
    assert path.stat().st_mode & 0o777 == 0o600
    assert not identity.SECRET_FILE.exists()


def test_bad_proxy_secret_is_not_silently_replaced(mu_isolated_root):
    from deeptutor.multi_user import identity

    limits.proxy_secret(create=True)
    path = identity.AUTH_DIR / limits.PROXY_SECRET_FILENAME
    path.write_text("broken")
    with pytest.raises(identity.IdentityStoreError):
        limits.proxy_secret(create=True)
    assert path.read_text() == "broken"


def test_non_ascii_proxy_secret_fails_as_storage_error(mu_isolated_root):
    from deeptutor.multi_user import identity

    limits.proxy_secret(create=True)
    path = identity.AUTH_DIR / limits.PROXY_SECRET_FILENAME
    path.write_text("损坏", encoding="utf-8")
    with pytest.raises(identity.IdentityStoreError):
        limits.proxy_secret(create=True)
