import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const BACKEND_URL = process.env.INTERNAL_BACKEND_URL || "http://localhost:8000";
const CLIENT_SLUG = /^[a-z0-9][a-z0-9-]{1,98}$/;
// Same shape the backend accepts (app/db/models/client.py): host or *.host, optional :port.
const HOST_PATTERN = /^(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)*(:\d{1,5})?$/;
const DENY_FRAMING = "frame-ancestors 'none'";

function frameAncestorsFor(domains: unknown): string {
  if (!Array.isArray(domains)) return DENY_FRAMING;
  const safe = domains.filter(
    (d): d is string => typeof d === "string" && HOST_PATTERN.test(d),
  );
  return safe.length ? `frame-ancestors 'self' ${safe.join(" ")}` : `frame-ancestors 'self'`;
}

export async function middleware(request: NextRequest) {
  const response = NextResponse.next();
  const clientSlug = request.nextUrl.searchParams.get("client");

  // Without a valid client slug, the embed page is not framed anywhere.
  if (!clientSlug || !CLIENT_SLUG.test(clientSlug)) {
    response.headers.set("Content-Security-Policy", DENY_FRAMING);
    return response;
  }

  try {
    // Server-to-server call. It carries no browser Origin, so it uses the unauthenticated
    // frame-ancestors lookup rather than the origin-checked widget config.
    const res = await fetch(
      `${BACKEND_URL}/api/v1/widget/frame-ancestors?client=${encodeURIComponent(clientSlug)}`,
      { cache: "no-store", signal: AbortSignal.timeout(3000) },
    );
    if (!res.ok) {
      response.headers.set("Content-Security-Policy", DENY_FRAMING);
      return response;
    }
    const body = (await res.json()) as { allowed_domains?: unknown };
    response.headers.set(
      "Content-Security-Policy",
      `${frameAncestorsFor(body.allowed_domains)};`,
    );
  } catch {
    // Backend down or slow: fail closed.
    response.headers.set("Content-Security-Policy", DENY_FRAMING);
  }

  return response;
}

export const config = {
  matcher: ["/embed/:path*"],
};
