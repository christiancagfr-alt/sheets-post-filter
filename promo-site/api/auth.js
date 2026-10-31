const crypto = require("crypto");

const COOKIE_NAME = "q_gallery_auth";

function getAccessPassword() {
  return process.env.ACCESS_PASSWORD || "";
}

function getSigningSecret() {
  return process.env.AUTH_SECRET || process.env.APPS_SCRIPT_API_SECRET || getAccessPassword() || "";
}

function secretsEqual(left, right) {
  const a = crypto.createHash("sha256").update("cmp|" + String(left || "")).digest();
  const b = crypto.createHash("sha256").update("cmp|" + String(right || "")).digest();
  return crypto.timingSafeEqual(a, b);
}

function sign(value) {
  const secret = getSigningSecret();
  if (!secret) return "";
  return crypto
    .createHmac("sha256", secret)
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
  const [expires, mac] = String(cookies[COOKIE_NAME] || "").split(".");
  if (!/^\d+$/.test(expires) || Number(expires) < Date.now()) return false;
  return secretsEqual(mac, sign(`session|${expires}|${password}`));
}

function setAuthCookie(res) {
  const password = getAccessPassword();
  const expires = String(Date.now() + 2592000000);
  const token = expires + "." + sign(`session|${expires}|${password}`);
  res.setHeader(
    "Set-Cookie",
    `${COOKIE_NAME}=${encodeURIComponent(token)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000`
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
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Content-Type-Options", "nosniff");
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
    if (!password || secretsEqual(body.password, password)) {
      setAuthCookie(res);
      send(res, 200, { ok: true, authenticated: true });
      return;
    }
    send(res, 401, { ok: false, authenticated: false, error: "密码错误。" });
    return;
  }

  send(res, 400, { ok: false, error: "Unknown action" });
};
