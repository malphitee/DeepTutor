import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RegisterPage from "@/app/(auth)/register/page";

const mocks = vi.hoisted(() => ({
  status: vi.fn(),
  register: vi.fn(),
  replace: vi.fn(),
  router: { replace: vi.fn() },
}));
const t = (key: string, values?: Record<string, unknown>) =>
  key.replace(/{{(.*?)}}/g, (_, name) => String(values?.[name] ?? name));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t }) }));
vi.mock("next/navigation", () => ({ useRouter: () => mocks.router }));
vi.mock("@/lib/auth", () => ({
  register: mocks.register,
  fetchRegistrationStatus: mocks.status,
  fetchAuthStatus: vi.fn(async () => ({ authenticated: false })),
}));

const invited = {
  available: true,
  is_first_user: false,
  invite_required: true,
};
const bootstrap = {
  available: true,
  is_first_user: true,
  invite_required: false,
};

beforeEach(() => {
  mocks.status.mockReset().mockResolvedValue(invited);
  mocks.register
    .mockReset()
    .mockResolvedValue({ ok: true, is_first_user: false });
});
afterEach(() => vi.useRealTimers());

async function fillAccount() {
  fireEvent.change(await screen.findByLabelText("Email or username"), {
    target: { value: "alice" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "password123" },
  });
  fireEvent.change(screen.getByLabelText("Confirm password"), {
    target: { value: "password123" },
  });
}
function submit() {
  fireEvent.submit(
    screen.getByRole("button", { name: "Create account" }).closest("form")!,
  );
}

it("waits for status and retries errors without showing bootstrap admission", async () => {
  let reject!: (error: Error) => void;
  mocks.status
    .mockReturnValueOnce(
      new Promise((_, fail) => {
        reject = fail;
      }),
    )
    .mockResolvedValueOnce(invited);
  render(<RegisterPage />);
  expect(
    screen.getByText("Checking registration availability…"),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Create account" }),
  ).not.toBeInTheDocument();
  await act(async () => reject(new Error("offline")));
  expect(screen.queryByText("First user:")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByLabelText("Invitation code")).toBeRequired();
});

it("registers the bootstrap account without a code", async () => {
  mocks.status.mockResolvedValue(bootstrap);
  mocks.register.mockResolvedValue({ ok: true, is_first_user: true });
  render(<RegisterPage />);
  await fillAccount();
  expect(screen.getByText("First user:")).toBeInTheDocument();
  expect(screen.queryByLabelText("Invitation code")).not.toBeInTheDocument();
  submit();
  await waitFor(() =>
    expect(mocks.router.replace).toHaveBeenCalledWith("/login?registered=1"),
  );
  expect(mocks.register).toHaveBeenCalledWith(
    "alice",
    "password123",
    undefined,
  );
});

it("requires an invite for standard accounts and preserves the login handoff", async () => {
  render(<RegisterPage />);
  await fillAccount();
  submit();
  expect(mocks.register).not.toHaveBeenCalled();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "An invitation code is required.",
  );
  fireEvent.change(screen.getByLabelText("Invitation code"), {
    target: { value: " abcd-efgh-jklm " },
  });
  submit();
  await waitFor(() =>
    expect(mocks.router.replace).toHaveBeenCalledWith(
      "/login?registered=1&invite=1",
    ),
  );
  expect(mocks.register).toHaveBeenCalledWith(
    "alice",
    "password123",
    "abcd-efgh-jklm",
  );
  expect(screen.getByText(/configure your own model/)).toBeInTheDocument();
});

it("shows unavailable registration without accepting credentials", async () => {
  mocks.status.mockResolvedValue({
    available: false,
    is_first_user: false,
    invite_required: false,
  });
  render(<RegisterPage />);
  expect(
    await screen.findByText(/Self-registration is unavailable/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Create account" }),
  ).not.toBeInTheDocument();
});

it("keeps server errors inline and refreshes stale bootstrap status", async () => {
  mocks.status.mockResolvedValueOnce(bootstrap).mockResolvedValue(invited);
  mocks.register.mockResolvedValue({
    ok: false,
    error: "An invitation code is required.",
  });
  render(<RegisterPage />);
  await fillAccount();
  submit();
  expect(await screen.findByLabelText("Invitation code")).toBeRequired();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "An invitation code is required.",
  );
  expect(mocks.router.replace).not.toHaveBeenCalled();
});

it("honors registration cooldown and re-enables submission after retry-after", async () => {
  mocks.register.mockResolvedValue({
    ok: false,
    error: "Too many attempts",
    retry_after: 2,
  });
  render(<RegisterPage />);
  await fillAccount();
  fireEvent.change(screen.getByLabelText("Invitation code"), {
    target: { value: "ABCD-EFGH-JKLM" },
  });
  vi.useFakeTimers();
  await act(async () => submit());
  expect(screen.getByRole("alert")).toHaveTextContent("Too many attempts");
  expect(screen.getByText("Try again in 2 seconds.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create account" })).toBeDisabled();
  await act(async () => vi.advanceTimersByTime(1000));
  await act(async () => vi.advanceTimersByTime(1000));
  expect(screen.getByRole("button", { name: "Create account" })).toBeEnabled();
});
