import { apiFetch, apiUrl } from "@/lib/api";

export interface InviteRecord {
  id: string;
  code_hint: string;
  created_at: string;
  created_by: string;
  expires_at: string | null;
  max_uses: number;
  used_count: number;
  note: string;
  revoked_at: string | null;
  revoked_by: string | null;
  status: "active" | "expired" | "exhausted" | "revoked";
  redemptions: { user_id: string; username: string; redeemed_at: string }[];
}

export interface CreateInvitesRequest {
  batch_count: number;
  max_uses: number;
  expires_in_days: number | null;
  note: string;
}

async function readResponse<T>(
  response: Response,
  fallback: string,
): Promise<T> {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail) && typeof detail[0]?.msg === "string"
          ? detail[0].msg
          : fallback,
    );
  }
  return data as T;
}

export async function listInvites(
  offset = 0,
  limit = 50,
): Promise<{ items: InviteRecord[]; total: number }> {
  return readResponse(
    await apiFetch(
      apiUrl(`/api/auth/invites?offset=${offset}&limit=${limit}`),
      {
        cache: "no-store",
      },
    ),
    "Failed to load invitation codes.",
  );
}

/** Plaintext codes are returned only here; callers must keep them in memory only. */
export async function createInvites(
  body: CreateInvitesRequest,
): Promise<{ invites: (InviteRecord & { code: string })[] }> {
  return readResponse(
    await apiFetch(apiUrl("/api/auth/invites"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
    "Failed to create invitation codes.",
  );
}

export async function revokeInvite(
  id: string,
): Promise<{ invite: InviteRecord; ok: boolean }> {
  return readResponse(
    await apiFetch(
      apiUrl(`/api/auth/invites/${encodeURIComponent(id)}/revoke`),
      {
        method: "POST",
      },
    ),
    "Failed to revoke invitation code.",
  );
}
