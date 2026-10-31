"""Local tests for image/video configuration, multiple books and isolated streams."""
import copy
import hashlib
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from fetch_posts import Config,extract_spreadsheet_id,load_config,save_config
import worksheet_channels as channels
import worksheet_merge as merge
import summary_publish as publish
from test_worksheet_merge import FakeBook,FakeSheet

FIELDS=[{'header':name,'column':column,'hyperlink':column=='B'} for name,column in zip(['日期','成品链接','标题','分类'],'ABCD')]


class WorksheetChannelTests(unittest.TestCase):
    def legacy(self):
        return Config(ui_active_menu='existing-menu',wm_source_url='v'*25,wm_target_url='t'*25,
                      wm_target_sheet='数据汇总',wm_media_type='video',wm_release_days=7,wm_columns=copy.deepcopy(FIELDS))

    def combined(self):
        cfg=self.legacy();cfg.wm_channels=channels.normalize_channels(asdict(cfg))
        image=next(row for row in cfg.wm_channels if row['id']=='image')
        image['enabled']=True
        image['settings'].update(wm_source_urls=['s'*25,'u'*25],wm_columns=copy.deepcopy(FIELDS),wm_exclude_sheets=['排除'])
        return cfg

    def execute(self,cfg,books):
        with patch.object(merge,'authorize_cfg',return_value=object()),patch.object(merge,'open_by_url_or_id',side_effect=lambda _gc,url,**_kw:books[extract_spreadsheet_id(url)]):
            return merge.run_worksheet_merge(cfg,log=lambda _:None)

    def test_legacy_video_configuration_and_stream_identity_are_preserved(self):
        cfg=self.legacy();before=asdict(cfg)
        rows=channels.normalize_channels(before);video=rows[1];image=rows[0]
        self.assertTrue(video['enabled']);self.assertTrue(video['legacy_stream'])
        self.assertEqual(video['settings']['wm_source_urls'],['v'*25])
        self.assertEqual(video['settings']['wm_target_sheet'],'数据汇总')
        self.assertEqual(video['settings']['wm_release_days'],7)
        self.assertFalse(image['enabled']);self.assertEqual(image['settings']['wm_release_days'],4)
        self.assertEqual(asdict(cfg),before)
        cfg.wm_channels=rows
        child=channels.channel_config(cfg,video)
        self.assertEqual(channels.stream_identity(child),'existing-menu')
        image_child=channels.channel_config(cfg,image)
        self.assertEqual(channels.stream_identity(image_child),'existing-menu|image')
        self.assertEqual(child.ui_active_menu,image_child.ui_active_menu)

    def test_multiple_sources_merge_into_image_sheet_and_video_into_another_sheet(self):
        cfg=self.combined()
        first=FakeSheet('图一',1,[['日','链','名','类'],['2026-10-20','图甲','甲','图片分类']]);first.links[1,1]='https://example.com/a.jpg'
        second=FakeSheet('图二',2,[['日','链','名','类'],['2026-10-21','图乙','乙','另一类']]);second.links[1,1]='https://example.com/b.jpg'
        video=FakeSheet('视频',3,[['日','链','名','类'],['2026-10-20','视频甲','片','视频分类']]);video.links[1,1]='https://example.com/c.mp4'
        target=FakeBook('t'*25,[])
        books={'s'*25:FakeBook('s'*25,[first,FakeSheet('隐藏',4,[['不读']],hidden=True)]),
               'u'*25:FakeBook('u'*25,[second,FakeSheet('排除',5,[['不读']])]),
               'v'*25:FakeBook('v'*25,[video]),'t'*25:target}
        result=self.execute(cfg,books)
        self.assertEqual(result['total_rows'],3)
        self.assertEqual(set(result['channels']),{'image','video'})
        images=target.worksheet('图片汇总');videos=target.worksheet('数据汇总')
        self.assertEqual(images.data[1:3],[['2026-10-20','图甲','甲','图片分类'],['2026-10-21','图乙','乙','另一类']])
        self.assertEqual(images.links[1,1],'https://example.com/a.jpg')
        self.assertEqual(images.links[2,1],'https://example.com/b.jpg')
        self.assertEqual(videos.data[1][1],'视频甲')
        self.assertEqual(videos.links[1,1],'https://example.com/c.mp4')

    def test_duplicate_workbook_links_are_read_once_even_with_different_gid(self):
        cfg=self.legacy();cfg.wm_source_urls=['v'*25,'https://docs.google.com/spreadsheets/d/'+'v'*25+'/edit#gid=99','u'*25]
        self.assertEqual(channels.source_urls(cfg),['v'*25,'u'*25])
        target=FakeBook('t'*25,[])
        books={'v'*25:FakeBook('v'*25,[FakeSheet('一',1,[['d','l','t','c'],['2026-10-20','https://example.com/a','a','c']])]),
               'u'*25:FakeBook('u'*25,[FakeSheet('二',2,[['d','l','t','c'],['2026-10-21','https://example.com/b','b','c']])]),'t'*25:target}
        self.assertEqual(self.execute(cfg,books)['total_rows'],2)
        self.assertEqual(len(books['v'*25].events),1)

    def test_later_source_read_failure_does_not_replace_existing_output(self):
        cfg=self.legacy();cfg.wm_source_urls=['v'*25,'u'*25]
        source1=FakeBook('v'*25,[FakeSheet('一',1,[['d','l','t','c'],['2026-10-20','https://example.com/a','a','c']])])
        source2=FakeBook('u'*25,[FakeSheet('坏表',2,[['d','l','t','c'],['2026-10-21','https://example.com/b','b','c']])]);source2.fail_title='坏表'
        target=FakeBook('t'*25,[FakeSheet('数据汇总',3,[['原有','保留']])]);before=copy.deepcopy(target.sheets[0].data)
        with self.assertRaises(PermissionError):self.execute(cfg,{'v'*25:source1,'u'*25:source2,'t'*25:target})
        self.assertEqual(target.sheets[0].data,before);self.assertFalse(target.events)

    def test_same_workbook_excludes_both_output_sheets_even_if_one_channel_disabled(self):
        cfg=self.combined();image=cfg.wm_channels[0];video=cfg.wm_channels[1]
        image['settings']['wm_source_urls']=['t'*25];video['enabled']=False
        target=FakeBook('t'*25,[FakeSheet('来源',1,[['d','l','t','c'],['2026-10-20','https://example.com/a','a','c']]),
                                 FakeSheet('图片汇总',2,[['d','l','t','c'],['旧图片','不读','不读','不读']]),
                                 FakeSheet('数据汇总',3,[['d','l','t','c'],['旧视频','不读','不读','不读']])])
        result=self.execute(cfg,{'t'*25:target})
        self.assertEqual(result['total_rows'],1)
        self.assertEqual(target.worksheet('数据汇总').data[1][0],'旧视频')
        self.assertEqual([event[1] for event in target.events if event[0]=='read'],['来源'])

    def test_both_channels_validate_before_any_channel_runs_and_sheet_names_cannot_collide(self):
        cfg=self.combined();cfg.wm_channels[1]['settings']['wm_target_sheet']='图片汇总'
        with self.assertRaisesRegex(ValueError,'不同的目标 Sheet'):channels.validate_merge_jobs(cfg)
        cfg=self.combined();cfg.wm_channels[1]['settings']['wm_source_url']='';cfg.wm_channels[1]['settings']['wm_source_urls']=[]
        with patch.object(merge,'authorize_cfg') as auth:
            with self.assertRaisesRegex(ValueError,'视频配置'):merge.run_worksheet_merge(cfg,log=lambda _:None)
        auth.assert_not_called()

    def test_headers_can_be_imported_from_next_eligible_workbook(self):
        cfg=self.legacy();cfg.wm_source_urls=['v'*25,'u'*25]
        first=FakeBook('v'*25,[FakeSheet('隐藏',1,[['不读']],hidden=True)])
        second=FakeBook('u'*25,[FakeSheet('表头',2,[['日期','成品链接','','分类']])])
        with patch.object(merge,'authorize_cfg',return_value=object()),patch.object(merge,'open_by_url_or_id',side_effect=[first,second]):
            fields=merge.read_merge_headers(cfg)
        self.assertEqual([field['column'] for field in fields],['A','B','D'])
        self.assertTrue(fields[1]['hyperlink'])

    def test_media_publishing_uses_shared_endpoint_and_distinct_streams_with_parent_permissions(self):
        cfg=self.combined();calls=[]
        def send(child,**kwargs):
            calls.append((child,kwargs))
            return {'ok':True,'totalRows':2 if child.wm_media_type=='image' else 3}
        with patch.object(publish,'_prepare_publish',return_value={'ready':True}) as prepare,patch.object(publish,'publish_summary_sheet',side_effect=send):
            result=channels.publish_media_channels(cfg,log=lambda _:None)
        self.assertEqual(prepare.call_count,1)
        self.assertEqual(result['totalRows'],5)
        self.assertEqual([child.wm_target_sheet for child,_kw in calls],['图片汇总','数据汇总'])
        self.assertEqual([child.ui_active_menu for child,_kw in calls],['existing-menu','existing-menu'])
        hashes=[hashlib.sha256(channels.stream_identity(child).encode()).hexdigest()[:24] for child,_kw in calls]
        self.assertNotEqual(*hashes)
        self.assertEqual(hashes[1],hashlib.sha256(b'existing-menu').hexdigest()[:24])
        self.assertEqual([child.wm_release_days for child,_kw in calls],[4,7])

    def test_configuration_roundtrip_preserves_both_source_lists_and_enabled_flags(self):
        from app import _cfg_from_payload
        cfg=self.combined();cfg.wm_active_channel='video'
        parsed=_cfg_from_payload(asdict(cfg),base=Config())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json';save_config(parsed,path);restored=load_config(path)
        rows=channels.normalize_channels(asdict(restored))
        self.assertEqual(rows[0]['settings']['wm_source_urls'],['s'*25,'u'*25])
        self.assertEqual(rows[1]['settings']['wm_source_urls'],['v'*25])
        self.assertEqual(restored.wm_target_url,'t'*25)
        self.assertTrue(all(row['enabled'] for row in rows[:2]))
        self.assertFalse(rows[2]['enabled'])
        self.assertEqual(restored.wm_active_channel,'video')


if __name__=='__main__':unittest.main()
