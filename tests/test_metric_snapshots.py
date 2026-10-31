import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from metric_sheet_io import MetricReader, MetricReadUnavailable, MetricSnapshot, decode_snapshot
from sheet_metric_sync import _prepare_one_source_sheet, _read_streaming_source_sheet, build_source_index


class MetricSnapshotTests(unittest.TestCase):
    def test_snapshot_keeps_dates_text_zero_boolean_and_formula_flags(self):
        payload = {'sheets': [{'properties': {'sheetId': 7}, 'data': [{
            'startRow': 4, 'startColumn': 1,
            'rowData': [{'values': [
                {'effectiveValue': {'numberValue': 46317}, 'formattedValue': '10-22', 'userEnteredValue': {'formulaValue': '=A4+1'}},
                {'effectiveValue': {'numberValue': 0}, 'formattedValue': '000.00'},
                {'effectiveValue': {'boolValue': False}, 'formattedValue': 'FALSE'},
                {'effectiveValue': {'errorValue': {'type': 'DIVIDE_BY_ZERO'}}, 'formattedValue': '#DIV/0!'},
                {'effectiveValue': {'numberValue': 0}},
            ]}],
        }]}]}
        snapshot = decode_snapshot(payload, 7)
        self.assertEqual(snapshot.values[4], ['', 46317, 0, False, '#DIV/0!', 0])
        self.assertEqual(snapshot.display[4], ['', '10-22', '000.00', 'FALSE', '#DIV/0!', ''])
        self.assertEqual(snapshot.formulas[4], ['', '=A4+1', '', '', '', ''])
        with self.assertRaises(MetricReadUnavailable):
            decode_snapshot(payload, 8)

    def test_timeout_uses_numeric_filter_without_infinite_account_rotation(self):
        sheet = SimpleNamespace(client=SimpleNamespace(session=object()))
        reader = MetricReader(sheet, log=lambda _line: None)
        with patch.object(reader, '_request', side_effect=requests.ReadTimeout()), patch.object(
            reader, '_filtered_values', return_value={'values': [['001.00']]}
        ) as alternate, patch('metric_sheet_io.time.sleep'):
            result = reader.read("'表'!F7:F7", render='FORMATTED_VALUE')
        self.assertEqual(result, [['001.00']])
        self.assertEqual(reader.calls, 2)
        alternate.assert_called_once_with("'表'!F7:F7", 'FORMATTED_VALUE')

    def test_all_routes_failing_stops_after_three_attempts(self):
        sheet = SimpleNamespace(client=SimpleNamespace(session=object()))
        reader = MetricReader(sheet, log=lambda _line: None)
        with patch.object(reader, '_request', side_effect=requests.ReadTimeout()), patch.object(
            reader, '_filtered_values', side_effect=requests.ReadTimeout()
        ), patch('metric_sheet_io.time.sleep'):
            with self.assertRaises(MetricReadUnavailable):
                reader.read("'表'!F7:F7", render='FORMATTED_VALUE')
        self.assertEqual(reader.calls, 3)

    def test_one_snapshot_supplies_headers_dates_and_literal_metric_text(self):
        values = [[''] * 10 for _ in range(7)]
        values[0][5], values[1][5] = 'TK01', 'example_person_a'
        values[4][0] = 46317
        values[4][5:9] = [1234.5, 0, 3, 4]
        display = [list(row) for row in values]
        display[4][0] = '10-22'
        display[4][5:9] = ['1,234.50', '000', '3', '4']
        snapshot = MetricSnapshot(values, display, [])
        snapshot_call = Mock(return_value=snapshot)
        source = SimpleNamespace(client=SimpleNamespace(session=object()), _metric_reader=SimpleNamespace(snapshot=snapshot_call))
        worksheet = SimpleNamespace(title='表', row_count=7, col_count=10)
        plan = _prepare_one_source_sheet(
            source, worksheet, log=lambda _line: None,
            code_row=1, name_row=2, date_col=0, data_start_row=5,
            metric_start_col=5, group_width=6,
        )
        rows = _read_streaming_source_sheet(source, plan, log=lambda _line: None, metric_count=4)
        snapshot_call.assert_called_once_with(worksheet, include_formulas=False)
        self.assertEqual(rows[4][0], 46317)
        self.assertEqual(rows[4][5:9], ['1,234.50', '000', '3', '4'])
        index, conflicts, _stats = build_source_index(
            [('表', rows)], code_row=1, name_row=2, date_col=0,
            data_start_row=5, metric_start_col=5, group_width=6,
            metric_count=4, date_mode='month_day',
        )
        self.assertFalse(conflicts)
        self.assertEqual(index[('example_person_a', 'tk01', '10-22')].values, ('1,234.50', '000', '3', '4'))
