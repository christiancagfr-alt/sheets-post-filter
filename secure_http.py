"""Network boundary for credential requests: HTTPS and no redirects."""
import ssl
import certifi
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


def create_ssl_context():
    """Retain OS trust and add the CA bundle shipped inside the frozen app.

    Python's compiled-in macOS certificate path may not exist on another Mac.
    requests already uses certifi; urllib must load it explicitly as well.
    """
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def credential_urlopen(request, timeout):
    validate_https(request.full_url)
    return build_opener(NoRedirect(), HTTPSHandler(context=create_ssl_context())).open(request, timeout=timeout)
