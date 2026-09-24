"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import {
  changeOwnPassword,
  PasswordChangeError,
  type ProfileInfo,
} from "@/lib/profile-api";

type PasswordCapability = Pick<
  ProfileInfo,
  "password_change_supported" | "password_change_unavailable_reason"
>;

export function PasswordChangeCard({ profile }: { profile: PasswordCapability }) {
  const { t } = useTranslation();
  const router = useRouter();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessionExpired, setSessionExpired] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !profile.password_change_supported) return;
    setError(null);
    if (!currentPassword) {
      setError(t("Enter your current password."));
      return;
    }
    // Python validates Unicode characters, whereas JS string.length counts
    // UTF-16 units. Count code points so both sides accept the same passwords.
    if ([...newPassword].length < 8) {
      setError(t("Password must be at least 8 characters."));
      return;
    }
    if (new TextEncoder().encode(newPassword).length > 72) {
      setError(t("Password must be at most 72 UTF-8 bytes."));
      return;
    }
    if (newPassword !== confirmation) {
      setError(t("Passwords do not match"));
      return;
    }

    setBusy(true);
    try {
      await changeOwnPassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmation("");
      setSaved(true);
      router.replace("/login?password_changed=1");
    } catch (err) {
      if (err instanceof PasswordChangeError) {
        if (err.status === 401 || err.code === "session_expired") {
          setSessionExpired(true);
          setError(t("Your session has expired. Sign in again to change your password."));
        } else if (err.code === "current_password_incorrect") {
          setError(t("Current password is incorrect."));
        } else if (err.code === "password_change_unsupported") {
          setError(t("Password changes are not available for this account."));
        } else if (err.code === "account_storage_unavailable") {
          setError(t("Account storage is unavailable. Try again later."));
        } else if (err.status === 422) {
          setError(t("Use at least 8 characters and at most 72 UTF-8 bytes."));
        } else {
          setError(t("Failed to change password. Please try again."));
        }
      } else {
        setError(t("Could not reach the server"));
      }
      setBusy(false);
    }
  }

  return (
    <section className="mt-4 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6 shadow-sm">
      <h2 className="text-sm font-semibold text-[var(--foreground)]">
        {t("Change password")}
      </h2>
      {!profile.password_change_supported ? (
        <p className="mt-2 text-sm text-[var(--muted-foreground)]">
          {profile.password_change_unavailable_reason === "environment_admin"
            ? t("This administrator password is managed by the deployment configuration. Update it there to change it.")
            : profile.password_change_unavailable_reason === "external_auth"
              ? t("Your password is managed by your authentication provider. Change it through that provider.")
              : t("Password changes are not available for this account.")}
        </p>
      ) : saved ? (
        <p role="status" className="mt-2 text-sm text-green-600 dark:text-green-400">
          {t("Password updated. All sessions have been signed out. Sign in with your new password.")}
        </p>
      ) : (
        <form onSubmit={handleSubmit} noValidate aria-busy={busy} className="mt-2">
          <p id="password-session-notice" className="text-sm text-[var(--muted-foreground)]">
            {t("Changing your password signs you out on all devices. You will need to sign in again.")}
          </p>
          <fieldset disabled={busy || sessionExpired} className="mt-4 space-y-4">
            <div>
              <label htmlFor="current-password" className="mb-1.5 block text-sm font-medium text-[var(--foreground)]">
                {t("Current password")}
              </label>
              <input
                id="current-password"
                type="password"
                autoComplete="current-password"
                required
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3.5 py-2.5 text-sm text-[var(--foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] disabled:opacity-50"
              />
            </div>
            <div>
              <label htmlFor="new-password" className="mb-1.5 block text-sm font-medium text-[var(--foreground)]">
                {t("New password")}
              </label>
              <input
                id="new-password"
                type="password"
                autoComplete="new-password"
                required
                aria-describedby="password-requirements password-session-notice"
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3.5 py-2.5 text-sm text-[var(--foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] disabled:opacity-50"
              />
              <p id="password-requirements" className="mt-1.5 text-xs text-[var(--muted-foreground)]">
                {t("Use at least 8 characters and at most 72 UTF-8 bytes.")}
              </p>
            </div>
            <div>
              <label htmlFor="confirm-new-password" className="mb-1.5 block text-sm font-medium text-[var(--foreground)]">
                {t("Confirm new password")}
              </label>
              <input
                id="confirm-new-password"
                type="password"
                autoComplete="new-password"
                required
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3.5 py-2.5 text-sm text-[var(--foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] disabled:opacity-50"
              />
            </div>
            <button
              type="submit"
              className="rounded-lg bg-[var(--primary)] px-4 py-2.5 text-sm font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? t("Saving…") : t("Change password")}
            </button>
          </fieldset>
          {error && (
            <p role="alert" className="mt-4 rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
              {error}
            </p>
          )}
        </form>
      )}
      {(saved || sessionExpired) && (
        <Link href="/login" className="mt-3 inline-block text-sm font-medium text-[var(--primary)] hover:underline">
          {t("Sign in")}
        </Link>
      )}
    </section>
  );
}
