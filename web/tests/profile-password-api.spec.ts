import { beforeEach, describe, expect, it, vi } from "vitest";
import { changeOwnPassword } from "@/lib/profile-api";

const mocks = vi.hoisted(() => ({ fetch: vi.fn(), invalidateAuth: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiFetch: mocks.fetch, apiUrl: (path: string) => path }));
vi.mock("@/lib/auth", () => ({ invalidateAuthStatusCache: mocks.invalidateAuth }));

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
beforeEach(() => mocks.fetch.mockReset());

describe("password-change client", () => {
  it("sends the current and new passwords unchanged and invalidates the ended session", async () => {
    mocks.fetch.mockResolvedValue(json({ ok: true, reauthenticate: true }));
    await changeOwnPassword(" current password ", " new password ");
    expect(mocks.fetch).toHaveBeenCalledWith("/api/auth/profile/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: " current password ", new_password: " new password " }),
      skipAuthRedirect: true,
    });
    expect(mocks.invalidateAuth).toHaveBeenCalledOnce();
  });

  it.each([
    [400, "current_password_incorrect"],
    [401, "session_expired"],
    [503, "account_storage_unavailable"],
    [422, "request_failed"],
  ])("preserves errors for inline feedback (%s)", async (status, code) => {
    mocks.fetch.mockResolvedValue(json(status === 422 ? { detail: [{ msg: "invalid input" }] } : { error_code: code }, status));
    await expect(changeOwnPassword("old password", "new password")).rejects.toMatchObject({ code, status });
    expect(mocks.invalidateAuth).toHaveBeenCalledTimes(status === 401 ? 1 : 0);
  });

  it.each([{}, null])("does not claim a successful password change for malformed success responses: %j", async (response) => {
    mocks.fetch.mockResolvedValue(json(response));
    await expect(changeOwnPassword("old password", "new password")).rejects.toMatchObject({ code: "invalid_response" });
    expect(mocks.invalidateAuth).not.toHaveBeenCalled();
  });
});
