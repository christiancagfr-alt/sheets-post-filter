import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import app
from local_secrets import atomic_json_write, read_private_json, PREFIX, SecretStoreError
from secure_http import validate_https, NoRedirect
from template_registry import new_template_settings, TEMPLATE_NAMES


class SecurityBoundaries(unittest.TestCase):
    def test_new_templates_are_independent_and_empty(self):
        for kind in TEMPLATE_NAMES:
            first = new_template_settings(kind)
            for key in ("sources", "vd_types", "vd_report_categories", "wm_source_urls", "wm_channels"):
                self.assertEqual(first[key], [])
            for key in ("target_url", "cf_publish_secret", "cf_profile_id", "vd_source_url", "wm_source_url", "wm_direct_url"):
                self.assertEqual(first[key], "")
            self.assertFalse(first["wm_schedule_enabled"])
            first["sources"].append({"url": "https://example.com"})
            self.assertEqual(new_template_settings(kind)["sources"], [])

    def test_local_api_rejects_cross_site_and_dns_rebinding(self):
        client = app.test_client()
        for headers in ({"Origin": "https://example.com"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(client.post("/api/save", json={}, headers=headers).status_code, 403)
        self.assertEqual(client.get("/api/config", base_url="http://example.com").status_code, 403)
        self.assertEqual(client.post("/api/save", data='{}', content_type="text/plain").status_code, 415)
        response = client.get("/", headers={"Origin": "http://localhost"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_credential_urls_reject_unsafe_schemes_and_hosts(self):
        for url in ("http://example.com", "file:///tmp/x", "https://user:pass@example.com", "https://example.com:1234", "https://example.com\n"):
            with self.assertRaises(ValueError): validate_https(url)
        with self.assertRaises(ValueError):validate_https("https://example.com", hosts={"api.cloudflare.com"})
        self.assertIsNone(NoRedirect().redirect_request(None, None, 307, "", {}, "https://example.com"))

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration")
    def test_windows_roundtrip_migration_and_no_plaintext_on_disk(self):
        value={"api_token":"example-test-token", "profiles":[{"access_password":"example-password"}], "name":"示例"}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"cloudflare.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(read_private_json(path), value)
            raw=path.read_text(encoding="utf-8")
            self.assertNotIn("example-test-token", raw)
            self.assertNotIn("example-password", raw)
            self.assertIn(PREFIX, raw)
            atomic_json_write(path, value)
            self.assertEqual(read_private_json(path), value)
            original=path.read_bytes()
            with patch("local_secrets._dpapi", side_effect=SecretStoreError("example")):
                with self.assertRaises(SecretStoreError):read_private_json(path)
            self.assertEqual(path.read_bytes(),original)

    def test_oauth4_client_signing_compatibility(self):
        from oauthlib.oauth2 import WebApplicationClient
        from requests_oauthlib import OAuth2Session
        import google.auth.transport.requests
        import gspread
        client=WebApplicationClient("example-client")
        session=OAuth2Session(client=client, redirect_uri="https://example.com/callback")
        url,_state=session.authorization_url("https://example.com/authorize")
        self.assertIn("client_id=example-client",url)
        self.assertIn("grant_type=authorization_code",client.prepare_request_body(code="example-code"))

    def test_automatic_page_probe_blocks_local_and_private_destinations(self):
        from public_urls import public_http_url
        for url in ("file:///etc/passwd", "http://localhost/video", "http://device.local/video"):
            with self.assertRaises(ValueError):public_http_url(url)
        with patch("public_urls.socket.getaddrinfo", return_value=[(2,1,6,"",("127.0.0.1",80))]):
            with self.assertRaises(ValueError):public_http_url("http://example.com/video")

    def test_google_service_account_token_flow_with_ephemeral_key(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        from google.oauth2.service_account import Credentials
        import gspread
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
        credentials=Credentials.from_service_account_info({"type":"service_account",
            "client_email":"example@example.invalid", "token_uri":"https://oauth2.googleapis.com/token", "private_key":pem},
            scopes=["https://www.googleapis.com/auth/spreadsheets"])
        class Response:
            status=200
            data=b'{"access_token":"example-token","expires_in":3600,"token_type":"Bearer"}'
        def transport(**kwargs):
            self.assertEqual(kwargs["url"],"https://oauth2.googleapis.com/token")
            self.assertIn(b"assertion=",kwargs["body"])
            return Response()
        credentials.refresh(transport)
        self.assertEqual(credentials.token,"example-token")
        self.assertIsNotNone(gspread.authorize(credentials))


if __name__ == "__main__":unittest.main()
