/** Server-only bridge: the socket peer, not a browser header, identifies a registrant. */
import { createHash, createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { isIP } from "node:net";
import path from "node:path";

import { resolveBackendApiBase } from "@/lib/backend-runtime-config";

export function normalizeRegistrationIp(value: string): string | null {
  const address = value.trim();
  if (address.includes("%") || !isIP(address)) return null;
  if (isIP(address) === 4) return address;
  const normalized = new URL(`http://[${address}]/`).hostname.slice(1, -1);
  const mapped = /^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/i.exec(normalized);
  if (mapped) {
    const high = Number.parseInt(mapped[1], 16);
    const low = Number.parseInt(mapped[2], 16);
    return `${high >> 8}.${high & 255}.${low >> 8}.${low & 255}`;
  }
  return normalized;
}

export function registrationClientIp(
  socketAddress: string,
  forwardedFor: string,
  trustedProxies: string[],
): string {
  const peer = normalizeRegistrationIp(socketAddress);
  if (!peer) throw new Error("Registration socket peer is unavailable");
  const trusted = new Set(trustedProxies.map(normalizeRegistrationIp).filter(Boolean));
  const chain = forwardedFor ? forwardedFor.split(",") : [];
  if (chain.length > 32) return peer;
  let current = peer;
  for (const raw of chain.reverse()) {
    if (!trusted.has(current)) break;
    const candidate = normalizeRegistrationIp(raw);
    if (!candidate) return peer;
    current = candidate;
  }
  return current;
}

export function registrationProof(
  secret: string,
  peer: string,
  timestamp: string,
  body: string,
): string {
  const digest = createHash("sha256").update(body, "utf8").digest("hex");
  const payload = `${timestamp}\nPOST\n/api/auth/register\n${peer}\n${digest}`;
  return createHmac("sha256", secret).update(payload).digest("hex");
}

export async function forwardRegistration(
  socketAddress: string,
  forwardedFor: string,
  body: unknown,
  options: {
    runtimeHome?: string;
    backendBase?: string;
    fetcher?: typeof fetch;
    now?: number;
  } = {},
): Promise<Response> {
  const home = options.runtimeHome ?? process.env.DEEPTUTOR_HOME ?? path.resolve(process.cwd(), "..");
  // Both processes use the runtime data tree. The API creates this separate
  // owner-only key on startup; the frontend never reads the JWT signing key.
  const secret = readFileSync(path.join(home, "data/system/auth/registration_proxy_secret"), "utf8").trim();
  if (!/^[0-9a-f]{64}$/.test(secret)) throw new Error("Registration forwarding is unavailable");
  let trustedProxies: string[] = [];
  try {
    const settings = JSON.parse(readFileSync(path.join(home, "data/user/settings/auth.json"), "utf8"));
    if (Array.isArray(settings.registration_trusted_proxies)) {
      trustedProxies = settings.registration_trusted_proxies
        .slice(0, 32)
        .filter((value: unknown): value is string => typeof value === "string");
    }
  } catch {
    // Missing or invalid trust config grants no forwarding trust.
  }
  const peer = registrationClientIp(socketAddress, forwardedFor, trustedProxies);
  const payload = JSON.stringify(body);
  if (!payload || Buffer.byteLength(payload, "utf8") > 8192) throw new Error("Invalid registration payload");
  const timestamp = String(Math.floor((options.now ?? Date.now()) / 1000));
  return (options.fetcher ?? fetch)(new URL("/api/auth/register", options.backendBase ?? resolveBackendApiBase()), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-deeptutor-registration-ip": peer,
      "x-deeptutor-registration-time": timestamp,
      "x-deeptutor-registration-signature": registrationProof(secret, peer, timestamp, payload),
    },
    body: payload,
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.timeout(30_000),
  });
}
