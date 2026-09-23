# CONTEXT — Domain Glossary

Domain terms sharpened for DeepTutor. Update inline as concepts land in
conversation; ADRs in `docs-for-user/adr/` record the decisions behind them.

## Identity & Access

- **Admission Gate（准入门）** — The two-state registration gate on
  `POST /api/auth/register`: *bootstrap state* only when the readable user store
  is empty and no `auth.json` bootstrap admin is configured; *invite-gated state*
  otherwise. Damaged storage is unavailable, never empty. There is no
  open-registration mode. [ADR-0005]
- **Bootstrap Account（引导账号）** — The first account created while the user
  store is empty and no configured bootstrap admin exists; auto-promoted to
  admin, no code required. A configured `username` plus `password_hash` counts
  as an existing administrator even though its overlay is not stored in `users.json`.
- **Invite Code（邀请码）** — An admin-issued, high-entropy admission secret
  for self-registration. Carries no account attributes (pure admission):
  redeeming always yields `role=user`, `preset=standard`. Has `max_uses`
  (default 1; range 1–1000), optional expiry (default 7 days; 1–365 or `null`
  for no expiry), a note, and is revocable. Codes are 12 Crockford Base32
  characters; only their SHA-256 digest and last-four-character hint persist.
  Full codes appear once at generation. No grant is created on registration.
  [ADR-0005]
- **Redemption（兑换）** — Creating an account and consuming one use under a
  shared identity/invite transaction. A durable registration journal is the
  commit point; after it, failures are recovered by roll forward, not refund.
  Username conflicts before commit consume nothing. Deleting an account never
  refunds its use; user ID, username, and redemption time remain recorded. [ADR-0005]
- **Exhausted / Revoked / Expired（用尽/撤销/过期）** — Blocking states of an
  invite code; error responses do not distinguish them (no status oracle).
- **Device Credential（设备凭证）** — Admin-issued pairing code + PIN for
  device login with daily limits. The machine-flow analogue of invite codes;
  deliberately uncoupled. The current admin page has users/invites tabs only;
  this implementation does not add device-credential UI.
  See `deeptutor/multi_user/device_credentials.py`.
- **Grant（授权）** — Per-user permission record under
  `data/system/grants/` controlling models, KBs, skills, partners, tools.
  Sharing is always explicit grants, never role-derived.

## Stores

- **User Store** — `data/system/auth/users.json`, the single identity store
  (bcrypt + JWT, atomic writes, process-local lock). Red line: no second user
  system.
- **Invite Store** — `data/system/auth/invites.json`, same file discipline as
  the user store, with a shared process-local reentrant lock. Built-in auth
  enforces `backend_workers=1` at startup and at invitation endpoints. [ADR-0005]
- **Registration Journal（注册事务日志）** —
  `data/system/auth/registration-journal.json`, containing both store snapshots
  and a checksum. Recovery completes both replacements before any further
  identity/invite operation; it never interprets a broken journal as no transaction.
- **Registration Peer Proof（注册来源证明）** — A 60-second HMAC proof binding
  the Next API bridge's socket peer to method/path/body/time. Its independent
  `registration_proxy_secret` is shared with the API, not with the browser;
  it is not the JWT signing secret. Only the Next bridge interprets forwarding
  headers through configured exact proxy IPs (`registration_trusted_proxies`,
  default `[]`). The backend accepts a valid proof or uses its real socket peer;
  it never trusts unsigned XFF.

## Frontend Routing

- **Navigation Readiness（导航就绪）** — Pages API coexistence makes App Router
  path/query/route hooks nullable. `web/types/navigation-compat.d.ts` keeps this
  contract visible during standalone type checks, and Pages joins type and
  architecture scans. A null value means routing is not ready; deep-link
  consumers wait before initialization, redirects, or one-time query consumption.

[ADR-0005]: docs-for-user/adr/0005-invite-code-registration.md

2026-09-23 的实现选择、运行方法及已验证/待验收状态见
[邀请码注册实现记录](docs-for-user/invite-registration-implementation.md)。
