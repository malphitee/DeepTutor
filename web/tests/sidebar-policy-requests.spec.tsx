import { render, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import UtilitySidebar from "@/components/sidebar/UtilitySidebar";
import WorkspaceSidebar from "@/components/sidebar/WorkspaceSidebar";

const state = vi.hoisted(() => ({
  policy: { allowed_surfaces: ["chat", "reading"] },
  sessions: vi.fn().mockResolvedValue([]),
  courses: vi.fn().mockResolvedValue([]),
  mastery: vi.fn().mockResolvedValue([]),
  reading: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/hooks/useLearningAccess", () => ({ useLearningAccess: () => ({
  resolved: true, statusAvailable: true, isAdmin: false,
  policy: state.policy,
}) }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/context/AppShellContext", () => ({ useAppShell: () => ({ activeSessionId: null, setActiveSessionId: vi.fn() }) }));
vi.mock("@/features/chat/ChatStateAdapter", () => ({ useChatStateAdapter: () => ({
  newSession: vi.fn(), configureSession: vi.fn(), cancelStreamingTurn: vi.fn(), selectedSessionId: null, sessionStatuses: {}, sidebarRefreshToken: 0,
}) }));
vi.mock("@/lib/session-api", () => ({ listSessions: state.sessions, listAllSessions: state.sessions }));
vi.mock("@/lib/courses-api", () => ({ listCourses: state.courses }));
vi.mock("@/lib/reading-workspace-api", () => ({ fetchReadingCollectionIndex: state.reading }));
vi.mock("@/lib/learning-api", () => ({ fetchMasteryTopicIndex: state.mastery }));
vi.mock("@/components/sidebar/SidebarShell", () => ({ SidebarShell: () => null }));
vi.mock("@/lib/session-unread", () => ({ reconcileUnread: vi.fn() }));

beforeEach(() => { state.policy = { allowed_surfaces: ["chat", "reading"] }; });

for (const [name, Sidebar] of [["utility", UtilitySidebar], ["workspace", WorkspaceSidebar]] as const) {
  it(`${name} sidebar suppresses chat and managed-index requests for reading-only policy`, async () => {
    state.policy = { allowed_surfaces: ["reading"] };
    render(<Sidebar />);
    await waitFor(() => expect(state.reading).toHaveBeenCalled());
    expect(state.sessions).not.toHaveBeenCalled();
    expect(state.courses).not.toHaveBeenCalled();
    expect(state.mastery).not.toHaveBeenCalled();
  });
  it(`${name} sidebar suppresses reading and managed-index requests for chat-only policy`, async () => {
    state.policy = { allowed_surfaces: ["chat"] };
    render(<Sidebar />);
    await waitFor(() => expect(state.sessions).toHaveBeenCalled());
    expect(state.reading).not.toHaveBeenCalled();
    expect(state.courses).not.toHaveBeenCalled();
    expect(state.mastery).not.toHaveBeenCalled();
  });
}
