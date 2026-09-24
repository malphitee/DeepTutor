import type { AuthStatus } from "@/lib/auth";
import { effectiveLearningPolicy, isAdministrator } from "@/lib/learning-access";

export interface SettingsAccess {
  /** False until the backend has resolved the runtime auth mode and account. */
  resolved: boolean;
  /** Admin-owned settings stay hidden on auth failures and for ordinary users. */
  hideAdminOnly: boolean;
  /** The self-service learner profile belongs only to learner accounts. */
  showLearnerOnly: boolean;
  /** Guardian management is an administrator surface. */
  showGuardianOnly: boolean;
  /** Effective policy restricts settings to safe personal preferences. */
  learningRestricted?: boolean;
}

export const PENDING_SETTINGS_ACCESS: SettingsAccess = {
  resolved: false,
  hideAdminOnly: true,
  showLearnerOnly: false,
  showGuardianOnly: false,
  learningRestricted: true,
};

/** Convert the backend's account identity into the settings visibility model. */
export function settingsAccessFromAuthStatus(
  authStatus: AuthStatus | null,
): SettingsAccess {
  if (!authStatus) {
    return { ...PENDING_SETTINGS_ACCESS, resolved: true };
  }

  const admin = isAdministrator(authStatus);
  const learningRestricted = Boolean(effectiveLearningPolicy(authStatus));
  return {
    resolved: true,
    hideAdminOnly: !admin,
    showLearnerOnly: Boolean(authStatus.enabled && authStatus.authenticated && !admin && authStatus.preset === "learner"),
    showGuardianOnly: admin,
    learningRestricted,
  };
}
