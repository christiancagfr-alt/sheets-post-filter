"""Direct publication reads existing Sheets without merge configuration or writes."""
import copy
import unittest
from dataclasses import asdict
from unittest.mock import patch

from fetch_posts import Config
import summary_direct as direct
import summary_publish as publish
import worksheet_channels as channels
from test_worksheet_merge import FakeBook, FakeSheet


class DirectPublishTests(unittest.TestCase):
    def config(self):
        return Config(ui_active_menu='all-media',wm_publish_source='direct',wm_direct_url='s'*25,
                      wm_direct_sheet='图片',wm_target_url='',wm_source_url='',wm_columns=[])

    def book(self):
        ws=FakeSheet('图片',10,[['日期','成品链接','标题','分类','','','制作人'],
                                ['2026-10-20','点击打开','一张图','类别','','','制作人甲']])
        ws.links[1,1]='https://drive.google.com/file/d/EXAMPLE_IDENTIFIER_0066/view'
        return FakeBook('s'*25,[FakeSheet('隐藏',9,[['不读']],hidden=True),ws])

    def test_direct_rows_headers_and_rich_links_need_no_merge_or_destination(self):
        cfg=self.config();book=self.book();before=copy.deepcopy(book.sheets[1].data)
        with patch.object(direct,'authorize_cfg',return_value=object()),patch.object(direct,'open_by_url_or_id',return_value=book):
            headers=direct.read_direct_headers(cfg,log=lambda _:None)
            rows,links=direct.read_direct_rows(cfg,log=lambda _:None)
        self.assertEqual(headers[-1]['column'],'G')
        assets,metadata,_=publish.rows_to_summary_assets(cfg,rows,links)
        self.assertEqual(assets[0]['maker'],'制作人甲')
        self.assertEqual(assets[0]['sourceUrl'],book.sheets[1].links[1,1])
        self.assertEqual(book.sheets[1].data,before)
        self.assertTrue(all(event[0] in ('read','rich_links') for event in book.events))

    def test_gid_sheet_selection_and_hidden_sheet_rejection(self):
        cfg=self.config();cfg.wm_direct_sheet='';cfg.wm_direct_url='https://docs.google.com/spreadsheets/d/'+'s'*25+'/edit#gid=10'
        with patch.object(direct,'authorize_cfg',return_value=object()),patch.object(direct,'open_by_url_or_id',return_value=self.book()):
            self.assertEqual(direct.open_direct_sheet(cfg)[1].title,'图片')
            cfg.wm_direct_url=cfg.wm_direct_url.replace('gid=10','gid=9')
            with self.assertRaisesRegex(ValueError,'隐藏'):direct.open_direct_sheet(cfg)

    def test_direct_publish_preflight_before_google_and_no_merge_validation(self):
        cfg=self.config()
        with patch.object(publish,'_prepare_publish',return_value={'ready':True}),patch.object(direct,'read_direct_rows',return_value=([],[])) as read,patch.object(publish,'publish_summary_rows',return_value={'ok':True}) as send,patch('worksheet_merge.validate_merge_config',side_effect=AssertionError('must not merge')):
            self.assertTrue(publish.publish_summary_sheet(cfg,log=lambda _:None)['ok'])
        read.assert_called_once();send.assert_called_once()
        with patch.object(publish,'_prepare_publish',side_effect=RuntimeError('offline')),patch.object(direct,'read_direct_rows') as read:
            with self.assertRaisesRegex(RuntimeError,'offline'):publish.publish_summary_sheet(cfg,log=lambda _:None)
        read.assert_not_called()

    def test_three_direct_channels_publish_independently_and_keep_existing_video_stream(self):
        cfg=Config(ui_active_menu='all-media',wm_media_type='video',wm_source_url='s'*25,
                   wm_target_url='t'*25,wm_target_sheet='旧视频汇总')
        cfg.wm_channels=channels.normalize_channels(asdict(cfg))
        for row in cfg.wm_channels:
            row['enabled']=True
            row['settings'].update(wm_publish_source='direct',wm_direct_url='s'*25,wm_direct_sheet=row['name'])
        cfg.wm_target_url=''
        calls=[]
        def send(child,**kwargs):
            calls.append(child)
            return {'ok':True,'totalRows':1}
        with patch.object(publish,'_prepare_publish',return_value={}),patch.object(publish,'publish_summary_sheet',side_effect=send):
            result=channels.publish_media_channels(cfg,log=lambda _:None)
        self.assertEqual(set(result['channels']),{'image','video','effect_video'})
        self.assertEqual([child.wm_media_type for child in calls],['image','video','video'])
        self.assertEqual([child._wm_channel_name for child in calls],['图片','KB视频','效果视频'])
        self.assertEqual(channels.stream_identity(calls[1]),'all-media')
        self.assertEqual(channels.stream_identity(calls[2]),'all-media|effect_video')
        self.assertEqual(len({child._wm_catalog_id for child in calls}),3)
        with self.assertRaisesRegex(ValueError,'无需汇总'):channels.validate_merge_jobs(cfg)

    def test_desktop_publish_button_accepts_direct_config_without_merge_urls(self):
        import app
        cfg=self.config()
        with patch('cf_accounts.site_for_menu',return_value=({},{})),patch.object(app,'save_config') as save,patch.object(app,'enqueue_job',return_value=None) as enqueue:
            self.assertIsNone(app.start_worksheet_publish_job(cfg))
        save.assert_called_once();enqueue.assert_called_once()
