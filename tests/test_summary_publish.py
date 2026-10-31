import copy
import unittest
from unittest.mock import patch

from fetch_posts import Config
import cf_accounts as accounts
import summary_publish as publish
from sheet_links import cell_link


class SummaryPublishTests(unittest.TestCase):
    def cfg(self,kind='image',days=4):
        return Config(ui_active_menu='images-menu',cf_profile_id='one',wm_gallery_host='media.example.com',
                      wm_media_type=kind,wm_release_days=days,
                      wm_columns=[{'header':name,'column':column} for name,column in zip(['日期','成品链接','名字','分类'],'ABCD')])

    def test_image_and_video_release_dates_categories_and_drive_previews(self):
        row=['2026-10-20','成品文件','名字','示例分类1']
        url='https://drive.google.com/file/d/EXAMPLE_IDENTIFIER_0066/view'
        for kind,days,date in [('image',4,'2026-10-24'),('video',7,'2026-10-27')]:
            assets,meta,skipped=publish.rows_to_summary_assets(self.cfg(kind,days),[row],[{1:url}])
            self.assertEqual(assets[0]['releaseDate'],date)
            self.assertEqual(assets[0]['type'],'示例分类1')
            self.assertIn('thumbnail',assets[0]['previewUrl'])
            self.assertEqual(assets[0]['mediaType'],kind)
            self.assertEqual(meta['releaseCounts'][date]['示例分类1'],1)
            self.assertEqual(skipped,0)
        self.assertTrue(assets[0]['videoUrl'].endswith('/preview'))
        self.assertIn('export=download',assets[0]['downloadUrl'])
        self.assertNotEqual(assets[0]['downloadUrl'],assets[0]['viewUrl'])

    def test_download_link_keeps_drive_resource_key_and_other_file_hosts(self):
        url='https://drive.google.com/file/d/EXAMPLE_IDENTIFIER_0066/view?resourcekey=shared-key'
        self.assertIn('resourcekey=shared-key',publish.download_url(url))
        other='https://files.example.com/image.png?id=abc123456789'
        self.assertEqual(publish.download_url(other),other)

    def test_category_exclusions_filter_only_published_assets_and_counts(self):
        cfg=self.cfg();cfg.wm_gallery_exclude_categories=[' 不分发 ','未分类']
        rows=[['2026-10-20','https://example.com/a.jpg','甲','不分发'],
              ['2026-10-20','https://example.com/b.jpg','乙','保留'],
              ['2026-10-20','https://example.com/c.jpg','丙','']]
        before=copy.deepcopy(rows)
        assets,metadata,skipped=publish.rows_to_summary_assets(cfg,rows)
        self.assertEqual([asset['type'] for asset in assets],['保留'])
        self.assertEqual(metadata['releaseCounts'],{'2026-10-24':{'保留':1}})
        self.assertEqual(metadata['excludedRows'],2);self.assertEqual(skipped,0)
        self.assertEqual(rows,before)

    def test_excluding_all_valid_rows_publishes_empty_stream_but_invalid_rows_still_preserve_old_data(self):
        cfg=self.cfg();cfg.wm_gallery_exclude_categories=['不分发']
        context={'site':{'host':'media.example.com'},'url':'https://media.example.com/api/summary-publish','secret':'test','features':['incremental-v1']}
        with patch.object(publish,'_post',return_value={'ok':True}) as send:
            publish.publish_summary_rows(cfg,[['2026-10-20','https://example.com/a.jpg','甲','不分发']],log=lambda _:None,_context=context)
        calls=[item.args[2] for item in send.call_args_list]
        self.assertEqual([body['action'] for body in calls],['status','finalize'])
        self.assertEqual(calls[-1]['totalRows'],0);self.assertEqual(calls[-1]['chunkCount'],0)
        self.assertEqual(calls[-1]['metadata']['releaseCounts'],{})
        with self.assertRaisesRegex(ValueError,'没有可发布'):
            publish.rows_to_summary_assets(cfg,[['bad date','bad link','甲','不分发']])

    def test_merge_keeps_excluded_category_rows_in_google_target(self):
        from test_worksheet_merge import FakeBook,FakeSheet
        import worksheet_merge as merge
        cfg=self.cfg();cfg.wm_source_url='s'*25;cfg.wm_target_url='t'*25;cfg.wm_target_sheet='结果'
        cfg.wm_publish_after_sync=True;cfg.wm_gallery_exclude_categories=['不分发']
        source=FakeBook('s'*25,[FakeSheet('来源',1,[['日期','成品链接','标题','分类'],
            ['2026-10-20','https://example.com/a.jpg','甲','不分发'],['2026-10-20','https://example.com/b.jpg','乙','保留']])])
        target=FakeBook('t'*25,[])
        with patch.object(merge,'authorize_cfg',return_value=object()),patch.object(merge,'open_by_url_or_id',side_effect=[source,target]),patch.object(publish,'_prepare_publish',return_value={'site':{'host':'media.example.com'},'url':'https://media.example.com/api/summary-publish','secret':'test'}),patch.object(publish,'_post',return_value={'ok':True}) as send:
            merge.run_worksheet_merge(cfg,log=lambda _:None)
        self.assertEqual([row[3] for row in target.sheets[0].data[1:]],['不分发','保留'])
        chunk=next(call.args[2] for call in send.call_args_list if call.args[2]['action']=='put-chunk')
        self.assertEqual([asset['type'] for asset in chunk['assets']],['保留'])

    def test_adjustable_columns_thumbnail_and_date_delay(self):
        cfg=self.cfg('video',0)
        cfg.wm_target_start_col='C'
        cfg.wm_date_col='F';cfg.wm_link_col='E';cfg.wm_category_col='C';cfg.wm_title_col='D';cfg.wm_thumbnail_col='G'
        cfg.wm_columns.append({'header':'额外','column':'E'})
        rows=[['自定义类别','标题','https://example.com/a.mp4','2026-10-20','https://example.com/thumb.jpg']]
        assets,meta,_skip=publish.rows_to_summary_assets(cfg,rows)
        self.assertEqual(assets[0]['releaseDate'],'2026-10-20')
        self.assertEqual(assets[0]['previewUrl'],'https://example.com/thumb.jpg')
        self.assertEqual(assets[0]['type'],'自定义类别')

    def test_all_invalid_rows_do_not_replace_site_and_bad_columns_fail(self):
        with self.assertRaisesRegex(ValueError,'没有可发布'):publish.rows_to_summary_assets(self.cfg(),[['date','bad','x','cat']])
        cfg=self.cfg();cfg.wm_category_col='Z'
        with self.assertRaisesRegex(ValueError,'列范围'):publish.media_settings(cfg)

    def test_publish_handshake_stream_isolation_and_rule_changes(self):
        cfg=self.cfg()
        site={'host':cfg.wm_gallery_host,'secret':'secret','publish_url':'https://media.example.com/api/summary-publish'}
        sent=[]
        def post(url,secret,body,cancelled):
            sent.append(copy.deepcopy(body))
            return {'ok':True,'service':'summary-gallery/v1'}
        with patch.object(publish,'site_for_menu',return_value=({},site)),patch.object(publish,'_post',side_effect=post):
            publish.publish_summary_rows(cfg,[['2026-10-20','https://example.com/a.jpg','甲','分类']],log=lambda _:None)
            first=sent[-1]
            cfg.ui_active_menu='videos-menu';cfg.wm_media_type='video';cfg.wm_release_days=7
            publish.publish_summary_rows(cfg,[['2026-10-20','https://example.com/a.mp4','甲','分类']],log=lambda _:None)
        self.assertNotEqual(first['stream'],sent[-1]['stream'])
        self.assertEqual(sent[-1]['metadata']['delayDays'],7)
        self.assertEqual([body['action'] for body in sent],['hello','put-chunk','finalize']*2)

    def test_old_gallery_protocol_is_rejected_before_upload(self):
        cfg=self.cfg();site={'host':cfg.wm_gallery_host,'secret':'s','publish_url':'https://media.example.com/api/summary-publish'}
        with patch.object(publish,'site_for_menu',return_value=({},site)),patch.object(publish,'_post',return_value={'ok':True,'service':'old'}) as post:
            with self.assertRaisesRegex(ValueError,'不是新的'):publish.publish_summary_rows(cfg,[],log=lambda _:None)
            self.assertEqual(post.call_count,1)

    def test_maker_column_is_independent_from_title_and_supports_g(self):
        cfg=self.cfg()
        cfg.wm_columns += [{'header':'附加','column':column} for column in 'EFG']
        assets,_,_=publish.rows_to_summary_assets(cfg,[['2026-10-20','https://example.com/a.jpg','素材标题','类别','','','制作人甲']])
        self.assertEqual(assets[0]['maker'],'制作人甲')
        self.assertEqual(assets[0]['title'],'素材标题')
        cfg.wm_maker_col=''
        self.assertEqual(publish.rows_to_summary_assets(cfg,[['2026-10-20','https://example.com/a.jpg','素材标题','类别']])[0][0]['maker'],'')

    def test_incremental_publish_skips_unchanged_and_resumes_existing_chunks(self):
        cfg=self.cfg();sent=[]
        context={'site':{'host':cfg.wm_gallery_host},'url':'https://media.example.com/api/summary-publish','secret':'test','features':['incremental-v1']}
        rows=[['2026-10-20','https://example.com/'+str(i)+'.jpg','甲','分类'] for i in range(1001)]
        def upload(url,secret,body,cancelled):
            sent.append(copy.deepcopy(body))
            return {'ok':True}
        with patch.object(publish,'_post',side_effect=upload):
            publish.publish_summary_rows(cfg,rows,log=lambda _:None,_context=context)
        final=sent[-1];hashes=final['chunkHashes']
        self.assertEqual(len(hashes),3)
        sent.clear()
        def unchanged(url,secret,body,cancelled):
            sent.append(body);return {'ok':True,'buildId':final['buildId']}
        with patch.object(publish,'_post',side_effect=unchanged):
            result=publish.publish_summary_rows(cfg,rows,log=lambda _:None,_context=context)
        self.assertTrue(result['skipped']);self.assertEqual(len(sent),1)
        sent.clear()
        def resumed(url,secret,body,cancelled):
            sent.append(body)
            return {'ok':True,'chunkHashes':hashes[:2]} if body['action']=='status' else {'ok':True}
        with patch.object(publish,'_post',side_effect=resumed):
            publish.publish_summary_rows(cfg,rows,log=lambda _:None,_context=context)
        self.assertEqual([body['action'] for body in sent],['status','put-chunk','finalize'])
        self.assertEqual(sent[1]['index'],3)

    def test_hyperlink_formula_and_rich_text_targets_are_supported(self):
        self.assertEqual(cell_link({'userEnteredValue':{'formulaValue':'=HYPERLINK("https://example.com/x","成品")'}}),'https://example.com/x')
        self.assertEqual(cell_link({'textFormatRuns':[{'format':{'link':{'uri':'https://example.com/y'}}}]}),'https://example.com/y')
        self.assertEqual(cell_link({'hyperlink':'javascript:alert(1)'}),'')

    def test_manual_publish_reads_saved_target_hyperlink_labels(self):
        from test_worksheet_merge import FakeSheet,FakeBook
        cfg=self.cfg();cfg.wm_target_url='t'*25;cfg.wm_source_url='s'*25;cfg.wm_target_sheet='结果'
        ws=FakeSheet('结果',1,[['日期','成品链接','标题','分类'],['2026-10-20','点击观看','标题','分类']])
        ws.links[1,1]='https://drive.google.com/file/d/abc1234567890123456/view'
        book=FakeBook('t'*25,[ws])
        with patch.object(publish,'_prepare_publish',return_value={'ready':True}),patch.object(publish,'authorize_cfg',return_value=object()),patch.object(publish,'open_by_url_or_id',return_value=book),patch.object(publish,'publish_summary_rows',return_value={'ok':True}) as send:
            publish.publish_summary_sheet(cfg,log=lambda _:None)
        self.assertEqual(send.call_args.args[1][0][1],'点击观看')
        self.assertEqual(send.call_args.args[2][0][1],ws.links[1,1])

    def test_522_fallback_is_verified_against_selected_accounts_pages_project(self):
        profile={'account_id':'owner','api_token':'local-test'}
        site={'host':'media.example.com','project':'owned-project','secret':'test','publish_url':'https://media.example.com/api/summary-publish'}
        with patch.object(publish,'site_for_menu',return_value=(profile,site)),patch.object(publish,'_post',side_effect=[publish.GalleryPublishHTTPError(522,'media.example.com'),{'ok':True,'service':'summary-gallery/v1'}]) as send,patch('promo_deploy.CloudflareAPI') as api_class:
            api=api_class.return_value
            api.result.return_value={'name':'owned-project','subdomain':'owned-project.pages.dev'}
            context=publish._prepare_publish(self.cfg(),log=lambda _:None)
        api.get.assert_called_once_with('/accounts/owner/pages/projects/owned-project')
        self.assertEqual(context['url'],'https://owned-project.pages.dev/api/summary-publish')
        self.assertEqual(send.call_args_list[1].args[0],context['url'])

    def test_account_or_domain_verification_failure_does_not_send_secret_to_fallback(self):
        profile={'account_id':'owner','api_token':'local-test'}
        site={'host':'media.example.com','project':'owned-project','secret':'test'}
        with patch.object(publish,'site_for_menu',return_value=(profile,site)),patch.object(publish,'_post',side_effect=publish.GalleryPublishHTTPError(522,'media.example.com')) as send,patch('promo_deploy.CloudflareAPI') as api_class:
            api_class.return_value.result.return_value={'name':'different','subdomain':'different.pages.dev'}
            with self.assertRaisesRegex(ValueError,'无法核验'):publish._prepare_publish(self.cfg(),log=lambda _:None)
        self.assertEqual(send.call_count,1)

    def test_manual_publish_checks_endpoint_before_reading_any_sheets(self):
        cfg=self.cfg();cfg.wm_source_url='s'*25;cfg.wm_target_url='t'*25
        with patch.object(publish,'_prepare_publish',side_effect=publish.GalleryPublishHTTPError(522,'media.example.com')),patch.object(publish,'authorize_cfg') as auth:
            with self.assertRaises(publish.GalleryPublishHTTPError):publish.publish_summary_sheet(cfg,log=lambda _:None)
        auth.assert_not_called()

    def test_legacy_gallery_detects_link_changes_and_fingerprints_each_destination(self):
        import publish_cloudflare as legacy
        asset={'id':'a','date':'2026-10-20','postLink':'https://example.com/one'}
        changed={**asset,'postLink':'https://example.com/two'}
        self.assertNotEqual(legacy.assets_fingerprint([asset]),legacy.assets_fingerprint([changed]))
        state={'cf_fingerprint':legacy.assets_fingerprint([asset])}
        cfg=Config(cf_publish_url='https://one.example.com/api/publish-cache',cf_publish_secret='s')
        with patch.object(legacy,'rows_to_assets',return_value=[asset]),patch.object(legacy,'load_sync_state',side_effect=lambda:copy.deepcopy(state)),patch.object(legacy,'save_sync_state',side_effect=lambda value:state.update(copy.deepcopy(value))),patch.object(legacy,'remember_cf_fingerprint'),patch.object(legacy,'_http_json',return_value={'ok':True}) as send:
            first=legacy.publish_assets_to_cloudflare(cfg,[],[],log=lambda _:None)
            cfg.cf_publish_url='https://two.example.com/api/publish-cache'
            second=legacy.publish_assets_to_cloudflare(cfg,[],[],log=lambda _:None)
            cfg.cf_publish_url='https://one.example.com/api/publish-cache'
            third=legacy.publish_assets_to_cloudflare(cfg,[],[],log=lambda _:None)
        self.assertFalse(first.get('skipped',False));self.assertFalse(second.get('skipped',False));self.assertTrue(third['skipped'])
        self.assertEqual(send.call_count,4)


class AccountIsolationTests(unittest.TestCase):
    def setUp(self):
        self.state={}
        self.load=patch.object(accounts,'load_cf_state',side_effect=lambda:copy.deepcopy(self.state))
        self.save=patch.object(accounts,'save_cf_state',side_effect=lambda data:self.state.update(copy.deepcopy(data)))
        self.load.start();self.save.start()
        self.addCleanup(self.load.stop);self.addCleanup(self.save.stop)

    def test_multiple_tokens_and_menu_scopes_survive_new_login(self):
        accounts.register_accounts('token-one',[{'id':'one','name':'一'}],allowed_menu_ids=['images'])
        accounts.register_accounts('token-two',[{'id':'two','name':'二'}],allowed_menu_ids=['videos'])
        rows={row['id']:row for row in accounts.profiles()}
        self.assertEqual(rows['one']['api_token'],'token-one')
        self.assertEqual(rows['two']['api_token'],'token-two')
        self.assertEqual(accounts.require_menu('one','images')['id'],'one')
        with self.assertRaisesRegex(ValueError,'未获准'):accounts.require_menu('one','videos')

    def test_legacy_account_site_owner_not_changed_by_another_login(self):
        self.state={'account_id':'old','account_name':'老账号','api_token':'example-old-token',
                    'host':'old.example.com','project':'old-project','bucket':'old-bucket','secret':'old-secret'}
        accounts.register_accounts('new-token',[{'id':'new','name':'新账号'}])
        site=accounts.known_gallery_sites(self.state)[0]
        self.assertEqual(site['account_id'],'old')
        self.assertEqual({row['id'] for row in accounts.profiles()},{'old','new'})

    def test_selected_account_cannot_publish_other_account_site(self):
        accounts.register_accounts('token-one',[{'id':'one','name':'一'}],allowed_menu_ids=['images'])
        self.state['sites']=[{'host':'media.example.com','project':'p','bucket':'b','account_id':'two','kind':'summary'}]
        with self.assertRaisesRegex(ValueError,'不属于'):accounts.site_for_menu('one','images','media.example.com',kind='summary')


if __name__=='__main__':unittest.main()
