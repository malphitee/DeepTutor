import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { InviteManager } from "@/features/multi-user/components/InviteManager";
import type { InviteRecord } from "@/lib/invites";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  revoke: vi.fn(),
  copy: vi.fn(),
}));
const t = (key: string, values?: Record<string, unknown>) =>
  key.replace(/{{(.*?)}}/g, (_, name) => String(values?.[name] ?? name));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t, i18n: { language: "en" } }),
}));
vi.mock("@/lib/invites", () => ({
  listInvites: mocks.list,
  createInvites: mocks.create,
  revokeInvite: mocks.revoke,
}));

const invite: InviteRecord = {
  id: "invite-1",
  code_hint: "JKMN",
  created_at: "2026-09-23T00:00:00Z",
  created_by: "admin-1",
  expires_at: "2026-09-30T00:00:00Z",
  max_uses: 3,
  used_count: 1,
  note: "Physics class",
  revoked_at: null,
  revoked_by: null,
  status: "active",
  redemptions: [
    {
      user_id: "user-1",
      username: "alice",
      redeemed_at: "2026-09-23T01:00:00Z",
    },
  ],
};

beforeEach(() => {
  mocks.list.mockReset().mockResolvedValue({ items: [invite], total: 1 });
  mocks.create
    .mockReset()
    .mockResolvedValue({ invites: [{ ...invite, code: "ABCD-EFGH-JKMN" }] });
  mocks.revoke.mockReset().mockResolvedValue({
    invite: {
      ...invite,
      status: "revoked",
      revoked_at: "2026-09-23T02:00:00Z",
    },
    ok: true,
  });
  mocks.copy.mockReset().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: mocks.copy },
  });
});

it("lists masked codes, status, use counts, expiry, notes and redemption history", async () => {
  render(<InviteManager />);
  expect(await screen.findByText("••••-••••-JKMN")).toBeInTheDocument();
  expect(screen.getByText("Active")).toBeInTheDocument();
  expect(screen.getByText("1 / 3")).toBeInTheDocument();
  expect(screen.getByText("Physics class")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Redemption history (1)"));
  expect(screen.getByText("alice")).toBeInTheDocument();
  expect(screen.getByText("user-1")).toBeInTheDocument();
  expect(mocks.list).toHaveBeenCalledWith(0, 50);
});

it("creates a batch with explicit expiry settings and discards plaintext when closed", async () => {
  const store = vi.spyOn(localStorage, "setItem");
  const sessionStore = vi.spyOn(sessionStorage, "setItem");
  const { unmount } = render(<InviteManager />);
  await screen.findByText("••••-••••-JKMN");
  fireEvent.change(screen.getByLabelText("Number of codes"), {
    target: { value: "2" },
  });
  fireEvent.change(screen.getByLabelText("Uses per code"), {
    target: { value: "5" },
  });
  fireEvent.click(screen.getByLabelText("Never expires"));
  fireEvent.change(screen.getByLabelText("Note"), {
    target: { value: " Study group " },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Create invitation codes" }),
  );
  expect(
    await screen.findByRole("textbox", { name: "Generated invitation codes" }),
  ).toHaveValue("ABCD-EFGH-JKMN");
  expect(mocks.create).toHaveBeenCalledWith({
    batch_count: 2,
    max_uses: 5,
    expires_in_days: null,
    note: "Study group",
  });
  fireEvent.click(screen.getByRole("button", { name: "Copy all codes" }));
  await screen.findByRole("button", { name: "Copied" });
  expect(mocks.copy).toHaveBeenCalledWith("ABCD-EFGH-JKMN");
  expect(store).not.toHaveBeenCalled();
  expect(sessionStore).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("button", { name: "Close and discard codes" }),
  );
  expect(screen.queryByDisplayValue("ABCD-EFGH-JKMN")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Copy all codes" }),
  ).not.toBeInTheDocument();
  unmount();
  render(<InviteManager />);
  await screen.findByText("••••-••••-JKMN");
  expect(screen.queryByDisplayValue("ABCD-EFGH-JKMN")).not.toBeInTheDocument();
});

it("uses single-use seven-day defaults and clears secrets on leaving the manager", async () => {
  const { unmount } = render(<InviteManager />);
  await screen.findByText("••••-••••-JKMN");
  fireEvent.click(
    screen.getByRole("button", { name: "Create invitation codes" }),
  );
  await screen.findByDisplayValue("ABCD-EFGH-JKMN");
  expect(mocks.create).toHaveBeenCalledWith({
    batch_count: 1,
    max_uses: 1,
    expires_in_days: 7,
    note: "",
  });
  unmount();
  render(<InviteManager />);
  await screen.findByText("••••-••••-JKMN");
  expect(screen.queryByDisplayValue("ABCD-EFGH-JKMN")).not.toBeInTheDocument();
});

it("confirms revocation and accepts an already-revoked response without duplicate submissions", async () => {
  render(<InviteManager />);
  fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
  const dialog = screen.getByRole("alertdialog", {
    name: "Revoke invitation code",
  });
  expect(
    within(dialog).getByText(/Existing accounts will remain active/),
  ).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));
  await waitFor(() =>
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
  );
  expect(mocks.revoke).toHaveBeenCalledOnce();
  expect(mocks.revoke).toHaveBeenCalledWith("invite-1");
  expect(screen.getByRole("button", { name: "Revoked" })).toBeDisabled();
});

it("retries a failed list and displays mutation errors inline", async () => {
  mocks.list.mockRejectedValueOnce(new Error("List temporarily unavailable"));
  mocks.create.mockRejectedValueOnce(new Error("Creation failed"));
  render(<InviteManager />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "List temporarily unavailable",
  );
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByText("••••-••••-JKMN");
  fireEvent.click(
    screen.getByRole("button", { name: "Create invitation codes" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("Creation failed");
  expect(
    screen.queryByRole("textbox", { name: "Generated invitation codes" }),
  ).not.toBeInTheDocument();
});

it("preserves one-time codes if the clipboard is unavailable", async () => {
  mocks.copy.mockRejectedValueOnce(new Error("denied"));
  render(<InviteManager />);
  await screen.findByText("••••-••••-JKMN");
  fireEvent.click(
    screen.getByRole("button", { name: "Create invitation codes" }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Copy all codes" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Select and copy them manually",
  );
  expect(
    screen.getByRole("textbox", { name: "Generated invitation codes" }),
  ).toHaveValue("ABCD-EFGH-JKMN");
});

it("paginates the administration list", async () => {
  mocks.list.mockResolvedValue({ items: [invite], total: 51 });
  render(<InviteManager />);
  await screen.findByText("••••-••••-JKMN");
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(mocks.list).toHaveBeenCalledWith(50, 50));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled(),
  );
});
