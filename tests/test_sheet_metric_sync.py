from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app
import fetch_posts
from sheet_metric_sync import (
    _prepare_one_source_sheet,
    _read_target_sheet_ranges,
    _selected_source_sheets,
    build_source_index,
    normalize_date_key,
    plan_target_changes,
)


class SheetMetricSyncTests(unittest.TestCase):
    def test_date_formats_match_by_month_day(self):
        for value in ("10-22", "10/22", "2026-10-22"):
            self.assertEqual(normalize_date_key(value, "month_day")[0], "10-22")
        self.assertEqual(normalize_date_key("2026-10-22", "full_date")[0], "2026-10-22")
        self.assertIsNone(normalize_date_key("10-22", "full_date"))

    def test_source_groups_use_independent_configured_rows_and_columns(self):
        rows = [[""] * 17 for _ in range(6)]
        rows[0][5], rows[1][5] = "TK01", "example_person_a"
        rows[0][11], rows[1][11] = "TK02", "example_person_b"
        rows[4][0] = "10-22"
        rows[4][5:9] = [100, 5, 200, 6]
        rows[4][11:15] = [300, 7, 400, 8]
        index, conflicts, stats = build_source_index(
            [("来源一", rows)],
            code_row=1,
            name_row=2,
            date_col=0,
            data_start_row=5,
            metric_start_col=5,
            group_width=6,
            metric_count=4,
            date_mode="month_day",
        )
        self.assertFalse(conflicts)
        self.assertEqual(stats["source_records"], 2)
        self.assertEqual(index[("example_person_a", "tk01", "10-22")].values, (100, 5, 200, 6))
        self.assertEqual(index[("example_person_b", "tk02", "10-22")].values, (300, 7, 400, 8))

    def test_target_only_changes_allowed_columns_and_skips_formula(self):
        source_rows = [[""] * 11 for _ in range(6)]
        source_rows[0][5], source_rows[1][5] = "TK01", "example_person_a"
        source_rows[4][0] = "2026-10-22"
        source_rows[4][5:9] = [100, 5, 200, 6]
        index, conflicts, _stats = build_source_index(
            [("来源", source_rows)],
            code_row=1,
            name_row=2,
            date_col=0,
            data_start_row=5,
            metric_start_col=5,
            group_width=6,
            metric_count=4,
            date_mode="month_day",
        )
        target = [[""] * 20 for _ in range(8)]
        formulas = [[""] * 20 for _ in range(8)]
        target[2][11] = "10/22"
        target[6][0], target[6][1] = "example_person_a", "TK01"
        target[6][11:19] = [0, 0, 0, 0, "禁止1", "禁止2", "禁止3", "禁止4"]
        formulas[6][12] = "=SUM(A1:A2)"
        changes, stats = plan_target_changes(
            target,
            formulas,
            index,
            conflicts,
            name_col=0,
            code_col=1,
            date_row=3,
            data_start_row=7,
            group_start_col=11,
            group_width=8,
            write_count=4,
            date_mode="month_day",
        )
        self.assertEqual([(item.col, item.new) for item in changes], [(11, 100), (13, 200), (14, 6)])
        self.assertEqual(stats["formula_skips"], 1)
        self.assertTrue(all(item.col < 15 for item in changes))

    def test_config_keeps_source_and_target_positions_independent(self):
        cfg = app._cfg_from_payload(
            {
                "sm_source_code_row": "4",
                "sm_source_name_row": "6",
                "sm_source_date_col": "C",
                "sm_source_metric_start_col": "J",
                "sm_target_name_col": "D",
                "sm_target_code_col": "E",
                "sm_target_date_row": "9",
                "sm_target_group_start_col": "Q",
                "sm_target_write_count": "3",
            }
        )
        self.assertEqual((cfg.sm_source_code_row, cfg.sm_source_name_row), (4, 6))
        self.assertEqual((cfg.sm_source_date_col, cfg.sm_source_metric_start_col), ("C", "J"))
        self.assertEqual((cfg.sm_target_name_col, cfg.sm_target_code_col), ("D", "E"))
        self.assertEqual((cfg.sm_target_date_row, cfg.sm_target_group_start_col), (9, "Q"))
        self.assertEqual(cfg.sm_target_write_count, 3)

    def test_hidden_source_sheets_are_skipped(self):
        logs = []
        source_ss = SimpleNamespace(
            worksheets=lambda: [
                SimpleNamespace(title="可见一", _properties={"hidden": False}),
                SimpleNamespace(title="隐藏一", _properties={"hidden": True}),
                SimpleNamespace(title="可见二", _properties={}),
            ]
        )
        cfg = SimpleNamespace(
            sm_source_mode="all",
            sm_source_sheet="",
            sm_exclude_exact=[],
            sm_exclude_contains=[],
            sm_source_url="",
            sm_target_sheet="",
        )
        selected = _selected_source_sheets(source_ss, cfg, "target-id", logs.append)
        self.assertEqual([ws.title for ws in selected], ["可见一", "可见二"])
        self.assertTrue(any("隐藏一" in line for line in logs))

    def test_source_plan_reads_one_finite_header_range(self):
        class FakeSpreadsheet:
            def __init__(self):
                self.calls = []

            def values_get(self, a1_range, params=None):
                self.calls.append((a1_range, params))
                return {"values": [["TK01"], ["名字一"]]}

        spreadsheet = FakeSpreadsheet()
        worksheet = SimpleNamespace(title="O'Brien", col_count=20, row_count=100)
        plan = _prepare_one_source_sheet(
            spreadsheet,
            worksheet,
            log=lambda _message: None,
            code_row=1,
            name_row=2,
            date_col=0,
            data_start_row=5,
            metric_start_col=5,
            group_width=6,
        )
        self.assertEqual(len(spreadsheet.calls), 1)
        self.assertEqual(spreadsheet.calls[0][0], "'O''Brien'!F1:T2")
        self.assertEqual(plan.group_cols, [5])

    def test_503_rotates_service_account_before_waiting(self):
        class FakePool:
            paths = ["one", "two"]

            def __init__(self):
                self.rotations = 0

            def rotate(self, _reason):
                self.rotations += 1
                return True

            def note_success(self):
                pass

        attempts = {"count": 0}

        def request():
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("APIError: [503]: The service is currently unavailable")
            return "ok"

        pool = FakePool()
        with patch.object(fetch_posts, "current_credential_pool", return_value=pool), patch.object(
            fetch_posts.time, "sleep"
        ) as sleep:
            self.assertEqual(fetch_posts.with_retry(request, log=lambda _line: None), "ok")
        self.assertEqual(pool.rotations, 1)
        sleep.assert_not_called()

    def test_target_reader_limits_download_to_configured_region(self):
        class FakeSpreadsheet:
            def __init__(self):
                self.calls = []

            def values_get(self, a1_range, params=None):
                self.calls.append((a1_range, params))
                if a1_range == "'目标'!L3:AD3":
                    header = [""] * 9
                    header[0], header[8] = "10-21", "10-22"
                    return {"values": [header]}
                return {"values": [["名字", "代码"]]}

        spreadsheet = FakeSpreadsheet()
        _read_target_sheet_ranges(
            spreadsheet,
            SimpleNamespace(title="目标", col_count=30, row_count=100),
            log=lambda _message: None,
            name_col=0,
            code_col=1,
            date_row=3,
            data_start_row=7,
            group_start_col=11,
            group_width=8,
            write_count=4,
            date_mode="month_day",
        )
        self.assertEqual([call[0] for call in spreadsheet.calls], [
            "'目标'!L3:AD3",
            "'目标'!A7:W100",
        ])


if __name__ == "__main__":
    unittest.main()
