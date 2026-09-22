# ADR-0005: Invite-Code-Gated Self-Registration

## Status

Accepted

## Context

Built-in multi-user auth currently makes self-registration bootstrap-only: the
first account is auto-promoted to admin while the user store is empty, after
which `POST /api/auth/register` returns 403 and every further account must be
hand-created by the admin via `POST /api/auth/users` or `/admin/users`.

Onboarding through admin-created accounts does not scale past a handful of
users (one manual form per person), yet fully open self-registration is
unacceptable for a self-hosted deployment. An invite-code gate splits the
difference: the admin decides *that* people may join by issuing codes, and
those people complete registration themselves.

Constraints inherited from the user-isolation plan
(`docs-for-user/user-isolation-development-plan.md`):

- No second user system; identities live in `data/system/auth/users.json`
  through `deeptutor/multi_user/identity.py`.
- No PocketBase in multi-user mode.
- Exactly one admin role; sharing only via explicit grants.

The nearest existing precedent is device credentials
(`deeptutor/multi_user/device_credentials.py`): admin-issued random codes with
expiry and failed-attempt accounting. Invite codes are the human analogue of
that machine flow.

## Decision

Decisions below were confirmed with the maintainer in a design session on
2026-09-22.

- **Coexistence, not replacement.** Invite-code self-registration and
  admin-created accounts remain parallel paths into the same `users.json`.
  Codes do not deprecate `POST /api/auth/users` — learner/guardian scenarios
  still need direct provisioning.
- **Two-state admission gate.** While the user store is empty, registration
  stays code-free and the first account becomes admin (unchanged bootstrap).
  Once an admin exists, registration requires a valid code. There is no third
  "open registration" mode and no new `registration_mode` setting: with zero
  outstanding codes the gate degrades to today's closed posture.
- **Codes are pure admission.** A code carries no role or preset. A redeemed
  registration always produces `role=user`, `preset=standard`, enabled
  immediately — no second approval step. Any later specialization (learner
  preset, grants) stays an admin operation.
- **Code attributes.** High-entropy random string — 12-character Crockford
  base32 (≈60 bits), displayed hyphen-grouped (`XXXX-XXXX-XXXX`), matched
  case-insensitively with hyphens stripped. Optional `max_uses` (default 1),
  optional expiry (default 7 days), free-text note, revocable. Batch
  generation supported.
- **Atomic redemption.** Checking a code and incrementing `used_count` happen
  under the store write lock in one step. A registration that then fails for
  unrelated reasons (username conflict) must roll the increment back — a code
  is consumed only by a successful account creation. Redemption records (which
  user, when) are persisted per code and surfaced to the admin.
- **Abuse control.** Per-IP, in-memory failure cooldown on invalid-code
  registration attempts (threshold on the order of 10 failures / 15 minutes),
  mirroring the device-credential failure-accounting pattern at the IP
  dimension. Invalid codes return one uniform error without distinguishing
  expired / exhausted / revoked, so the endpoint is not a code-status oracle.
- **Management surface.** Admin-only endpoints under `/api/auth` (create, list
  with redemptions, revoke), exposed as a tab inside `/admin/users` next to
  user management and device credentials. All management actions go through
  the existing admin audit trail (`log_admin_action`).
- **Storage.** `data/system/auth/invites.json` following the `identity.py`
  patterns: canonical records, atomic writes, process-local write lock. The
  single-process assumption is the same one `users.json` already makes.

## Consequences

- The registration posture changes from closed to invite-gated once an admin
  exists; the 403 copy changes from "ask an administrator" to "an invitation
  code is required". Deployments that never issue codes see no behavioral
  difference beyond error copy.
- `RegisterRequest` grows an optional `invite_code` field; old clients without
  it simply fail the gate, same as today.
- The register UI needs an invite-code input whenever it is not the first
  user; `/api/auth/status` (or `is_first_user`) already carries enough signal.
- Multi-worker deployments keep documented limitations: the per-IP cooldown
  and redemption lock are process-local, so limits are per-worker. This
  matches the existing user-store posture rather than introducing new
  infrastructure.
- Device credentials (machine flow) and invite codes (human flow) stay
  uncoupled; no shared redemption machinery.
- `tests/multi_user/test_registration_invite_only.py` semantics extend from
  "first-user promotion" to "code-gated registration", and
  `docs-for-user/user-isolation-test-matrix.md` plus the deployment guide need
  a matching update when this lands.
