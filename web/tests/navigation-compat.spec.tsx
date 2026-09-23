import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import LoginPage from "@/app/(auth)/login/page";
import { useChatRouteSession } from "@/features/chat/controllers/useChatRouteSession";

const mocks = vi.hoisted(() => ({
  query: null as URLSearchParams | null,
  params: null as { sessionId?: string } | null,
  auth: vi.fn(),
  first: vi.fn(),
  router: { replace: vi.fn() },
}));
const t = (key: string) => key;
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t }) }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => mocks.query,
  useParams: () => mocks.params,
  useRouter: () => mocks.router,
}));
vi.mock("@/lib/auth", () => ({
  login: vi.fn(),
  fetchAuthStatus: mocks.auth,
  checkIsFirstUser: mocks.first,
}));

beforeEach(() => {
  mocks.query = null;
  mocks.params = null;
  mocks.auth.mockReset().mockResolvedValue({ authenticated: true });
  mocks.first.mockReset().mockResolvedValue(false);
});

it("waits for navigation readiness before redirecting an authenticated login visitor", async () => {
  const { rerender } = render(<LoginPage />);
  expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
  expect(mocks.auth).not.toHaveBeenCalled();
  expect(mocks.router.replace).not.toHaveBeenCalled();
  mocks.query = new URLSearchParams({ next: "/notebooks/note-1?course=c1" });
  rerender(<LoginPage />);
  await waitFor(() => expect(mocks.router.replace).toHaveBeenCalledWith("/notebooks/note-1?course=c1"));
});

it("resolves a session when initially unavailable route params become ready", () => {
  const { result, rerender } = renderHook(() => useChatRouteSession());
  expect(result.current.sessionId).toBeNull();
  mocks.params = { sessionId: " session-1 " };
  rerender();
  expect(result.current.sessionId).toBe("session-1");
  mocks.params = {};
  rerender();
  expect(result.current.sessionId).toBeNull();
});
