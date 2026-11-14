import unittest
from unittest.mock import Mock, patch
from gallery_analytics import aggregate_visits, dataset_name, ensure_binding, read_analytics, _CACHE
from promo_deploy import CloudflareError


class AnalyticsTests(unittest.TestCase):
    def test_dedupes_daily_browsers_and_weights_duration(self):
        rows=[{'visitor':'example-a','session':'example-1','started':1767225600,'seconds':20,'weight':1},
              {'visitor':'example-a','session':'example-2','started':1767229200,'seconds':40,'weight':1},
              {'visitor':'example-b','session':'example-3','started':1767225600,'seconds':60,'weight':2}]
        result=aggregate_visits(rows)
        self.assertEqual(result['days'][0]['visitors'],2)
        self.assertEqual(result['days'][0]['visits'],4)
        self.assertEqual(result['days'][0]['average_seconds'],45)
        self.assertEqual(len(result['hours']),2)
        self.assertTrue(result['sampled'])

    def test_bindings_preserve_other_bindings_and_never_patch_secrets(self):
        api=Mock()
        project={'name':'example-project','deployment_configs':{'production':{
            'analytics_engine_datasets':{'OTHER':{'dataset':'example_other'}},'env_vars':{'EXAMPLE':'secret'}}}}
        ensure_binding(api,'example-account',project)
        config=api.patch.call_args.args[1]['deployment_configs']['production']
        self.assertNotIn('env_vars',config)
        self.assertEqual(config['analytics_engine_datasets']['OTHER']['dataset'],'example_other')
        self.assertEqual(config['analytics_engine_datasets']['GALLERY_ANALYTICS']['dataset'],dataset_name('example-project'))
        self.assertNotEqual(dataset_name('example-project'),dataset_name('example-project-2'))

    def test_read_cached_and_scoped_to_account_project_and_hosts(self):
        _CACHE.clear();api=Mock();api.post.return_value={'data':[]}
        site={'host':'example.com','pages_url':'https://example.pages.dev','project':'example-project'}
        config={'analytics_engine_datasets':{'GALLERY_ANALYTICS':{'dataset':dataset_name(site['project'])}}}
        with patch('gallery_management.find_project',return_value={}),patch('gallery_management.validate_project',return_value=config):
            self.assertFalse(read_analytics(api,'example-account',site)['cached'])
            self.assertTrue(read_analytics(api,'example-account',site)['cached'])
        api.post.assert_called_once()
        sql=api.post.call_args.kwargs['raw_body']
        self.assertIn("index1 IN ('example.com','example.pages.dev')",sql)
        self.assertIn('FROM events.analyticsEngine.' + dataset_name(site['project']),sql)
        self.assertNotIn('max(',sql)
        self.assertIn('LIMIT 20001',sql)

    def test_missing_binding_does_not_create_or_query(self):
        _CACHE.clear();api=Mock()
        with patch('gallery_management.find_project',return_value={}),patch('gallery_management.validate_project',return_value={}):
            with self.assertRaisesRegex(ValueError,'尚未启用'):
                read_analytics(api,'example',{'host':'example.com','project':'example'})
        api.patch.assert_not_called();api.post.assert_not_called()
