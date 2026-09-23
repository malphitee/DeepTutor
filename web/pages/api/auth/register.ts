import type { NextApiRequest, NextApiResponse } from "next";

import { forwardRegistration } from "@/lib/server/registration-proxy";

export const config = { api: { bodyParser: { sizeLimit: "8kb" } } };

export default async function registerBridge(req: NextApiRequest, res: NextApiResponse) {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    res.status(405).json({ detail: "Method not allowed" });
    return;
  }
  if (!String(req.headers["content-type"] ?? "").toLowerCase().startsWith("application/json")) {
    res.status(415).json({ detail: "Registration requires JSON" });
    return;
  }
  try {
    const forwarded = req.headers["x-forwarded-for"];
    const upstream = await forwardRegistration(
      req.socket.remoteAddress ?? "",
      Array.isArray(forwarded) ? forwarded.join(",") : forwarded ?? "",
      req.body,
    );
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) res.setHeader("Retry-After", retryAfter);
    res.setHeader("Content-Type", upstream.headers.get("content-type") ?? "application/json");
    res.status(upstream.status).send(await upstream.text());
  } catch {
    res.status(503).json({
      detail: "Account registration is temporarily unavailable. Please try again later.",
      error_code: "registration_proxy_unavailable",
    });
  }
}
