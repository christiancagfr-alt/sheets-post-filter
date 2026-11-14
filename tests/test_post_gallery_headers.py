import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from publish_cloudflare import rows_to_assets, split_sheet_for_publish, overlay_formula_urls, publish_configured_sheet
from cf_accounts import site_for_menu

HEADERS = ['管理员&#x20;', '语言', '专页id ', '专页名字', '帖文链接', '引流', '帖文ID', '头像', '图片链接', '发帖日期', '👍点赞', '💬评论', '🔁分享', '帖文类型', '📜帖文内容', '帖文翻译', '文案类型', 'OCR', 'OCR翻译', '图片类型', '音频文本', '音频文本翻译', '题材类型', '来源类型', '缩略图', '交教会', '转换率', '美工', '', '', '']
ROW = ['示例管理员', '示例语言', 'example-page', '示例专页', 'https://example.com/post', 0, 'example-post', 'https://example.com/avatar.jpg', 'https://example.com/full.jpg', '2026-01-02', 1200, 0, 3, '图片', '示例正文', '示例翻译', '示例文案类型', '示例OCR', '示例OCR翻译', '示例图片类型', '示例音频', '示例音频翻译', '示例题材', '示例来源', 'https://example.com/thumb.jpg', 4, 0.25, '示例美工', '不可读取的旧AC列', '', '']

class HeaderPublishingTests(unittest.TestCase):
    def test_actual_headers_and_reordered_columns_produce_identical_assets(self):
        expected=rows_to_assets(HEADERS,[ROW])[0]
        actual=rows_to_assets(list(reversed(HEADERS)),[list(reversed(ROW))])[0]
        self.assertEqual(actual,expected)
        for key,value in {'name':'示例管理员','pageName':'示例专页','maker':'example-page','postTranslation':'示例翻译','copyType':'示例文案类型','topicType':'示例题材','likes':'1200','comments':'0','water':'4','conversionRate':'0.25','group':'','thumbnailUrl':'https://example.com/thumb.jpg','imageUrl':'https://example.com/full.jpg'}.items():
            self.assertEqual(actual[key],value)

    def test_missing_optional_fields_never_read_old_positions(self):
        item=rows_to_assets(['帖文链接','管理员'],[['https://example.com/post','示例']])[0]
        self.assertEqual(item['postId'],'')
        self.assertEqual(item['maker'],'')
        self.assertEqual(item['thumbnailUrl'],'')
        self.assertEqual(item['water'],'')
        self.assertEqual(item['group'],'')

    def test_missing_duplicate_or_headerless_data_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'帖文链接'):rows_to_assets([], [ROW])
        with self.assertRaisesRegex(ValueError,'重复'):rows_to_assets(['帖文链接','帖文链接'],[['a','b']])
        with self.assertRaisesRegex(ValueError,'表头'):split_sheet_for_publish([ROW],1,False)

    def test_formulas_follow_real_column_positions(self):
        ws=Mock();ws.batch_get.return_value=[[["=HYPERLINK(\"https://example.com/post\",\"打开\")"]],[["=IMAGE(\"https://example.com/pic.jpg\")"]]]
        rows=[['','','']]
        overlay_formula_urls(ws,rows,2,log=lambda _:None,headers=['语言','帖文链接','缩略图'])
        self.assertEqual(ws.batch_get.call_args.args[0],['B2:B2','C2:C2'])
        self.assertEqual(rows[0],['','https://example.com/post','https://example.com/pic.jpg'])

    def test_direct_publish_never_needs_result_tables_or_runs_aggregation(self):
        cfg=SimpleNamespace(cf_publish_mode='direct',cf_direct_url='https://docs.google.com/spreadsheets/d/example/edit#gid=42',cf_direct_sheet='',cf_direct_header_row='1')
        ws=Mock(title='示例子表');ws.title='示例子表';ws.get_all_values.return_value=[HEADERS,ROW];ws.batch_get.return_value=[]
        ss=Mock();ss.get_worksheet_by_id.return_value=ws
        with patch('fetch_posts.open_by_url_or_id',return_value=ss),patch('publish_cloudflare.publish_assets_to_cloudflare',return_value={'ok':True}) as publish:
            result=publish_configured_sheet(cfg,Mock(),log=lambda _:None)
        self.assertTrue(result['ok']);ss.get_worksheet_by_id.assert_called_once_with(42)
        self.assertEqual(publish.call_args.args[1],[h.strip() for h in HEADERS if h.strip()])
        self.assertEqual(publish.call_args.kwargs['start_row'],2)
        ws.update.assert_not_called();ss.add_worksheet.assert_not_called()

    def test_bad_header_row_stops_before_publishing(self):
        cfg=SimpleNamespace(cf_publish_mode='direct',cf_direct_url='example',cf_direct_sheet='示例',cf_direct_header_row='2')
        ss=Mock();ss.worksheet.return_value.get_all_values.return_value=[HEADERS,ROW]
        with patch('fetch_posts.open_by_url_or_id',return_value=ss),patch('publish_cloudflare.publish_assets_to_cloudflare') as publish:
            with self.assertRaisesRegex(ValueError,'表头'):publish_configured_sheet(cfg,Mock(),log=lambda _:None)
        publish.assert_not_called()

    def test_pages_alias_validates_same_owner_and_kind(self):
        site={'host':'gallery.example.com','pages_url':'https://example.pages.dev','account_id':'example-account','kind':'promo'}
        with patch('cf_accounts.require_menu',return_value={'account_id':'example-account'}),patch('cf_accounts.load_cf_state',return_value={}),patch('cf_accounts.known_gallery_sites',return_value=[site]):
            self.assertEqual(site_for_menu('example','example-menu','example.pages.dev',kind='promo')[1],site)
            with self.assertRaises(ValueError):site_for_menu('example','example-menu','other.pages.dev',kind='promo')
            with self.assertRaisesRegex(ValueError,'多工作表'):site_for_menu('example','example-menu','example.pages.dev',kind='summary')


class ActualSourceTests(unittest.TestCase):
    def test_source_reader_keeps_named_columns_and_ignores_blank_tail(self):
        from publish_cloudflare import read_post_source
        ws=Mock();ws.title='示例';ws.get_all_values.return_value=[HEADERS,ROW];ws.batch_get.return_value=[]
        ss=Mock();ss.worksheet.return_value=ws
        headers,rows=read_post_source(ss,'https://example.com/sheet','示例','1',lambda _:None)
        self.assertEqual(len(headers),28);self.assertEqual(len(rows[0]),28)
        self.assertEqual(headers[0],'管理员&#x20;');self.assertEqual(rows[0][-1],'示例美工')

    def test_filter_run_uses_actual_header_order_not_configured_legacy_fields(self):
        from fetch_posts import Config, run
        cfg=Config(sources=[{'url':'https://docs.google.com/spreadsheets/d/example-sheet/edit','name':'示例'}],
            target_url='https://docs.google.com/spreadsheets/d/example-output/edit',write_hot=False,
            start_date='2026-01-01',end_date='2026-01-31',fields=[{'name':'旧字段','sheet':'旧表','range':'Z:Z'}])
        source=Mock();source.title='示例源表';dest=Mock();dest.title='示例结果表'
        with patch('fetch_posts.authorize_cfg'),patch('fetch_posts.open_by_url_or_id',side_effect=[dest,source]),patch('publish_cloudflare.read_post_source',return_value=(HEADERS[:28],[ROW[:28]])),patch('fetch_posts.build_datasource_from_ss') as legacy:
            result=run(cfg,dry_run=True,log=lambda _:None)
        self.assertEqual(result['total_rows'],1);self.assertEqual(result['headers'],HEADERS[:28]);legacy.assert_not_called()

    def test_source_error_prevents_any_writes(self):
        from fetch_posts import Config, run
        cfg=Config(sources=[{'url':'https://docs.google.com/spreadsheets/d/example-sheet/edit'}],target_url='https://docs.google.com/spreadsheets/d/example-output/edit',write_hot=False,start_date='2026-01-01',end_date='2026-01-31')
        with patch('fetch_posts.authorize_cfg'),patch('fetch_posts.open_by_url_or_id'),patch('publish_cloudflare.read_post_source',side_effect=ValueError('缺少表头')),patch('fetch_posts.write_output') as write:
            with self.assertRaisesRegex(RuntimeError,'停止写入'):run(cfg,log=lambda _:None)
        write.assert_not_called()

    def test_new_config_settings_roundtrip(self):
        import app
        cfg=app._cfg_from_payload({'cf_publish_mode':'direct','cf_direct_url':'https://example.com/sheet','cf_direct_sheet':'示例','cf_direct_header_row':'3','filter_source_sheet':'示例来源','filter_header_row':'2'})
        self.assertEqual(cfg.cf_publish_mode,'direct');self.assertEqual(cfg.cf_direct_header_row,'3')
        self.assertEqual(cfg.filter_source_sheet,'示例来源');self.assertEqual(cfg.filter_header_row,'2')

if __name__=='__main__':unittest.main()

class UnifiedFilterTests(unittest.TestCase):
    def test_direct_run_bypasses_output_and_filter_validation(self):
        from fetch_posts import Config, run
        cfg=Config(cf_publish_mode='direct',target_url='',write_all=False,write_hot=False)
        with patch('fetch_posts.authorize_cfg'),patch('publish_cloudflare.publish_configured_sheet',return_value={'sent':1}) as publish,patch('fetch_posts.resolve_dates') as dates,patch('fetch_posts.write_output') as write:
            result=run(cfg,log=lambda _:None)
        self.assertEqual(result['sent'],1);publish.assert_called_once();dates.assert_not_called();write.assert_not_called()

    def test_mapping_is_name_based_and_stale_mapping_stops(self):
        from publish_cloudflare import mapped_post_headers
        mappings=[{'source':'原文地址','target':'帖文链接'},{'source':'赞数','target':'点赞'}]
        self.assertEqual(mapped_post_headers(['赞数','语言','原文地址'],mappings),['点赞','语言','帖文链接'])
        with self.assertRaisesRegex(ValueError,'不存在'):
            mapped_post_headers(['语言','原文地址'],mappings)
        with self.assertRaisesRegex(ValueError,'重复'):
            mapped_post_headers(['原文地址','帖文链接'],mappings[:1])

    def test_mapping_payload_roundtrip(self):
        from app import _cfg_from_payload
        from fetch_posts import Config
        value=[{'source':'原文地址','target':'帖文链接'}]
        self.assertEqual(_cfg_from_payload({'filter_header_mappings':value},base=Config()).filter_header_mappings,value)

    def test_multiple_sources_align_columns_before_direct_publish(self):
        from fetch_posts import Config
        cfg=Config(cf_publish_mode='direct',sources=[{'url':'https://docs.google.com/spreadsheets/d/example-one/edit'},{'url':'https://docs.google.com/spreadsheets/d/example-two/edit'}])
        with patch('fetch_posts.open_by_url_or_id'),patch('publish_cloudflare.read_post_source',side_effect=[(['帖文链接','语言'],[['https://example.com/1','中文']]),(['语言','帖文链接'],[['英语','https://example.com/2']])]),patch('publish_cloudflare.publish_assets_to_cloudflare',return_value={}) as publish:
            publish_configured_sheet(cfg,Mock(),log=lambda _:None)
        self.assertEqual(publish.call_args.args[2],[['https://example.com/1','中文'],['https://example.com/2','英语']])
