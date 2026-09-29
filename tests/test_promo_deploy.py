from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from promo_deploy import (  # noqa: E402
    AUTH_HELP,
    CloudflareAPI,
    TOKEN_CREATE_URL,
    TOKEN_PERMISSIONS,
    generate_publish_secret,
    is_auth_failure,
    is_missing_resource,
    looks_like_global_api_key,
    normalize_host,
    pages_env_vars,
    pages_file_hash,
    rewrite_gallery_html,
    slug_from_host,
)


class PromoDeployHelperTests(unittest.TestCase):
    def test_normalize_host_strips_scheme_and_path(self):
        self.assertEqual(normalize_host("https://Gallery.Example.com/foo"), "gallery.example.com")

    def test_slug_from_host_is_pages_safe(self):
        slug = slug_from_host("Gallery.Example.com")
        self.assertTrue(slug.startswith("gallery-"))
        self.assertLessEqual(len(slug), 58)
        self.assertRegex(slug, r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

    def test_rewrite_html_points_cdn_at_new_host(self):
        src = (
            'window.Q_GALLERY_DRIVE_CACHE_MANIFEST_URL = '
            '"https://gallery-cache.zhixianglife.com/promo/manifest.json";\n'
            'window.Q_GALLERY_DRIVE_FALLBACK_MANIFEST_URL = '
            '"https://drive.google.com/uc?export=download&id=abc";'
        )
        out = rewrite_gallery_html(src, "promo.my-site.com")
        self.assertIn("https://promo.my-site.com/cdn/promo/manifest.json", out)
        self.assertNotIn("gallery-cache.zhixianglife.com", out)
        self.assertIn('Q_GALLERY_DRIVE_FALLBACK_MANIFEST_URL = ""', out)

    def test_secret_is_long_and_unique(self):
        first = generate_publish_secret()
        second = generate_publish_secret()
        self.assertGreaterEqual(len(first), 32)
        self.assertNotEqual(first, second)

    def test_pages_hash_is_32_hex(self):
        digest = pages_file_hash(b"<html>ok</html>", "index.html")
        self.assertEqual(len(digest), 32)
        self.assertRegex(digest, r"^[0-9a-f]{32}$")

    def test_token_create_url_uses_cloudflare_permission_keys(self):
        keys = {item["key"] for item in TOKEN_PERMISSIONS}
        self.assertEqual(keys, {"page", "workers_r2", "workers_scripts", "dns", "account_settings"})
        self.assertNotIn("pages", keys)
        self.assertNotIn("workers_r2_storage", keys)
        self.assertIn("permissionGroupKeys=", TOKEN_CREATE_URL)
        self.assertIn("accountId=", TOKEN_CREATE_URL)
        self.assertIn("zoneId=all", TOKEN_CREATE_URL)
        self.assertIn("page", TOKEN_CREATE_URL)
        self.assertIn("workers_r2", TOKEN_CREATE_URL)
        self.assertNotIn("workers_r2_storage", TOKEN_CREATE_URL)

    def test_auth_error_tells_user_to_recreate_token(self):
        body = '{"success":false,"errors":[{"code":10000,"message":"Authentication error"}]}'
        text = CloudflareAPI._format_error(403, body)
        self.assertIn("拒绝了这个令牌", text)
        self.assertIn("10000", text)
        self.assertIn("登录 Cloudflare", text)
        self.assertTrue(is_auth_failure(Exception(text)))
        self.assertFalse(is_missing_resource(Exception(text)))
        self.assertTrue(is_missing_resource(Exception("Cloudflare HTTP 404：not found")))
        self.assertTrue(looks_like_global_api_key("a" * 37))
        self.assertFalse(looks_like_global_api_key("cfut_not_a_global_key_example_token"))
        self.assertIn("登录 Cloudflare", AUTH_HELP)
        self.assertIn("Pages 编辑", AUTH_HELP)

    def test_pages_env_vars_set_or_clear_access_password(self):
        with_pw = pages_env_vars("pub-secret", "https://example.com/cdn", "site-pass")
        self.assertEqual(with_pw["ACCESS_PASSWORD"]["value"], "site-pass")
        self.assertEqual(with_pw["ACCESS_PASSWORD"]["type"], "secret_text")
        cleared = pages_env_vars("pub-secret", "https://example.com/cdn", "")
        self.assertIsNone(cleared["ACCESS_PASSWORD"])
        self.assertEqual(cleared["CACHE_PUBLISH_SECRET"]["value"], "pub-secret")


if __name__ == "__main__":
    unittest.main()
