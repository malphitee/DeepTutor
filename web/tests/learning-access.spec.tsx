import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthStatus } from "@/lib/auth";
import type { LearningAccess } from "@/hooks/useLearningAccess";
import { effectiveLearningPolicy, canAccessLearningPath, canAccessLearningSession } from "@/lib/learning-access";
import { settingsAccessFromAuthStatus } from "@/features/settings/navigation/settings-access";
import { visibleSettingsPages } from "@/features/settings/navigation/settings-pages";
import { PRIMARY_NAV, primaryNavForPolicy } from "@/components/sidebar/nav-entries";
import { SidebarShell } from "@/components/sidebar/SidebarShell";
import type { SessionSummary } from "@/lib/session-api";
import CapabilityGate from "@/components/access/CapabilityGate";
import SettingsPageContent from "@/components/settings/SettingsPageContent";
import { SidebarHome, SidebarNav } from "@/components/sidebar/SidebarNav";

const state = vi.hoisted(() => ({
  path: "/chat",
  access: { resolved: true, statusAvailable: true, isAdmin: false, policy: null } as LearningAccess,
  settingsAccess: { resolved: true, hideAdminOnly: true, showLearnerOnly: false, showGuardianOnly: false, learningRestricted: true },
  settingsLoading: false,
}));
vi.mock("next/image", () => ({ default: () => null }));
vi.mock("@/context/AppShellContext", () => ({ useAppShell: () => ({ sidebarCollapsed: false, setSidebarCollapsed: vi.fn() }) }));
vi.mock("@/components/layout/AppShell", () => ({ useSidebarDrawer: () => null }));
vi.mock("@/hooks/useDevice", () => ({ useDevice: () => ({ isMobile: false }) }));
vi.mock("@/hooks/useChatWorkspaces", () => ({ useChatWorkspaces: () => ({ workspaces: [], error: "" }) }));
vi.mock("@/components/sidebar/VersionBadge", () => ({ VersionBadge: () => <span>Version badge</span> }));
vi.mock("@/hooks/useLearningAccess", () => ({ useLearningAccess: () => state.access }));
vi.mock("@/features/settings/navigation/SettingsAccessProvider", () => ({ useSettingsAccess: () => state.settingsAccess }));
vi.mock("@/features/settings/store/SettingsStore", () => ({ useSettings: () => ({ settingsLoading: state.settingsLoading, saving: false, applying: false }) }));
vi.mock("next/navigation", () => ({ usePathname: () => state.path, useRouter: () => ({ replace: vi.fn() }) }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key, i18n: { language: "en" } }) }));
vi.mock("next/dynamic", () => ({ default: () => function Setting() { return <div>Setting content</div>; } }));
vi.mock("@/components/access/RequireCapability", () => ({ RequireCapability: ({ children }: { children: React.ReactNode }) => <>{children}</> }));

const policy = {
  age_band: "under_13", locked_persona: "tutor", allowed_capabilities: ["chat"],
  default_capability: "chat", allowed_surfaces: ["chat", "reading"],
};
const account = (overrides: Partial<AuthStatus> = {}): AuthStatus => ({
  enabled: true, authenticated: true, is_admin: false, role: "user", preset: "standard", ...overrides,
});
const session = (id: string, preferences: SessionSummary["preferences"] = {}): SessionSummary => ({
  id, session_id: id, title: id, preferences, created_at: 1, updated_at: 1, message_count: 0, last_message: "",
});

beforeEach(() => {
  state.path = "/chat";
  state.access = { resolved: true, statusAvailable: true, isAdmin: false, policy };
  state.settingsAccess = { ...settingsAccessFromAuthStatus(account({ learning_policy: policy })), learningRestricted: true };
  state.settingsLoading = false;
});

describe("settings visibility follows account role and effective policy", () => {
  it.each(["standard", "custom", "learner"] as const)("uses policy even when preset is %s", preset => {
    const access = settingsAccessFromAuthStatus(account({ preset, learning_policy: policy }));
    const keys = visibleSettingsPages(access).map(page => page.key);
    expect(keys.sort()).toEqual((preset === "learner" ? ["appearance", "general", "learner-profile"] : ["appearance", "general"]).sort());
  });
  it("hides infrastructure and guardian management from ordinary users", () => {
    const keys = visibleSettingsPages(settingsAccessFromAuthStatus(account())).map(page => page.key);
    for (const key of ["guardian", "connections", "llm", "task-models", "embedding", "search", "voice", "multimodal", "tools", "capabilities", "knowledge", "network", "status", "about"]) expect(keys).not.toContain(key);
    expect(keys).toContain("workspace");
  });
  it.each([account({ role: "admin", is_admin: true, learning_policy: policy }), { enabled: false, authenticated: false }])("preserves all administrator settings", status => {
    expect(effectiveLearningPolicy(status)).toBeNull();
    const keys = visibleSettingsPages(settingsAccessFromAuthStatus(status)).map(page => page.key);
    for (const key of ["guardian", "workspace", "connections", "llm", "voice", "multimodal", "tools", "capabilities", "knowledge", "network", "status", "about"]) expect(keys).toContain(key);
    expect(primaryNavForPolicy(effectiveLearningPolicy(status))).toEqual(PRIMARY_NAV);
  });
  it("shows a direct or legacy-link permission response without waiting for restricted settings data", () => {
    state.settingsLoading = true;
    const view = render(<SettingsPageContent section="tts" />);
    expect(screen.getByRole("alert")).toHaveTextContent("You do not have permission to access this settings page.");
    expect(screen.queryByText("Setting content")).toBeNull();
    view.rerender(<SettingsPageContent section="workspace" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Contact your administrator");
  });
});

describe("learning navigation", () => {
  it("keeps only chat and reading links, regardless of stale saved nav ordering", () => {
    localStorage.setItem("deeptutor.sidebar.navLayout", JSON.stringify({ order: ["/space", "/agents", "/learning", "/partners"], collapsed: [] }));
    render(<><SidebarHome onHomeClick={vi.fn()} /><SidebarNav collapsed={false} onHomeClick={vi.fn()} onNavigate={vi.fn()} /></>);
    expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/chat");
    expect(screen.getByRole("link", { name: "Immersive Reading" })).toHaveAttribute("href", "/learning/reading");
    for (const name of ["Partners", "Personalized Learning", "Learning Space", "Co-Writer", "My Agents"]) expect(screen.queryByRole("link", { name })).toBeNull();
  });
  it("honors narrower allowed surfaces in nav and old history", () => {
    const readingOnly = { ...policy, allowed_surfaces: ["reading"] };
    expect(primaryNavForPolicy(readingOnly).map(item => item.href)).toEqual(["/learning/reading"]);
    expect(canAccessLearningSession(session("chat"), readingOnly)).toBe(false);
    expect(canAccessLearningSession(session("reading", { workspace_mode: "immersive_reading", reading_workspace_id: "book" }), readingOnly)).toBe(true);
  });
  it("filters stale mastery and watching sessions and workspace headings", () => {
    const roots = [session("chat"), session("reading", { workspace_mode: "immersive_reading", reading_workspace_id: "book" }), session("mastery", { workspace_mode: "mastery_path" }), session("watching", { workspace_mode: "immersive_watching" })];
    render(<SidebarShell sessions={roots} showSessions onSelectSession={vi.fn()} onRenameSession={vi.fn()} onDeleteSession={vi.fn()} onOrganizeSession={vi.fn()} />);
    expect(screen.getByText("chat")).toBeInTheDocument();
    expect(screen.getByText("reading")).toBeInTheDocument();
    expect(screen.queryByText("mastery")).toBeNull();
    expect(screen.queryByText("watching")).toBeNull();
    expect(screen.queryByText("Version badge")).toBeNull();
    expect(screen.queryByText("Setting content")).toBeNull();
  });
  it.each(["/profile", "/settings/appearance", "/chat/a", "/learning/reading/book/sessions/a"])("allows safe direct path %s", path => {
    state.path = path;
    render(<CapabilityGate><span>Allowed content</span></CapabilityGate>);
    expect(screen.getByText("Allowed content")).toBeInTheDocument();
  });
  it.each(["/space", "/learning/mastery", "/partners", "/agents"])("blocks disallowed direct path %s before mounting it", path => {
    state.path = path;
    render(<CapabilityGate><span>Restricted content</span></CapabilityGate>);
    expect(screen.queryByText("Restricted content")).toBeNull();
    expect(screen.getByText(/Contact your administrator/)).toBeInTheDocument();
  });
  it("matches path boundaries and fails closed while access is unavailable", () => {
    expect(canAccessLearningPath("/chat-history", policy)).toBe(false);
    expect(canAccessLearningPath("/learning/reading-other", policy)).toBe(false);
    state.access.statusAvailable = false;
    render(<CapabilityGate><span>Restricted content</span></CapabilityGate>);
    expect(screen.getByRole("alert")).toHaveTextContent("Unable to verify your access.");
    expect(screen.queryByText("Restricted content")).toBeNull();
  });
});
