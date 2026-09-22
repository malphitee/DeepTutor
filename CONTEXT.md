# CONTEXT — Domain Glossary

Domain terms sharpened for DeepTutor. Update inline as concepts land in
conversation; ADRs in `docs-for-user/adr/` record the decisions behind them.

## Identity & Access

- **Admission Gate（准入门）** — The two-state registration gate on
  `POST /api/auth/register`: *bootstrap state* while the user store is empty,
  *invite-gated state* once an admin exists. There is no open-registration
  mode. [ADR-0005]
- **Bootstrap Account（引导账号）** — The first account created while the user
  store is empty; auto-promoted to admin, no code required.
- **Invite Code（邀请码）** — An admin-issued, high-entropy admission secret
  for self-registration. Carries no account attributes (pure admission):
  redeeming always yields `role=user`, `preset=standard`. Has `max_uses`
  (default 1), optional expiry (default 7 days), a note, and is revocable.
  [ADR-0005]
- **Redemption（兑换）** — Consuming one use of an invite code, atomically
  (check-and-increment under the store lock) as part of a *successful*
  account creation; failed registrations roll back. Recorded per code (who,
  when). [ADR-0005]
- **Exhausted / Revoked / Expired（用尽/撤销/过期）** — Blocking states of an
  invite code; error responses do not distinguish them (no status oracle).
- **Device Credential（设备凭证）** — Admin-issued pairing code + PIN for
  device login with daily limits. The machine-flow analogue of invite codes;
  deliberately uncoupled. See `deeptutor/multi_user/device_credentials.py`.
- **Grant（授权）** — Per-user permission record under
  `data/system/grants/` controlling models, KBs, skills, partners, tools.
  Sharing is always explicit grants, never role-derived.

## Stores

- **User Store** — `data/system/auth/users.json`, the single identity store
  (bcrypt + JWT, atomic writes, process-local lock). Red line: no second user
  system.
- **Invite Store** — `data/system/auth/invites.json`, same file discipline as
  the user store. [ADR-0005]
