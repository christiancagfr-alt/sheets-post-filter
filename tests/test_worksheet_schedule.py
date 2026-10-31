"""Clock-controlled scheduling and fake I/O pipeline tests; no real timers or network."""
import copy
import unittest
from dataclasses import asdict
from datetime import datetime,timedelta
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import app
from fetch_posts import Config
import worksheet_channels as channels


class Clock(datetime):
    current=datetime(2026,10,30,10,0)
    @classmethod
    def now(cls,tz=None):
        return cls.fromtimestamp(cls.current.timestamp(),tz)


class WorksheetScheduleTests(unittest.TestCase):
    def setUp(self):
        Clock.current=datetime(2026,10,30,10,0)
        app.stop_all_menu_schedulers()
        self.clock=patch.object(app,'datetime',Clock);self.clock.start();self.addCleanup(self.clock.stop)
        self.thread=patch.object(app,'_ensure_sched_thread');self.thread.start();self.addCleanup(self.thread.stop)
        self.last=patch.object(app,'_schedule_last_run',return_value='');self.last.start();self.addCleanup(self.last.stop)
        self.addCleanup(app.stop_all_menu_schedulers)

    def menu(self,mid='media-one',minutes=180):
        return {'id':mid,'template':'worksheet_merge','settings':{'wm_schedule_enabled':True,'wm_schedule_minutes':minutes,
                'wm_schedule_merge':True,'wm_schedule_publish':True}}

    def test_presets_custom_and_invalid_intervals(self):
        for minutes in (180,360,480,720,17,1):
            self.assertEqual(app._menu_schedule_spec(self.menu(minutes=minutes))['minutes'],minutes)
        for minutes in (0,-1,'bad',525601):
            self.assertIsNone(app._menu_schedule_spec(self.menu(minutes=minutes)))
        item=self.menu();item['settings'].update(wm_schedule_merge=False,wm_schedule_publish=False)
        self.assertIsNone(app._menu_schedule_spec(item))

    def test_first_run_after_interval_and_saving_other_menu_does_not_reset_it(self):
        menus=[self.menu(),self.menu('media-two',360)]
        app.sync_schedulers_from_menus(menus)
        first=app._menu_schedules['media-one']['next_run']
        self.assertEqual(first,Clock.now()+timedelta(hours=3))
        Clock.current+=timedelta(minutes=23)
        app.sync_schedulers_from_menus(menus+[{'id':'filter','template':'filter','settings':{'schedule_enabled':False}}])
        self.assertEqual(app._menu_schedules['media-one']['next_run'],first)
        self.assertEqual(app._menu_schedules['media-two']['minutes'],360)
        app.sync_schedulers_from_menus([menus[1]])
        self.assertFalse(app.menu_schedule_snapshot('media-one')['enabled'])
        self.assertTrue(app.menu_schedule_snapshot('media-two')['enabled'])

    def test_due_job_fires_once_and_busy_job_retries_without_duplicates(self):
        app.sync_schedulers_from_menus([self.menu()]);st=app._menu_schedules['media-one']
        cfg=Config(wm_schedule_enabled=True)
        with patch.object(app,'_validate_worksheet_sync'),patch.object(app,'enqueue_job',return_value=None) as enqueue,patch.object(app,'_remember_schedule_run'),patch.object(app,'_log'):
            app._try_fire(st,'worksheet_sync',cfg_factory=lambda:cfg)
            enqueue.assert_not_called()
            Clock.current+=timedelta(hours=3)
            app._try_fire(st,'worksheet_sync',cfg_factory=lambda:cfg)
            app._try_fire(st,'worksheet_sync',cfg_factory=lambda:cfg)
            enqueue.assert_called_once()
            self.assertEqual(enqueue.call_args.kwargs['job_key'],'media-one')
            self.assertEqual(st['next_run'],Clock.now()+timedelta(hours=3))
            Clock.current+=timedelta(hours=3);enqueue.return_value='busy'
            app._try_fire(st,'worksheet_sync',cfg_factory=lambda:cfg)
            self.assertEqual(st['next_run'],Clock.now()+timedelta(minutes=1))

    def test_restart_keeps_remaining_time_or_catches_up_once_when_overdue(self):
        with patch.object(app,'_schedule_last_run',return_value='2026-10-30T09:00:00'):
            app.sync_schedulers_from_menus([self.menu()])
        self.assertEqual(app._menu_schedules['media-one']['next_run'],Clock(2026,10,30,12,0))
        app.stop_all_menu_schedulers();Clock.current=datetime(2026,10,30,16,0)
        with patch.object(app,'_schedule_last_run',return_value='2026-10-30T09:00:00'):
            app.sync_schedulers_from_menus([self.menu()])
        self.assertEqual(app._menu_schedules['media-one']['next_run'],Clock.now()+timedelta(seconds=12))

    def test_schedule_validation_failure_moves_to_next_period_instead_of_looping(self):
        app.sync_schedulers_from_menus([self.menu()]);Clock.current+=timedelta(hours=3)
        st=app._menu_schedules['media-one']
        with patch.object(app,'_validate_worksheet_sync',side_effect=ValueError('bad config')),patch.object(app,'_record_job_failure'),patch.object(app,'enqueue_job') as enqueue:
            app._try_fire(st,'worksheet_sync',cfg_factory=lambda:Config(wm_schedule_enabled=True))
        enqueue.assert_not_called();self.assertEqual(st['next_run'],Clock.now()+timedelta(hours=3))

    def config(self):
        cfg=Config(ui_active_menu='cycle',wm_source_url='s'*25,wm_target_url='t'*25,wm_target_sheet='视频汇总',wm_media_type='video',
            wm_columns=[{'header':h,'column':c} for h,c in zip(['日期','链接','标题','分类'],'ABCD')])
        cfg.wm_channels=channels.normalize_channels(asdict(cfg))
        cfg.wm_channels[0].update(enabled=True)
        cfg.wm_channels[0]['settings'].update(wm_source_urls=['s'*25],wm_columns=copy.deepcopy(cfg.wm_columns))
        cfg.wm_channels[2].update(enabled=True)
        cfg.wm_channels[2]['settings'].update(wm_publish_source='direct',wm_direct_url='e'*25,wm_direct_sheet='效果')
        return cfg

    def test_merge_then_publish_and_direct_publish_run_once_per_category(self):
        cfg=self.config();events=[]
        def merge(child,**kwargs):
            events.append(('merge',child._wm_channel_name,child.wm_publish_after_sync))
            return {'total_rows':3,'published':{'totalRows':3}}
        def publish(child,**kwargs):
            events.append(('publish',child._wm_channel_name));return {'totalRows':2}
        with patch('worksheet_merge.run_worksheet_merge',side_effect=merge),patch('summary_publish.publish_summary_sheet',side_effect=publish):
            result=channels.run_scheduled_media_sync(cfg,log=lambda _:None)
        self.assertEqual(events,[('merge','图片',True),('merge','KB视频',True),('publish','效果视频')])
        self.assertEqual((result['merged_rows'],result['published_rows'],result['total_rows']),(6,8,8))

    def test_merge_only_ignores_auto_publish_flag_and_publish_only_never_writes_sheet(self):
        cfg=self.config();cfg.wm_schedule_publish=False;cfg.wm_publish_after_sync=True
        with patch('worksheet_merge.run_worksheet_merge',return_value={'total_rows':3}) as merge,patch('summary_publish.publish_summary_sheet') as publish:
            channels.run_scheduled_media_sync(cfg,log=lambda _:None)
        self.assertEqual(merge.call_count,2);self.assertFalse(merge.call_args.args[0].wm_publish_after_sync);publish.assert_not_called()
        cfg.wm_schedule_merge=False;cfg.wm_schedule_publish=True
        with patch('worksheet_merge.run_worksheet_merge') as merge,patch('summary_publish.publish_summary_sheet',return_value={'totalRows':2}) as publish:
            channels.run_scheduled_media_sync(cfg,log=lambda _:None)
        merge.assert_not_called();self.assertEqual(publish.call_count,3)

    def test_concurrent_manual_and_timer_enqueue_is_deduplicated_per_menu(self):
        cfg=Config(ui_active_menu='race-test');state,_=app._job_parts('race-test');state.update(running=False,queued=False)
        with patch.object(app,'_ensure_queue_worker'),patch.object(app._run_queue,'put') as put,patch.object(app,'_log'):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:app.enqueue_job('worksheet_sync',cfg),range(2)))
        self.assertEqual(results.count(None),1);put.assert_called_once();state.update(running=False,queued=False)

    def test_scheduling_flags_roundtrip_in_payload(self):
        cfg=app._cfg_from_payload({'wm_schedule_enabled':True,'wm_schedule_minutes':'480','wm_schedule_merge':False,'wm_schedule_publish':True},base=Config())
        self.assertTrue(cfg.wm_schedule_enabled);self.assertEqual(int(cfg.wm_schedule_minutes),480)
        self.assertFalse(cfg.wm_schedule_merge);self.assertTrue(cfg.wm_schedule_publish)
