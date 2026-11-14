import copy
import unittest
from unittest.mock import Mock,patch

from gallery_management import find_project,connect_existing,change_access_password,restore_publish_secret
from promo_deploy import CloudflareError,deploy_gallery


def site():
    return {'host':'gallery.example.com','project':'example-project','bucket':'example-bucket',
            'secret':'example-publish-key','account_id':'example-owner','kind':'summary',
            'pages_url':'https://example-project.pages.dev','site_url':'https://gallery.example.com',
            'publish_url':'https://gallery.example.com/api/summary-publish'}


def project():
    return {'name':'example-project','subdomain':'example-project.pages.dev',
            'domains':['gallery.example.com','example-project.pages.dev'],
            'deployment_configs':{name:{'r2_buckets':{'GALLERY_CACHE':{'name':'example-bucket'}},
                'env_vars':{'CACHE_PUBLISH_SECRET':{'type':'secret_text','value':''},
                            'OTHER':{'type':'plain_text','value':'example-value'}}} for name in ('production','preview')}}


def api():
    obj=Mock();obj.result.side_effect=lambda x:x
    obj.get.return_value=project()
    return obj


class GalleryManagementTests(unittest.TestCase):
    def test_discover_uses_remote_domain_and_does_not_create(self):
        client=api();client.get.side_effect=[[project()],project()]
        result=find_project(client,'example-owner','gallery.example.com')
        self.assertEqual(result['name'],'example-project')
        client.post.assert_not_called();client.patch.assert_not_called()

    def test_discovery_follows_server_pages_without_forcing_page_size(self):
        client=api()
        client.result.side_effect=lambda value:value['result'] if 'result' in value else value
        client.get.side_effect=[
            {'result':[{'name':'example-other','subdomain':'example-other.pages.dev'}],
             'result_info':{'page':1,'per_page':1,'total_pages':2}},
            {'result':[project()], 'result_info':{'page':2,'total_pages':2}},
            project()]
        self.assertEqual(find_project(client,'example-owner','gallery.example.com')['name'],'example-project')
        self.assertTrue(client.get.call_args_list[1].args[0].endswith('?page=2'))
        self.assertTrue(all('per_page' not in call.args[0] for call in client.get.call_args_list))

    def test_discovery_stops_at_last_server_page(self):
        client=api()
        client.result.side_effect=lambda value:value['result']
        client.get.return_value={'result':[{'name':'example-other'}],
                                 'result_info':{'total_pages':1}}
        self.assertIsNone(find_project(client,'example-owner','gallery.example.com'))
        self.assertEqual(client.get.call_count,1)

    def test_wrong_domain_rejected(self):
        client=api()
        with self.assertRaises(CloudflareError):find_project(client,'example-owner','different.example.com',project_hint='example-project')

    def test_connect_checks_original_key_and_only_saves_local_state(self):
        client=api()
        client.get.side_effect=[[project()],project()]
        with patch('gallery_management.load_cf_state',return_value={}),patch('gallery_management.save_cf_state') as save,patch('summary_publish._post',return_value={'service':'summary-gallery/v1'}) as post:
            result=connect_existing(client,'example-owner','gallery.example.com','example-key')
        self.assertFalse(result['deployed'])
        self.assertEqual(result['project'],'example-project')
        self.assertEqual(post.call_args.args[0],'https://example-project.pages.dev/api/summary-publish')
        self.assertEqual(post.call_args.args[2],{'action':'hello'})
        self.assertEqual(save.call_args.args[0]['sites'][0]['secret'],'example-key')
        client.patch.assert_not_called();client.post.assert_not_called()

    def test_wrong_key_never_generates_replacement_or_mutates_site(self):
        client=api()
        client.get.side_effect=[[project()],project()]
        with patch('gallery_management.load_cf_state',return_value={}),patch('gallery_management.save_cf_state') as save,patch('summary_publish._post',side_effect=RuntimeError('example denied')):
            with self.assertRaises(RuntimeError):connect_existing(client,'example-owner','gallery.example.com','wrong-example-key')
        save.assert_not_called();client.patch.assert_not_called();client.post.assert_not_called()

    def test_password_patch_only_upserts_access_password_not_redacted_secrets(self):
        client=api();state={'sites':[site()]}
        before=copy.deepcopy(project())
        with patch('gallery_management.load_cf_state',return_value=state),patch('gallery_management.save_cf_state') as save,patch('gallery_management.redeploy_existing') as deploy,patch('summary_publish._post',return_value={'ok':True}):
            change_access_password(client,'example-owner','gallery.example.com','example-new-password')
        payload=client.patch.call_args.args[1]
        for config in payload['deployment_configs'].values():
            self.assertEqual(config['env_vars'],{'ACCESS_PASSWORD':{'type':'secret_text','value':'example-new-password'}})
        self.assertEqual(project(),before)
        deploy.assert_called_once()
        self.assertEqual(save.call_args.args[0]['sites'][0]['secret'],'example-publish-key')

    def test_wrong_bucket_blocks_password_patch(self):
        client=api();bad=project();bad['deployment_configs']['production']['r2_buckets']['GALLERY_CACHE']['name']='other-example'
        client.get.return_value=bad
        with patch('gallery_management.load_cf_state',return_value={'sites':[site()]}):
            with self.assertRaises(CloudflareError):change_access_password(client,'example-owner','gallery.example.com','example-password')
        client.patch.assert_not_called()

    def test_migration_without_local_site_does_not_overwrite_existing_remote(self):
        with patch('promo_deploy.load_cf_state',return_value={}),patch('promo_deploy.CloudflareAPI') as factory,patch('gallery_management.find_project',return_value=project()),patch('promo_deploy.generate_publish_secret') as generate:
            with self.assertRaisesRegex(CloudflareError,'已经部署过'):
                deploy_gallery('gallery.example.com',token='example-token',account_id='example-owner',site_kind='summary',create_if_missing=True)
        factory.return_value.post.assert_not_called();factory.return_value.patch.assert_not_called();generate.assert_not_called()

    def test_reuse_skips_deployment_and_does_not_change_key(self):
        with patch('promo_deploy.load_cf_state',return_value={'sites':[site()]}),patch('promo_deploy.CloudflareAPI') as factory,patch('gallery_management.find_project',return_value=project()),patch('gallery_management.connect_existing',return_value={**site(),'deployed':False}) as connect,patch('promo_deploy._create_deployment') as create:
            result=deploy_gallery('gallery.example.com',token='example-token',account_id='example-owner',site_kind='summary')
        self.assertFalse(result['deployed']);create.assert_not_called()
        self.assertEqual(connect.call_args.args[3],'example-publish-key')
        factory.return_value.patch.assert_not_called()

    def test_missing_site_requires_explicit_create(self):
        with patch('promo_deploy.load_cf_state',return_value={}),patch('promo_deploy.CloudflareAPI') as factory,patch('gallery_management.find_project',return_value=None):
            with self.assertRaisesRegex(CloudflareError,'创建新站点'):
                deploy_gallery('gallery.example.com',token='example-token',account_id='example-owner',site_kind='summary')
        factory.return_value.post.assert_not_called();factory.return_value.patch.assert_not_called()

    def test_explicit_original_key_recovery_does_not_submit_access_password(self):
        client=api();original='example-original-publish-key-value'
        with patch('gallery_management.load_cf_state',return_value={'sites':[site()]}),patch('gallery_management.save_cf_state') as save,patch('gallery_management.redeploy_existing'),patch('summary_publish._post',return_value={'service':'summary-gallery/v1'}) as hello:
            restore_publish_secret(client,'example-owner','gallery.example.com',original)
        for config in client.patch.call_args.args[1]['deployment_configs'].values():
            self.assertEqual(config,{'env_vars':{'CACHE_PUBLISH_SECRET':{'type':'secret_text','value':original}}})
        self.assertEqual(save.call_args.args[0]['sites'][0]['secret'],original)
        self.assertEqual(hello.call_args.args[2],{'action':'hello'})

    def test_recovery_without_original_key_never_mutates_cloudflare(self):
        client=api()
        with self.assertRaises(CloudflareError):restore_publish_secret(client,'example-owner','gallery.example.com','')
        client.patch.assert_not_called();client.post.assert_not_called()



class CredentialManagementTests(unittest.TestCase):
    def state_store(self, kind='promo'):
        value=site();value['kind']=kind;value['profile_id']='example-owner'
        value['access_password']='example-old-password'
        state={'sites':[value]}
        def save(updates):state.update(copy.deepcopy(updates))
        return value,state,save

    def test_promo_key_change_verifies_endpoint_and_keeps_other_values(self):
        from gallery_management import change_publish_secret
        value,state,save=self.state_store()
        with patch('gallery_management.load_cf_state',side_effect=lambda:copy.deepcopy(state)),patch('gallery_management.save_cf_state',side_effect=save),patch('gallery_management.redeploy_existing'),patch('gallery_management._sync_saved_publish_settings') as sync,patch('summary_publish._post',return_value={'ok':True,'service':'sheets-gallery-publish-cache'}) as post:
            change_publish_secret(api(),'example-owner',value['host'],'example-new-publish-secret-value')
        self.assertEqual(post.call_args.args[0],'https://example-project.pages.dev/api/publish-cache')
        self.assertEqual(post.call_args.args[2],{'action':'hello'})
        self.assertEqual(state['sites'][0]['secret'],'example-new-publish-secret-value')
        self.assertEqual(state['sites'][0]['access_password'],'example-old-password')
        self.assertEqual(state['pending_credentials'],{})
        sync.assert_called_once()

    def test_promo_password_change_requires_authenticated_response(self):
        value,state,save=self.state_store()
        with patch('gallery_management.load_cf_state',side_effect=lambda:copy.deepcopy(state)),patch('gallery_management.save_cf_state',side_effect=save),patch('gallery_management.redeploy_existing'),patch('summary_publish._post',return_value={'ok':True,'authenticated':True}) as post:
            change_access_password(api(),'example-owner',value['host'],'example-new-password')
        self.assertEqual(post.call_args.args[0],'https://example-project.pages.dev/api/auth')
        self.assertEqual(state['sites'][0]['access_password'],'example-new-password')
        self.assertEqual(state['sites'][0]['secret'],value['secret'])

    def test_failed_deployment_keeps_pending_value_without_claiming_success(self):
        from gallery_management import change_publish_secret
        value,state,save=self.state_store('summary')
        with patch('gallery_management.load_cf_state',side_effect=lambda:copy.deepcopy(state)),patch('gallery_management.save_cf_state',side_effect=save),patch('gallery_management.redeploy_existing',side_effect=RuntimeError('example failure')),patch('summary_publish._post') as post:
            with self.assertRaisesRegex(CloudflareError,'待确认'):
                change_publish_secret(api(),'example-owner',value['host'],'example-pending-secret-value')
        self.assertEqual(state['sites'][0]['secret'],value['secret'])
        self.assertEqual(state['pending_credentials'][value['host']]['secret'],'example-pending-secret-value')
        post.assert_not_called()

    def test_failed_password_verification_keeps_old_confirmed_value(self):
        value,state,save=self.state_store()
        with patch('gallery_management.load_cf_state',side_effect=lambda:copy.deepcopy(state)),patch('gallery_management.save_cf_state',side_effect=save),patch('gallery_management.redeploy_existing'),patch('summary_publish._post',return_value={'ok':True,'authenticated':False}):
            with self.assertRaisesRegex(CloudflareError,'尚未确认'):
                change_access_password(api(),'example-owner',value['host'],'example-new-password')
        self.assertEqual(state['sites'][0]['access_password'],'example-old-password')

    def test_key_change_updates_only_bound_configurations_including_pages_url(self):
        import tempfile,json
        from pathlib import Path
        from gallery_management import _sync_saved_publish_settings
        value=site();value['profile_id']='example-owner'
        own={'cf_profile_id':'example-owner','cf_publish_url':value['pages_url']+'/api/publish-cache','cf_publish_secret':'example-old'}
        other={'cf_profile_id':'example-other-owner','cf_publish_url':own['cf_publish_url'],'cf_publish_secret':'example-keep'}
        different={'cf_profile_id':'example-owner','cf_publish_url':'https://other.example.com/api/publish-cache','cf_publish_secret':'example-keep'}
        data={**own,'ui_menus':[{'settings':own},{'settings':other},{'settings':different}],'ui_archived_menus':[{'settings':dict(own)}]}
        with tempfile.TemporaryDirectory() as folder,patch('fetch_posts.SCRIPT_DIR',Path(folder)):
            path=Path(folder)/'config.json';path.write_text(json.dumps(data))
            _sync_saved_publish_settings(value,'example-new-secret-value')
            from local_secrets import read_private_json
            saved=read_private_json(path,migrate=False)
        self.assertEqual(saved['cf_publish_secret'],'example-new-secret-value')
        self.assertEqual(saved['ui_menus'][0]['settings']['cf_publish_secret'],'example-new-secret-value')
        self.assertEqual(saved['ui_archived_menus'][0]['settings']['cf_publish_secret'],'example-new-secret-value')
        self.assertEqual(saved['ui_menus'][1]['settings']['cf_publish_secret'],'example-keep')
        self.assertEqual(saved['ui_menus'][2]['settings']['cf_publish_secret'],'example-keep')

    def test_wrong_owner_never_patches_or_saves_pending_credentials(self):
        from gallery_management import change_publish_secret
        client=api()
        with patch('gallery_management.load_cf_state',return_value={'sites':[site()]}),patch('gallery_management.save_cf_state') as save:
            with self.assertRaises(CloudflareError):change_publish_secret(client,'example-wrong-owner','gallery.example.com','example-new-secret-value')
        save.assert_not_called();client.patch.assert_not_called()

if __name__=='__main__':unittest.main()
