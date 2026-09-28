import { isAuthenticated, authCookieHeader, json, readJsonBody, getAccessPassword } from "../_lib/auth.js";

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== "POST") {
    return json({ ok: false, error: "Method not allowed" }, 405);
  }

  const body = await readJsonBody(request);
  const action = body.action || "status";

  if (action === "status") {
    return json({ ok: true, authenticated: await isAuthenticated(request, env) });
  }

  if (action === "logout") {
    return json(
      { ok: true, authenticated: false },
      200,
      { "Set-Cookie": await authCookieHeader(env, true) }
    );
  }

  if (action === "login") {
    const password = getAccessPassword(env);
    if (!password || body.password === password) {
      return json(
        { ok: true, authenticated: true },
        200,
        { "Set-Cookie": await authCookieHeader(env, false) }
      );
    }
    return json({ ok: false, authenticated: false, error: "密码错误。" }, 401);
  }

  return json({ ok: false, error: "Unknown action" }, 400);
}
