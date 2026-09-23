import assert from "node:assert/strict";
import { createHmac, createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { forwardRegistration, normalizeRegistrationIp, registrationClientIp } from "../lib/server/registration-proxy";

test("registration bridge takes the socket peer and ignores forged XFF by default", () => {
  assert.equal(registrationClientIp("::ffff:192.0.2.1", "203.0.113.1", []), "192.0.2.1");
  assert.equal(normalizeRegistrationIp("2001:DB8:0::1"), "2001:db8::1");
  assert.equal(normalizeRegistrationIp("fe80::1%eth0"), null);
});

test("trusted proxy chains stop at the first untrusted hop", () => {
  assert.equal(registrationClientIp("127.0.0.1", "spoof, 192.0.2.1", ["127.0.0.1"]), "192.0.2.1");
  assert.equal(registrationClientIp("127.0.0.1", "203.0.113.1, 192.0.2.1", ["127.0.0.1", "192.0.2.1"]), "203.0.113.1");
  assert.equal(registrationClientIp("127.0.0.1", "bad", ["127.0.0.1"]), "127.0.0.1");
});

test("frontend proof binds the canonical client IP, time and exact forwarded JSON", async () => {
  const home = mkdtempSync(path.join(os.tmpdir(), "registration-bridge-"));
  try {
    mkdirSync(path.join(home, "data/system/auth"), { recursive: true });
    const key = "a".repeat(64);
    writeFileSync(path.join(home, "data/system/auth/registration_proxy_secret"), key);
    const response = await forwardRegistration("192.0.2.1", "203.0.113.10", { username: "alice", invite_code: "TEST-TEST-TEST" }, {
      runtimeHome: home, backendBase: "http://127.0.0.1:8001", now: 1234000,
      fetcher: async (url, init) => {
        assert.equal(String(url), "http://127.0.0.1:8001/api/auth/register");
        const headers = new Headers(init?.headers);
        assert.equal(headers.get("x-deeptutor-registration-ip"), "192.0.2.1");
        assert.equal(headers.get("x-deeptutor-registration-time"), "1234");
        const digest = createHash("sha256").update(String(init?.body)).digest("hex");
        const expected = createHmac("sha256", key).update(`1234\nPOST\n/api/auth/register\n192.0.2.1\n${digest}`).digest("hex");
        assert.equal(headers.get("x-deeptutor-registration-signature"), expected);
        assert.equal(headers.has("x-forwarded-for"), false);
        return new Response('{"ok":true}', { status: 201 });
      },
    });
    assert.equal(response.status, 201);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});
