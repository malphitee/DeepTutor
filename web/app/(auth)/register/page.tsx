"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import {
  register,
  fetchRegistrationStatus,
  fetchAuthStatus,
  type RegistrationStatus,
} from "@/lib/auth";

const INVITE_CODE_EXAMPLE = "XXXX-XXXX-XXXX";

export default function RegisterPage() {
  const { t } = useTranslation();
  const router = useRouter();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [registrationStatus, setRegistrationStatus] =
    useState<RegistrationStatus | null>(null);
  const [statusError, setStatusError] = useState(false);
  const [checkingStatus, setCheckingStatus] = useState(true);
  const [retryAfter, setRetryAfter] = useState(0);

  const checkRegistration = useCallback(async () => {
    setCheckingStatus(true);
    setStatusError(false);
    try {
      setRegistrationStatus(await fetchRegistrationStatus());
    } catch {
      setRegistrationStatus(null);
      setStatusError(true);
    } finally {
      setCheckingStatus(false);
    }
  }, []);

  useEffect(() => {
    // Redirect if already logged in
    fetchAuthStatus().then((status) => {
      if (status?.authenticated) router.replace("/");
    });

    void checkRegistration();
  }, [router, checkRegistration]);

  useEffect(() => {
    if (retryAfter <= 0) return;
    const timer = window.setTimeout(
      () => setRetryAfter((value) => Math.max(0, value - 1)),
      1000,
    );
    return () => window.clearTimeout(timer);
  }, [retryAfter]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (
      loading ||
      checkingStatus ||
      !registrationStatus?.available ||
      retryAfter > 0
    )
      return;
    setError("");

    if (registrationStatus.invite_required && !inviteCode.trim()) {
      setError(t("An invitation code is required."));
      return;
    }

    if (password !== confirmPassword) {
      setError(t("Passwords do not match"));
      return;
    }

    setLoading(true);
    const result = await register(
      username,
      password,
      registrationStatus.invite_required ? inviteCode.trim() : undefined,
    );

    if (result.ok) {
      router.replace(
        result.is_first_user === false
          ? "/login?registered=1&invite=1"
          : "/login?registered=1",
      );
    } else {
      setError(t(result.error ?? "Registration failed"));
      setRetryAfter(result.retry_after ?? 0);
      setLoading(false);
      // The first account may have been created while this form was open.
      void checkRegistration();
    }
  }

  return (
    <div className="w-full max-w-sm">
      {/* Logo / Title */}
      <div className="text-center mb-8">
        <h1 className="font-serif text-2xl font-semibold text-[var(--foreground)] tracking-tight">
          DeepTutor
        </h1>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          {t("Create your account")}
        </p>
      </div>

      {/* First-user notice */}
      {!checkingStatus &&
        registrationStatus?.available &&
        registrationStatus.is_first_user && (
          <div className="mb-4 rounded-lg border border-blue-500/30 bg-blue-500/10 px-4 py-3 text-sm text-blue-600 dark:text-blue-400">
            <strong>{t("First user:")}</strong>{" "}
            {t(
              "You will be granted admin privileges and can manage other users from the admin dashboard.",
            )}
          </div>
        )}

      {checkingStatus && (
        <p
          role="status"
          className="mb-4 text-sm text-[var(--muted-foreground)]"
        >
          {t("Checking registration availability…")}
        </p>
      )}
      {statusError && (
        <div role="alert" className="mb-4 space-y-2 text-sm text-red-500">
          <p>{t("Could not check registration availability.")}</p>
          <button
            type="button"
            onClick={() => void checkRegistration()}
            className="underline"
          >
            {t("Retry")}
          </button>
        </div>
      )}
      {!checkingStatus &&
        registrationStatus &&
        !registrationStatus.available && (
          <p
            role="status"
            className="mb-4 text-sm text-[var(--muted-foreground)]"
          >
            {t(
              "Self-registration is unavailable. Ask an administrator to create your account.",
            )}
          </p>
        )}

      {/* Card */}
      {registrationStatus?.available && (
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-2xl shadow-sm px-8 py-8">
          <form onSubmit={handleSubmit} className="space-y-5">
            {registrationStatus.invite_required && (
              <div>
                <label
                  htmlFor="invite-code"
                  className="mb-1.5 block text-sm font-medium"
                >
                  {t("Invitation code")}
                </label>
                <input
                  id="invite-code"
                  type="text"
                  autoComplete="off"
                  autoCapitalize="characters"
                  spellCheck={false}
                  required
                  maxLength={64}
                  value={inviteCode}
                  onChange={(event) => setInviteCode(event.target.value)}
                  placeholder={INVITE_CODE_EXAMPLE}
                  className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3.5 py-2.5 text-sm uppercase focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
                />
                <p className="mt-2 text-xs text-[var(--muted-foreground)]">
                  {t(
                    "Your account will use the Standard preset. After signing in, configure your own model or ask an administrator to grant access.",
                  )}
                </p>
              </div>
            )}
            {/* Email or username */}
            <div>
              <label
                htmlFor="username"
                className="block text-sm font-medium text-[var(--foreground)] mb-1.5"
              >
                {t("Email or username")}
              </label>
              <input
                id="username"
                type="text"
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full px-3.5 py-2.5 rounded-lg border border-[var(--border)]
                         bg-[var(--background)] text-[var(--foreground)]
                         placeholder:text-[var(--muted-foreground)]
                         focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent
                         transition-shadow text-sm"
                placeholder="you@example.com"
              />
            </div>

            {/* Password */}
            <div>
              <label
                htmlFor="password"
                className="block text-sm font-medium text-[var(--foreground)] mb-1.5"
              >
                {t("Password")}
              </label>
              <input
                id="password"
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full px-3.5 py-2.5 rounded-lg border border-[var(--border)]
                         bg-[var(--background)] text-[var(--foreground)]
                         placeholder:text-[var(--muted-foreground)]
                         focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent
                         transition-shadow text-sm"
                placeholder="••••••••"
              />
              <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                {t("At least 8 characters")}
              </p>
            </div>

            {/* Confirm Password */}
            <div>
              <label
                htmlFor="confirmPassword"
                className="block text-sm font-medium text-[var(--foreground)] mb-1.5"
              >
                {t("Confirm password")}
              </label>
              <input
                id="confirmPassword"
                type="password"
                autoComplete="new-password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full px-3.5 py-2.5 rounded-lg border border-[var(--border)]
                         bg-[var(--background)] text-[var(--foreground)]
                         placeholder:text-[var(--muted-foreground)]
                         focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent
                         transition-shadow text-sm"
                placeholder="••••••••"
              />
            </div>

            {/* Error message */}
            {error && (
              <p
                role="alert"
                className="text-sm text-red-500 bg-red-500/10 rounded-lg px-3 py-2"
              >
                {error}
              </p>
            )}
            {retryAfter > 0 && (
              <p
                role="status"
                className="text-sm text-[var(--muted-foreground)]"
              >
                {t("Try again in {{seconds}} seconds.", {
                  seconds: retryAfter,
                })}
              </p>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={loading || checkingStatus || retryAfter > 0}
              className="w-full py-2.5 px-4 rounded-lg font-medium text-sm
                       bg-[var(--primary)] text-[var(--primary-foreground)]
                       hover:opacity-90 active:opacity-80
                       disabled:opacity-50 disabled:cursor-not-allowed
                       transition-opacity"
            >
              {loading ? t("Creating account…") : t("Create account")}
            </button>
          </form>
        </div>
      )}

      <p className="mt-6 text-center text-sm text-[var(--muted-foreground)]">
        {t("Already have an account?")}{" "}
        <Link
          href="/login"
          className="text-[var(--primary)] hover:underline font-medium"
        >
          {t("Sign in")}
        </Link>
      </p>

      <p className="mt-3 text-center text-xs text-[var(--muted-foreground)]">
        {t("DeepTutor · Agent-Native Learning")}
      </p>
    </div>
  );
}
