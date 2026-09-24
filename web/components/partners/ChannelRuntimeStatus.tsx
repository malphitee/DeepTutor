"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Loader2, QrCode, RefreshCw, Wifi } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  getPartnerChannelRuntime,
  reloadPartnerChannels,
  startPartner,
  type PartnerChannelRuntimeEntry,
} from "@/lib/partners-api";

const REFRESH_MS = 2500;
const STALE_STATUS_MS = 60_000;
const REQUEST_TIMEOUT_MS = 10_000;

const BUSY_STATUSES = new Set(["connecting", "starting"]);

export default function ChannelRuntimeStatus({
  partnerId,
  channel,
  enabled,
}: {
  partnerId: string;
  channel: string;
  enabled: boolean;
}) {
  const { t } = useTranslation();
  const [entry, setEntry] = useState<PartnerChannelRuntimeEntry | null>(null);
  const [pollError, setPollError] = useState(false);
  const [stale, setStale] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [retryGeneration, setRetryGeneration] = useState(0);
  const [runtimeRunning, setRuntimeRunning] = useState(false);
  const [actionError, setActionError] = useState("");
  const busySinceRef = useRef<number | null>(null);
  const epochRef = useRef(0);
  const retryControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    epochRef.current += 1;
    setEntry(null);
    setPollError(false);
    setStale(false);
    setActionError("");
    setRetrying(false);
    busySinceRef.current = null;
    if (!enabled) {
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let requestController: AbortController | null = null;
    const watchdog = setInterval(() => {
      if (busySinceRef.current !== null) {
        setStale(Date.now() - busySinceRef.current >= STALE_STATUS_MS);
      }
    }, REFRESH_MS);

    const refresh = async () => {
      requestController = new AbortController();
      const controller = requestController;
      const deadline = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
      try {
        const response = await getPartnerChannelRuntime(
          partnerId,
          controller.signal,
        );
        if (cancelled) return;
        const now = Date.now();
        const next = response.channels[channel] ?? null;
        if (!next?.setup?.status) throw new Error("Missing channel status");
        setEntry(next);
        setRuntimeRunning(response.running);
        setPollError(false);
        if (BUSY_STATUSES.has(next.setup.status)) {
          busySinceRef.current =
            next.setup_updated_at && next.setup_updated_at > 0
              ? Math.min(now, next.setup_updated_at * 1000)
              : (busySinceRef.current ?? now);
          setStale(now - busySinceRef.current >= STALE_STATUS_MS);
        } else {
          busySinceRef.current = null;
          setStale(false);
          if (["connected", "running"].includes(next.setup.status))
            setActionError("");
        }
      } catch {
        if (!cancelled) setPollError(true);
      } finally {
        clearTimeout(deadline);
        if (!cancelled) timer = setTimeout(refresh, REFRESH_MS);
      }
    };

    void refresh();
    return () => {
      cancelled = true;
      epochRef.current += 1;
      requestController?.abort();
      retryControllerRef.current?.abort();
      retryControllerRef.current = null;
      if (timer) clearTimeout(timer);
      clearInterval(watchdog);
    };
  }, [channel, enabled, partnerId, retryGeneration]);

  const retry = async () => {
    if (pollError) {
      setRetryGeneration((value) => value + 1);
      return;
    }
    const epoch = epochRef.current;
    const controller = new AbortController();
    retryControllerRef.current = controller;
    const deadline = setTimeout(() => controller.abort(), 30_000);
    setRetrying(true);
    setActionError("");
    try {
      if (runtimeRunning)
        await reloadPartnerChannels(partnerId, controller.signal);
      else await startPartner(partnerId, controller.signal);
      if (epoch !== epochRef.current) return;
      setRetryGeneration((value) => value + 1);
    } catch (error) {
      if (epoch !== epochRef.current) return;
      setActionError(
        error instanceof Error && error.name !== "AbortError"
          ? error.message
          : "Channel restart failed. Check the server and try again.",
      );
    } finally {
      clearTimeout(deadline);
      if (epoch === epochRef.current) {
        retryControllerRef.current = null;
        setRetrying(false);
      }
    }
  };

  const setup = entry?.setup;
  const status = setup?.status || "";
  const hasRuntimeError = pollError || stale || Boolean(actionError);
  const label = useMemo(() => {
    if (pollError) return t("Channel status unavailable");
    if (hasRuntimeError) return t("Connection failed");
    switch (setup?.status) {
      case "connected":
        return t("Connected");
      case "connecting":
      case "starting":
        return t("Connecting");
      case "running":
        return t("Listener running");
      case "waiting_for_scan":
        return t("Waiting for scan");
      case "action_required":
        return channel === "weixin"
          ? t("Waiting for scan")
          : t("Configuration required");
      case "unavailable":
        return t("Unavailable");
      case "error":
        return t("Connection failed");
      case "disconnected":
        return t("Not connected");
      default:
        return "";
    }
  }, [channel, hasRuntimeError, pollError, setup?.status, t]);

  if ((!setup?.status && !hasRuntimeError) || !label) return null;

  const isError =
    hasRuntimeError || status === "error" || status === "unavailable";
  const isBusy = !hasRuntimeError && BUSY_STATUSES.has(status);
  const isConnected =
    !hasRuntimeError && (status === "connected" || status === "running");
  const canRetry = isError;
  const message = pollError
    ? "Unable to read channel status. Refresh to try again."
    : actionError ||
      (stale
        ? "Channel startup timed out. Retry the channel."
        : setup?.message);

  return (
    <div
      role="status"
      className={`rounded-lg border px-3 py-2.5 text-[11px] ${
        isError
          ? "border-red-500/35 bg-red-500/10 text-red-700 dark:text-red-300"
          : "border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)]"
      }`}
    >
      <div className="flex items-center gap-2 font-medium">
        {isBusy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : isError ? (
          <AlertCircle className="h-3.5 w-3.5" aria-hidden />
        ) : isConnected ? (
          <Wifi className="h-3.5 w-3.5 text-emerald-500" aria-hidden />
        ) : (
          <QrCode className="h-3.5 w-3.5 text-[var(--primary)]" aria-hidden />
        )}
        <span>
          {t("Status")}: {label}
        </span>
      </div>
      {message && <p className="mt-1.5 leading-relaxed">{t(message)}</p>}
      {setup?.qr_data_url ? (
        /* QR data URLs are generated in-process and cannot use Next's image
           optimizer; this mirrors the existing channel-onboarding panel. */
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={setup.qr_data_url}
          alt={t("Scan to connect")}
          className="mt-2 h-[148px] w-[148px] rounded-md bg-white p-1.5"
        />
      ) : setup?.qr_payload ? (
        <p className="mt-2 break-all font-mono text-[10px]">
          {setup.qr_payload}
        </p>
      ) : null}
      {canRetry && (
        <button
          type="button"
          onClick={() => void retry()}
          disabled={retrying}
          className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-2 py-1 font-medium text-[var(--foreground)] hover:bg-[var(--muted)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RefreshCw
            className={`h-3 w-3 ${retrying ? "animate-spin" : ""}`}
            aria-hidden
          />
          {retrying
            ? t("Retrying…")
            : pollError
              ? t("Refresh status")
              : t("Try again")}
        </button>
      )}
    </div>
  );
}
