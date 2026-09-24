import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProfilePage from "@/app/(utility)/profile/page";
import LoginPage from "@/app/(auth)/login/page";
import { PasswordChangeCard } from "@/components/profile/PasswordChangeCard";
import { PasswordChangeError } from "@/lib/profile-api";
import { initI18n } from "@/i18n/init";

const mocks = vi.hoisted(() => ({
  changePassword: vi.fn(),
  getProfile: vi.fn(),
  authStatus: vi.fn(),
  router: { replace: vi.fn() },
  searchParams: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => mocks.router,
  useSearchParams: () => mocks.searchParams,
}));
vi.mock("@/lib/profile-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/profile-api")>()),
  changeOwnPassword: mocks.changePassword,
  getProfile: mocks.getProfile,
}));
vi.mock("@/lib/auth", () => ({
  fetchAuthStatus: mocks.authStatus,
  checkIsFirstUser: vi.fn(async () => false),
  invalidateAuthStatusCache: vi.fn(),
  logout: vi.fn(),
  login: vi.fn(),
}));

initI18n("en");
const supported = { password_change_supported: true };
const savedMessage =
  "Password updated. All sessions have been signed out. Sign in with your new password.";

beforeEach(() => {
  mocks.changePassword.mockReset().mockResolvedValue(undefined);
  mocks.authStatus.mockReset().mockResolvedValue({ enabled: true, authenticated: true });
  mocks.getProfile.mockReset().mockResolvedValue({
    id: "alice-id",
    username: "alice",
    role: "user",
    created_at: "2026-01-01T00:00:00Z",
    ...supported,
  });
  mocks.searchParams = new URLSearchParams();
});

function fill(current = "old password", password = "new password", confirm = password) {
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: current } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: confirm } });
}

function submit() {
  fireEvent.submit(screen.getByRole("button", { name: "Change password" }).closest("form")!);
}

describe("profile password form", () => {
  it("exposes password changes from the signed-in profile", async () => {
    render(<ProfilePage />);
    expect(await screen.findByLabelText("Current password")).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByLabelText("New password")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByText("Changing your password signs you out on all devices. You will need to sign in again.")).toBeVisible();
  });

  it.each([
    ["", "new password", "new password", "Enter your current password."],
    ["old", "short", "short", "Password must be at least 8 characters."],
    ["old", "😀😀😀😀", "😀😀😀😀", "Password must be at least 8 characters."],
    ["old", "密".repeat(25), "密".repeat(25), "Password must be at most 72 UTF-8 bytes."],
    ["old", "a".repeat(73), "a".repeat(73), "Password must be at most 72 UTF-8 bytes."],
    ["old", "new password", "different password", "Passwords do not match"],
  ])("validates before submitting (%s / %s)", (current, password, confirm, message) => {
    render(<PasswordChangeCard profile={supported} />);
    fill(current, password, confirm);
    submit();
    expect(screen.getByRole("alert")).toHaveTextContent(message);
    expect(mocks.changePassword).not.toHaveBeenCalled();
  });

  it.each(["密".repeat(24), "😀".repeat(8)])("accepts Unicode within the shared policy", async (password) => {
    render(<PasswordChangeCard profile={supported} />);
    fill(" current with spaces ", password);
    submit();
    await waitFor(() => expect(mocks.router.replace).toHaveBeenCalledWith("/login?password_changed=1"));
    expect(mocks.changePassword).toHaveBeenCalledWith(" current with spaces ", password);
  });

  it("prevents duplicate submission and clears the form before returning to sign-in", async () => {
    let finish!: () => void;
    mocks.changePassword.mockReturnValue(new Promise<void>((resolve) => { finish = resolve; }));
    render(<PasswordChangeCard profile={supported} />);
    fill();
    const form = screen.getByRole("button", { name: "Change password" }).closest("form")!;
    fireEvent.submit(form);
    expect(screen.getByLabelText("Current password")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    fireEvent.submit(form);
    expect(mocks.changePassword).toHaveBeenCalledTimes(1);
    await act(async () => finish());
    expect(screen.queryByLabelText("Current password")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(savedMessage);
    expect(mocks.router.replace).toHaveBeenCalledWith("/login?password_changed=1");
  });

  it("keeps incorrect-current-password feedback inline and allows correction", async () => {
    mocks.changePassword.mockRejectedValueOnce(new PasswordChangeError("current_password_incorrect", 400));
    render(<PasswordChangeCard profile={supported} />);
    fill();
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("Current password is incorrect.");
    expect(mocks.router.replace).not.toHaveBeenCalled();
    expect(screen.getByLabelText("New password")).toHaveValue("new password");
    fill("correct password");
    submit();
    await waitFor(() => expect(mocks.router.replace).toHaveBeenCalledWith("/login?password_changed=1"));
  });

  it("offers sign-in when the current session expires", async () => {
    mocks.changePassword.mockRejectedValue(new PasswordChangeError("session_expired", 401));
    render(<PasswordChangeCard profile={supported} />);
    fill();
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your session has expired.");
    expect(screen.getByRole("button", { name: "Change password" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it.each([
    ["environment_admin", "This administrator password is managed by the deployment configuration. Update it there to change it."],
    ["external_auth", "Your password is managed by your authentication provider. Change it through that provider."],
  ] as const)("explains unavailable password management for %s", (reason, message) => {
    render(<PasswordChangeCard profile={{ password_change_supported: false, password_change_unavailable_reason: reason }} />);
    expect(screen.getByText(message)).toBeVisible();
    expect(screen.queryByLabelText("Current password")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change password" })).not.toBeInTheDocument();
  });

  it("preserves the success confirmation on the login page", async () => {
    mocks.searchParams = new URLSearchParams("password_changed=1");
    mocks.authStatus.mockResolvedValue({ enabled: true, authenticated: false });
    await act(async () => { render(<LoginPage />); });
    expect(screen.getByRole("status")).toHaveTextContent(savedMessage);
    expect(screen.getByRole("button", { name: "Sign in" })).toBeVisible();
    expect(mocks.router.replace).not.toHaveBeenCalled();
  });
});
