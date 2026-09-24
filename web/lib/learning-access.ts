import type { AuthStatus } from "@/lib/auth";
import { READING_HOME } from "@/lib/learning-routes";
import { sessionRoute } from "@/lib/mastery-session";
import type { SessionSummary } from "@/lib/session-api";

export type LearningPolicy = NonNullable<AuthStatus["learning_policy"]>;

/** The effective policy, rather than an account preset, controls learning access. */
export function effectiveLearningPolicy(status: AuthStatus | null): LearningPolicy | null {
  if (!status?.enabled || !status.authenticated || status.is_admin || status.role === "admin") return null;
  return status.learning_policy ?? null;
}

export function isAdministrator(status: AuthStatus | null): boolean {
  return Boolean(status && (!status.enabled || (status.authenticated && (status.is_admin || status.role === "admin"))));
}

const within = (path: string, root: string) => path === root || path.startsWith(`${root}/`);

/** Settings applies its own personal-page allowlist after this surface gate. */
export function canAccessLearningPath(pathname: string, policy: LearningPolicy | null): boolean {
  if (!policy) return true;
  const path = pathname.split(/[?#]/, 1)[0];
  if (within(path, "/settings") || path === "/profile") return true;
  const surfaces = policy.allowed_surfaces ?? ["chat", "reading"];
  if ((path === "/" || within(path, "/chat")) && surfaces.includes("chat")) return true;
  return within(path, READING_HOME) && surfaces.includes("reading");
}

/** Stored history must obey the same surface rules as direct navigation. */
export function canAccessLearningSession(session: SessionSummary, policy: LearningPolicy | null): boolean {
  if (!policy) return true;
  // A stale mode without its topic id would otherwise fall back to /chat.
  const mode = session.preferences?.workspace_mode;
  if (mode && mode !== "immersive_reading") return false;
  return canAccessLearningPath(sessionRoute(session), policy);
}
