"""Local integration tests; all Google I/O is an in-memory fake."""
import copy
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fetch_posts import Config, col_letter_to_index, load_config, save_config
from template_registry import partition_menus
import worksheet_merge as merge


class FakeSheet:
    def __init__(self, title, identity, data, hidden=False, rows=None, cols=None):
        self.title, self.id = title, identity
        self.data = copy.deepcopy(data)
        self.row_count = rows or max(1, len(data))
        self.col_count = cols or max(1, max((len(row) for row in data), default=1))
        self._properties = {'hidden': hidden}
        self.links = {}

    def resize(self, rows, cols):
        self.row_count, self.col_count = rows, cols


class FakeBook:
    def __init__(self, identity, sheets):
        self.id, self.sheets = identity, sheets
        self.client = SimpleNamespace(session=None)
        self.events = []
        self.fail_title = ''

    def worksheets(self):
        return self.sheets

    def worksheet(self, title):
        return next(ws for ws in self.sheets if ws.title == title)

    def add_worksheet(self, title, rows, cols):
        self.events.append(('create', title))
        ws = FakeSheet(title, max((sheet.id for sheet in self.sheets),default=0)+1, [], rows=rows, cols=cols)
        self.sheets.append(ws)
        return ws

    def bounds(self, a1):
        match = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+)(\d+):([A-Z]+)(\d+)", a1)
        title, left, top, right, bottom = match.groups()
        return self.worksheet(title.replace("''", "'")), col_letter_to_index(left), int(top)-1, col_letter_to_index(right)+1, int(bottom)

    def values_get(self, a1, params):
        ws, left, top, right, bottom = self.bounds(a1)
        self.events.append(('read', ws.title, params['valueRenderOption'], top, bottom))
        if ws.title == self.fail_title:
            raise PermissionError('No permission')
        return {'values': [row[left:right] for row in ws.data[top:bottom]]}

    def values_batch_update(self, body):
        self.events.append(('write', body['valueInputOption']))
        for item in body['data']:
            ws, left, top, right, bottom = self.bounds(item['range'])
            while len(ws.data) < bottom:
                ws.data.append([])
            for index, values in enumerate(item['values'], top):
                ws.data[index].extend([''] * max(0, right-len(ws.data[index])))
                ws.data[index][left:right] = values
        return {}

    def values_batch_clear(self, body):
        self.events.append(('clear',))
        for a1 in body['ranges']:
            ws, left, top, right, bottom = self.bounds(a1)
            for row in ws.data[top:bottom]:
                row[left:min(right,len(row))] = [''] * max(0,min(right,len(row))-left)
        return {}

    def fetch_sheet_metadata(self, params):
        data=[]
        for a1 in params['ranges']:
            ws,left,top,right,bottom=self.bounds(a1)
            data.append({'startRow':top,'startColumn':left,'rowData':[
                {'values':[{'hyperlink':ws.links.get((row,left),'')}]} for row in range(top,min(bottom,len(ws.data)))
            ]})
        self.events.append(('rich_links',))
        return {'sheets':[{'data':data}]}

    def batch_update(self, body):
        self.events.append(('link_metadata',))
        for request in body['requests']:
            update=request['updateCells'];bounds=update['range']
            # Google treats clearing rich-text runs as a link reset, even when
            # the same update sets a whole-cell link. Never submit both fields.
            self_last_fields=update['fields']
            ws=next(ws for ws in self.sheets if ws.id==bounds['sheetId'])
            for offset,row in enumerate(update['rows']):
                link=row['values'][0]['userEnteredFormat']['textFormat'].get('link',{}).get('uri','')
                if 'textFormatRuns' in self_last_fields and row['values'][0].get('textFormatRuns')==[]:link=''
                ws.links[bounds['startRowIndex']+offset,bounds['startColumnIndex']]=link
        return {}


class WorksheetMergeTests(unittest.TestCase):
    def config(self, **changes):
        return Config(wm_source_url='s'*25, wm_target_url='t'*25, wm_target_sheet='结果',
                      wm_columns=[{'header': '号码', 'column': 'B'}, {'header': '姓名', 'column': 'A'}], **changes)

    def run_merge(self, cfg, source, target, **kwargs):
        with patch.object(merge, 'authorize_cfg', return_value=object()), patch.object(
            merge, 'open_by_url_or_id', side_effect=[source, target] if source is not target else [source]
        ):
            return merge.run_worksheet_merge(cfg, log=lambda _line: None, **kwargs)

    def test_merge_text_reorder_exclusions_hidden_and_clear_old_tail(self):
        source = FakeBook('s'*25, [FakeSheet('一',1,[['姓名','电话'],['甲','00123'],['',''],['乙','=字面文本']]),
                                   FakeSheet('不要',2,[['姓名','电话'],['丙','99']]),
                                   FakeSheet('隐藏',3,[['姓名','电话'],['丁','88']],hidden=True),
                                   FakeSheet("二'组",4,[['姓名','电话'],['戊','00456']])])
        result_sheet = FakeSheet('结果',10,[['外部','旧标题','旧标题','右侧'],['保留','旧1','旧2','右侧'],
                                              ['保留','旧3','旧4','右侧'],['保留','旧5','旧6','右侧'],
                                              ['保留','应清空','应清空','右侧']])
        target = FakeBook('t'*25,[result_sheet])
        cfg = self.config(wm_exclude_sheets=['不要'],wm_target_start_col='B')
        result = self.run_merge(cfg,source,target)
        self.assertEqual(result['total_rows'],3)
        self.assertEqual(result['ok_sheets'],2)
        self.assertEqual(result_sheet.data[0],['外部','号码','姓名','右侧'])
        self.assertEqual([row[1:3] for row in result_sheet.data[1:]],
                         [['00123','甲'],['=字面文本','乙'],['00456','戊'],['','']])
        self.assertEqual([x[1] for x in source.events],['一',"二'组"])
        self.assertTrue(all(x[2]=='FORMATTED_VALUE' for x in source.events))
        self.assertTrue(all(x[1]=='RAW' for x in target.events if x[0]=='write'))

    def test_same_book_skips_output_and_repeated_runs_do_not_duplicate(self):
        book = FakeBook('s'*25,[FakeSheet('一',1,[['名','号'],['甲','001']]),
                                FakeSheet('结果',2,[['old','old'],['old','old']])])
        cfg=self.config()
        cfg.wm_target_url=cfg.wm_source_url
        for _ in range(2):
            result=self.run_merge(cfg,book,book)
            self.assertEqual(result['total_rows'],1)
        self.assertEqual(book.worksheet('结果').data[:2],[['号码','姓名'],['001','甲']])
        self.assertEqual([x[1] for x in book.events if x[0]=='read'],['一','一'])

    def test_include_mode_reads_only_exact_named_visible_sheets_and_never_output(self):
        book=FakeBook('s'*25,[FakeSheet('指定',1,[['名','号'],['甲','001']]),
                               FakeSheet('指定2',2,[['名','号'],['乙','002']]),
                               FakeSheet('隐藏',3,[['名','号'],['丙','003']],hidden=True),
                               FakeSheet('结果',4,[['旧','旧'],['原有','内容']])])
        cfg=self.config(wm_sheet_filter_mode='include',wm_exclude_sheets=['指定','隐藏','结果'])
        cfg.wm_target_url=cfg.wm_source_url
        result=self.run_merge(cfg,book,book)
        self.assertEqual(result['ok_sheets'],1)
        self.assertEqual(book.worksheet('结果').data[:2],[['号码','姓名'],['001','甲']])
        self.assertEqual([event[1] for event in book.events if event[0]=='read'],['指定'])

    def test_empty_or_unmatched_include_list_never_changes_destination(self):
        cfg=self.config(wm_sheet_filter_mode='include',wm_exclude_sheets=[])
        with self.assertRaisesRegex(ValueError,'至少填写'):merge.validate_merge_config(cfg)
        with patch.object(merge,'authorize_cfg') as auth:
            with self.assertRaisesRegex(ValueError,'至少填写'):merge.read_merge_headers(cfg)
        auth.assert_not_called()
        source=FakeBook('s'*25,[FakeSheet('指定',1,[['名','号'],['甲','001']])])
        target=FakeBook('t'*25,[FakeSheet('结果',2,[['旧','保留']])])
        cfg.wm_exclude_sheets=['不存在']
        with self.assertRaisesRegex(ValueError,'没有可汇总'):self.run_merge(cfg,source,target)
        self.assertFalse(target.events)
        self.assertEqual(target.sheets[0].data,[['旧','保留']])

    def test_read_failure_keeps_target_untouched(self):
        source=FakeBook('s'*25,[FakeSheet('一',1,[['名','号'],['甲','001']]),FakeSheet('坏表',2,[['名','号'],['乙','002']])])
        source.fail_title='坏表'
        target=FakeBook('t'*25,[FakeSheet('结果',3,[['old','old'],['保留','001']])])
        before=copy.deepcopy(target.sheets[0].data)
        with self.assertRaises(PermissionError):self.run_merge(self.config(),source,target)
        self.assertEqual(target.sheets[0].data,before)
        self.assertEqual(target.events,[])

    def test_missing_source_column_no_write(self):
        source=FakeBook('s'*25,[FakeSheet('窄表',1,[['名'],['甲']])])
        target=FakeBook('t'*25,[])
        with self.assertRaisesRegex(ValueError,'来源列 B'):self.run_merge(self.config(),source,target)
        self.assertFalse(target.events)

    def test_custom_rows_create_output_and_preserve_blanks_inside_data(self):
        source=FakeBook('s'*25,[FakeSheet('一',1,[['说明'],['表头','表头'],['甲',''],['乙','002']])])
        target=FakeBook('t'*25,[])
        cfg=self.config(wm_source_header_row=2,wm_source_start_row=3,wm_target_header_row=3,wm_target_start_row=5)
        self.run_merge(cfg,source,target)
        data=target.sheets[0].data
        self.assertEqual(data[2],['号码','姓名'])
        self.assertEqual(data[4:6],[['','甲'],['002','乙']])

    def test_empty_sources_clear_previous_results_but_no_eligible_sheet_does_not(self):
        source=FakeBook('s'*25,[FakeSheet('一',1,[['名','号']])])
        target=FakeBook('t'*25,[FakeSheet('结果',2,[['名','号'],['旧','123']])])
        self.assertEqual(self.run_merge(self.config(),source,target)['total_rows'],0)
        self.assertEqual(target.sheets[0].data[1],['',''])
        target.events=[]
        with self.assertRaisesRegex(ValueError,'没有可汇总'):
            self.run_merge(self.config(wm_exclude_sheets=['一']),source,target)
        self.assertFalse(target.events)

    def test_cancel_before_writes_preserves_target(self):
        source=FakeBook('s'*25,[FakeSheet('一',1,[['名','号'],['甲','001']])])
        target=FakeBook('t'*25,[])
        cancelled=lambda: bool(source.events)
        with self.assertRaisesRegex(RuntimeError,'取消'):
            self.run_merge(self.config(),source,target,cancelled=cancelled)
        self.assertFalse(target.events)

    def test_chunked_reads_keep_data_after_blank_block(self):
        data=[['名','号']]+[['',''] for _ in range(2000)]+[['最后','0001']]
        source=FakeBook('s'*25,[FakeSheet('一',1,data)])
        target=FakeBook('t'*25,[])
        self.assertEqual(self.run_merge(self.config(),source,target)['total_rows'],1)
        self.assertEqual(target.sheets[0].data[1],['0001','最后'])

    def test_large_output_is_batched_and_keeps_all_rows_in_order(self):
        data=[['名','号']]+[[f'人{i}',f'{i:05d}'] for i in range(2005)]
        source=FakeBook('s'*25,[FakeSheet('一',1,data)])
        target=FakeBook('t'*25,[])
        result=self.run_merge(self.config(),source,target)
        self.assertEqual(result['total_rows'],2005)
        self.assertEqual(len(target.sheets[0].data),2006)
        self.assertEqual(target.sheets[0].data[1],['00000','人0'])
        self.assertEqual(target.sheets[0].data[-1],['02004','人2004'])
        self.assertEqual(len([x for x in target.events if x[0]=='write']),4)

    def test_start_job_validates_before_saving_and_keeps_menu_job_id(self):
        import app
        cfg=self.config()
        cfg.ui_active_menu='independent-merge'
        with patch.object(app,'save_config') as save,patch.object(app,'enqueue_job',return_value=None) as enqueue:
            self.assertIsNone(app.start_worksheet_merge_job(cfg))
            save.assert_called_once_with(cfg)
            enqueue.assert_called_once_with('worksheet_merge',cfg)
            self.assertEqual(enqueue.call_args.args[1].ui_active_menu,'independent-merge')
        cfg.wm_target_start_row=0
        with patch.object(app,'save_config') as save,patch.object(app,'enqueue_job') as enqueue:
            self.assertTrue(app.start_worksheet_merge_job(cfg))
            save.assert_not_called()
            enqueue.assert_not_called()

    def test_read_headers_uses_visible_not_excluded_sheet_and_keeps_column_positions(self):
        source=FakeBook('s'*25,[FakeSheet('隐藏',1,[['忽略']],hidden=True),FakeSheet('排除',2,[['忽略']]),
                                FakeSheet('数据',3,[['名字','','号码']])])
        cfg=self.config(wm_exclude_sheets=['排除'])
        with patch.object(merge,'authorize_cfg',return_value=object()),patch.object(merge,'open_by_url_or_id',return_value=source):
            columns=merge.read_merge_headers(cfg)
        self.assertEqual(columns,[{'header':'名字','column':'A','hyperlink':False},{'header':'号码','column':'C','hyperlink':False}])
        self.assertEqual(source.events[0][2],'FORMATTED_VALUE')

    def test_read_headers_respects_include_mode(self):
        source=FakeBook('s'*25,[FakeSheet('忽略',1,[['错误表头']]),FakeSheet('指定',2,[['名字','','号码']])])
        cfg=self.config(wm_sheet_filter_mode='include',wm_exclude_sheets=['指定'])
        with patch.object(merge,'authorize_cfg',return_value=object()),patch.object(merge,'open_by_url_or_id',return_value=source):
            columns=merge.read_merge_headers(cfg)
        self.assertEqual([column['header'] for column in columns],['名字','号码'])
        self.assertEqual([event[1] for event in source.events],['指定'])

    def test_validation_prevents_invalid_positions_and_missing_fields(self):
        for key,value in [('wm_source_start_row',1),('wm_target_start_row',1),('wm_target_start_col','A1'),
                          ('wm_source_header_row','abc'),('wm_columns',[]),('wm_target_sheet','')]:
            cfg=self.config()
            setattr(cfg,key,value)
            with self.subTest(key=key),self.assertRaises(ValueError):merge.validate_merge_config(cfg)

    def test_rich_link_label_and_url_survive_merge_without_formula_or_other_style_writes(self):
        ws=FakeSheet('素材',1,[['姓名','成品链接'],['甲','点击观看'],['乙','https://drive.google.com/file/d/another1234567890/view']])
        ws.links[1,1]='https://drive.google.com/file/d/first1234567890/view'
        source=FakeBook('s'*25,[ws])
        target=FakeBook('t'*25,[FakeSheet('结果',2,[['旧','旧'],['旧','旧']])])
        cfg=self.config()
        cfg.wm_columns=[{'header':'姓名','column':'A'},{'header':'成品链接','column':'B'}]
        self.run_merge(cfg,source,target)
        dest=target.sheets[0]
        self.assertEqual(dest.data[1],['甲','点击观看'])
        self.assertEqual(dest.links[1,1],ws.links[1,1])
        self.assertEqual(dest.links[2,1],ws.data[2][1])
        self.assertTrue(all(event[1]=='RAW' for event in target.events if event[0]=='write'))

    def test_link_checkbox_can_explicitly_disable_automatic_header_detection(self):
        cfg=self.config()
        cfg.wm_columns=[{'header':'成品链接','column':'A','hyperlink':False}]
        self.assertFalse(merge.validate_merge_config(cfg).columns[0]['hyperlink'])

    def test_existing_link_repair_matches_values_without_reordering_or_rewriting_data(self):
        source_sheet=FakeSheet('来源',1,[['姓名','链接'],['甲','作品甲'],['乙','作品乙']])
        source_sheet.links[1,1]='https://example.com/a';source_sheet.links[2,1]='https://example.com/b'
        source=FakeBook('s'*25,[source_sheet])
        target_sheet=FakeSheet('结果',2,[['姓名','链接'],['乙','作品乙'],['甲','作品甲'],['用户修改','不匹配']])
        target=FakeBook('t'*25,[target_sheet])
        cfg=self.config();cfg.wm_columns=[{'header':'姓名','column':'A'},{'header':'成品链接','column':'B'}]
        before=copy.deepcopy(target_sheet.data)
        with patch.object(merge,'authorize_cfg',return_value=object()),patch.object(merge,'open_by_url_or_id',side_effect=[source,target]):
            result=merge.repair_worksheet_links(cfg,log=lambda _:None)
        self.assertEqual(result['total_rows'],2)
        self.assertEqual(target_sheet.data,before)
        self.assertEqual(target_sheet.links[1,1],'https://example.com/b')
        self.assertEqual(target_sheet.links[2,1],'https://example.com/a')
        self.assertNotIn((3,1),target_sheet.links)
        self.assertFalse(any(event[0] in ('write','clear','create') for event in target.events))

    def test_link_repair_skips_ambiguous_equal_labels_pointing_to_different_urls(self):
        one=FakeSheet('一',1,[['名字','链接'],['甲','同名文件']]);one.links[1,1]='https://example.com/one'
        two=FakeSheet('二',2,[['名字','链接'],['甲','同名文件']]);two.links[1,1]='https://example.com/two'
        source=FakeBook('s'*25,[one,two]);target=FakeBook('t'*25,[FakeSheet('结果',3,[['姓名','链接'],['甲','同名文件']])])
        cfg=self.config();cfg.wm_columns=[{'header':'姓名','column':'A'},{'header':'成品链接','column':'B'}]
        with patch.object(merge,'authorize_cfg',return_value=object()),patch.object(merge,'open_by_url_or_id',side_effect=[source,target]):
            result=merge.repair_worksheet_links(cfg,log=lambda _:None)
        self.assertEqual(result['total_rows'],0)
        self.assertEqual(result['ambiguous'],1)
        self.assertFalse(target.sheets[0].links)

    def test_link_repair_resolves_duplicate_labels_only_after_full_sequence_matches(self):
        one=FakeSheet('一',1,[['名字','链接'],['甲','同名文件']]);one.links[1,1]='https://example.com/one'
        two=FakeSheet('二',2,[['名字','链接'],['甲','同名文件']]);two.links[1,1]='https://example.com/two'
        source=FakeBook('s'*25,[one,two]);dest=FakeSheet('结果',3,[['姓名','链接'],['甲','同名文件'],['甲','同名文件']])
        target=FakeBook('t'*25,[dest]);before=copy.deepcopy(dest.data)
        cfg=self.config();cfg.wm_columns=[{'header':'姓名','column':'A'},{'header':'成品链接','column':'B'}]
        with patch.object(merge,'authorize_cfg',return_value=object()),patch.object(merge,'open_by_url_or_id',side_effect=[source,target]):
            result=merge.repair_worksheet_links(cfg,log=lambda _:None)
        self.assertEqual(result['total_rows'],2)
        self.assertEqual(result['ambiguous'],0)
        self.assertEqual(dest.links[1,1],'https://example.com/one')
        self.assertEqual(dest.links[2,1],'https://example.com/two')
        self.assertEqual(dest.data,before)

    def test_link_repair_keeps_modified_rows_while_stable_unique_anchors_align_duplicates(self):
        data=[['姓名','链接'],['唯一1','一'],['甲','重名'],['甲','重名'],['旧名','旧文件'],['唯一2','二']]
        ws=FakeSheet('来源',1,data)
        for i in range(1,6):ws.links[i,1]=f'https://example.com/{i}'
        source=FakeBook('s'*25,[ws]);dest=FakeSheet('结果',2,data)
        dest.data[4]=['用户修改','新文字'];before=copy.deepcopy(dest.data)
        target=FakeBook('t'*25,[dest]);cfg=self.config()
        cfg.wm_columns=[{'header':'姓名','column':'A'},{'header':'成品链接','column':'B'}]
        with patch.object(merge,'authorize_cfg',return_value=object()),patch.object(merge,'open_by_url_or_id',side_effect=[source,target]):
            result=merge.repair_worksheet_links(cfg,log=lambda _:None)
        self.assertEqual(result['total_rows'],4)
        self.assertEqual(dest.links[2,1],'https://example.com/2')
        self.assertEqual(dest.links[3,1],'https://example.com/3')
        self.assertNotIn((4,1),dest.links)
        self.assertEqual(dest.data,before)

    def test_link_repair_can_match_unique_file_caption_after_other_fields_changed(self):
        ws=FakeSheet('来源',1,[['姓名','链接'],['甲','唯一成品文件']]);ws.links[1,1]='https://example.com/file'
        source=FakeBook('s'*25,[ws]);dest=FakeSheet('结果',2,[['姓名','链接'],['人工更正姓名','唯一成品文件']])
        target=FakeBook('t'*25,[dest]);before=copy.deepcopy(dest.data);cfg=self.config()
        cfg.wm_columns=[{'header':'姓名','column':'A'},{'header':'成品链接','column':'B'}]
        with patch.object(merge,'authorize_cfg',return_value=object()),patch.object(merge,'open_by_url_or_id',side_effect=[source,target]):
            result=merge.repair_worksheet_links(cfg,log=lambda _:None)
        self.assertEqual(result['total_rows'],1)
        self.assertEqual(dest.links[1,1],'https://example.com/file')
        self.assertEqual(dest.data,before)

    def test_config_roundtrip_preserves_new_fields_and_archived_menus(self):
        from app import _cfg_from_payload
        cfg=_cfg_from_payload({'wm_source_url':'s'*25,'wm_target_url':'t'*25,'wm_target_sheet':'结果',
                               'wm_columns':[{'header':'电话','column':'B'}], 'wm_exclude_sheets':['不要'],
                               'wm_source_start_row':'4','ui_archived_menus':[{'id':'old','template':'catalog'}]},base=Config())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json'
            save_config(cfg,path)
            restored=load_config(path)
        self.assertEqual(restored.wm_columns,cfg.wm_columns)
        self.assertEqual(restored.ui_archived_menus,cfg.ui_archived_menus)
        self.assertEqual(merge.validate_merge_config(restored).source_start,4)

    def test_menu_partition_preserves_names_and_every_removed_configuration(self):
        menus=[{'id':key,'template':key,'name':'自定义名字','settings':{'url':key}}
               for key in ['filter','posts','video','catalog','align','custom','sheet_metrics','roster']]
        original=copy.deepcopy(menus)
        active,archived=partition_menus(menus)
        self.assertEqual([x['template'] for x in active],['filter','posts','video'])
        self.assertEqual(len(archived),5)
        self.assertEqual(menus,original)
        self.assertEqual(partition_menus(active,archived),(active,archived))


if __name__=='__main__':unittest.main()
