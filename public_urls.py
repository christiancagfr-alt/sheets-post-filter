"""Keep automatic page probes away from local files and private networks."""
import ipaddress
import socket
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler


def public_http_url(url):
    if len(url)>8192 or any(ord(char)<32 for char in url):
        raise ValueError("无效的视频网页地址")
    parsed=urlsplit(url)
    if parsed.scheme not in {"https","http"} or not parsed.hostname or parsed.username or parsed.password or len(parsed.hostname)>253:
        raise ValueError("视频网页只支持公开 HTTP/HTTPS 地址")
    host=parsed.hostname.lower().rstrip(".")
    if host=="localhost" or host.endswith((".localhost", ".local")):
        raise ValueError("视频网页不支持本机或内网地址")
    addresses=socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme=="https" else 80),type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0].split('%')[0]).is_global for row in addresses):
        raise ValueError("视频网页不支持本机或内网地址")
    return url


class PublicRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_http_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)
