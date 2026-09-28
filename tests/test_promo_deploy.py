from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from promo_deploy import (  # noqa: E402
    generate_publish_secret,
    normalize_host,
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


if __name__ == "__main__":
    unittest.main()
