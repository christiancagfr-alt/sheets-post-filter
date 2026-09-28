# -*- coding: utf-8 -*-
"""把 promo-site 一键部署到用户自己的 Cloudflare：登录账号、填域名、生成密钥。"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from fetch_posts import RESOURCE_DIR, SCRIPT_DIR

LogFn = Callable[[str], None]
API = "https://api.cloudflare.com/client/v4"
TOKEN_CREATE_URL = (
    "https://dash.cloudflare.com/profile/api-tokens"
    "?permissionGroupKeys="
    + urllib.parse.quote(
        '[{"key":"pages","type":"edit"},{"key":"workers_r2_storage","type":"edit"},'
        '{"key":"dns","type":"edit"},{"key":"account_settings","type":"read"}]'
    )
    + "&name="
    + urllib.parse.quote("数据汇总工具-图库部署")
)
SKIP_DIR_NAMES = {
    "functions",
    "api",
    "google-apps-script",
    "node_modules",
    ".wrangler",
    "__pycache__",
}
SKIP_FILE_NAMES = {
    "wrangler.toml",
    "vercel.json",
    "_routes.json",
    "gallery-worker.js",
    "_test_publish_body.json",
    "q-gallery-cache-manifest.json",
}
SKIP_SUFFIXES = {".md", ".map"}


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def token_store_path() -> Path:
    return SCRIPT_DIR / "cloudflare.json"


def load_cf_state() -> dict[str, Any]:
    path = token_store_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cf_state(data: dict[str, Any]) -> Path:
    path = token_store_path()
    current = load_cf_state()
    current.update({k: v for k, v in data.items() if v is not None})
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def promo_site_dir() -> Path:
    for candidate in (
        RESOURCE_DIR / "promo-site",
        SCRIPT_DIR / "promo-site",
        Path(__file__).resolve().parent / "promo-site",
    ):
        if (candidate / "index.html").exists() or (candidate / "gallery-worker.js").exists():
            return candidate
    return Path(__file__).resolve().parent / "promo-site"


def normalize_host(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^https?://", "", text, flags=re.I)
    text = text.split("/")[0].strip().strip(".")
    return text.lower()


def slug_from_host(host: str, prefix: str = "gallery") -> str:
    host = normalize_host(host)
    raw = re.sub(r"[^a-z0-9]+", "-", host).strip("-")
    slug = f"{prefix}-{raw}" if raw else prefix
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if len(slug) > 58:
        slug = slug[:58].rstrip("-")
    if not re.match(r"^[a-z0-9]", slug):
        slug = f"g{slug}"
    if not re.search(r"[a-z0-9]$", slug):
        slug = f"{slug}0"
    return slug


def generate_publish_secret() -> str:
    return secrets.token_urlsafe(32)


def rewrite_gallery_html(text: str, host: str) -> str:
    origin = f"https://{normalize_host(host)}"
    manifest = f"{origin}/cdn/promo/manifest.json"
    text = re.sub(
        r"window\.Q_GALLERY_DRIVE_CACHE_MANIFEST_URL\s*=\s*[\"'][^\"']*[\"']",
        f'window.Q_GALLERY_DRIVE_CACHE_MANIFEST_URL = "{manifest}"',
        text,
    )
    text = re.sub(
        r"window\.Q_GALLERY_DRIVE_FALLBACK_MANIFEST_URL\s*=\s*[\"'][^\"']*[\"']",
        'window.Q_GALLERY_DRIVE_FALLBACK_MANIFEST_URL = ""',
        text,
    )
    return text


def pages_file_hash(data: bytes, rel_path: str) -> str:
    from blake3 import blake3

    ext = Path(rel_path.replace("\\", "/")).suffix[1:]
    payload = base64.b64encode(data) + ext.encode("ascii")
    return blake3(payload).hexdigest()[:32]


def _guess_type(rel_path: str) -> str:
    guessed, _ = mimetypes.guess_type(rel_path)
    return guessed or "application/octet-stream"


def collect_static_files(site_dir: Path, host: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    root = site_dir.resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        if parts[0] in SKIP_DIR_NAMES:
            continue
        if path.name in SKIP_FILE_NAMES or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        data = path.read_bytes()
        if path.suffix.lower() in {".html", ".js", ".css"}:
            text = data.decode("utf-8", errors="replace")
            data = rewrite_gallery_html(text, host).encode("utf-8")
        files.append(
            {
                "path": "/" + rel,
                "data": data,
                "hash": pages_file_hash(data, rel),
                "content_type": _guess_type(rel),
            }
        )
    return files


class CloudflareError(RuntimeError):
    pass


class CloudflareAPI:
    def __init__(self, token: str, log: LogFn | None = None):
        self.token = str(token or "").strip()
        if not self.token:
            raise CloudflareError("请先登录 Cloudflare（粘贴 API 令牌）")
        self.log = log or (lambda _m: None)

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Any = None,
        token: str | None = None,
        form: list[tuple[str, Any]] | None = None,
        raw_url: bool = False,
    ) -> Any:
        url = path if raw_url or path.startswith("http") else API + path
        data = None
        headers = {
            "Authorization": f"Bearer {token or self.token}",
            "User-Agent": "sheets-post-filter-gallery-deploy",
        }
        if form is not None:
            boundary = "----SheetsGallery" + secrets.token_hex(8)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            chunks: list[bytes] = []
            for name, value in form:
                chunks.append(f"--{boundary}\r\n".encode())
                if isinstance(value, tuple):
                    filename, content, content_type = value
                    chunks.append(
                        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
                    )
                    chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode())
                    chunks.append(content if isinstance(content, bytes) else str(content).encode("utf-8"))
                    chunks.append(b"\r\n")
                else:
                    chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
                    chunks.append(str(value).encode("utf-8"))
                    chunks.append(b"\r\n")
            chunks.append(f"--{boundary}--\r\n".encode())
            data = b"".join(chunks)
        elif payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=90) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise CloudflareError(self._format_error(exc.code, body)) from exc
        except urllib.error.URLError as exc:
            raise CloudflareError(f"无法连接 Cloudflare：{exc.reason}") from exc
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
        if isinstance(parsed, dict) and parsed.get("success") is False:
            raise CloudflareError(self._format_error(400, raw))
        return parsed

    @staticmethod
    def _format_error(status: int, body: str) -> str:
        try:
            data = json.loads(body)
            errors = data.get("errors") or []
            if errors:
                return f"Cloudflare HTTP {status}：{errors[0].get('message') or body[:300]}"
        except Exception:
            pass
        return f"Cloudflare HTTP {status}：{(body or '')[:300]}"

    def get(self, path: str, **kw) -> Any:
        return self._request("GET", path, **kw)

    def post(self, path: str, payload=None, **kw) -> Any:
        return self._request("POST", path, payload=payload, **kw)

    def patch(self, path: str, payload=None, **kw) -> Any:
        return self._request("PATCH", path, payload=payload, **kw)

    def result(self, data: Any) -> Any:
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data

    def list_accounts(self) -> list[dict[str, str]]:
        data = self.result(self.get("/accounts?per_page=50")) or []
        out = []
        for item in data if isinstance(data, list) else []:
            out.append({"id": str(item.get("id") or ""), "name": str(item.get("name") or item.get("id") or "")})
        return [row for row in out if row["id"]]

    def verify_token(self) -> dict[str, str]:
        accounts = self.list_accounts()
        if not accounts:
            raise CloudflareError("这个令牌读不到任何账号。请勾选 Account Settings 读取、Pages 编辑、R2 编辑、DNS 编辑。")
        return accounts[0]


def login_with_token(token: str, account_id: str = "") -> dict[str, Any]:
    api = CloudflareAPI(token)
    accounts = api.list_accounts()
    if not accounts:
        raise CloudflareError("登录失败：令牌无效或没有账号权限")
    chosen = next((item for item in accounts if item["id"] == account_id), accounts[0])
    state = {
        "api_token": token.strip(),
        "account_id": chosen["id"],
        "account_name": chosen["name"],
        "accounts": accounts,
    }
    save_cf_state(state)
    return state


def _ensure_bucket(api: CloudflareAPI, account_id: str, bucket: str, log: LogFn) -> None:
    try:
        api.get(f"/accounts/{account_id}/r2/buckets/{bucket}")
        log(f"R2 桶已存在：{bucket}")
        return
    except CloudflareError:
        pass
    log(f"正在创建 R2 桶 {bucket} …")
    api.post(f"/accounts/{account_id}/r2/buckets", {"name": bucket})


def _ensure_project(api: CloudflareAPI, account_id: str, project: str, log: LogFn) -> None:
    try:
        api.get(f"/accounts/{account_id}/pages/projects/{project}")
        log(f"Pages 项目已存在：{project}")
        return
    except CloudflareError:
        pass
    log(f"正在创建 Pages 项目 {project} …")
    api.post(
        f"/accounts/{account_id}/pages/projects",
        {"name": project, "production_branch": "production"},
    )


def _configure_project(
    api: CloudflareAPI,
    account_id: str,
    project: str,
    bucket: str,
    secret: str,
    public_base: str,
    log: LogFn,
) -> None:
    log("正在绑定 R2 并写入密钥 …")
    env_cfg = {
        "compatibility_date": "2024-11-01",
        "compatibility_flags": ["nodejs_compat"],
        "r2_buckets": {"GALLERY_CACHE": {"name": bucket}},
        "env_vars": {
            "CACHE_PUBLISH_SECRET": {"type": "secret_text", "value": secret},
            "PUBLIC_CACHE_BASE": {"type": "plain_text", "value": public_base},
        },
    }
    api.patch(
        f"/accounts/{account_id}/pages/projects/{project}",
        {"deployment_configs": {"production": env_cfg, "preview": env_cfg}},
    )


def _upload_assets(api: CloudflareAPI, account_id: str, project: str, files: list[dict[str, Any]], log: LogFn) -> dict[str, str]:
    token_data = api.result(api.get(f"/accounts/{account_id}/pages/projects/{project}/upload-token")) or {}
    jwt = str(token_data.get("jwt") or "")
    if not jwt:
        raise CloudflareError("没有拿到 Pages 上传令牌")
    hashes = [item["hash"] for item in files]
    missing_data = api.result(
        api.post("https://api.cloudflare.com/client/v4/pages/assets/check-missing", {"hashes": hashes}, token=jwt, raw_url=True)
    )
    missing = set(missing_data or [])
    to_upload = [item for item in files if item["hash"] in missing]
    log(f"静态文件 {len(files)} 个，需新上传 {len(to_upload)} 个")
    batch: list[dict[str, Any]] = []
    for item in to_upload:
        batch.append(
            {
                "key": item["hash"],
                "value": base64.b64encode(item["data"]).decode("ascii"),
                "metadata": {"contentType": item["content_type"]},
                "base64": True,
            }
        )
        if len(batch) >= 12:
            api.post("https://api.cloudflare.com/client/v4/pages/assets/upload", batch, token=jwt, raw_url=True)
            batch = []
    if batch:
        api.post("https://api.cloudflare.com/client/v4/pages/assets/upload", batch, token=jwt, raw_url=True)
    api.post(
        "https://api.cloudflare.com/client/v4/pages/assets/upsert-hashes",
        {"hashes": hashes},
        token=jwt,
        raw_url=True,
    )
    return {item["path"]: item["hash"] for item in files}


def _create_deployment(
    api: CloudflareAPI,
    account_id: str,
    project: str,
    manifest: dict[str, str],
    worker: bytes,
    log: LogFn,
) -> dict[str, Any]:
    log("正在发布 Pages 部署 …")
    form = [
        ("manifest", json.dumps(manifest, separators=(",", ":"))),
        ("branch", "production"),
        ("commit_dirty", "true"),
        ("commit_message", "gallery one-click deploy"),
        ("_worker.js", ("_worker.js", worker, "application/javascript+module")),
    ]
    data = api.result(api.post(f"/accounts/{account_id}/pages/projects/{project}/deployments", form=form))
    return data if isinstance(data, dict) else {}


def _attach_domain(api: CloudflareAPI, account_id: str, project: str, host: str, log: LogFn) -> str:
    pages_host = f"{project}.pages.dev"
    if host.endswith(".pages.dev"):
        return f"https://{host}"
    try:
        api.post(f"/accounts/{account_id}/pages/projects/{project}/domains", {"name": host})
        log(f"已添加自定义域 {host}")
    except CloudflareError as exc:
        if "already" in str(exc).lower() or "taken" in str(exc).lower() or "exists" in str(exc).lower():
            log(f"自定义域已存在：{host}")
        else:
            log(f"添加自定义域：{exc}")
    zone_name = host
    zone_id = ""
    while "." in zone_name:
        try:
            zones = api.result(api.get("/zones?name=" + urllib.parse.quote(zone_name))) or []
            if zones:
                zone_id = str(zones[0].get("id") or "")
                break
        except CloudflareError:
            pass
        zone_name = zone_name.split(".", 1)[1]
    if zone_id:
        record_name = host[: -len(zone_name)].rstrip(".") or "@"
        try:
            existing = api.result(
                api.get(f"/zones/{zone_id}/dns_records?type=CNAME&name={urllib.parse.quote(host)}")
            ) or []
            if existing:
                log("DNS CNAME 已存在")
            else:
                api.post(
                    f"/zones/{zone_id}/dns_records",
                    {"type": "CNAME", "name": record_name, "content": pages_host, "proxied": True, "ttl": 1},
                )
                log(f"已写入 DNS：{host} → {pages_host}")
        except CloudflareError as exc:
            log(f"DNS 未能自动写上（可稍后在 Cloudflare 手动加 CNAME）：{exc}")
    else:
        log(f"没找到域名所在区域。请把 {host} 的 CNAME 指到 {pages_host}，并在 Cloudflare 代理。")
    return f"https://{host}"


def deploy_gallery(
    host: str,
    *,
    token: str = "",
    account_id: str = "",
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Login must already have stored a token, or pass token=."""
    log = log or (lambda m: None)
    host = normalize_host(host)
    if not host or "." not in host:
        raise CloudflareError("请填写网站域名，例如 gallery.example.com")
    state = load_cf_state()
    token = (token or state.get("api_token") or "").strip()
    account_id = (account_id or state.get("account_id") or "").strip()
    api = CloudflareAPI(token, log=log)
    if not account_id:
        account_id = api.verify_token()["id"]
    project = str(state.get("project") or slug_from_host(host))
    bucket = str(state.get("bucket") or slug_from_host(host, "gallery-json"))
    if len(bucket) < 3:
        bucket = f"{bucket}-r2"
    secret = str(state.get("secret") or "").strip() or generate_publish_secret()
    site_dir = promo_site_dir()
    worker_path = site_dir / "gallery-worker.js"
    if not worker_path.exists():
        raise CloudflareError(f"找不到部署模板：{worker_path}")
    public_base = f"https://{host}/cdn"
    log(f"使用站点目录 {site_dir}")
    _ensure_bucket(api, account_id, bucket, log)
    _ensure_project(api, account_id, project, log)
    _configure_project(api, account_id, project, bucket, secret, public_base, log)
    files = collect_static_files(site_dir, host)
    if not files:
        raise CloudflareError("站点目录里没有可上传的静态文件")
    manifest = _upload_assets(api, account_id, project, files, log)
    worker = rewrite_gallery_html(worker_path.read_text(encoding="utf-8"), host).encode("utf-8")
    deployment = _create_deployment(api, account_id, project, manifest, worker, log)
    site_url = _attach_domain(api, account_id, project, host, log)
    publish_url = f"{site_url.rstrip('/')}/api/publish-cache"
    pages_url = f"https://{project}.pages.dev"
    result = {
        "ok": True,
        "host": host,
        "account_id": account_id,
        "project": project,
        "bucket": bucket,
        "secret": secret,
        "site_url": site_url,
        "pages_url": pages_url,
        "publish_url": publish_url,
        "cdn_manifest_url": f"{public_base}/promo/manifest.json",
        "deployment_id": str(deployment.get("id") or ""),
        "deployment_url": str(deployment.get("url") or pages_url),
    }
    save_cf_state(
        {
            "api_token": token,
            "account_id": account_id,
            "host": host,
            "project": project,
            "bucket": bucket,
            "secret": secret,
            "publish_url": publish_url,
            "site_url": site_url,
        }
    )
    log(f"部署完成：{site_url}")
    log(f"发布地址已填入软件：{publish_url}")
    return result
