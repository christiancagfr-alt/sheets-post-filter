/**
 * Shared auth helpers for Cloudflare Pages Functions.
 */

export const COOKIE_NAME = "q_gallery_auth";

export function getAccessPassword(env) {
  return String(env.ACCESS_PASSWORD || "").trim();
}

export function getSigningSecret(env) {
  return (
    String(env.AUTH_SECRET || env.APPS_SCRIPT_API_SECRET || getAccessPassword(env) || "q-gallery").trim() ||
    "q-gallery"
  );
}

export async function sign(value, env) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(getSigningSecret(env)),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(String(value || "")));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function parseCookies(header = "") {
  return Object.fromEntries(
    String(header || "")
      .split(";")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const index = part.indexOf("=");
        return index === -1
          ? [part, ""]
          : [part.slice(0, index), decodeURIComponent(part.slice(index + 1))];
      })
  );
}

export async function isAuthenticated(request, env) {
  const password = getAccessPassword(env);
  if (!password) return true;
  const cookies = parseCookies(request.headers.get("cookie") || "");
  const expected = await sign(password, env);
  return cookies[COOKIE_NAME] === expected;
}

export async function authCookieHeader(env, clear = false) {
  if (clear) {
    return `${COOKIE_NAME}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;
  }
  const password = getAccessPassword(env);
  const token = await sign(password, env);
  return `${COOKIE_NAME}=${encodeURIComponent(token)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000`;
}

export function json(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...extraHeaders,
    },
  });
}

export async function readJsonBody(request) {
  try {
    const text = await request.text();
    if (!text) return {};
    return JSON.parse(text);
  } catch {
    return {};
  }
}
