from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from publish_cloudflare import drive_file_id, public_image_url  # noqa: E402


class DriveImageUrlTests(unittest.TestCase):
    def test_file_view_link_becomes_lh3(self):
        src = "https://drive.google.com/file/d/abc123XYZ-_9/view?usp=sharing"
        self.assertEqual(drive_file_id(src), "abc123XYZ-_9")
        self.assertEqual(public_image_url(src), "https://lh3.googleusercontent.com/d/abc123XYZ-_9")

    def test_uc_export_link(self):
        src = "https://drive.google.com/uc?export=view&id=fileId99"
        self.assertEqual(public_image_url(src), "https://lh3.googleusercontent.com/d/fileId99")

    def test_image_formula(self):
        src = '=IMAGE("https://drive.google.com/file/d/zzz111/view")'
        self.assertEqual(public_image_url(src), "https://lh3.googleusercontent.com/d/zzz111")

    def test_gyazo_unchanged(self):
        src = "https://i.gyazo.com/abcdef.jpg"
        self.assertEqual(public_image_url(src), src)


if __name__ == "__main__":
    unittest.main()
