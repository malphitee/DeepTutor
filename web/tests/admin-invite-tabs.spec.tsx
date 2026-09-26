import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import AdminUsersPage from "@/app/(admin)/admin/users/page";

const mocks = vi.hoisted(() => ({
  auth: vi.fn(),
  users: vi.fn(),
  invites: vi.fn(),
  create: vi.fn(),
  router: { replace: vi.fn() },
}));
const t = (key: string, values?: Record<string, unknown>) =>
  key.replace(/{{(.*?)}}/g, (_, name) => String(values?.[name] ?? name));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t, i18n: { language: "en" } }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => mocks.router }));
vi.mock("@/lib/auth", () => ({ fetchAuthStatus: mocks.auth }));
vi.mock("@/lib/admin-api", () => ({
  listUsers: mocks.users,
  deleteUser: vi.fn(),
  setUserRole: vi.fn(),
  createUser: vi.fn(),
}));
vi.mock("@/lib/invites", () => ({
  listInvites: mocks.invites,
  createInvites: mocks.create,
  revokeInvite: vi.fn(),
}));
vi.mock("@/features/multi-user/components/GrantEditor", () => ({
  GrantEditor: () => null,
}));
vi.mock("@/features/multi-user/components/BookPermissionEditor", () => ({
  BookPermissionEditor: () => null,
}));
vi.mock("@/features/multi-user/components/LearnerProfileEditor", () => ({
  LearnerProfileEditor: () => null,
}));
vi.mock("@/features/multi-user/components/GuardianRelationshipsEditor", () => ({
  GuardianRelationshipsEditor: () => null,
}));

beforeEach(() => {
  mocks.auth
    .mockReset()
    .mockResolvedValue({
      authenticated: true,
      role: "admin",
      username: "admin",
    });
  mocks.users.mockReset().mockResolvedValue([]);
  mocks.invites.mockReset().mockResolvedValue({ items: [], total: 0 });
  mocks.create
    .mockReset()
    .mockResolvedValue({ invites: [{ code: "ABCD-EFGH-JKMN" }] });
});

it("mounts invitation management only on its tab and discards generated codes when leaving", async () => {
  render(<AdminUsersPage />);
  await waitFor(() => expect(mocks.users).toHaveBeenCalledOnce());
  expect(screen.getAllByRole("tab")).toHaveLength(2);
  expect(screen.getByRole("tab", { name: "Users" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(mocks.invites).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("tab", { name: "Invitation codes" }));
  await screen.findByText("No invitation codes yet.");
  fireEvent.click(
    screen.getByRole("button", { name: "Create invitation codes" }),
  );
  await screen.findByDisplayValue("ABCD-EFGH-JKMN");
  fireEvent.click(screen.getByRole("tab", { name: "Users" }));
  expect(screen.queryByDisplayValue("ABCD-EFGH-JKMN")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Invitation codes" }));
  await screen.findByText("No invitation codes yet.");
  expect(screen.queryByDisplayValue("ABCD-EFGH-JKMN")).not.toBeInTheDocument();
});

it("never loads invitation management for a non-admin visitor", async () => {
  mocks.auth.mockResolvedValue({
    authenticated: true,
    role: "user",
    username: "alice",
  });
  render(<AdminUsersPage />);
  await waitFor(() => expect(mocks.router.replace).toHaveBeenCalledWith("/"));
  fireEvent.click(screen.getByRole("tab", { name: "Invitation codes" }));
  expect(mocks.invites).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("button", { name: "Create invitation codes" }),
  ).not.toBeInTheDocument();
});
