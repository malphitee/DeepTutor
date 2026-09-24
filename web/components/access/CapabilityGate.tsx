"use client";

import Link from "next/link";
import { WorkspaceAccessDenied } from "@/components/workspaces/WorkspaceAccessDenied";
import { useTranslation } from "react-i18next";
import { useLearningAccess } from "@/hooks/useLearningAccess";
import { canAccessLearningPath } from "@/lib/learning-access";
import { usePathname } from "next/navigation";

import { capabilityForPath } from "@/lib/capability-routes";

import { RequireCapability } from "./RequireCapability";

/**
 * Route-level gate: derives the required model capability from the current
 * pathname and locks the page when the user lacks it. Mounted once per authed
 * layout group, so direct-URL access to a gated feature is covered too.
 */
export default function CapabilityGate({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname() ?? "";
  const capability = capabilityForPath(pathname);
  const access = useLearningAccess();
  const { t } = useTranslation();
  if (!access.resolved) return <div aria-busy="true" className="min-h-48" />;
  if (!access.statusAvailable || !canAccessLearningPath(pathname, access.policy)) {
    if (access.statusAvailable && (pathname === "/space" || pathname.startsWith("/space/"))) return <WorkspaceAccessDenied />;
    return (
      <div role="alert" className="space-y-4 p-8">
        <h1 className="text-xl font-semibold">
          {t(access.statusAvailable ? "You do not have permission to access this page." : "Unable to verify your access. Please try again.")}
        </h1>
        {access.statusAvailable && <p>{t("Contact your administrator to request access.")}</p>}
        <Link href="/settings/general" className="text-sm underline">{t("Back to settings")}</Link>
      </div>
    );
  }
  return (
    <RequireCapability capability={capability}>{children}</RequireCapability>
  );
}
