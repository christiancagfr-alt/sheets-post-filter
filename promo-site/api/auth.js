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

function setAuthCookie(res) {
  const password = getAccessPassword();
  res.setHeader(
    "Set-Cookie",
    `${COOKIE_NAME}=${encodeURIComponent(sign(password))}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000`
  );
}

function clearAuthCookie(res) {
  res.setHeader(
    "Set-Cookie",
    `${COOKIE_NAME}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`
  );
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

  const body = typeof req.body === "object" && req.body ? req.body : {};
  const action = body.action || "status";

  if (action === "status") {
    send(res, 200, { ok: true, authenticated: isAuthenticated(req) });
    return;
  }

  if (action === "logout") {
    clearAuthCookie(res);
    send(res, 200, { ok: true, authenticated: false });
    return;
  }

  if (action === "login") {
    const password = getAccessPassword();
    if (!password || body.password === password) {
      setAuthCookie(res);
      send(res, 200, { ok: true, authenticated: true });
      return;
    }
    send(res, 401, { ok: false, authenticated: false, error: "密码错误。" });
    return;
  }

  send(res, 400, { ok: false, error: "Unknown action" });
};
