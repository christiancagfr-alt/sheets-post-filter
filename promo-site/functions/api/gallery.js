import { isAuthenticated, json, readJsonBody } from "../_lib/auth.js";

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== "POST") {
    return json({ ok: false, error: "Method not allowed" }, 405);
  }

  if (!(await isAuthenticated(request, env))) {
    return json({ ok: false, error: "需要访问密码。" }, 401);
  }

  const scriptUrl = String(
    env.APPS_SCRIPT_URL ||
      // 兜底：与历史 Vercel 部署相同，避免 secret 未注入时整站无法同步
      "https://script.google.com/macros/s/AKfycbx1OaBPC9_w-xNlmBSf89pkpKDP0bzMeOkAHXCyEDvBCN4eMy9Iu6abEudHMViQUXiYEA/exec"
  ).trim();
  if (!scriptUrl) {
    return json({ ok: false, error: "APPS_SCRIPT_URL is not configured." }, 500);
  }

  const body = await readJsonBody(request);
  const payload = {
    ...body,
    secret: env.APPS_SCRIPT_API_SECRET || body.secret || "",
  };

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 55000);
    const response = await fetch(scriptUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    clearTimeout(timeout);

    const text = await response.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      data = { ok: false, error: text || response.statusText };
    }
    return json(data, response.ok ? 200 : response.status || 502);
  } catch (error) {
    const isAbort = error && error.name === "AbortError";
    return json(
      {
        ok: false,
        error: isAbort ? "Apps Script request timed out." : error.message || String(error),
      },
      isAbort ? 504 : 502
    );
  }
}
