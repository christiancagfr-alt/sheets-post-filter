"""Network boundary for credential requests: HTTPS and no redirects."""
import ssl
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_https(url, *, hosts=None):
    if not isinstance(url, str) or len(url) > 8192 or any(ord(char) < 32 for char in url):
        raise ValueError("无效的 HTTPS 地址")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or len(parsed.hostname) > 253 or parsed.port not in (None, 443)):
        raise ValueError("请填写不含用户名密码的 HTTPS 地址")
    if hosts is not None and parsed.hostname.lower() not in hosts:
        raise ValueError("请求地址不在允许的服务域名中")
    return url


def credential_urlopen(request, timeout):
    validate_https(request.full_url)
    try:
        # Frozen macOS Python does not always discover the system trust store.
        # certifi is bundled with the application, so use its CA roots explicitly.
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):
        context = ssl.create_default_context()
    return build_opener(NoRedirect(), HTTPSHandler(context=context)).open(request, timeout=timeout)
