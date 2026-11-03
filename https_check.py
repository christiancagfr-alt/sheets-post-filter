"""Frozen certificate-chain check against a public, credential-free API."""
import json
import os
from pathlib import Path
import ssl
from urllib.request import Request


def run_https_check():
    from secure_http import create_ssl_context, credential_urlopen
    import certifi
    from version import APP_VERSION
    report=os.environ.get('SHEETS_POST_FILTER_HTTPS_REPORT')
    if not report:raise ValueError('An HTTPS report path is required')
    context=create_ssl_context()
    assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname
    assert Path(certifi.where()).is_file() and context.cert_store_stats()['x509_ca']>0
    # GET public IP ranges only: no user configuration, tokens or gallery data.
    with credential_urlopen(Request('https://api.cloudflare.com/client/v4/ips'),timeout=20) as response:
        assert response.status==200
        result=json.loads(response.read(256*1024))
        assert result.get('success') is True
    Path(report).write_text(json.dumps({'ok':True,'version':APP_VERSION,
        'certificate_verification':True,'hostname_verification':True,'bundled_ca_loaded':True}),encoding='utf-8')
