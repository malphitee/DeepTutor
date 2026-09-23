"""Bounded registration throttling and authenticated frontend peer forwarding.

The Next Pages API bridge sees the actual socket peer, unlike App Router
Request objects. Its HMAC binds that address to one registration request;
untrusted browsers cannot choose their rate-limit bucket with forwarding headers.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
from ipaddress import ip_address
import math
import os
import secrets
import threading
import time
from typing import Callable, Mapping

from deeptutor.utils.secret_files import ensure_private_directory, ensure_private_file

MAX_FAILURES = 10
WINDOW_SECONDS = 15 * 60
MAX_BUCKETS = 10_000
PROXY_SIGNATURE_MAX_AGE = 60
PROXY_SECRET_FILENAME = "registration_proxy_secret"


@dataclass
class _Bucket:
    failures: int
    window_started: float
    locked_until: float = 0


class RegistrationLimiter:
    """Ten failed invitations in a 15-minute window lock an IP for 15 minutes.

    Successful registrations clear the bucket. Expired buckets are reclaimed;
    if the bounded table is full, new peers briefly fail closed rather than
    evicting a locked peer and allowing it to resume guessing.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def _prune(self, now: float) -> None:
        for key, bucket in list(self._buckets.items()):
            expiry = max(bucket.window_started + WINDOW_SECONDS, bucket.locked_until)
            if now >= expiry:
                del self._buckets[key]

    def check(self, peer: str) -> int:
        with self._lock:
            now = self._clock()
            self._prune(now)
            bucket = self._buckets.get(peer)
            if bucket is not None:
                return max(0, math.ceil(bucket.locked_until - now))
            return 60 if len(self._buckets) >= MAX_BUCKETS else 0

    def failure(self, peer: str) -> int:
        with self._lock:
            now = self._clock()
            self._prune(now)
            bucket = self._buckets.get(peer)
            if bucket is None:
                if len(self._buckets) >= MAX_BUCKETS:
                    return 60
                bucket = self._buckets[peer] = _Bucket(0, now)
            if bucket.locked_until > now:
                return math.ceil(bucket.locked_until - now)
            bucket.failures += 1
            if bucket.failures >= MAX_FAILURES:
                bucket.locked_until = now + WINDOW_SECONDS
                return WINDOW_SECONDS
            return 0

    def success(self, peer: str) -> None:
        with self._lock:
            self._buckets.pop(peer, None)

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()


registration_limiter = RegistrationLimiter()


def normalize_ip(value: str) -> str | None:
    try:
        address = ip_address(value.strip())
    except ValueError:
        return None
    if "%" in str(address):
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    return str(mapped or address)


_secret_lock = threading.Lock()


def proxy_secret(*, create: bool = False) -> str:
    from . import identity
    from .identity import IdentityStoreError

    path = identity.AUTH_DIR / PROXY_SECRET_FILENAME
    try:
        with _secret_lock:
            if path.is_symlink():
                raise IdentityStoreError("Registration proxy key cannot be a symbolic link")
            if create and not path.exists():
                ensure_private_directory(path.parent)
                try:
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                        handle.write(secrets.token_hex(32))
                        handle.flush()
                        os.fsync(handle.fileno())
            value = path.read_text(encoding="ascii").strip()
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise IdentityStoreError("Invalid registration proxy key")
            ensure_private_file(path)
            return value
    except (OSError, UnicodeError) as exc:
        raise IdentityStoreError("Registration proxy key is unavailable") from exc


def registration_peer(
    peer: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    now: float | None = None,
) -> str:
    """Accept a body-bound proof from the frontend, otherwise use the real peer."""
    forwarded = headers.get("x-deeptutor-registration-ip", "")
    stamp = headers.get("x-deeptutor-registration-time", "")
    proof = headers.get("x-deeptutor-registration-signature", "")
    normalized = normalize_ip(forwarded)
    if normalized and normalized == forwarded and stamp.isascii() and stamp.isdecimal():
        current = time.time() if now is None else now
        if len(stamp) <= 12 and abs(current - int(stamp)) <= PROXY_SIGNATURE_MAX_AGE:
            message = f"{stamp}\nPOST\n/api/auth/register\n{normalized}\n{hashlib.sha256(body).hexdigest()}"
            from .identity import IdentityStoreError

            try:
                key = proxy_secret()
            except IdentityStoreError:
                key = ""
            if key and len(proof) == 64 and all(char in "0123456789abcdef" for char in proof):
                expected = hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()
                if hmac.compare_digest(expected, proof):
                    return normalized
    # Even a trusted loopback peer may be Next's *generic* rewrite reached via
    # an encoded URL, bypassing the dedicated socket bridge. That rewrite can
    # preserve browser-supplied XFF. Only the signed proof crosses this boundary.
    return normalize_ip(peer) or peer
