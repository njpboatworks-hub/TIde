// Shared helpers for the Cloudflare Pages Functions in functions/api (Nockd's server side).
// Settings come from the Cloudflare project (Settings -> Variables and Secrets), never from this code.
const DEFAULT_ORIGINS = "https://njpboatworks.com,https://www.njpboatworks.com,capacitor://localhost";

// capacitor://localhost is the Nockd iPhone app; the nockd*.pages.dev pattern covers the
// Cloudflare address (Cloudflare adds a few letters if "nockd" is taken) and its preview copies
export function okOrigin(env, o) {
  const allowed = (env.ALLOWED_ORIGINS || DEFAULT_ORIGINS).split(",").map((s) => s.trim()).filter(Boolean);
  return allowed.includes(o) || /^https:\/\/([a-z0-9-]+\.)?nockd[a-z0-9-]*\.pages\.dev$/.test(o);
}

// CORS headers plus a JSON reply helper. Returns { cors, json, origin, allowed }.
export function replies(env, request, methods) {
  const origin = request.headers.get("origin") || "";
  const allowed = !origin || okOrigin(env, origin);
  const cors = origin && allowed ? { "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Methods": methods, Vary: "Origin" } : {};
  const json = (status, body, extra = {}) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...cors, ...extra } });
  return { origin, allowed, cors, json };
}

export async function sha256hex(s) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function randomHex(bytes) {
  return [...crypto.getRandomValues(new Uint8Array(bytes))].map((b) => b.toString(16).padStart(2, "0")).join("");
}
