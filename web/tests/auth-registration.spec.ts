import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchRegistrationStatus, register } from "@/lib/auth";
import { createInvites, listInvites, revokeInvite } from "@/lib/invites";

const mocks = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock("@/lib/api", () => ({
  apiFetch: mocks.fetch,
  apiUrl: (path: string) => path,
  setRuntimeAuthEnabled: vi.fn(),
}));

beforeEach(() => mocks.fetch.mockReset());
const json = (data: unknown, status = 200, headers?: Record<string, string>) =>
  new Response(JSON.stringify(data), { status, headers });

describe("registration client", () => {
  it.each([
    { available: true, is_first_user: true, invite_required: false },
    { available: true, is_first_user: false, invite_required: true },
    { available: false, is_first_user: false, invite_required: false },
  ])("reads explicit admission status: %j", async (status) => {
    mocks.fetch.mockResolvedValue(json(status));
    await expect(fetchRegistrationStatus()).resolves.toEqual(status);
    expect(mocks.fetch).toHaveBeenCalledWith(
      "/api/auth/registration-status",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("does not turn unavailable or malformed responses into bootstrap", async () => {
    mocks.fetch
      .mockResolvedValueOnce(json({}, 503))
      .mockResolvedValueOnce(json({ available: true }))
      .mockResolvedValueOnce(
        json({ available: true, is_first_user: true, invite_required: true }),
      );
    await expect(fetchRegistrationStatus()).rejects.toThrow();
    await expect(fetchRegistrationStatus()).rejects.toThrow();
    await expect(fetchRegistrationStatus()).rejects.toThrow();
  });

  it("sends the invitation code and preserves the created account admission type", async () => {
    mocks.fetch.mockResolvedValue(
      json({ role: "user", is_first_user: false }, 201),
    );
    await expect(
      register("alice", "password123", "ABCD-EFGH-JKLM"),
    ).resolves.toEqual({
      ok: true,
      role: "user",
      is_first_user: false,
    });
    expect(JSON.parse(mocks.fetch.mock.calls[0][1].body)).toEqual({
      username: "alice",
      password: "password123",
      invite_code: "ABCD-EFGH-JKLM",
    });
  });

  it("surfaces server errors and retry-after without redirecting to login", async () => {
    mocks.fetch.mockResolvedValue(
      json(
        { detail: "Try later", error_code: "registration_rate_limited" },
        429,
        {
          "Retry-After": "900",
        },
      ),
    );
    await expect(register("alice", "password123", "CODE")).resolves.toEqual({
      ok: false,
      error: "Try later",
      error_code: "registration_rate_limited",
      retry_after: 900,
    });
    expect(mocks.fetch.mock.calls[0][1].skipAuthRedirect).toBe(true);
  });

  it("normalizes validation failures and network failures", async () => {
    mocks.fetch
      .mockResolvedValueOnce(
        json({ detail: [{ msg: "Value error, Enter a valid email address" }] }, 422),
      )
      .mockRejectedValueOnce(new Error("offline"));
    expect((await register("alice", "password123")).error).toBe(
      "Enter a valid email address",
    );
    expect((await register("alice", "password123")).error).toBe(
      "Could not reach the server",
    );
  });
});

describe("invite management client", () => {
  it("uses paginated reads, explicit creation options, and the idempotent revoke endpoint", async () => {
    mocks.fetch
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ invites: [] }, 201))
      .mockResolvedValueOnce(json({ invite: { id: "inv/1" }, ok: true }));
    await listInvites(50, 50);
    const request = {
      batch_count: 2,
      max_uses: 3,
      expires_in_days: null,
      note: "Class",
    };
    await createInvites(request);
    await revokeInvite("inv/1");
    expect(mocks.fetch.mock.calls[0][0]).toBe(
      "/api/auth/invites?offset=50&limit=50",
    );
    expect(JSON.parse(mocks.fetch.mock.calls[1][1].body)).toEqual(request);
    expect(mocks.fetch.mock.calls[2]).toEqual([
      "/api/auth/invites/inv%2F1/revoke",
      { method: "POST" },
    ]);
  });

  it("reports management errors from the server", async () => {
    mocks.fetch.mockResolvedValue(json({ detail: "Admin required" }, 403));
    await expect(listInvites()).rejects.toThrow("Admin required");
  });
});
