/**
 * Drive 图片代理：浏览器直连 drive.google.com/uc 常被拦或返回 HTML。
 * GET /api/drive-image?id=FILE_ID
 * GET /api/drive-image?url=https://drive.google.com/file/d/ID/view
 */
const MAX_BYTES = 4 * 1024 * 1024;
const CACHE_SEC = 24 * 3600;

function driveFileId(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const fileMatch = text.match(/(?:drive|docs)\.google\.com\/file\/d\/([^/?#]+)/i);
  if (fileMatch) return fileMatch[1];
  const lh3 = text.match(/lh3\.googleusercontent\.com\/d\/([a-zA-Z0-9_-]+)/i);
  if (lh3) return lh3[1];
  if (/(?:drive|docs|usercontent)\.google\.com/i.test(text)) {
    const idMatch = text.match(/[?&]id=([^&#]+)/i);
    if (idMatch) return decodeURIComponent(idMatch[1]);
  }
  if (/^[a-zA-Z0-9_-]{20,}$/.test(text)) return text;
  return "";
}

function candidatesFor(id) {
  const safe = encodeURIComponent(id);
  return [
    `https://lh3.googleusercontent.com/d/${id}`,
    `https://drive.google.com/thumbnail?id=${safe}&sz=w1000`,
    `https://drive.google.com/uc?export=view&id=${safe}`,
    `https://drive.usercontent.google.com/download?id=${safe}&export=view`,
  ];
}

export async function onRequestGet(context) {
  const { request } = context;
  const reqUrl = new URL(request.url);
  const id = driveFileId(reqUrl.searchParams.get("id") || reqUrl.searchParams.get("url") || "");
  if (!id) {
    return new Response("Missing Drive file id", { status: 400, headers: { "Cache-Control": "no-store" } });
  }

  for (const target of candidatesFor(id)) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 12000);
      const upstream = await fetch(target, {
        redirect: "follow",
        signal: controller.signal,
        headers: {
          "User-Agent": "q-gallery-promo-drive-image/1.0",
          Accept: "image/*,*/*;q=0.8",
          Referer: "https://drive.google.com/",
        },
      });
      clearTimeout(timer);
      const type = String(upstream.headers.get("content-type") || "").toLowerCase();
      if (!upstream.ok) continue;
      if (type.includes("text/html")) continue;
      if (!type.startsWith("image/") && !type.includes("octet-stream") && type) continue;
      const buffer = await upstream.arrayBuffer();
      if (!buffer.byteLength || buffer.byteLength > MAX_BYTES) continue;
      return new Response(buffer, {
        status: 200,
        headers: {
          "Content-Type": type.startsWith("image/") ? type : "image/jpeg",
          "Cache-Control": `public, max-age=${CACHE_SEC}`,
          "Access-Control-Allow-Origin": "*",
        },
      });
    } catch {
      continue;
    }
  }
  return new Response("Drive image unavailable", { status: 404, headers: { "Cache-Control": "public, max-age=120" } });
}
