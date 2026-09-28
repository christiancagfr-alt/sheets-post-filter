const crypto = require("crypto");

const COOKIE_NAME = "q_gallery_auth";

function getAccessPassword() {
  return process.env.ACCESS_PASSWORD || "";
}

function getSigningSecret() {
  return process.env.AUTH_SECRET || process.env.APPS_SCRIPT_API_SECRET || getAccessPassword() || "q-gallery";
}

function sign(value) {
  return crypto
    .createHmac("sha256", getSigningSecret())
    .update(value)
    .digest("hex");
}

function parseCookies(header = "") {
  return Object.fromEntries(
    header
      .split(";")
      .map(part => part.trim())
      .filter(Boolean)
      .map(part => {
        const index = part.indexOf("=");
        return index === -1
          ? [part, ""]
          : [part.slice(0, index), decodeURIComponent(part.slice(index + 1))];
      })
  );
}

function isAuthenticated(req) {
  const password = getAccessPassword();
  if (!password) return true;
  const cookies = parseCookies(req.headers.cookie || "");
  return cookies[COOKIE_NAME] === sign(password);
}

function send(res, status, payload) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify(payload));
}

module.exports = async function handler(req, res) {
  if (req.method !== "POST") {
    send(res, 405, { ok: false, error: "Method not allowed" });
    return;
  }

  if (!isAuthenticated(req)) {
    send(res, 401, { ok: false, error: "需要访问密码。" });
    return;
  }

  const scriptUrl = process.env.APPS_SCRIPT_URL;
  if (!scriptUrl) {
    send(res, 500, { ok: false, error: "APPS_SCRIPT_URL is not configured." });
    return;
  }

  const body = typeof req.body === "object" && req.body ? req.body : {};
  const payload = {
    ...body,
    secret: process.env.APPS_SCRIPT_API_SECRET || body.secret || ""
  };

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 55000);
    const response = await fetch(scriptUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal
    });
    clearTimeout(timeout);

    const text = await response.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch (error) {
      data = { ok: false, error: text || response.statusText };
    }
    send(res, response.ok ? 200 : response.status, data);
  } catch (error) {
    const isAbort = error && error.name === "AbortError";
    send(res, 504, {
      ok: false,
      error: isAbort ? "Apps Script request timed out." : error.message || String(error)
    });
  }
};
