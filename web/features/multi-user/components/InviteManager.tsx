"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  createInvites,
  listInvites,
  revokeInvite,
  type InviteRecord,
} from "@/lib/invites";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

const PAGE_SIZE = 50;
const inputClass =
  "mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm";
const buttonClass =
  "rounded-lg border border-[var(--border)] px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50";

export function InviteManager() {
  const { t, i18n } = useTranslation();
  const [items, setItems] = useState<InviteRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [creating, setCreating] = useState(false);
  const [batchCount, setBatchCount] = useState("1");
  const [maxUses, setMaxUses] = useState("1");
  const [expiresInDays, setExpiresInDays] = useState("7");
  const [noExpiry, setNoExpiry] = useState(false);
  const [note, setNote] = useState("");
  // Deliberately separate plaintext from list data and never persist it.
  const [issuedCodes, setIssuedCodes] = useState<string[] | null>(null);
  const [copied, setCopied] = useState(false);
  const [copying, setCopying] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<InviteRecord | null>(null);
  const [revoking, setRevoking] = useState(false);
  const requestVersion = useRef(0);

  const load = useCallback(
    async (pageOffset: number) => {
      const version = ++requestVersion.current;
      setLoading(true);
      setLoadError("");
      try {
        const result = await listInvites(pageOffset, PAGE_SIZE);
        if (requestVersion.current !== version) return;
        setItems(result.items);
        setTotal(result.total);
      } catch (error) {
        if (requestVersion.current === version) {
          setLoadError(
            error instanceof Error
              ? t(error.message)
              : t("Failed to load invitation codes."),
          );
        }
      } finally {
        if (requestVersion.current === version) setLoading(false);
      }
    },
    [t],
  );

  useEffect(() => {
    void load(offset);
    return () => {
      requestVersion.current += 1;
    };
  }, [load, offset]);

  const formatDate = (value: string | null) => {
    if (value === null) return t("Never expires");
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? "—"
      : date.toLocaleString(i18n.language);
  };
  const statusLabel = (status: InviteRecord["status"]) => {
    switch (status) {
      case "active":
        return t("Active");
      case "expired":
        return t("Expired");
      case "exhausted":
        return t("Exhausted");
      case "revoked":
        return t("Revoked");
    }
  };

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (creating || issuedCodes) return;
    setCreating(true);
    setActionError("");
    try {
      const result = await createInvites({
        batch_count: Number(batchCount),
        max_uses: Number(maxUses),
        expires_in_days: noExpiry ? null : Number(expiresInDays),
        note: note.trim(),
      });
      setIssuedCodes(result.invites.map((invite) => invite.code));
      setCopied(false);
      if (offset === 0) void load(0);
      else setOffset(0);
    } catch (error) {
      setActionError(
        error instanceof Error
          ? t(error.message)
          : t("Failed to create invitation codes."),
      );
    } finally {
      setCreating(false);
    }
  }

  async function copyCodes() {
    if (!issuedCodes || copying) return;
    setCopying(true);
    setActionError("");
    try {
      await navigator.clipboard.writeText(issuedCodes.join("\n"));
      setCopied(true);
    } catch {
      setActionError(
        t("Could not copy invitation codes. Select and copy them manually."),
      );
    } finally {
      setCopying(false);
    }
  }

  async function handleRevoke() {
    if (!revokeTarget || revoking) return;
    setRevoking(true);
    setActionError("");
    try {
      const result = await revokeInvite(revokeTarget.id);
      setItems((current) =>
        current.map((item) =>
          item.id === result.invite.id ? result.invite : item,
        ),
      );
      setRevokeTarget(null);
    } catch (error) {
      setActionError(
        error instanceof Error
          ? t(error.message)
          : t("Failed to revoke invitation code."),
      );
      setRevokeTarget(null);
    } finally {
      setRevoking(false);
    }
  }

  return (
    <section className="space-y-6" aria-label={t("Invitation codes")}>
      <p className="text-sm text-[var(--muted-foreground)]">
        {t(
          "Invitation codes create active Standard accounts. Model access must be configured by the user or granted by an administrator.",
        )}
      </p>
      <form
        onSubmit={handleCreate}
        className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5"
      >
        <h2 className="mb-4 font-medium">{t("Create invitation codes")}</h2>
        <fieldset
          disabled={creating || issuedCodes !== null}
          className="space-y-4 disabled:opacity-60"
        >
          <div className="grid gap-4 sm:grid-cols-3">
            <label className="text-sm">
              {t("Number of codes")}
              <input
                type="number"
                min={1}
                max={100}
                required
                value={batchCount}
                onChange={(event) => setBatchCount(event.target.value)}
                className={inputClass}
              />
            </label>
            <label className="text-sm">
              {t("Uses per code")}
              <input
                type="number"
                min={1}
                max={1000}
                required
                value={maxUses}
                onChange={(event) => setMaxUses(event.target.value)}
                className={inputClass}
              />
            </label>
            <label className="text-sm">
              {t("Expires in days")}
              <input
                type="number"
                min={1}
                max={365}
                required={!noExpiry}
                disabled={noExpiry}
                value={expiresInDays}
                onChange={(event) => setExpiresInDays(event.target.value)}
                className={inputClass}
              />
            </label>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={noExpiry}
              onChange={(event) => setNoExpiry(event.target.checked)}
            />
            {t("Never expires")}
          </label>
          <label className="block text-sm">
            {t("Note")}
            <input
              type="text"
              maxLength={200}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              className={inputClass}
            />
          </label>
          <button
            type="submit"
            className={`${buttonClass} bg-[var(--foreground)] text-[var(--background)]`}
          >
            {creating ? t("Creating…") : t("Create invitation codes")}
          </button>
        </fieldset>
      </form>

      {actionError && (
        <p
          role="alert"
          className="rounded-lg bg-red-500/10 p-3 text-sm text-red-500"
        >
          {actionError}
        </p>
      )}

      {issuedCodes && (
        <section
          aria-label={t("Generated invitation codes")}
          className="space-y-3 rounded-2xl border border-blue-500/30 bg-blue-500/10 p-5"
        >
          <h2 className="font-medium">{t("Save these codes now")}</h2>
          <p className="text-sm">
            {t(
              "Full codes are shown only once. Closing this result or leaving this tab discards them. Lost codes must be revoked and replaced.",
            )}
          </p>
          <textarea
            aria-label={t("Generated invitation codes")}
            readOnly
            value={issuedCodes.join("\n")}
            rows={Math.min(8, issuedCodes.length + 1)}
            spellCheck={false}
            className={`${inputClass} font-mono`}
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void copyCodes()}
              disabled={copying}
              className={buttonClass}
            >
              {copied ? t("Copied") : t("Copy all codes")}
            </button>
            <button
              type="button"
              disabled={copying}
              onClick={() => {
                setIssuedCodes(null);
                setCopied(false);
                setActionError("");
              }}
              className={buttonClass}
            >
              {t("Close and discard codes")}
            </button>
          </div>
        </section>
      )}

      <div className="flex items-center justify-between gap-3">
        <h2 className="font-medium">{t("Issued invitation codes")}</h2>
        <button
          type="button"
          disabled={loading}
          onClick={() => void load(offset)}
          className={buttonClass}
        >
          {t("Refresh")}
        </button>
      </div>
      {loading ? (
        <p role="status" className="text-sm">
          {t("Loading invitation codes…")}
        </p>
      ) : loadError ? (
        <div role="alert" className="space-y-2 text-sm text-red-500">
          <p>{loadError}</p>
          <button
            type="button"
            onClick={() => void load(offset)}
            className={buttonClass}
          >
            {t("Retry")}
          </button>
        </div>
      ) : items.length === 0 ? (
        <p className="text-sm text-[var(--muted-foreground)]">
          {t("No invitation codes yet.")}
        </p>
      ) : (
        <ul className="space-y-3">
          {items.map((invite) => (
            <li
              key={invite.id}
              className="space-y-3 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <code className="font-mono">
                    ••••-••••-{invite.code_hint}
                  </code>
                  <span className="rounded-full bg-[var(--muted)] px-2 py-1 text-xs">
                    {statusLabel(invite.status)}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setRevokeTarget(invite)}
                  disabled={invite.status === "revoked" || revoking || loading}
                  className={buttonClass}
                >
                  {invite.status === "revoked" ? t("Revoked") : t("Revoke")}
                </button>
              </div>
              <dl className="grid gap-2 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-[var(--muted-foreground)]">
                    {t("Uses")}
                  </dt>
                  <dd>
                    {invite.used_count} / {invite.max_uses}
                  </dd>
                </div>
                <div>
                  <dt className="text-[var(--muted-foreground)]">
                    {t("Expires")}
                  </dt>
                  <dd>{formatDate(invite.expires_at)}</dd>
                </div>
                <div>
                  <dt className="text-[var(--muted-foreground)]">
                    {t("Created")}
                  </dt>
                  <dd>{formatDate(invite.created_at)}</dd>
                </div>
                <div>
                  <dt className="text-[var(--muted-foreground)]">
                    {t("Note")}
                  </dt>
                  <dd className="break-words">{invite.note || "—"}</dd>
                </div>
              </dl>
              <details className="text-sm">
                <summary className="cursor-pointer">
                  {t("Redemption history ({{count}})", {
                    count: invite.redemptions.length,
                  })}
                </summary>
                {invite.redemptions.length === 0 ? (
                  <p className="mt-2 text-[var(--muted-foreground)]">
                    {t("No redemptions yet.")}
                  </p>
                ) : (
                  <ul className="mt-2 space-y-2">
                    {invite.redemptions.map((redemption, index) => (
                      <li
                        key={`${redemption.user_id}-${index}`}
                        className="flex flex-wrap justify-between gap-2"
                      >
                        <span>
                          {redemption.username}
                          <span className="ml-2 text-xs text-[var(--muted-foreground)]">
                            {redemption.user_id}
                          </span>
                        </span>
                        <time dateTime={redemption.redeemed_at}>
                          {formatDate(redemption.redeemed_at)}
                        </time>
                      </li>
                    ))}
                  </ul>
                )}
              </details>
            </li>
          ))}
        </ul>
      )}
      {!loadError && total > PAGE_SIZE && (
        <nav
          aria-label={t("Invitation code pages")}
          className="flex items-center justify-between gap-3 text-sm"
        >
          <button
            type="button"
            disabled={loading || offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            className={buttonClass}
          >
            {t("Previous")}
          </button>
          <span>
            {t("{{start}}–{{end}} of {{total}}", {
              start: offset + 1,
              end: Math.min(offset + PAGE_SIZE, total),
              total,
            })}
          </span>
          <button
            type="button"
            disabled={loading || offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
            className={buttonClass}
          >
            {t("Next")}
          </button>
        </nav>
      )}
      <ConfirmDialog
        open={revokeTarget !== null}
        title={t("Revoke invitation code")}
        confirmLabel={t("Revoke")}
        busyLabel={t("Revoking…")}
        busy={revoking}
        tone="danger"
        onConfirm={() => void handleRevoke()}
        onCancel={() => setRevokeTarget(null)}
      >
        <p>
          {t(
            "This code will no longer allow registration. Existing accounts will remain active.",
          )}
        </p>
        <p className="mt-2 font-mono">••••-••••-{revokeTarget?.code_hint}</p>
      </ConfirmDialog>
    </section>
  );
}
