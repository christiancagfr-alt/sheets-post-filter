"""Behavior tests for per-sheet commits, formula protection and recovery."""
import re
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import requests
import fetch_posts
import sheet_metric_sync as sync
from metric_sheet_io import MetricReadUnavailable, MetricReader, resolve_formula_cells


def matrix_slice(rows, a1):
    match = re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)(\d+)', a1)
    c1, r1, c2, r2 = match.groups()
    left, right = map(fetch_posts.col_letter_to_index, (c1, c2))
    return [list(row[left:right + 1]) for row in rows[int(r1) - 1:int(r2)]]


class FakeSpreadsheet:
    def __init__(self, identity, data, events, effective=None, fail_sheet=None):
        self.id = identity
        self.data = deepcopy(data)
        self.events = events
        self.effective = effective or {}
        self.fail_sheet = fail_sheet
        self.client = SimpleNamespace(session=None)

    def worksheets(self):
        return [self.worksheet(name) for name in self.data]

    def worksheet(self, name):
        rows = self.data[name]
        return SimpleNamespace(title=name, row_count=len(rows), col_count=max(map(len, rows)), _properties={})

    def values_get(self, a1_range, params=None):
        title, a1 = a1_range.rsplit('!', 1)
        title = title[1:-1].replace("''", "'")
        self.events.append(('read', title, a1, params['valueRenderOption']))
        if title == self.fail_sheet:
            raise requests.ReadTimeout('read timed out')
        source = self.data[title]
        if params['valueRenderOption'] != 'FORMULA':
            source = self.effective.get(title, source)
        return {'values': matrix_slice(source, a1)}

    def values_batch_update(self, body):
        self.events.append(('write', body['valueInputOption'], deepcopy(body['data'])))
        # Test target has exactly one sheet, just as the configured destination.
        rows = next(iter(self.data.values()))
        for item in body['data']:
            first, _last = item['range'].split(':')
            col, row = re.fullmatch(r'([A-Z]+)(\d+)', first).groups()
            left = fetch_posts.col_letter_to_index(col)
            for offset, values in enumerate(item['values']):
                rows[int(row) - 1 + offset][left:left + len(values)] = values
        return {}


def source_rows(name, value):
    rows = [[''] * 10 for _ in range(7)]
    rows[0][5], rows[1][5] = 'TK01', name
    rows[4][0], rows[4][5:9] = '10-22', [value, 2, '', 4]
    return rows


class MetricPipelineTests(unittest.TestCase):
    def execute(self, source, target, *, preview=False):
        cfg = fetch_posts.Config()
        cfg.sm_source_url = 's' * 25
        cfg.sm_target_url = 't' * 25
        cfg.sm_target_sheet = '目标'
        cfg.sm_target_date_row = 3
        with patch.object(sync, 'authorize_cfg', return_value=object()), patch.object(
            sync, 'open_metric_spreadsheet', side_effect=[source, target]
        ):
            return sync.run_sheet_metric_sync(cfg, preview=preview, log=lambda _line: None)

    def target(self, events):
        rows = [[''] * 20 for _ in range(9)]
        rows[2][11] = '10-22'
        rows[6][0:2] = ['甲', 'TK01']
        rows[7][0:2] = ['乙', 'TK01']
        rows[6][11:15] = ['001', '=SUM(A1:A2)', 9, 0]
        rows[7][11:15] = [0, 0, 0, 0]
        return FakeSpreadsheet('t' * 25, {'目标': rows}, events)

    def test_first_sheet_is_written_before_second_is_queried(self):
        events = []
        source = FakeSpreadsheet('s' * 25, {'一': source_rows('甲', 10), '二': source_rows('乙', 20)}, events)
        target = self.target(events)
        result = self.execute(source, target)
        first_write = next(i for i, event in enumerate(events) if event[0] == 'write')
        second_read = next(i for i, event in enumerate(events) if event[:2] == ('read', '二'))
        self.assertLess(first_write, second_read)
        self.assertEqual(target.data['目标'][6][11:15], ['10', '=SUM(A1:A2)', 9, '4'])
        self.assertEqual(target.data['目标'][7][11:15], ['20', '2', 0, '4'])
        self.assertEqual(result['written_cells'], 5)

    def test_cross_sheet_duplicate_restores_exact_original_strings(self):
        events = []
        source = FakeSpreadsheet('s' * 25, {'一': source_rows('甲', 10), '二': source_rows('甲', 20)}, events)
        target = self.target(events)
        original = deepcopy(target.data)
        result = self.execute(source, target)
        self.assertEqual(target.data, original)
        self.assertEqual(result['conflicts'], 1)
        self.assertEqual(result['written_cells'], 0)
        self.assertEqual([event[1] for event in events if event[0] == 'write'], ['RAW', 'RAW'])

    def test_later_read_failure_restores_earlier_sheet_changes(self):
        events = []
        source = FakeSpreadsheet('s' * 25, {'一': source_rows('甲', 10), '二': source_rows('乙', 20)}, events, fail_sheet='二')
        target = self.target(events)
        original = deepcopy(target.data)
        with self.assertRaises(MetricReadUnavailable):
            self.execute(source, target)
        self.assertEqual(target.data, original)

    def test_preview_never_writes_and_duplicate_is_excluded(self):
        events = []
        source = FakeSpreadsheet('s' * 25, {'一': source_rows('甲', 10), '二': source_rows('甲', 20)}, events)
        result = self.execute(source, self.target(events), preview=True)
        self.assertEqual(result['planned_cells'], 0)
        self.assertFalse(any(event[0] == 'write' for event in events))

    def test_blank_header_chunk_does_not_hide_later_groups(self):
        events = []
        rows = [[''] * 24 for _ in range(7)]
        rows[0][17], rows[1][17] = 'TK01', '甲'
        sheet = FakeSpreadsheet('source', {'表': rows}, events)
        plan = sync._prepare_one_source_sheet(
            sheet, sheet.worksheet('表'), log=lambda _line: None,
            code_row=1, name_row=2, date_col=0, data_start_row=5,
            metric_start_col=5, group_width=6, header_chunk_cols=6,
        )
        self.assertEqual(plan.group_cols, [17])

    def test_date_formula_run_resolves_in_one_request(self):
        rows = [['=DATE(2026,10,22)'], ['=A1+1'], ['=A2+1']]
        calls = []

        def read(a1, **_kwargs):
            calls.append(a1)
            return [[46317], [46318], [46319]]

        resolve_formula_cells(None, '日期', rows, [(0, 0), (1, 0), (2, 0)], read=read, log=lambda _line: None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(rows, [[46317], [46318], [46319]])

    def test_timeout_does_not_cycle_all_accounts(self):
        sheet = SimpleNamespace(client=SimpleNamespace(session=None))
        sheet.values_get = lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.ReadTimeout())
        reader = MetricReader(sheet, log=lambda _line: None)
        with self.assertRaises(MetricReadUnavailable):
            reader.read("'表'!A1:A1", what='读取表头')
        self.assertEqual(reader.calls, 1)

    def test_displayed_text_is_copied_with_raw_strings(self):
        events = []
        raw = source_rows('甲', '=SUM(B1:B3)')
        effective = source_rows('甲', '060.00')
        effective[4][6], effective[4][8] = '1,234.50', '12.5%'
        # An irrelevant formula in a second identity must not force calculation.
        raw[0].extend([''] * 6)
        raw[1].extend([''] * 6)
        raw[0][11], raw[1][11] = 'UNMATCHED', '丙'
        raw[4].extend([''] * 6)
        raw[4][11] = '=IMPORTRANGE("irrelevant","A1")'
        source = FakeSpreadsheet('s' * 25, {'一': raw}, events, effective={'一': effective})
        target = self.target(events)
        self.execute(source, target)
        self.assertEqual(target.data['目标'][6][11:15], ['060.00', '=SUM(A1:A2)', 9, '12.5%'])
        computed_reads = [event for event in events if event[0] == 'read' and event[1] == '一' and event[3] != 'FORMULA']
        self.assertEqual(computed_reads, [
            ('read', '一', 'A5:A7', 'UNFORMATTED_VALUE'),
            ('read', '一', 'F5:O7', 'FORMATTED_VALUE'),
        ])
