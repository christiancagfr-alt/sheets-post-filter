from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fetch_posts import (  # noqa: E402
    FieldMap,
    backfill_facebook_identity,
    parse_facebook_post_url,
)


class FacebookPostUrlTests(unittest.TestCase):
    def test_group_post_keeps_group_and_post_ids_separate(self):
        ref = parse_facebook_post_url(
            "https://fb.com/groups/900000000000003/posts/9000000000000001"
        )
        self.assertIsNotNone(ref)
        self.assertEqual(ref.owner_id, "900000000000003")
        self.assertEqual(ref.post_id, "9000000000000001")
        self.assertTrue(ref.is_group)
        self.assertNotEqual(ref.post_id, "9000000000000039000000000000001")

    def test_group_permalink_and_query_variants(self):
        ref = parse_facebook_post_url(
            "https://m.facebook.com/groups/900000000000003/permalink/9000000000000001/?ref=share"
        )
        self.assertEqual(ref.post_id, "9000000000000001")
        self.assertEqual(ref.owner_id, "900000000000003")

    def test_page_compound_link(self):
        ref = parse_facebook_post_url(
            "https://fb.com/9000000000000002_900000000000000004"
        )
        self.assertEqual(ref.owner_id, "9000000000000002")
        self.assertEqual(ref.post_id, "900000000000000004")
        self.assertFalse(ref.is_group)

    def test_backfills_only_blank_identity_cells(self):
        fields = [
            FieldMap("原始链接", "源数据", "B", 2),
            FieldMap("帖文id", "源数据", "D", 2),
            FieldMap("帖子链接", "源数据", "G", 2),
            FieldMap("主页id", "源数据", "P", 2),
        ]
        rows = [[
            "https://fb.com/groups/900000000000003/posts/9000000000000001",
            "",
            "",
            "",
        ]]
        messages = []
        changed = backfill_facebook_identity(rows, fields, log=messages.append)
        self.assertEqual(changed, 1)
        self.assertEqual(rows[0][1], "9000000000000001")
        self.assertEqual(
            rows[0][2],
            "https://www.facebook.com/groups/900000000000003/posts/9000000000000001",
        )
        self.assertEqual(rows[0][3], "900000000000003")
        self.assertIn("补齐 1 行", messages[0])


if __name__ == "__main__":
    unittest.main()
